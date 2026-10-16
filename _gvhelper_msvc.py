# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Load our own copy of Microsoft's C++ runtime before bgsage can load one.

bgsage's Windows extension is built with MSVC 14.51, and anything built with
14.40 or later crashes on import -- an access violation inside `MSVCP140.dll`
-- when the runtime it finds is older. Windows looks in System32, and plenty of
PCs have a 2019 or 2020 runtime there that nothing has updated since. GitHub's
runners never do, so only a real machine shows it.

`msvc-runtime` puts a current set of the DLLs at `sys.prefix`, which nothing
searches. Loading this one first is enough: Windows hands every later request
for `msvcp140.dll` the copy already in the process.

**Why a `.pth` file runs this, and not the helper's own startup.** The engine
runs in a process pool, and on Windows each worker is a fresh interpreter that
never passes through `gvhelper.cli` -- the first fix did it there, and the main
process survived while every worker crashed. `_gvhelper_msvc.pth` in
site-packages is read at the start of every interpreter in this environment,
workers included. It is a top-level module rather than part of `gvhelper` so
that start does not import the package.

A stopgap until the bgsage wheel carries its own runtime, which is the
permanent fix. Safe meanwhile because the Windows `bgsage` is pinned exactly
(`pyproject.toml`), so the pairing is one that has been run; moving that pin
means running it again on a machine with an old runtime.
"""

import os
import sys


def load() -> None:
    # `os.name` rather than `sys.platform`, which the tests set to "win32" to
    # reach the Windows paths of other commands on a Mac.
    if os.name != "nt":
        return
    import ctypes

    try:
        ctypes.WinDLL(os.path.join(sys.prefix, "msvcp140.dll"))
    except OSError:
        pass  # no copy of our own: the system's may yet be new enough


load()
