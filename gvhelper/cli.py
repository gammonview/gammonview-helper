# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""`gammonview-helper` -- link this computer, then analyse on it.

Four commands, and the first one is the only one most people will ever type:

    gammonview-helper link       # pair this machine with a GammonView account
    gammonview-helper run        # poll for work (what the login item runs)
    gammonview-helper status     # what is configured, and is the link alive
    gammonview-helper unlink     # forget this machine's credentials

`pause` and `resume` are the menu's Pause and Resume, for a helper with no menu
to click -- one over SSH, or on a desktop that shows no status icons.

And `start`, which nobody types: it is what opening "GammonView Helper" in
Applications runs (`macapp.py`), in the Windows Start menu (`winapp.py`), or
in a Linux desktop's applications (`linuxapp.py`). On Linux, where there is no
installer, `install` and `uninstall` are the installer.

Written for a terminal a non-technical person has been talked into opening. The
output is sentences rather than log lines, the word to confirm is unmissable,
and every failure says what to do next instead of only what went wrong -- the
audience for this app skews older and non-technical, and a stack trace is where
they stop.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser

from . import __version__
from .client import PairingClient, RelayError, Unauthorized, WorkerClient
from .config import AUTO, Config, config_dir, load, machine_name, platform_name, save
from .runner import available_presets, engine_version
from .store import backend_name, clear_token, load_token, save_token
from .winlog import log_when_windowless


def _say(message: str = "") -> None:
    print(message, flush=True)


def _banner(word: str) -> None:
    """The word, drawn so that it cannot be mistaken for anything else.

    This is the security-critical line of the whole install: the user is about
    to match it against three words in a browser, and a word they skim past is
    a word they will guess at. So it gets a box, blank lines, and nothing near
    it competing for attention.
    """
    line = "─" * (len(word) + 8)
    _say()
    _say(f"    ┌{line}┐")
    _say(f"    │    {word}    │")
    _say(f"    └{line}┘")
    _say()


def _minutes(seconds: int) -> str:
    """`300` as "about 5 minutes": a count of seconds that large is not read."""
    if seconds < 120:
        return f"about {seconds} seconds"
    return f"about {round(seconds / 60)} minutes"


def browser_here() -> bool:
    """Whether offering this computer's browser is a question worth asking.

    Not over SSH, where "this computer" is the one at the far end and its
    browser -- if it has one -- is on nobody's screen. Not on a Linux box with
    no display, which is the same thing without the SSH. And not without a
    terminal to answer in, since a prompt nobody can answer is a line of noise.
    """
    if sys.stdin is None or not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def open_in_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:  # pragma: no cover - a machine with no browser
        return False


def _enter_pressed(stop: threading.Event) -> bool:
    """Wait for Enter, giving up when `stop` is set. True if it was pressed.

    Looks rather than blocks, because the pairing finishing has to be able to
    end the wait: a thread parked in `input()` outlives the question it was
    asking, and holds the lock on stdin that interpreter shutdown wants. So
    the descriptor is read directly, and only once `select` says a line is
    there to read.
    """
    if sys.platform == "win32":
        import msvcrt

        while not stop.wait(0.1):
            while msvcrt.kbhit():
                if msvcrt.getwch() in "\r\n":
                    return True
        return False

    import select

    fd = sys.stdin.fileno()
    while not stop.is_set():
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            return bool(os.read(fd, 1024))  # empty is end of input: stop asking
    return False


