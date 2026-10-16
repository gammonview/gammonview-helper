# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Updating and uninstalling on Windows, without the installer.

The installer used to keep a copy of itself in the install root, for Settings'
Uninstall and the menu's Update to run. Windows Defender reads exactly that --
an unsigned program copying itself into AppData and registering it under
`Uninstall` -- as `Behavior:Win32/Persistence.A!ml`, and quarantines it. So the
installer now leaves nothing of itself behind, and the two things it was kept
for happen here instead.

**Neither can run inside the helper's own process.** Windows will not replace
or delete a file a running process has loaded, and the helper runs out of the
very venv an update replaces and an uninstall deletes. So each hands its last
step to a process outside what it changes:

* **An update** runs this file as a script, with the base interpreter uv
  installed under `<root>\\python` -- outside the venv -- and `-I`, so it
  imports nothing but the standard library. It ends the login item, stops
  everything else running from the venv, reinstalls the version the site pins
  with the installer's own `uv` and environment, and starts the login item
  again. It is copied to `%TEMP%` first, so the copy being run is not a file
  in the venv it is about to replace.
* **An uninstall** does everything it can in the helper's process, then leaves
  a hidden `cmd` to delete the root once that process has exited, since the
  interpreter running it lives in the root too.

Stdlib only, and no imports from `gvhelper`: as a script it runs where the
package is not importable, by design.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

#: Settings > Apps' entry, under HKEY_CURRENT_USER: `AppEntry.UninstallKey`.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GammonViewHelper"

#: How long an update waits for the venv to come free before giving up.
FREE_TIMEOUT = 30.0

#: The files in a venv that a running interpreter holds open.
_LOADED = {".pyd", ".dll", ".exe"}


def detach(popen, argv, **kw) -> None:
    """Start `argv` so that ending the login item does not end it too.

    Task Scheduler runs the helper in a job object, and `/End` ends the job:
    a child left in it would die with the helper it was started to replace.
    Breaking away needs the job's leave, so without it the start is retried
    plain rather than not made at all.
    """
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except PermissionError:
        popen(argv, creationflags=flags, **kw)


