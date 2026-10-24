# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""`gammonview-helper` as a terminal command, for an installer install.

The installer puts everything under `~/Library/Application Support/GammonView/`
and never touches the user's PATH (`launcher/README.md`, "What it installs, and
where"), which is right for the audience it is built for and leaves anyone who
*also* wants a terminal with a command at a path nobody types. A `uv tool`
install has the command already; this is only for the installer's.

**Asked for, never assumed.** The menu offers it, the way an editor offers to
put its command on PATH. Most people who use the installer will never open a
terminal, and a file written outside the install root without asking would
break the uninstall being one folder.

**A symlink in `~/.local/bin`, and nothing else.** No admin password (unlike
`/usr/local/bin`), and it is where `uv` puts its own tools, so it is often on
PATH already. Shell profiles are not edited: when the directory is not on PATH
the confirmation gives the line to add, for the person to add.

**Never someone else's command.** If `~/.local/bin/gammonview-helper` exists
and is not a link to this install -- a `uv tool install gammonview-helper` of
their own, typically -- it is left exactly as it is, and Remove will only ever
delete a link that points here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import update

def link_path() -> Path:
    """Where the command goes. A function, so tests can move `HOME`."""
    return Path.home() / ".local" / "bin" / "gammonview-helper"


def source(helper: str) -> Path | None:
    """The installer's entry point, when `helper` is one; else None.

    None for a `uv tool` install or a checkout, which have their own command
    already, so the menu offers nothing there.
    """
    root = update.installer_root(helper)
    if root is None:
        return None
    return root / "bin" / "gammonview-helper"


def state(src: Path) -> str:
    """`"ours"`, `"absent"`, or `"other"` -- what is at `link_path()` now.

    A link is ours when it names `src`, whether or not `src` is there: read with
    `readlink` rather than resolved, so a link left dangling by an uninstall and
    a reinstall is still recognised and replaced, not refused as a stranger's.
    """
    link = link_path()
    if link.is_symlink():
        return "ours" if Path(os.readlink(link)) == src else "other"
    if link.exists():
        return "other"
    return "absent"


def add(src: Path) -> str | None:
    """Link the command to `src`. None, or why not."""
    link = link_path()
    now = state(src)
    if now == "ours":
        return None
    if now == "other":
        return (
            f"There is already a gammonview-helper command at {link}, from "
            "another install, so it was left as it is."
        )
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(src)
    except OSError as e:
        return f"It could not be created at {link}: {e.strerror or e}."
    return None


def remove(src: Path) -> None:
    """Remove the link, if it is ours. Anything else there is left alone."""
    if state(src) == "ours":
        link_path().unlink()


def shell_path(timeout: float = 5.0) -> list[str] | None:
    """The PATH a new terminal window gets, or None if it could not be asked.

    Not this process's PATH: a login item starts with launchd's, which has
    none of the person's own directories in it. So the person's shell is
    asked, as a terminal would start it -- login *and* interactive, because
    zsh reads `.zshrc` only when interactive and that is where PATH lines
    usually go. `printenv` rather than `echo $PATH`, because fish prints a
    list with spaces; the last line, because an interactive start may print
    a greeting first.
    """
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        done = subprocess.run(
            [shell, "-l", "-i", "-c", "printenv PATH"],
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        return None
    return lines[-1].split(os.pathsep)


def on_path(entries: list[str] | None) -> bool | None:
    """Whether `link_path()`'s directory is in `entries`; None if unknown."""
    if entries is None:
        return None
    want = link_path().parent
    return any(
        Path(os.path.expanduser(e)) == want for e in entries if e
    )


def path_line(shell: str | None = None) -> str:
    """The one line that puts `~/.local/bin` on PATH, for the person's shell."""
    name = Path(shell or os.environ.get("SHELL") or "/bin/zsh").name
    if name == "fish":
        return "fish_add_path ~/.local/bin"
    profile = "~/.bash_profile" if name == "bash" else "~/.zshrc"
    return f"echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> {profile}"
