# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The terminal command the menu offers an installer install."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gvhelper import shellcmd


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def installed(tmp_path):
    """An installer install: its own `uv` beside the entry point, a `tools` venv."""
    root = tmp_path / "Application Support" / "GammonView"
    (root / "bin").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "bin" / "uv").write_text("")
    helper = root / "bin" / "gammonview-helper"
    helper.write_text("")
    return helper


def test_offered_only_to_the_installers_install(installed, tmp_path):
    assert shellcmd.source(str(installed)) == installed
    # A `uv tool` install has no `uv` of its own beside it: it has the command.
    other = tmp_path / "local" / "bin" / "gammonview-helper"
    other.parent.mkdir(parents=True)
    other.write_text("")
    assert shellcmd.source(str(other)) is None


def test_add_links_it_into_local_bin_and_remove_takes_it_away(home, installed):
    assert shellcmd.state(installed) == "absent"
    assert shellcmd.add(installed) is None
    link = home / ".local" / "bin" / "gammonview-helper"
    assert link.is_symlink() and Path(link.readlink()) == installed
    assert shellcmd.state(installed) == "ours"
    assert shellcmd.add(installed) is None  # twice is fine
    shellcmd.remove(installed)
    assert not link.exists() and not link.is_symlink()
    assert shellcmd.state(installed) == "absent"


def test_someone_elses_command_is_left_alone(home, installed, tmp_path):
    # Their own `uv tool install gammonview-helper`, which uv links the same way.
    theirs = tmp_path / "uv-tools" / "gammonview-helper"
    theirs.parent.mkdir()
    theirs.write_text("")
    link = home / ".local" / "bin" / "gammonview-helper"
    link.parent.mkdir(parents=True)
    link.symlink_to(theirs)

    assert shellcmd.state(installed) == "other"
    problem = shellcmd.add(installed)
    assert problem and "left as it is" in problem
    shellcmd.remove(installed)
    assert Path(link.readlink()) == theirs

    # A plain file there is someone else's too.
    link.unlink()
    link.write_text("#!/bin/sh\n")
    assert shellcmd.state(installed) == "other"
    shellcmd.remove(installed)
    assert link.read_text() == "#!/bin/sh\n"


def test_a_link_left_dangling_by_an_uninstall_is_still_ours(home, installed):
    assert shellcmd.add(installed) is None
    installed.unlink()  # the install root deleted, the link not
    assert shellcmd.state(installed) == "ours"
    shellcmd.remove(installed)
    assert shellcmd.state(installed) == "absent"


def test_on_path_reads_the_shells_path_not_this_processes(home):
    local = str(home / ".local" / "bin")
    assert shellcmd.on_path(["/usr/bin", local]) is True
    assert shellcmd.on_path(["/usr/bin", "~/.local/bin"]) is True
    assert shellcmd.on_path(["/usr/bin"]) is False
    assert shellcmd.on_path(None) is None


def test_the_shell_is_asked_as_a_terminal_would_start_it(monkeypatch):
    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv
        # An interactive start can greet first; PATH is the last line.
        return subprocess.CompletedProcess(argv, 0, stdout="Welcome!\n/a:/b\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("SHELL", "/opt/homebrew/bin/fish")
    assert shellcmd.shell_path() == ["/a", "/b"]
    assert seen["argv"] == ["/opt/homebrew/bin/fish", "-l", "-i", "-c", "printenv PATH"]

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(subprocess, "run", slow)
    assert shellcmd.shell_path() is None


@pytest.mark.parametrize(
    "shell, line",
    [
        ("/bin/zsh", "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.zshrc"),
        ("/bin/bash", "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bash_profile"),
        ("/opt/homebrew/bin/fish", "fish_add_path ~/.local/bin"),
    ],
)
def test_the_line_to_add_suits_the_shell(shell, line):
    assert shellcmd.path_line(shell) == line