def quiet() -> dict:
    return {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def uv_environment(root: Path) -> dict[str, str]:
    """`Installer.UvEnvironment`, and `update.environment`: all three must agree."""
    return {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_NO_MODIFY_PATH": "1",
    }


def install_argv(root: Path, version: str, python: str | None) -> list[str]:
    """`Installer.InstallArguments`, run with the installer's `uv`."""
    argv = [str(root / "bin" / "uv.exe"), "tool", "install", "--force"]
    if python:
        argv += ["--python", python]
    argv.append(f"gammonview-helper=={version}")
    return argv


def base_pythonw(base_executable: str | None = None) -> Path | None:
    """The windowless base interpreter behind this venv, or None.

    `sys._base_executable` is the interpreter the venv was made from, which
    the installer's uv put under `<root>\\python`, outside the venv.
    """
    base = base_executable or getattr(sys, "_base_executable", None)
    if not base:
        return None
    path = Path(base).with_name("pythonw.exe")
    return path if path.is_file() else None


# --- stopping what holds the files ---------------------------------------------


def _image_path(kernel32, pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def _pids() -> list[int]:
    import ctypes
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi")
    count = 4096
    while True:
        arr = (wintypes.DWORD * count)()
        used = wintypes.DWORD()
        if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(used)):
            return []
        n = used.value // ctypes.sizeof(wintypes.DWORD)
        if n < count:
            return list(arr[:n])
        count *= 2


def under(path: str, dirs: list[Path]) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    return any(p.startswith(os.path.normcase(str(d)).rstrip("\\/") + os.sep) for d in dirs)


def stop_everything_under(dirs: list[Path], spare: set[int] = frozenset()) -> None:
    """`Shell.StopEverythingUnder`: end every process whose program is in `dirs`.

    `spare` keeps this process, and its parent when that is the venv's
    launcher waiting on it, from being ended by the call that needs them.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    for pid in _pids():
        if pid in spare or pid == 0:
            continue
        path = _image_path(kernel32, pid)
        if path is None or not under(path, dirs):
            continue
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
        if not handle:
            continue
        try:
            kernel32.TerminateProcess(handle, 1)
            kernel32.WaitForSingleObject(handle, 10_000)
        finally:
            kernel32.CloseHandle(handle)


def held(venv: Path) -> Path | None:
    """A loaded file in `venv` that nothing could replace right now, or None.

    Asked of the files, not of the processes: whatever is holding one, uv
    would fail on it halfway through and leave a venv half old and half new.
    """
    if not venv.is_dir():
        return None
    for dirpath, _dirs, files in os.walk(venv):
        for name in files:
            if os.path.splitext(name)[1].lower() not in _LOADED:
                continue
            path = Path(dirpath) / name
            try:
                with open(path, "r+b"):
                    pass
            except PermissionError:
                return path
            except OSError:
                continue
    return None


def wait_until_free(venv: Path, timeout: float = FREE_TIMEOUT,
                    clock=time.monotonic, sleep=time.sleep) -> Path | None:
    """None once nothing holds `venv`; the file still held if `timeout` passes."""
    deadline = clock() + timeout
    while True:
        stuck = held(venv)
        if stuck is None or clock() >= deadline:
            return stuck
        sleep(0.5)


# --- the Settings entry ----------------------------------------------------------


def set_display_version(version: str) -> None:
    """Keep Settings' version in step with what an update installed. Throws nothing."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
    except OSError:
        pass


def remove_app_entry() -> None:
    """`AppEntry.Remove`: the Settings entry and the Start menu shortcut."""
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    try:
        (programs / "GammonView Helper.lnk").unlink()
    except OSError:
        pass


# --- update ------------------------------------------------------------------------


def start_update(root: Path, task: str, version: str, python: str | None,
                 popen=subprocess.Popen, interpreter: Path | None = None) -> bool:
    """Hand the update to a process outside the venv. True if it started.

    The caller should then get out of the way: the update ends the login item
    and anything else running from the venv, this process included.
    """
    interpreter = interpreter or base_pythonw()
    if interpreter is None:
        return False
    script = Path(tempfile.gettempdir()) / f"gammonview-update-{uuid.uuid4().hex[:8]}.py"
    try:
        shutil.copyfile(__file__, script)
        argv = [str(interpreter), "-I", str(script), "--root", str(root), "--task", task,
                "--version", version]
        if python:
            argv += ["--python", python]
        detach(popen, argv, close_fds=True, cwd=tempfile.gettempdir())
    except OSError:
        return False
    return True


def _say(title: str, message: str, warning: bool) -> None:
    import ctypes

    MB_ICONWARNING, MB_ICONINFORMATION = 0x30, 0x40
    MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
    ctypes.windll.user32.MessageBoxW(
        None, message, title,
        (MB_ICONWARNING if warning else MB_ICONINFORMATION) | MB_SETFOREGROUND | MB_TOPMOST)


def update(root: Path, task: str, version: str, python: str | None, log) -> bool:
    """The update itself, run from outside the venv. True if it installed."""
    venv = root / "tools" / "gammonview-helper"

    def schtasks(*args: str) -> int:
        done = subprocess.run(["schtasks", *args, "/TN", task], capture_output=True,
                              text=True, **quiet())
        log(f"schtasks {args[0]}: {done.returncode}")
        return done.returncode

    schtasks("/End")
    # The interpreter running this lives under `python` too; spared by pid.
    stop_everything_under([root / "tools", root / "bin", root / "python"], spare={os.getpid()})
    stuck = wait_until_free(venv)
    if stuck is not None:
        log(f"still in use: {stuck}")
        schtasks("/Run")
        _say("GammonView Helper was not updated",
             "Part of it was still in use, so nothing was changed. Restart the "
             "computer, then choose Update again.", True)
        return False

    argv = install_argv(root, version, python)
    log("running: " + " ".join(argv))
    done = subprocess.run(argv, env=uv_environment(root), capture_output=True, text=True,
                          **quiet())
    for line in (done.stderr or "").splitlines():
        log("uv: " + line)
    if done.returncode != 0:
        lines = [line for line in (done.stderr or "").splitlines() if line.strip()]
        schtasks("/Run")
        _say("GammonView Helper was not updated",
             (lines[-1] if lines else f"uv exited with status {done.returncode}")
             + "\n\nIf the helper does not come back, download the installer "
             "from gammonview.com and open it.", True)
        return False

    set_display_version(version)
    schtasks("/Run")
    log(f"updated to {version}")
    return True


def _main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--python")
    a = p.parse_args(argv)
    root = Path(a.root)

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "update.log", "a", encoding="utf-8") as f:
        # `pythonw` has no stderr; an exception with nowhere to go vanishes.
        sys.stdout = sys.stderr = f

        def log(line: str) -> None:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
            f.flush()

        try:
            ok = update(root, a.task, a.version, a.python, log)
        except Exception as e:  # noqa: BLE001 - logged, and said
            log(f"failed: {type(e).__name__}: {e}")
            subprocess.run(["schtasks", "/Run", "/TN", a.task], capture_output=True, **quiet())
            _say("GammonView Helper was not updated", f"{type(e).__name__}: {e}", True)
            ok = False
    try:
        os.remove(__file__)
    except OSError:
        pass
    return 0 if ok else 1


# --- uninstall ---------------------------------------------------------------------


def delete_later(root: Path, popen=subprocess.Popen) -> bool:
    """Delete `root` once this process has exited: a hidden `cmd`, waiting.

    Quoted for cmd rather than by `subprocess`'s rules: what matters to cmd is
    an `&` in a user name, not a space. Tried twice, for a scanner holding a
    file it has just been told was written.
    """
    target = str(root)
    command = (f'/d /c ping -n 4 127.0.0.1 >nul & rd /s /q "{target}" '
               f'& if exist "{target}" (ping -n 6 127.0.0.1 >nul & rd /s /q "{target}")')
    try:
        detach(popen, f"cmd.exe {command}", close_fds=True, cwd=tempfile.gettempdir())
    except OSError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
