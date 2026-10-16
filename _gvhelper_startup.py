# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""What every Python process in the helper's environment does first, on Windows.

Run from `_gvhelper_startup.pth`, which site-packages reads at the start of
every interpreter here -- the helper, and each engine worker it starts. Those
workers are the reason: on Windows a pool worker is a fresh interpreter that
never passes through `gvhelper.cli`, and two fixes made there kept the helper
alive while every worker died. The second, `stderr`, is in
`gvhelper/winlog.py`; the first is below. This is a top-level module rather
than part of `gvhelper` so that a console process, which needs neither, does
not import the package.

**Load our own copy of Microsoft's C++ runtime before bgsage can load one.**

bgsage's Windows extension is built with MSVC 14.51, and anything built with
14.40 or later crashes on import -- an access violation inside `MSVCP140.dll`
-- when the runtime it finds is older. Windows looks in System32, and plenty of
PCs have a 2019 or 2020 runtime there that nothing has updated since. GitHub's
runners never do, so only a real machine shows it.

`msvc-runtime` puts a current set of the DLLs at `sys.prefix`, which nothing
searches. Loading this one first is enough: Windows hands every later request
for `msvcp140.dll` the copy already in the process.

A stopgap until the bgsage wheel carries its own runtime, which is the
permanent fix. Safe meanwhile because the Windows `bgsage` is pinned exactly
(`pyproject.toml`), so the pairing is one that has been run; moving that pin
means running it again on a machine with an old runtime.
"""

import os
import sys


def load_msvc_runtime() -> None:
    # `os.name` rather than `sys.platform`, which the tests set to "win32" to
    # reach the Windows paths of other commands on a Mac.
    if os.name != "nt":
        return
    import ctypes

    try:
        ctypes.WinDLL(os.path.join(sys.prefix, "msvcp140.dll"))
    except OSError:
        pass  # no copy of our own: the system's may yet be new enough


def give_stderr() -> None:
    """A `pythonw` process -- the helper or one of its workers -- gets the log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from gvhelper.winlog import log_when_windowless

    log_when_windowless()


load_msvc_runtime()
give_stderr()