class _HumanLink:
    """`link`, narrated for somebody who has been talked into opening a terminal.

    It shows the page rather than opening it. The default browser is not
    always the one somebody signs in with, and the computer being linked is not
    always the one they are sitting at -- it can be a box across the room, or
    one at the end of an SSH connection. The page works from any device, so
    the link is the instruction, and this computer's browser is an offer:
    Enter takes it, while the pairing is polled in the meantime.
    """

    def __init__(self, ask: bool = False):
        self._ask = ask
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    def offer(self, machine: str, platform: str, word: str, url: str, expires_in: int) -> None:
        _say(f"Linking this computer ({machine}, {platform}) to GammonView.")
        _say("Open this page in a browser, on this computer or any other device:")
        _say(f"  {url}")
        _say()
        _say("Sign in if you are asked, then choose this word:")
        _banner(word)
        _say(f"You have {_minutes(expires_in)}.")
        if self._ask:
            _say("Press Enter to open the page in this computer's browser.")
            self._watcher = threading.Thread(
                target=self._watch, args=(url,), name="helper-enter", daemon=True
            )
            self._watcher.start()
        _say()

    def _watch(self, url: str) -> None:
        while _enter_pressed(self._stop):
            if not open_in_browser(url):
                _say("No browser opened here. Open the page above on any device instead.")

    def _done(self) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=1)

    def linked(self, store_name: str, worker_id: str | None) -> None:
        self._done()
        _say("This computer is linked.")
        _say(f"Credentials stored in: {store_name}")
        _say()
        # Said this way round because the common case is soon to be a helper
        # already running as a login item, which picks the new credential up
        # within `LINK_POLL_INTERVAL` without being told. Somebody who started
        # it by hand needs the command; somebody who did not should not be sent
        # to run a second copy.
        _say("If GammonView Helper is already running, it will start")
        _say("analysing within a few seconds. If not, start it with:")
        _say("  gammonview-helper run")

    def failed(self, reason: str, retry: bool) -> None:
        self._done()
        _say(reason)
        if retry:
            _say("Run `gammonview-helper link` to try again.")


class _PorcelainLink:
    """`link`, narrated for a program: one JSON object per line on stdout.

    What reads this is the launcher -- the native installer of step 7, which
    has to draw the confirmation word in its own window because the box
    `_HumanLink` draws is a terminal artefact. It could have scraped the human
    output for the word instead, and that would make every rewording of a
    sentence a bug in a **frozen binary already on other people's computers**,
    which is the one class of bug this project has no way to fix.

    Equally it could have reimplemented the device flow natively, and must not:
    pairing is versioned (`RELAY_CONTRACT`) and is exactly the kind of thing
    that changes, so it stays in the package the launcher can be told to
    upgrade.

    The compatibility rule is the release manifest's rule, and is simpler than
    a format version: **fields are added, never removed and never repurposed**,
    and a reader ignores what it does not recognise. A launcher built against
    today's `offer` keeps working against a helper that learns to send more.

    Two further promises the reader depends on. Exactly one terminal event --
    `linked` or `failed` -- is emitted on every path, so silence is never an
    outcome a reader has to interpret. And the output is ASCII: `json.dumps`
    escapes non-ASCII by default and that default is kept deliberately, because
    a machine called `Bj\u00f6rns MacBook` must not turn into a
    `UnicodeEncodeError` on a Windows console still running a legacy code page.
    """

    def _emit(self, event: str, **fields: object) -> None:
        _say(json.dumps({"event": event, **fields}))

    def offer(self, machine: str, platform: str, word: str, url: str, expires_in: int) -> None:
        self._emit(
            "offer",
            machine=machine,
            platform=platform,
            word=word,
            url=url,
            expires_in=expires_in,
        )

    def linked(self, store_name: str, worker_id: str | None) -> None:
        self._emit("linked", store=store_name, worker_id=worker_id)

    def failed(self, reason: str, retry: bool) -> None:
        self._emit("failed", reason=reason, retry=retry)


def cmd_link(cfg: Config, args: argparse.Namespace) -> int:
    """The device flow, from this side.

    Note what this command never learns: which account it is being linked to.
    The helper says what machine it is and shows a word; a signed-in person
    elsewhere decides whether that machine may analyse for them. That is what
    lets one unmodified installer work for anybody.

    Every line it produces goes through `out`, which is the whole of the
    difference `--porcelain` makes -- there is no second code path through the
    flow itself, so the installer and the terminal cannot disagree about what
    happened.

    `--no-browser` predates the offer, when the browser was opened without
    asking; it now only takes the offer away. It stays, because the installers
    already on people's computers pass it.
    """
    if args.porcelain:
        out = _PorcelainLink()
    else:
        out = _HumanLink(ask=not args.no_browser and browser_here())
    return pair(cfg, out, args.name or machine_name())


