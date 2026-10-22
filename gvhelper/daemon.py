# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The loop: ask for work, do it, hand it back, ask again.

Everything interesting about this file is how it fails, because a helper spends
almost all of its life waiting and the waiting is where the bugs are.

**A network failure is not an error, it is weather.** A laptop sleeps, changes
network, loses wifi in a lift. Every one of those surfaces as an exception from
a poll, and the right response to all of them is to wait a moment and poll
again. So the loop retries with a backoff and says so quietly, rather than
exiting and leaving a user to discover hours later that their helper stopped.

**The one exception is a 401**, which is the relay saying this machine is not
linked any more -- unlinked from account settings, or the account deleted. That
will still be true tomorrow, so it stops the loop and says what to do.

**A failed analysis is reported, not swallowed.** If `gvanalysis` raises, the
job is failed with the engine's own message and the loop continues. The
alternative -- dropping it -- leaves the browser on a spinner until the relay's
lease expires, which is minutes of a person watching nothing happen.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from . import USER_AGENT, __version__
from .client import POLL_WAIT, Job, RelayError, Unauthorized, WorkerClient
from .config import Config, machine_name
from . import machine, power
from .runner import (
    PresetRefused, Progress, analyze, available_presets, engine_features, engine_version,
    lower_priority,
)
from .ogsync import LocalWork, Syncer
from .store import load_token

#: How often progress is posted while a job runs.
#:
#: **One second, because that is what the browser polls at.** This number's
#: only consumer is a progress bar somebody is watching, and `gva/analyze.js`
#: asks the relay for it every `POLL_INTERVAL_MS` = 1000ms -- so anything
#: slower than that is a bar moving in visible steps while the browser asks
#: five times per step for news that has not changed. It was 5s, chosen
#: against the two bounds below without checking the rate at the other end.
#:
#: Those bounds are both still satisfied with room to spare, which is how the
#: wrong number survived: it must be far under the relay's 300s lease or a
#: long decision looks like a hang, and far over one decision's cost (tens of
#: milliseconds) or the helper spends its time reporting instead of analysing.
#: 1s sits 300x under one and ~30x over the other.
#:
#: The cost is one small POST a second for the length of a run, which also
#: renews the lease -- the shared worker gets this for free by writing counts
#: to a `Manager` dict on every decision (`gvserver/jobs.py`), an option a
#: process on somebody else's computer does not have.
PROGRESS_INTERVAL = 1.0

#: Backoff after a failed poll: start here, double, stop at the ceiling.
#: The ceiling is a minute because that is roughly how long a person waits
#: before wondering whether the thing is working, and the floor is two seconds
#: so a blip costs nothing noticeable.
RETRY_MIN = 2.0
RETRY_MAX = 60.0

#: The file a terminal pauses a running helper through, in `config_dir()`.
#:
#: A file rather than a signal or a socket because it is the one thing that
#: works the same on macOS, Windows and Linux, and the config directory is
#: already private to this user. It holds one word: `pause` or `resume` is a
#: request from `gammonview-helper pause` / `resume`, and the helper answers by
#: writing `paused` or removing the file. The answer is what lets the command
#: tell somebody the truth when no helper is running to hear it.
PAUSE_FILE = "paused"
PAUSE, RESUME, PAUSED = "pause", "resume", "paused"

#: How often a running helper looks at `PAUSE_FILE`: a stat a second on a
#: machine that is mostly waiting, and the longest a terminal waits to hear.
FOLLOW_INTERVAL = 1.0

#: How often a resting helper -- on battery, or outside its hours, by its
#: computer's settings -- looks again. Half a minute: plugging a laptop in
#: should feel answered, and the probe costs a subprocess on a Mac.
REST_INTERVAL = 30.0

#: How often a resting helper says hello, to hear a settings change it would
#: otherwise only hear on a poll it is not making.
REST_HELLO_INTERVAL = 60.0


