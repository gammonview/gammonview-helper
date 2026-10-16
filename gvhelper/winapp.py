# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""What the Windows installer leaves behind, from the helper's side.

The installer (`launcher/windows`) creates three things besides the install
root: the scheduled task that is the login item, a Start menu shortcut, and the
entry in Settings > Apps. It removes all three, and owns them. This module is
what those things *do* once they are opened, which is the part that may change
-- so it lives here, where it updates through PyPI, and not in a launcher that
is frozen and unsigned. `macapp.py` is the same arrangement on macOS.

* **The Start menu shortcut** runs `pythonw.exe -m gvhelper start`: start the
  login item if it is stopped, and say where the icon is either way. Windows 11
  puts a new notification-area icon in the overflow behind the ^ arrow, so a
  helper that starts in silence looks exactly like one that did not start.
* **Update from the menu, and Uninstall in Settings, are the helper's own**
  (`winmaint.py`). The installer keeps no copy of itself to do them: Windows
  Defender quarantines an unsigned program that copies itself into AppData.

Every `subprocess` call here passes `CREATE_NO_WINDOW`. Under `pythonw.exe`
there is no console to inherit, so Windows gives each console program it starts
a window of its own, and `schtasks` would flash one on the desktop.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

#: The task the installer registers: `LoginItem.TaskName` there, plus the SID.
TASK_PREFIX = "GammonView Helper "

#: Held by a running menu (`wintray.run`), so `start` can tell a helper that is
#: running from one that is not without parsing `schtasks`, whose output is
#: translated into the language Windows is set to.
MUTEX_NAME = "Local\\GammonViewHelperMenu"

CREATE_NO_WINDOW = 0x08000000

_WHERE = (
    "Its icon is in the notification area, at the bottom right of the screen. "
    "If you do not see it, click the ^ arrow beside the clock."
)

#: Spoken by `start`. Titles and messages, as in `macapp`.
STARTED = ("GammonView Helper has started.", _WHERE)
ALREADY_RUNNING = ("GammonView Helper is already running.", _WHERE)
NOT_SET_UP = (
    "GammonView Helper is not set up on this computer.",
    "To set it up again, download the installer from gammonview.com.",
)


def quiet() -> dict:
    """`subprocess` arguments that start a console program with no window."""
    return {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def sid_from_whoami(output: str) -> str | None:
    """The SID in `whoami /user /fo csv /nh`, which prints `"pc\\name","S-1-5-..."`."""
    for field in reversed(output.strip().split(",")):
        field = field.strip().strip('"')
        if field.startswith("S-1-"):
            return field
    return None


def current_sid(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str | None:
    try:
        done = run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                   **quiet())
    except OSError:
        return None
    return sid_from_whoami(done.stdout or "") if done.returncode == 0 else None


def task_name(sid: str) -> str:
    return TASK_PREFIX + sid


def hold_mutex():
    """Create the menu's mutex and return its handle, which must be kept."""
    import ctypes
    from ctypes import wintypes

    create = ctypes.windll.kernel32.CreateMutexW
    create.restype = wintypes.HANDLE
    return create(None, False, MUTEX_NAME)


def menu_running() -> bool:
    """Whether a helper with a menu is running in this sign-in session."""
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def start(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    running: Callable[[], bool] = menu_running,
    sid: Callable[[], str | None] = current_sid,
) -> tuple[str, str]:
    """Start the login item if it is stopped, and return what to tell the person.

    Started through the task rather than by running the helper here, so that
    it runs as the task always runs it -- restarted on failure, and stopped by
    an update by name. A task that is already running ignores
    `/Run` (`MultipleInstancesPolicy` is `IgnoreNew`), so a double-click
    cannot make two.
    """
    if running():
        return ALREADY_RUNNING
    me = sid()
    if me is None:
        return NOT_SET_UP
    done = run(["schtasks", "/Run", "/TN", task_name(me)], capture_output=True, text=True,
               **quiet())
    return STARTED if done.returncode == 0 else NOT_SET_UP


def install_root(prefix: str = sys.prefix) -> Path | None:
    """The installer's root, when this interpreter is the installer's venv.

    The installer puts the venv at `<root>/tools/gammonview-helper` and its own
    `uv.exe` in `<root>/bin`. Asked of the interpreter rather than of
    `sys.argv[0]` because the login item runs `pythonw.exe -m gvhelper`, where
    `argv[0]` is a file inside the package.
    """
    root = Path(prefix).parent.parent
    if (root / "bin" / "uv.exe").is_file() and (root / "tools").is_dir():
        return root
    return None


def start_update(
    root: Path,
    version: str,
    python: str | None,
    sid: Callable[[], str | None] = current_sid,
    launch: Callable[..., bool] | None = None,
) -> bool:
    """Hand an update to `winmaint`, naming this user's login item. True if started."""
    me = sid()
    if me is None:
        return False
    if launch is None:
        from .winmaint import start_update as launch
    return launch(root, task_name(me), version, python)


# --- dialogs -----------------------------------------------------------------

MB_OK = 0x0
MB_OKCANCEL = 0x1
MB_ICONQUESTION = 0x20
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000
IDOK = 1


def _box(title: str, message: str, flags: int) -> int:
    import ctypes

    # Foreground and topmost: a process with no window of its own otherwise
    # opens its dialog behind whatever the person is looking at.
    return ctypes.windll.user32.MessageBoxW(
        None, message, title, flags | MB_SETFOREGROUND | MB_TOPMOST
    )


def tell(message: tuple[str, str], warning: bool = False) -> None:
    _box(message[0], message[1], MB_OK | (MB_ICONWARNING if warning else MB_ICONINFORMATION))


def ask(title: str, message: str) -> bool:
    """OK or Cancel. MessageBoxW cannot relabel its buttons, so the title asks."""
    return _box(title, message, MB_OKCANCEL | MB_ICONQUESTION) == IDOK