def offer_text(word: str, url: str) -> str:
    """What the menus' Link dialog says, on all three. The question is theirs."""
    return (
        "Open this page in a browser, on this computer or any other device:\n\n"
        f"{url}\n\n"
        "Sign in if you are asked, then choose this word:\n\n"
        f"{word}\n\n"
        "If the word is not one of the three you are shown, close the page: "
        "something is wrong."
    )


def pair(cfg: Config, out, machine: str) -> int:
    """The flow itself, narrated through `out` (`offer`, `linked`, `failed`).

    Separate from `cmd_link` because the menu-bar item links too, and must run
    this very flow rather than a copy: pairing is the part of the helper most
    likely to change, and two copies of it is one that falls behind.

    It never opens a browser. Whether to, and which, is the narrator's
    question to put to the person, in whatever it is talking to them through.
    """
    pairing = PairingClient(cfg)
    platform = platform_name()

    try:
        offer = pairing.start(machine, platform)
    except RelayError as e:
        # No retry line: whatever stopped `start` -- no network, a site that is
        # down, a typo in `--site` -- running the same command again will not
        # fix, and telling somebody to try again is how they try four times.
        out.failed(f"Could not start linking: {e}", retry=False)
        return 1

    url = cfg.link_url(offer["code"])
    expires_in = int(offer.get("expires_in") or 60)
    out.offer(machine, platform, offer["word"], url, expires_in)

    # Poll until it is confirmed or the offer expires. The relay holds each poll
    # open for up to 30s, so a 60s window is two round trips and there is no
    # busy-waiting to throttle. A deadline of our own rather than trusting the
    # server's 404: the message a user needs when nothing happened is "it timed
    # out, run it again", which the 404 alone does not distinguish from "that
    # code was wrong".
    deadline = time.monotonic() + expires_in + 5
    while time.monotonic() < deadline:
        try:
            answer = pairing.poll(offer["code"], offer["secret"])
        except RelayError as e:
            # The relay deletes the row on a wrong word, so this is the honest
            # end of the unhappy path as well as the expiry. Both mean: start
            # again, and it is one command.
            out.failed(f"Linking did not complete: {e}", retry=True)
            return 1
        if answer.get("status") == "linked":
            save_token(cfg, answer["token"])
            cfg.worker_id = answer.get("worker_id")
            save(cfg)
            out.linked(backend_name(cfg), cfg.worker_id)
            return 0

    out.failed("Nobody confirmed in time.", retry=True)
    return 1


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    """Poll for work, waiting rather than exiting when there is no credential.

    **The loop is the point.** `serve` returns 2 for exactly one reason -- the
    relay would not accept this credential, either because there is none or
    because it was revoked -- and exiting on that was wrong once the helper
    became a login item. It turns the supervisor's restart throttle into the
    delay before a newly paired machine does any work, and `link` registers the
    worker with the site the moment you confirm it, so the site offers this
    machine jobs during the whole of that gap. Measured on gammonview.com: 31
    seconds of a 36-second first analysis.

    Waiting in-process fixes it everywhere at once, where kicking the
    supervisor would have meant a `launchctl` path, a `systemctl --user` path
    and a Windows path, each able to rot on its own.

    `--once` keeps the old behaviour and must: it is what the tests and a
    "does this work" check use, and neither can block for a credential that is
    never coming.
    """
    from .daemon import PAUSE_FILE, Control, serve, wait_for_token

    token = load_token(cfg)
    if not token and args.once:
        _say("This computer is not linked yet.")
        _say("Run `gammonview-helper link` first.")
        return 2

    def loop(control: Control | None) -> int:
        nonlocal token
        while True:
            if not token:
                _say("This computer is not linked yet. Waiting for `gammonview-helper link`.")
                token = wait_for_token(cfg, control=control)
                if token is None:  # Quit from the menu while waiting
                    return 0
            code = serve(cfg, token, once=args.once, control=control)
            if code != 2 or args.once:
                return code
            # The relay rejected this credential. Not fatal and not a reason to
            # stop: somebody re-pairing is the ordinary fix, and this way the
            # helper picks it up by itself. `current=` so the revoked token does
            # not read as an answer.
            token = wait_for_token(cfg, current=token, control=control)
            if token is None:
                return 0

    if sys.platform.startswith("linux") and not args.once:
        from . import linuxapp

        # How `update` in a terminal moves a running helper onto the new code.
        linuxapp.restart_on_hangup()

    # `--once` is a check that must return, so it neither follows `pause` nor
    # draws a menu, which runs until somebody quits it.
    if args.once:
        return loop(None)
    control = Control(flag=config_dir() / PAUSE_FILE)
    control.follow()
    if not args.no_menu:
        from . import tray

        if tray.available():
            return tray.run(cfg, control, lambda: loop(control))
    return loop(control)


