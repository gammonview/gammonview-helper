# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Somewhere to write for a process with no console.

Its own module, and a light one, because two kinds of process call it: the
helper itself (`cli.main`), and every engine worker it starts, which reach it
from `_gvhelper_startup.py` at interpreter start and should not import the
CLI's HTTP and keyring stack to do so.
"""

from __future__ import annotations

import sys

from .config import config_dir

#: Past this, the log is set aside as `helper.log.1` when the helper starts.
#: launchd has no rotation either, but a Mac's log is a terminal's scrollback
#: nobody reads; this one is the only record a Windows install keeps.
LOG_LIMIT = 5 * 1024 * 1024


def log_when_windowless() -> None:
    """Give a process with no console somewhere to write.

    The Windows login item runs `pythonw.exe -m gvhelper run` -- the console
    entry point would put a window on the desktop at every sign-in -- and under
    `pythonw` `sys.stdout` and `sys.stderr` are None. `print` then does nothing
    at all, so the helper would run with no record of what it did and a crash
    would leave no traceback. launchd gives the macOS helper a log file through
    the plist; this is the same file, opened from the inside.

    **None is not only silence.** `gvanalysis` calls `faulthandler.enable()`
    at import, which raises `RuntimeError: sys.stderr is None` -- so a worker
    with no stderr died importing the engine, and every analysis failed with
    `BrokenProcessPool`. The workers share the helper's file for that reason,
    and their tracebacks land in it too.

    `logs/helper.log` in the config directory, which on Windows is the
    installer's root, so the installer's Uninstall takes it with everything
    else. A no-op anywhere stdout exists, which is everywhere but there.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = config_dir() / "logs" / "helper.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            path.replace(path.with_name(path.name + ".1"))
    except OSError:
        # Including a worker that crosses the limit while the helper holds the
        # file open, which Windows refuses to rename. Still worth writing to.
        pass
    try:
        stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return  # nowhere to write is not a reason not to analyse
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
