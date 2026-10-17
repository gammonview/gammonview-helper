# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Whether a newer helper is out, and installing it from the menu.

**What counts as released is what the site says**, not what PyPI has: the
installer installs the version pinned in `helper-release.json` on the site, and
a release reaches PyPI before that pin moves (`docs/DesktopHelper.md`, release
order). Asking PyPI would offer a version the site has not yet agreed to.

**Two kinds of install update themselves.** The installer keeps its own `uv`
beside the helper (`Paths.swift`), so the menu runs the very command the
installer's Update button runs, with the same environment. A helper installed
with `uv tool install` is updated with the person's own `uv`, aimed at the tool
directory it is actually running from. Either way launchd then restarts the job
onto the new code, when launchd runs it. Anything else -- `pipx`, a checkout --
belongs to its own tool, and is told so rather than touched: there is no
command we could run for it and be sure it was the right one.

Updating is not upgrading: what gets installed is the version the site pins,
exactly, never "the newest on PyPI".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import httpx

from . import USER_AGENT
from .config import Config

#: How often the menu asks. A release is a few times a year, and the answer is
#: a small static file from the site's own nginx.
CHECK_INTERVAL = 6 * 60 * 60


def latest(cfg: Config, client: httpx.Client | None = None) -> dict | None:
    """The site's release manifest, or None if it cannot be had right now."""
    url = f"{cfg.site.rstrip('/')}/helper-release.json"
    try:
        if client is not None:
            r = client.get(url)
        else:
            r = httpx.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        release = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(release, dict) or not isinstance(release.get("version"), str):
        return None
    return release


def _parts(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return None


def is_release(version: str) -> bool:
    """True for a plain X.Y.Z, which is what PyPI gets; False for a dev build."""
    return _parts(version) is not None


def newer(release: str, current: str) -> bool:
    """True when `release` is a later plain X.Y.Z than `current`.

    Anything else -- a dev build, "unknown" from a checkout -- is never offered
    an update: somebody running one of those chose it.
    """
    a, b = _parts(release), _parts(current)
    return a is not None and b is not None and a > b


def installer_root(helper: str) -> Path | None:
    """The install root, when `helper` is the installer's entry point.

    The installer writes `<root>/bin/gammonview-helper` with its own `uv`
    beside it and the venv under `<root>/tools`; a `uv tool` install of
    somebody's own has neither in that arrangement. `uv.exe` on Windows.
    """
    bin_dir = Path(helper).parent
    root = bin_dir.parent
    has_uv = (bin_dir / "uv").is_file() or (bin_dir / "uv.exe").is_file()
    if has_uv and (root / "tools").is_dir():
        return root
    return None


def environment(root: Path) -> dict[str, str]:
    """The installer's `uvEnvironment`, which must stay the same as it."""
    return {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_NO_MODIFY_PATH": "1",
    }


@dataclass(frozen=True)
class Target:
    """How to reinstall this helper: which `uv`, with what environment."""

    uv: str
    env: dict[str, str]
    #: `--python` for the install. The installer's comes from the manifest, as
    #: the installer's own does; a `uv tool` install keeps the minor version it
    #: is running on, rather than letting uv pick a different interpreter.
    python: str | None


#: Where `uv`'s own installer and Homebrew put it. Searched after PATH because a
#: login item starts with launchd's PATH, which has none of them.
UV_LOCATIONS = ("~/.local/bin", "~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin")


def find_uv(
    environ: Mapping[str, str] = os.environ,
    which: Callable[..., str | None] = shutil.which,
) -> str | None:
    """The person's own `uv`, or None."""
    path = os.pathsep.join(
        [environ.get("PATH", "")] + [os.path.expanduser(p) for p in UV_LOCATIONS]
    )
    return which("uv", path=path)


def target(
    helper: str,
    prefix: str = sys.prefix,
    environ: Mapping[str, str] = os.environ,
    which: Callable[..., str | None] = shutil.which,
) -> Target | None:
    """How to update the helper whose entry point is `helper`, or None.

    `prefix` is the environment it runs in: a `uv tool` install's has the
    receipt uv writes there, which is how it is told apart from `pipx` or a
    checkout. The tool and bin directories are given to uv explicitly, from
    where this copy actually is, so a person who moved them with `UV_TOOL_DIR`
    has the same copy replaced rather than a second one installed.
    """
    root = installer_root(helper)
    if root is not None:
        return Target(str(root / "bin" / "uv"), environment(root), "release")
    tool = Path(prefix)
    if not (tool / "uv-receipt.toml").is_file():
        return None
    uv = find_uv(environ, which)
    if uv is None:
        return None
    env = {
        **environ,
        "UV_TOOL_DIR": str(tool.parent),
        "UV_TOOL_BIN_DIR": str(Path(helper).parent),
    }
    return Target(uv, env, f"{sys.version_info.major}.{sys.version_info.minor}")


def install(
    where: Target,
    release: dict,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """Install `release` over this helper. None, or what went wrong."""
    argv = [where.uv, "tool", "install", "--force"]
    python = release.get("python") if where.python == "release" else where.python
    if isinstance(python, str) and python:
        argv += ["--python", python]
    argv.append(f"gammonview-helper=={release['version']}")
    done = run(argv, env=where.env, capture_output=True, text=True)
    if done.returncode == 0:
        return None
    lines = [line for line in (done.stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else f"uv exited with status {done.returncode}"


def restart_login_item(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Restart the login item onto the new code. True if it was restarted.

    `kickstart -k` stops the running copy and starts the job again, so a helper
    updated from a terminal stops running the old code without anybody logging
    out. On Linux every running helper is sent SIGHUP, which `run` answers by
    `exec`ing itself. False where there is no login item to restart: Windows
    (see `winmaint`), or a helper that is not set up to start at login.
    """
    if sys.platform.startswith("linux"):
        from . import linuxapp

        return linuxapp.restart_running()
    if sys.platform != "darwin":
        return False
    from .tray import LAUNCHD_LABEL

    plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    if not plist.exists():
        return False
    done = run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
        capture_output=True, text=True,
    )
    return done.returncode == 0