#: How long `pause` and `resume` wait for the running helper to answer: two of
#: its looks at the file, and a little over.
ANSWER_WAIT = 2.5


def _ask_running_helper(request: str, answered) -> bool:
    """Write `request` into the pause file and wait for the helper to answer.

    False when nothing answered, meaning no helper is running in this config
    directory. The request is taken back then, so a helper started later does
    not find it -- though it would clear it anyway (`Control.follow`).
    """
    from .daemon import FOLLOW_INTERVAL, PAUSE_FILE, read_flag

    flag = config_dir() / PAUSE_FILE
    flag.write_text(request)
    deadline = time.monotonic() + max(ANSWER_WAIT, 2 * FOLLOW_INTERVAL + 0.5)
    while time.monotonic() < deadline:
        if answered(read_flag(flag)):
            return True
        time.sleep(0.1)
    flag.unlink(missing_ok=True)
    return False


def cmd_pause(cfg: Config, args: argparse.Namespace) -> int:
    """The menu's Pause, from a terminal: finish the current match, take no more.

    Also tells the site at once, as the menu does, rather than leaving it to
    the helper's next look -- which can be a held poll or a whole match away.
    """
    from .daemon import PAUSE, PAUSED, sign_off

    if not _ask_running_helper(PAUSE, lambda said: said == PAUSED):
        _say("GammonView Helper is not running, so there is nothing to pause.")
        return 1
    token = load_token(cfg)
    if token:
        client = WorkerClient(cfg, token)
        try:
            sign_off(client)
        finally:
            client.close()
    _say("GammonView Helper is paused. A match it is analysing now will finish;")
    _say("it will not take any more until you run `gammonview-helper resume`.")
    return 0


def cmd_resume(cfg: Config, args: argparse.Namespace) -> int:
    """Undo `pause` -- or Pause from the menu: the two are one state."""
    from .daemon import RESUME

    if not _ask_running_helper(RESUME, lambda said: said is None):
        _say("GammonView Helper is not running. Start it with `gammonview-helper start`.")
        return 1
    _say("GammonView Helper is taking matches again.")
    return 0


