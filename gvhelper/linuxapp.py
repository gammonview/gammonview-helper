# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""What `gammonview-helper install` sets up on Linux, and what it then does.

Linux has no installer. Somebody runs one line --
`uv tool install gammonview-helper && gammonview-helper install` -- and this
module is the second half: the parts the macOS and Windows installers create,
written by the helper itself. `macapp.py` and `winapp.py` are the same
arrangement on the other two.

* **The login item is an XDG autostart entry**, not a systemd user unit.
  Autostart entries are started by the desktop session, inside it, after the
  panel that will hold the icon; a user unit can start before the session's
  display and bus exist. And like the Windows task, nothing restarts a helper
  that exits cleanly, so Quit means what it says until the next log-in.
* **The app entry** in the desktop's applications list runs `start`, the way
  to bring the helper back after Quit without logging out -- what
  "GammonView Helper" in Applications or the Start menu is on the other two.
* **A running helper is found in `/proc`**, by its command line. A lock file
  or a bus name would only find a helper that took it, and one started from a
  terminal, or one with no session bus, takes neither.
* **Dialogs are `zenity`** (GNOME and most others) or `kdialog` (KDE). With
  neither there is nothing to draw a dialog with, and the tray says nothing
  rather than fail.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Callable, Iterable

#: The desktop-file id, and the icon's name in the icon theme.
APP_ID = "gammonview-helper"

#: Spoken by `start` and `install`. Titles and messages, as in `macapp`.
_WHERE = (
    "Its icon is with the other status icons in your desktop's panel: at the "
    "top right of the screen on Ubuntu."
)
STARTED = ("GammonView Helper has started.", _WHERE)
ALREADY_RUNNING = ("GammonView Helper is already running.", _WHERE)
NOT_SET_UP = (
    "GammonView Helper is not set up to start by itself.",
    "To set it up, run `gammonview-helper install` in a terminal.",
)


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def autostart_path() -> Path:
    return _config_home() / "autostart" / f"{APP_ID}.desktop"


def entry_path() -> Path:
    return _data_home() / "applications" / f"{APP_ID}.desktop"


def icon_target() -> Path:
    # 64x64 because that is what `tray.png` is; the theme scales the rest.
    return _data_home() / "icons" / "hicolor" / "64x64" / "apps" / f"{APP_ID}.png"


def log_path() -> Path:
    """Where a helper started by `start` writes. One from autostart goes to the
    journal, with the rest of the session's."""
    return _state_home() / "gammonview" / "helper.log"


def _quote(arg: str) -> str:
    """An `Exec=` argument: the desktop-entry spec's quoting, not the shell's."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def desktop_entry(helper: str, autostart: bool) -> str:
    """The `.desktop` file: the login item if `autostart`, else the app entry."""
    command = "run" if autostart else "start"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=GammonView Helper",
        "Comment=Analyses your GammonView matches on this computer",
        f"Exec={_quote(helper)} {command}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Utility;",
    ]
    if autostart:
        lines += ["NoDisplay=true", "X-GNOME-Autostart-enabled=true"]
    return "\n".join(lines) + "\n"


def install(helper: str) -> None:
    """Write the login item, the app entry and its icon."""
    for path, text in (
        (autostart_path(), desktop_entry(helper, autostart=True)),
        (entry_path(), desktop_entry(helper, autostart=False)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    icon = icon_target()
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes((files("gvhelper") / "resources" / "tray.png").read_bytes())


def installed() -> bool:
    return autostart_path().exists()


def ensure(helper: str) -> None:
    """Rewrite what `install` wrote, on every start, so an update reaches it.

    Only where it was installed: a helper somebody runs from a terminal must
    not make itself a login item by doing so.
    """
    if installed():
        install(helper)


def remove() -> None:
    for path in (autostart_path(), entry_path(), icon_target()):
        path.unlink(missing_ok=True)


# --- the running helper ----------------------------------------------------


def is_helper_run(argv: list[str]) -> bool:
    """Whether a command line is `gammonview-helper run`, however it was started."""
    if "run" not in argv:
        return False
    head = argv[: argv.index("run")]
    if any(Path(a).name == "gammonview-helper" for a in head):
        return True
    return any(a == "-m" and b == "gvhelper" for a, b in zip(head, head[1:]))


def running(proc: Path = Path("/proc"), me: int | None = None) -> list[int]:
    """The process ids of this user's helpers, other than this one."""
    me = os.getpid() if me is None else me
    uid = os.getuid()
    found = []
    for entry in proc.iterdir() if proc.is_dir() else ():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if is_helper_run([a.decode(errors="replace") for a in argv if a]):
            found.append(int(entry.name))
    return found


def signal_all(pids: Iterable[int], sig: int) -> int:
    """Send `sig` to each; how many it reached."""
    reached = 0
    for pid in pids:
        try:
            os.kill(pid, sig)
            reached += 1
        except OSError:
            pass
    return reached


def restart() -> None:
    """Become a fresh copy of this process: the new code, after an update.

    The same command line, so a helper started from the login item stays in
    the session the login item started it in. Sockets are not inherited across
    `exec` (PEP 446), so the old tray icon goes with the old process.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, sys.orig_argv)


def restart_on_hangup() -> None:
    """`update` from a terminal sends SIGHUP, which restarts onto the new code."""
    signal.signal(signal.SIGHUP, lambda *_: restart())


def restart_running() -> bool:
    """Ask every running helper to restart. True if there was one to ask."""
    return signal_all(running(), signal.SIGHUP) > 0


def stop_running() -> None:
    signal_all(running(), signal.SIGTERM)


def start(
    helper: str,
    popen: Callable[..., object] = subprocess.Popen,
    pids: Callable[[], list[int]] = running,
) -> tuple[str, str]:
    """Start a helper if none is running; what to tell the person."""
    if pids():
        return ALREADY_RUNNING
    if not installed():
        return NOT_SET_UP
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        popen(
            [helper, "run"],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return STARTED


# --- dialogs ---------------------------------------------------------------


def dialog_argv(
    kind: str, title: str, message: str, which: Callable[[str], str | None] = shutil.which
) -> list[str] | None:
    """The command that shows `message`; `kind` is info, warning or question."""
    if which("zenity"):
        return ["zenity", f"--{kind}", "--title", title, "--text", f"{title}\n\n{message}",
                "--no-markup", "--width", "420"]
    if which("kdialog"):
        flag = {"info": "--msgbox", "warning": "--sorry", "question": "--yesno"}[kind]
        return ["kdialog", "--title", title, flag, message]
    return None


def tell(message: tuple[str, str], warning: bool = False) -> None:
    argv = dialog_argv("warning" if warning else "info", *message)
    if argv is None:
        print(f"{message[0]} {message[1]}", flush=True)
        return
    subprocess.run(argv, capture_output=True)


def ask(title: str, message: str) -> bool:
    """Yes or No. With nothing to ask with, the menu choice is the answer."""
    argv = dialog_argv("question", title, message)
    if argv is None:
        return True
    return subprocess.run(argv, capture_output=True).returncode == 0