def read_flag(path: Path | None) -> str | None:
    """What `PAUSE_FILE` says, or None when there is nothing in it to read."""
    if path is None:
        return None
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


class Control:
    """What the menu-bar item can ask of the loop, and what it reads back.

    The loop runs on a background thread under the menu (AppKit owns the main
    one), so this is the whole of the interface between them: two requests
    going in -- pause, stop -- and a few facts coming out for the menu to draw.
    The terminal path builds one too and never touches it, so there is a
    single loop rather than a menu-bar variant of it.

    **Pause lets the current match finish.** It was queued from somebody's own
    browser and is partly done; abandoning it would cost them the work and a
    five-minute wait for the lease to lapse. Pause stops the *next* claim.

    **Stop does not.** Quit is the user saying the machine is wanted for
    something else right now, so the running job is failed with a sentence the
    browser shows, rather than left to time out.

    **`flag` is how a terminal reaches it.** `gammonview-helper pause` runs in
    another process, so it writes a request into a file (see `PAUSE_FILE`) and
    `follow` applies it here. The file also mirrors the menu's own Pause, so a
    helper paused from the menu can be resumed from the terminal and the other
    way round.
    """

    def __init__(self, flag: Path | None = None) -> None:
        self._cond = threading.Condition()
        self._paused = False
        self._stopping = False
        self._flag = flag
        #: "starting" | "unlinked" | "idle" | "busy" | "paused" | "retrying"
        self.state = "starting"
        #: The running job's counters, or None between jobs.
        self.progress: Progress | None = None
        #: The email of the account this machine is linked to, once `hello`
        #: has said; None before that, when unlinked, and on an older site.
        self.account: str | None = None
        #: The OpenGammon sync's line for the menu, or None when sync is off.
        self.og_line: str | None = None
        #: Why this computer is taking no work by its settings ("On battery"),
        #: or None. Not a pause: nobody pressed anything, and it lifts itself.
        self.resting: str | None = None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stopping(self) -> bool:
        return self._stopping

    def pause(self) -> None:
        with self._cond:
            self._paused = True
            self._cond.notify_all()
        self._mark(PAUSED)

    def resume(self) -> None:
        with self._cond:
            self._paused = False
            self._cond.notify_all()
        self._mark(None)

    def _mark(self, content: str | None) -> None:
        """Mirror the state into the flag file, which is also the answer to
        a request in it. Best effort: a read-only config directory costs the
        terminal its control and nothing else."""
        if self._flag is None:
            return
        try:
            if content is None:
                self._flag.unlink(missing_ok=True)
            else:
                self._flag.write_text(content)
        except OSError:
            pass

    def follow(self, interval: float = FOLLOW_INTERVAL) -> threading.Thread:
        """Apply requests from `gammonview-helper pause` / `resume`, on a thread.

        Whatever the file says when this starts is left over from a helper that
        has gone, so it is cleared: Pause is "not right now", the same as from
        the menu, and does not outlive the process.
        """
        self._mark(None)

        def run() -> None:
            while not self._stopping:
                request = read_flag(self._flag)
                if request == PAUSE:
                    self.pause()
                elif request == RESUME:
                    self.resume()
                self.sleep(interval)

        thread = threading.Thread(target=run, name="pause-flag", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    def wait_while_paused(self) -> None:
        """Block until resumed or stopped."""
        with self._cond:
            self._cond.wait_for(lambda: not self._paused or self._stopping)

    def sleep(self, seconds: float) -> None:
        """`time.sleep`, cut short by `stop` -- so Quit never waits out a backoff."""
        with self._cond:
            self._cond.wait_for(lambda: self._stopping, timeout=seconds)


def _sleep(control: Control | None, seconds: float) -> None:
    # `time.sleep` when nobody is listening, which is what the tests replace.
    if control is None:
        time.sleep(seconds)
    else:
        control.sleep(seconds)


#: What the browser shows when the helper is quit mid-analysis.
QUIT_MESSAGE = "GammonView Helper was quit on {machine} before this analysis finished."


def sign_off(client: WorkerClient) -> None:
    """Tell the site to stop routing work here. Best effort, and never raises.

    Failing to send it costs what it cost before the route existed -- two
    minutes of the site believing a quiet helper is still there -- so it is not
    worth surfacing, let alone worth refusing to pause over.
    """
    try:
        client.offline()
    except Exception as e:  # noqa: BLE001 - best effort, per the docstring
        _log(f"could not tell the site this computer is pausing: {e}")


def _log(message: str) -> None:
    """One line, flushed, with a clock on it.

    stdout rather than a logging framework: the helper runs under launchd or
    systemd, both of which capture stdout into the platform's own log, and
    a user asked to send their log should be able to read it first.
    """
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _parallelism(cfg: Config, settings: machine.Settings | None) -> tuple[int, int]:
    return machine.parallelism(cfg.jobs, cfg.threads, settings or machine.Settings())


def run_job(client: WorkerClient, job: Job, cfg: Config, control: Control | None = None,
            settings: machine.Settings | None = None) -> None:
    """Analyse one job and deliver it, or report why not.

    The analysis runs on a thread so this one can keep posting progress -- which
    is also what renews the lease, so a job that takes ten minutes is not
    reclaimed at five. `daemon=True` on the thread is deliberate: if the helper
    is killed mid-analysis the process should go, not hang waiting for an
    engine to finish work nobody is waiting for any more.
    """
    progress = Progress()
    result: dict = {}
    jobs, threads = _parallelism(cfg, settings)

    def work() -> None:
        try:
            if job.preset_spec is not None:
                result["bytes"] = analyze(
                    job.match_bytes, job.preset_name or "", progress,
                    jobs=jobs, threads=threads, spec=job.preset_spec,
                    resign_errors=job.resign_errors,
                )
            else:
                result["bytes"] = analyze(
                    job.match_bytes, job.preset, progress, jobs=jobs, threads=threads,
                    resign_errors=job.resign_errors,
                )
        except BaseException as e:  # noqa: BLE001 - reported, then re-raised to nobody
            result["error"] = e

    thread = threading.Thread(target=work, name=f"analyze-{job.id}", daemon=True)
    _log(f"analyzing {job.id} ({job.preset or 'default preset'}"
         f"{', judging resignations' if job.resign_errors else ''})")
    if control is not None:
        control.progress = progress
    thread.start()

    last_posted: tuple[int, int] | None = None
    while thread.is_alive():
        thread.join(PROGRESS_INTERVAL)
        if control is not None and control.stopping:
            # The engine thread is a daemon and goes with the process; what
            # must not go with it is the browser's answer.
            _log(f"job {job.id} abandoned: the helper is quitting")
            client.fail(job.id, QUIT_MESSAGE.format(machine=machine_name()))
            return
        done, total = progress.get()
        # Posted even when the counts have not moved, because this doubles as
        # the lease renewal: a single very slow decision must not read as a
        # stalled worker. Skipped only before the total is known, when there is
        # nothing to say and the relay would record a total of zero.
        if total or last_posted:
            try:
                client.progress(job.id, done, total)
                last_posted = (done, total)
            except Unauthorized:
                raise
            except RelayError as e:
                # Not fatal. The analysis is the expensive part and it is still
                # running; a progress post that failed will be retried five
                # seconds from now, and the lease has minutes on it.
                _log(f"progress not posted: {e}")

    if "error" in result:
        error = result["error"]
        _log(f"job {job.id} failed: {error}")
        # A refused preset is the user's to fix and says so in a sentence;
        # anything else keeps its type, which is the clue to a bug.
        message = str(error) if isinstance(error, PresetRefused) else f"{type(error).__name__}: {error}"
        client.fail(job.id, message)
        return

    payload = result.get("bytes")
    if not payload:  # pragma: no cover - a thread that neither raised nor produced
        client.fail(job.id, "The analysis produced nothing.")
        return

    client.deliver(job.id, payload)
    done, total = progress.get()
    _log(f"delivered {job.id} ({total or done} decisions, {len(payload)} bytes)")


def run_synced(item: dict, cfg: Config, control: Control | None = None,
               settings: machine.Settings | None = None) -> None:
    """Analyse one synced OpenGammon `.mat` for the sync thread waiting on it.

    `run_job`'s shape without the relay: there is no job to post progress to,
    so the counts go on the item instead, and the sync thread -- which may have
    a player watching the "Check OpenGammon" button -- says how far it has got.
    The menu shows it like any other match and Quit abandons it the same way.
    The sync thread posts the result.
    """
    progress = Progress()
    item["progress"] = progress  # read by the sync thread, for the button
    result: dict = {}
    jobs, threads = _parallelism(cfg, settings)

    def work() -> None:
        try:
            result["bytes"] = analyze(
                item["mat"].encode("utf-8"), item.get("preset") or "", progress,
                jobs=jobs, threads=threads, suffix=".mat",
                spec=item.get("spec"), resign_errors=bool(item.get("resign_errors")),
            )
        except BaseException as e:  # noqa: BLE001 - handed to the sync thread
            result["error"] = e

    _log(f"analyzing OpenGammon match {item['label']} ({item.get('preset') or 'default preset'})")
    thread = threading.Thread(target=work, name=f"og-{item['label']}", daemon=True)
    if control is not None:
        control.progress = progress
    thread.start()
    while thread.is_alive():
        thread.join(PROGRESS_INTERVAL)
        if control is not None and control.stopping:
            return  # the sync thread is released by `LocalWork.close`
    if "error" in result:
        error = result["error"]
        item_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        _finish(item, error=item_error)
    else:
        _finish(item, result=result.get("bytes"))


def _finish(item: dict, **kw) -> None:
    item["_work"].finish(item, **kw)


def start_sync(cfg: Config, token: str, name: str, control: Control | None,
               current: machine.Current | None = None,
               available=None) -> tuple[Syncer, threading.Thread]:
    """The OpenGammon sync, on a thread of its own with a client of its own.

    Its own `WorkerClient` so the sync's requests never queue behind the work
    loop's held poll. It reads its setting from `hello`; while the player has
    sync off, all it does is ask again every few minutes.

    Its hellos keep this computer's settings in `current`, as the loop's own
    polls do, and say whether the loop is taking work (`available`): a hello
    from a paused helper used to clear the site's pause, and put the computer
    back in the menu within five minutes of pressing Pause.
    """
    sync_client = WorkerClient(cfg, token, settings=current)
    if available is not None:
        sync_client.available = available
    syncer = Syncer(
        sync_client,
        lambda: sync_client.register(name, __version__, engine_version(), available_presets()),
        presets=available_presets,
        features=engine_features,
        work=LocalWork(),
        control=control,
    )

    def run() -> None:
        try:
            syncer.run()
        finally:
            sync_client.close()

    thread = threading.Thread(target=run, name="opengammon-sync", daemon=True)
    thread.start()
    return syncer, thread


#: How often an unlinked helper looks in the credential store.
#:
#: The number exists because pairing and polling are two processes. `link`
#: registers the worker with the relay *immediately*, so the website starts
#: offering this machine work at once -- but the daemon only finds the new
#: credential when it next looks. Anything it waits here is time the site
#: believes there is a helper and no helper is listening.
#:
#: Measured, not guessed: the first job on gammonview.com sat queued for 31
#: seconds, which was the login item's restart throttle, not the engine. Three
#: seconds puts that gap under the one-second dispatch latency plus a blink,
#: and costs a keyring read every three seconds on a machine that is by
#: definition doing nothing else.
LINK_POLL_INTERVAL = 3.0

#: How often waiting says so out loud. The wait is unbounded -- a login item
#: on a machine nobody has paired yet is a correct state that can last for
#: days -- so this is the difference between a log and a log file.
LINK_LOG_INTERVAL = 900.0


def wait_for_token(
    cfg: Config, *, current: str | None = None, control: Control | None = None
) -> str | None:
    """Block until the credential store holds a usable token.

    Returns None only when `control` is stopped -- Quit from the menu while the
    helper is waiting to be linked.

    Two callers, one behaviour. A helper that has never been linked waits with
    `current=None`; a helper whose token the relay just rejected waits with the
    dead one, so that re-reading the same revoked credential does not count as
    success and spin.

    **This is why the daemon no longer exits when it is not linked.** It used
    to return 2, which under a login item means: launchd restarts it, it exits
    again, and the restart throttle -- 60 seconds, chosen to keep exactly that
    loop quiet -- becomes the delay before a freshly paired machine starts
    working. Waiting in-process instead removes the loop, so there is nothing
    to throttle, and it does so identically on macOS, Linux and Windows rather
    than needing `launchctl kickstart`, `systemctl --user restart`, and
    whatever Windows would have wanted.
    """
    said_at = 0.0
    if control is not None:
        control.state = "unlinked"
        control.account = None
    while True:
        if control is not None and control.stopping:
            return None
        token = load_token(cfg)
        if token and token != current:
            return token
        now = time.monotonic()
        if said_at == 0.0 or now - said_at >= LINK_LOG_INTERVAL:
            _log("not linked yet -- waiting for `gammonview-helper link`")
            said_at = now
        _sleep(control, LINK_POLL_INTERVAL)


def serve(
    cfg: Config, token: str, *, once: bool = False, control: Control | None = None
) -> int:
    """Poll for work until something stops us. Returns a process exit code.

    `once` runs a single poll-and-work cycle, which is what the tests use and
    what makes a "does this actually work" check possible without a signal.

    `control` is the menu-bar item's handle on the loop; see `Control`. Pause
    and stop are both checked between polls, so a held poll (up to 25s) runs
    out first -- and anything it claims in that time is run, because a claimed
    job has nowhere else to go until its lease lapses.
    """
    lower_priority(cfg.nice)
    # This computer's settings, as the site last said them: one holder, kept
    # by both this loop's client and the sync thread's.
    current = machine.Current()
    client = WorkerClient(cfg, token, settings=current)
    name = machine_name()
    #: Why the settings have this computer resting, or None. A list so the
    #: `available` closure below reads the loop's latest answer.
    resting: list[str | None] = [None]

    def available() -> bool:
        return resting[0] is None and not (control is not None and control.paused)

    client.available = available

    try:
        account = client.hello(name, __version__, engine_version(), available_presets())
        if control is not None:
            control.account = account
    except Unauthorized as e:
        _log(str(e))
        return 2
    except RelayError as e:
        # Not fatal: `hello` is how the site learns this machine's presets, and
        # a helper that cannot register can still claim work. Failing to start
        # over a cosmetic call would be the wrong trade.
        _log(f"could not register: {e}")

    _log(f"{USER_AGENT} linked as {name!r} -> {cfg.api_base}")
    _log(f"presets: {', '.join(available_presets()) or '(engine not installed)'}")

    # Not under `once`, which is one poll-and-work cycle for the tests and has
    # nothing for a background thread to do.
    syncer = None if once else start_sync(cfg, token, name, control, current, available)[0]
    work = syncer.work if syncer is not None else None

    delay = RETRY_MIN
    signed_off = False
    next_hello = 0.0
    try:
        while True:
            if control is not None:
                if control.stopping:
                    return 0
                if control.paused:
                    # Said once per pause rather than once per loop: the
                    # menu has normally said it already, and this is the
                    # retry for when that attempt could not reach the site.
                    if not signed_off:
                        sign_off(client)
                        signed_off = True
                        _log("paused")
                    control.state = "paused"
                    control.wait_while_paused()
                    continue
                if signed_off and resting[0] is None:
                    _log("resumed")
                    signed_off = False
                control.state = "idle"

            # Resting by this computer's settings: like a pause, except that
            # nobody pressed anything and it lifts itself. Signed off once, so
            # the site stops offering this computer at once; a hello now
            # and then, unavailable, to hear the settings change.
            settings = current.get()
            reason = machine.resting(
                settings, at=time.time(),
                on_battery=None if settings.on_battery else power.on_battery(),
            )
            if reason is not None:
                if resting[0] is None:
                    resting[0] = reason
                    sign_off(client)
                    signed_off = True
                    _log(f"not taking work: {reason[0].lower()}{reason[1:]}")
                    next_hello = time.monotonic() + REST_HELLO_INTERVAL
                elif time.monotonic() >= next_hello:
                    next_hello = time.monotonic() + REST_HELLO_INTERVAL
                    try:
                        client.register(name, __version__, engine_version(), available_presets())
                    except Unauthorized as e:
                        _log(str(e))
                        return 2
                    except RelayError as e:
                        _log(f"could not hear this computer's settings: {e}")
                resting[0] = reason
                if control is not None:
                    control.resting = reason
                    control.state = "resting"
                if once:
                    return 0
                _sleep(control, REST_INTERVAL)
                continue
            if resting[0] is not None:
                resting[0] = None
                signed_off = False
                if control is not None:
                    control.resting = None
                    control.state = "idle"
                _log("taking work again")

            try:
                # A synced match waiting to be analysed asks without holding the
                # poll open: the site's own jobs still go first, but an empty
                # queue should not cost the sync twenty seconds a match -- unless
                # the settings hold synced matches while the computer is in use.
                waiting = work is not None and work.pending()
                held = waiting and machine.holds_sync(
                    settings, idle=power.idle_seconds() if settings.sync_when_idle else None)
                job = client.next_job(wait=0 if waiting and not held else POLL_WAIT)
                delay = RETRY_MIN
                # A poll may have brought new settings; the match runs by them.
                settings = current.get()
                if job is None and work is not None and not held:
                    item = work.take()
                    if item is not None:
                        item["_work"] = work
                        if control is not None:
                            control.state = "busy"
                        try:
                            run_synced(item, cfg, control, settings)
                        finally:
                            if control is not None:
                                control.progress = None
                if job is not None:
                    if control is not None:
                        control.state = "busy"
                    try:
                        run_job(client, job, cfg, control, settings)
                    finally:
                        if control is not None:
                            control.progress = None
                if once:
                    return 0
            except Unauthorized as e:
                _log(str(e))
                _log("Run `gammonview-helper link` to link this computer again.")
                return 2
            except RelayError as e:
                # The status is part of the message because of what the first
                # real failure looked like: "Could not ask for work." repeated
                # every few minutes, with nothing to say whether that was the
                # relay refusing, the account gone, or -- as it turned out -- a
                # proxy 504. A number here would have named it immediately.
                where = f" (HTTP {e.status})" if e.status else ""
                _log(f"{e}{where} -- retrying in {delay:.0f}s")
            except Exception as e:  # noqa: BLE001 - weather, per the module docstring
                _log(f"{type(e).__name__}: {e} -- retrying in {delay:.0f}s")
            else:
                continue
            if once:
                return 1
            if control is not None:
                control.state = "retrying"
            _sleep(control, delay)
            delay = min(RETRY_MAX, delay * 2)
    except KeyboardInterrupt:
        _log("stopped")
        return 0
    finally:
        if work is not None:
            work.close()
        client.close()


def main() -> int:  # pragma: no cover - thin wrapper, exercised via the CLI
    from .cli import main as cli_main

    return cli_main(["run"] + sys.argv[1:])