def _parallelism(cfg: Config) -> str:
    """The `jobs x threads` line, with `0` spelt out.

    Printed for somebody diagnosing "why is this slow", so a bare `0 jobs x 0
    threads` is the one thing it must not say -- zero reads as *none* and the
    setting means the opposite.
    """
    jobs = "auto" if cfg.jobs == AUTO else str(cfg.jobs)
    threads = "auto" if cfg.threads == AUTO else str(cfg.threads)
    sized = " (sized by the engine)" if AUTO in (cfg.jobs, cfg.threads) else ""
    return f"{jobs} jobs x {threads} threads{sized}"


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    """What is configured, and -- if asked -- whether the link still works.

    The check is a real request, because every interesting way this breaks is
    invisible from here: a token revoked in account settings, a site that moved,
    an account deleted. Local state will look perfect in all three.

    `--porcelain` exists for one question the launcher has to answer on every
    run after the first: **is this machine already linked?** Pairing again when
    it is leaves a second worker registered against the account, and the user
    sees a machine listed twice in settings with no way to tell which is real.
    Reading `linked     : yes` out of the report below would have made a column
    of aligned prose into a load-bearing interface.
    """
    token = load_token(cfg)
    facts = {
        "event": "status",
        "version": __version__,
        "machine": machine_name(),
        "platform": platform_name(),
        "site": cfg.api_base,
        "linked": bool(token),
        "worker_id": cfg.worker_id,
        "store": backend_name(cfg),
        "engine": engine_version(),
        "presets": available_presets(),
    }

    if not args.porcelain:
        _say(f"gammonview-helper {__version__}")
        _say(f"  computer   : {facts['machine']} ({facts['platform']})")
        _say(f"  site       : {facts['site']}")
        _say(f"  linked     : {'yes' if token else 'no'}")
        if cfg.worker_id:
            _say(f"  worker id  : {cfg.worker_id}")
        _say(f"  credentials: {facts['store']}")
        _say(f"  engine     : {facts['engine']}")
        _say(f"  presets    : {', '.join(facts['presets']) or '(engine not installed)'}")
        _say(f"  parallelism: {_parallelism(cfg)}, nice {cfg.nice}")

    def report(link: str | None, human: str | None) -> None:
        """One line, in whichever of the two languages was asked for."""
        if args.porcelain:
            _say(json.dumps({**facts, "link": link}))
        elif human:
            _say(f"  link       : {human}")

    if not token:
        # Not an error state to a launcher -- it is the state a fresh install is
        # in, and the reason the launcher is about to run `link`.
        report(None, None)
        return 1

    client = WorkerClient(cfg, token)
    try:
        # `hello` rather than a dedicated ping: it is idempotent, it is the call
        # the daemon makes at startup anyway, and making it here means `status`
        # also refreshes what the site knows this machine can do.
        account = client.hello(machine_name(), __version__, engine_version(), available_presets())
        if account and not args.porcelain:
            _say(f"  account    : {account}")
        report("working", "working")
        return 0
    except Unauthorized:
        report(
            "revoked",
            "this computer is no longer linked (unlink it here and link again)",
        )
        return 1
    except RelayError as e:
        report("unreachable", f"could not reach the relay ({e})")
        return 1
    finally:
        client.close()


def unlink(cfg: Config, *, remote: bool = True) -> tuple[str, str | None]:
    """Both halves of unlinking, as `cmd_unlink` describes them.

    Returns what happened to the account's row -- "removed", "left" (tried and
    failed, with the reason) or "skipped" -- and the reason. A function of its
    own because the menu-bar item unlinks too, and must do it this way rather
    than with a copy that forgets the order.
    """
    token = load_token(cfg)

    # "skipped" is a truthful third answer and not a synonym for "left": one
    # means nobody tried, the other means it was tried and did not work. The
    # launcher shows a different sentence for each.
    account = "skipped"
    reason = None
    if token and remote:
        client = WorkerClient(cfg, token)
        try:
            client.retire()
            account = "removed"
        except RelayError as e:
            account, reason = "left", str(e)
        finally:
            client.close()

    clear_token(cfg)
    cfg.worker_id = None
    save(cfg)
    return account, reason


def cmd_unlink(cfg: Config, args: argparse.Namespace) -> int:
    """Unlink this computer: the row in the account, then the credentials here.

    This used to be local only, and said so at length -- the helper holds a
    worker token, a worker token is refused by every route taking
    `current_user`, so it could not remove its own row and telling someone
    otherwise would have left them believing they had revoked something they
    had not. All of that is still true of the account routes. What changed is
    that the relay grew one worker route that is not about a job:
    `DELETE /relay/worker`, which names no machine and can therefore only reach
    the caller's own. See `worker_retire` in `gvaccounts/relay.py` for why that
    is safe to offer a credential otherwise trusted with nothing.

    **Order matters and is the opposite of the obvious one.** The remote half
    goes first, because it needs the token that the local half destroys. Doing
    it the other way round would leave the row unremovable from here forever,
    which is precisely the state this command exists to stop happening.

    **A failure to reach the site is not a failure to unlink.** Someone
    uninstalling on a laptop with no network still wants the credentials off
    the machine, and refusing would leave them with software they asked to have
    removed. So the local half runs unconditionally and the outcome of the
    remote half is *reported* rather than raised -- the one thing that must not
    happen is claiming the row is gone when it is not.
    """
    account, reason = unlink(cfg, remote=not args.local_only)

    if args.porcelain:
        # `--porcelain` is the installer's Remove and nothing else, and the
        # installer predates the app the helper writes into Applications, so it
        # cannot delete it. Done here, the last moment the package still exists.
        if sys.platform == "darwin":
            from . import macapp

            macapp.remove()
        _say(json.dumps({"event": "unlinked", "account": account, "reason": reason}))
        return 0

    _say("This computer has forgotten its GammonView credentials.")
    _say("It will not receive any more matches.")
    _say()
    if account == "removed":
        _say("It has also been removed from your account settings.")
    elif account == "left":
        _say(f"It could not be removed from your account settings ({reason}).")
        _say("Use Unlink there to finish, when you are next online.")
    else:
        _say("The machine may still be listed in your account settings on the website.")
        _say("Use Unlink there to remove it.")
    return 0


