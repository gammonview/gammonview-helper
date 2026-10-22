# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Updating and uninstalling on Windows -- what can be decided off Windows.

Ending processes and opening a dialog are Win32 calls; what is tested is the
command lines, which files count as held, and that the hand-off is to a copy
of this file run outside the venv.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from gvhelper import update, winmaint


def test_the_install_command_is_the_installers(tmp_path):
    # `Installer.InstallArguments`, with the installer's own uv.
    assert winmaint.install_argv(tmp_path, "0.9.1", "3.13") == [
        str(tmp_path / "bin" / "uv.exe"), "tool", "install", "--force", "--refresh-package", "gammonview-helper",
        "--python", "3.13", "gammonview-helper==0.9.1"]
    assert winmaint.install_argv(tmp_path, "0.9.1", None)[-2:] == [
        "gammonview-helper", "gammonview-helper==0.9.1"]


def test_the_uv_environment_is_the_one_update_uses(tmp_path):
    keys = [k for k in update.environment(tmp_path) if k.startswith("UV_")]
    assert {k: winmaint.uv_environment(tmp_path)[k] for k in keys} == \
        {k: update.environment(tmp_path)[k] for k in keys}


def test_the_hand_off_runs_a_copy_of_this_file_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(winmaint.tempfile, "gettempdir", lambda: str(tmp_path))
    pythonw = tmp_path / "python" / "pythonw.exe"
    started = []

    def popen(argv, **kw):
        started.append((argv, kw))

    assert winmaint.start_update(tmp_path / "root", "GammonView Helper S-1", "0.9.1", "3.13",
                                 popen=popen, interpreter=pythonw)
    (argv, kw), = started
    assert argv[:2] == [str(pythonw), "-I"], "stdlib only: nothing from the venv"
    script = Path(argv[2])
    assert script.parent == tmp_path and script.read_text() == Path(winmaint.__file__).read_text()
    assert argv[3:] == ["--root", str(tmp_path / "root"), "--task", "GammonView Helper S-1",
                        "--version", "0.9.1", "--python", "3.13"]
    assert kw["creationflags"] & winmaint.CREATE_BREAKAWAY_FROM_JOB


def test_a_job_that_refuses_breakaway_still_gets_the_hand_off():
    flags = []

    def popen(argv, creationflags, **kw):
        flags.append(creationflags)
        if creationflags & winmaint.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("access denied")

    winmaint.detach(popen, ["x"])
    assert len(flags) == 2 and not flags[1] & winmaint.CREATE_BREAKAWAY_FROM_JOB


def test_no_base_interpreter_means_no_hand_off(tmp_path):
    assert winmaint.base_pythonw(str(tmp_path / "python.exe")) is None
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert winmaint.base_pythonw(str(tmp_path / "python.exe")) == tmp_path / "pythonw.exe"


def test_the_script_imports_nothing_but_the_standard_library():
    """It runs with `-I` from %TEMP%, where `gvhelper` is not importable."""
    tree = ast.parse(Path(winmaint.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import"
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names


def test_only_files_under_the_install_count(tmp_path):
    root = tmp_path / "GammonView"
    assert winmaint.under(str(root / "python" / "pythonw.exe"), [root / "python"])
    assert not winmaint.under(str(tmp_path / "GammonViewOther" / "x.exe"), [root])


def test_a_venv_nothing_holds_is_free(tmp_path):
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib" / "engine.pyd").write_bytes(b"")
    (tmp_path / "Lib" / "module.py").write_text("")
    assert winmaint.held(tmp_path) is None
    assert winmaint.held(tmp_path / "missing") is None


def test_a_held_venv_is_waited_for_then_reported(tmp_path, monkeypatch):
    stuck = tmp_path / "engine.pyd"
    answers = iter([stuck, stuck, None])
    monkeypatch.setattr(winmaint, "held", lambda venv: next(answers))
    assert winmaint.wait_until_free(tmp_path, timeout=10, clock=lambda: 0, sleep=lambda s: None) is None

    monkeypatch.setattr(winmaint, "held", lambda venv: stuck)
    now = iter([0, 0, 31])
    assert winmaint.wait_until_free(tmp_path, timeout=30, clock=lambda: next(now),
                                    sleep=lambda s: None) == stuck


def test_the_root_is_deleted_by_cmd_after_this_process_ends(tmp_path):
    started = []
    root = tmp_path / "Jo & Sam" / "GammonView"
    assert winmaint.delete_later(root, popen=lambda argv, **kw: started.append(argv))
    (command,) = started
    assert command.startswith("cmd.exe /d /c ping")
    assert f'rd /s /q "{root}"' in command, "quoted, for the & in the name"