def cmd_start(cfg: Config, args: argparse.Namespace) -> int:
    """Start the login item if it is stopped: what opening the app does."""
    if sys.platform == "win32":
        from . import winapp

        winapp.tell(winapp.start())
        return 0
    if sys.platform.startswith("linux"):
        from . import linuxapp

        linuxapp.tell(linuxapp.start(os.path.abspath(sys.argv[0])))
        return 0
    if sys.platform != "darwin":
        _say("`start` is for macOS, Windows and Linux. Elsewhere, run `gammonview-helper run`.")
        return 2
    from . import macapp

    message = macapp.start()
    if message is not None:
        macapp.tell(message)
    return 0


def cmd_update(cfg: Config, args: argparse.Namespace) -> int:
    """Install the version the site has released, and restart onto it.

    The one command a person is told to type when a helper is out of date, so
    it does both halves -- the install and the restart -- that otherwise take
    two commands somebody has to know. See `update.py` for which installs it
    can update.
    """
    from . import update

    release = update.latest(cfg)
    if release is None:
        _say(f"Could not reach {cfg.site} to ask which version is current.")
        return 1
    version = release["version"]
    if not update.is_release(__version__):
        _say(f"This is a development build ({__version__}); update it from its checkout.")
        return 0
    if not update.newer(version, __version__):
        _say(f"GammonView Helper {__version__} is up to date.")
        return 0

    helper = os.path.abspath(sys.argv[0])
    root = update.installer_root(helper)
    if sys.platform == "win32" and root is not None:
        # Windows will not replace files a running process has loaded, and
        # this command and the login item both run out of the venv the update
        # replaces. So it is handed to a process outside it (`winmaint`).
        from . import winapp

        if winapp.start_update(root, version, release.get("python")):
            _say(f"Updating GammonView Helper to {version} in the background. It takes a")
            _say("minute or two, and the helper starts again when it is done.")
            _say(f"Its log is {root / 'logs' / 'update.log'}.")
            return 0
        _say(f"GammonView Helper {version} is out. To update, download the GammonView")
        _say("installer from gammonview.com, open it, and choose Update.")
        return 1

    where = update.target(helper)
    if where is None:
        _say("This copy was not installed by the GammonView installer or with `uv tool`,")
        _say("so update it the way you installed it.")
        return 1

    _say(f"Updating GammonView Helper {__version__} to {version}. This takes a minute or two.")
    problem = update.install(where, release)
    if problem is not None:
        _say(f"The update did not finish: {problem}")
        return 1
    if update.restart_login_item():
        _say(f"Updated to {version}, and restarted.")
    else:
        _say(f"Updated to {version}. If the helper is running, stop it and start it again")
        _say("to use the new version.")
    return 0


def cmd_uninstall(cfg: Config, args: argparse.Namespace) -> int:
    """Take the helper off this Windows computer: what Settings > Apps runs.

    The installer's own Uninstall, done from inside the package because the
    installer keeps no copy of itself to be run (see `winmaint`). Same order:
    unlink first, while there is still a token to do it with, then the login
    item, then the files -- the last of them left to `cmd`, since the
    interpreter running this is one of them.
    """
    if sys.platform.startswith("linux"):
        return _uninstall_linux(cfg, args)
    if sys.platform != "win32":
        _say("`uninstall` is for Windows and Linux. On macOS, open the GammonView")
        _say("installer and choose Uninstall.")
        return 2
    import subprocess

    from . import winapp, winmaint

    root = winapp.install_root()
    if root is None:
        _say("This copy was not installed by the GammonView installer, so remove it the")
        _say("way you installed it.")
        return 1
    if not args.yes and not winapp.ask(
        "Uninstall GammonView Helper?",
        "This removes GammonView Helper from this computer and unlinks it from "
        "your GammonView account.\n\n"
        "Your matches and analyses on gammonview.com are not affected.",
    ):
        return 1

    account, reason = unlink(cfg)
    sid = winapp.current_sid()
    if sid is not None:
        for verb in ("/End", "/Delete"):
            extra = ["/F"] if verb == "/Delete" else []
            subprocess.run(["schtasks", verb, "/TN", winapp.task_name(sid), *extra],
                           capture_output=True, **winapp.quiet())
    # This process and the venv's launcher waiting on it are spared; both are
    # gone by the time `cmd` deletes the root.
    winmaint.stop_everything_under(
        [root / "tools", root / "bin", root / "python"], spare={os.getpid(), os.getppid()})
    winmaint.remove_app_entry()

    if account == "removed":
        closing = "It has also been removed from your account settings."
    elif account == "left":
        closing = (f"It could not be removed from your account settings ({reason}), so it "
                   "may still be listed there. Use Unlink beside it on gammonview.com.")
    else:
        closing = ("It may still be listed in your account settings. Use Unlink beside it "
                   "on gammonview.com.")
    message = ("GammonView Helper is no longer on this computer.\n\n" + closing
               + "\n\nYou can install it again any time from gammonview.com.")
    if args.yes:
        _say(message)
    else:
        winapp.tell(("Removed", message))
    # Last, so the wait in `cmd` starts when this process is about to end
    # rather than while a dialog is open.
    winmaint.delete_later(root)
    return 0


def _entry_point() -> str | None:
    """The installed `gammonview-helper` command, which desktop files can run.

    Not a checkout's `python -m gvhelper`: a login item pointing into somebody's
    working copy would run whatever is checked out at the next log-in.
    """
    helper = os.path.abspath(sys.argv[0])
    return helper if os.path.basename(helper) == "gammonview-helper" else None


def _linked_account(account: str | None, reason: str | None) -> str:
    if account == "removed":
        return "It has also been removed from your account settings."
    if account == "left":
        return (f"It could not be removed from your account settings ({reason}). "
                "Use Unlink beside it on gammonview.com.")
    return "It may still be listed in your account settings. Use Unlink beside it on gammonview.com."


def cmd_install(cfg: Config, args: argparse.Namespace) -> int:
    """Set the helper up on Linux, where there is no installer to do it.

    The second half of `uv tool install gammonview-helper && gammonview-helper
    install`: link the machine if it is not linked, then what the installers
    create on the other two -- a login item and something to open (see
    `linuxapp`) -- and start it.
    """
    if not sys.platform.startswith("linux"):
        _say("`install` is for Linux. On macOS and Windows, download the GammonView")
        _say("installer from gammonview.com.")
        return 2
    from . import linuxapp, linuxtray

    helper = _entry_point()
    if helper is None:
        _say("Run this as `gammonview-helper install`, from the copy you installed with `uv tool`.")
        return 1
    if not load_token(cfg):
        code = cmd_link(cfg, argparse.Namespace(name=None, no_browser=False, porcelain=False))
        if code != 0:
            return code
        _say()

    linuxapp.install(helper)
    _say("GammonView Helper will now start by itself whenever you log in.")
    desktop = linuxtray.available() and not os.environ.get("SSH_CONNECTION")
    if not desktop:
        _say("It will start at your next log-in.")
        return 0
    if linuxapp.running():
        linuxapp.restart_running()
    else:
        linuxapp.start(helper)
    if linuxtray.has_host():
        _say("It is running now. " + linuxapp.STARTED[1])
    else:
        # Also what a locked screen says: GNOME switches extensions off while
        # it is locked, and the icon comes back on its own when it unlocks.
        _say("It is running now. Your desktop is not showing status icons at the moment,")
        _say("so there is no icon yet; it appears when they are shown. On GNOME, status")
        _say("icons come from the AppIndicator extension, which Ubuntu turns on.")
    _say("To remove it: gammonview-helper uninstall")
    return 0


def _uninstall_linux(cfg: Config, args: argparse.Namespace) -> int:
    """`install` undone: unlink, stop, remove the desktop files, then the tool.

    The same order as Windows, for the same reason -- the token is needed to
    unlink, so that goes first. Unlike Windows, nothing here is in the way of
    deleting the files this process runs from, so `uv` does it directly.
    """
    import subprocess

    from . import linuxapp, update

    helper = _entry_point()
    if not args.yes:
        _say("This removes GammonView Helper from this computer and unlinks it from your")
        _say("GammonView account. Your matches and analyses on gammonview.com are not affected.")
        try:
            if input("Remove it? [y/N] ").strip().lower() not in ("y", "yes"):
                return 1
        except EOFError:
            return 1

    account, reason = unlink(cfg)
    linuxapp.stop_running()
    linuxapp.remove()
    _say("GammonView Helper is unlinked, and will no longer start when you log in.")
    _say(_linked_account(account, reason))

    where = update.target(helper) if helper else None
    if where is None:
        _say("This copy was not installed with `uv tool`, so remove its files the way you")
        _say("installed it.")
        return 0
    done = subprocess.run([where.uv, "tool", "uninstall", "gammonview-helper"],
                          env=where.env, capture_output=True, text=True)
    if done.returncode != 0:
        _say("Its files could not be removed: run `uv tool uninstall gammonview-helper`.")
        return 1
    _say("Its files have been removed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gammonview-helper",
        description="Analyze GammonView matches on your own computer.",
    )
    parser.add_argument("--version", action="version", version=f"gammonview-helper {__version__}")
    parser.add_argument(
        "--site",
        help="GammonView site to talk to (default: %s)" % Config().site,
    )
    sub = parser.add_subparsers(dest="command")

    link = sub.add_parser("link", help="link this computer to a GammonView account")
    link.add_argument("--name", help="what to call this computer on the confirmation screen")
    link.add_argument(
        "--no-browser",
        action="store_true",
        help="show the link without offering to open it here (for a machine with no browser)",
    )
    link.add_argument(
        "--porcelain",
        action="store_true",
        help="emit one JSON object per line instead of sentences (what the installer reads)",
    )
    link.set_defaults(func=cmd_link)

    run = sub.add_parser("run", help="wait for matches to analyze")
    run.add_argument(
        "--once",
        action="store_true",
        help="do one poll and exit, instead of running until stopped",
    )
    run.add_argument(
        "--no-menu",
        action="store_true",
        help="do not show the menu-bar item (macOS)",
    )
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="show configuration and check the link")
    status.add_argument(
        "--porcelain",
        action="store_true",
        help="emit one JSON object instead of a report (what the installer reads)",
    )
    status.set_defaults(func=cmd_status)

    pause = sub.add_parser(
        "pause", help="stop taking matches after the current one, until `resume`"
    )
    pause.set_defaults(func=cmd_pause)

    resume = sub.add_parser("resume", help="take matches again after `pause`")
    resume.set_defaults(func=cmd_resume)

    unlink = sub.add_parser("unlink", help="unlink this computer from its account")
    unlink.add_argument(
        "--local-only",
        action="store_true",
        help="forget the credentials here without telling the site (what this used to do)",
    )
    unlink.add_argument(
        "--porcelain",
        action="store_true",
        help="emit one JSON object instead of sentences (what the installer reads)",
    )
    unlink.set_defaults(func=cmd_unlink)

    start = sub.add_parser(
        "start",
        help="start the login item if it is stopped (what the app runs)",
    )
    start.set_defaults(func=cmd_start)

    upd = sub.add_parser(
        "update", help="install the version gammonview.com has released, and restart onto it"
    )
    upd.set_defaults(func=cmd_update)

    setup = sub.add_parser(
        "install",
        help="link, start at log-in, and add to your applications (Linux)",
    )
    setup.set_defaults(func=cmd_install)

    rm = sub.add_parser(
        "uninstall",
        help="remove the helper from this computer (Windows and Linux)",
    )
    rm.add_argument("--yes", action="store_true", help="do not ask first, and print the outcome")
    rm.set_defaults(func=cmd_uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    log_when_windowless()
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    cfg = load()
    if args.site:
        cfg.site = args.site
    return args.func(cfg, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
