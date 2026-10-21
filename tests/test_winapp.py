# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""What the Start menu shortcut and the Windows menu decide -- without Windows.

The dialogs and the mutex are Win32 calls, so what is tested is everything
around them: which task is started, what is said, and where the installer is.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from gvhelper import tray, winapp, wintray
from gvhelper.daemon import Control

SID = "S-1-5-21-1004336348-1177238915-682003330-1001"


def _done(code: int = 0, out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


def test_the_sid_is_read_out_of_whoami():
    assert winapp.sid_from_whoami(f'"desktop-1\\\\nick","{SID}"\r\n') == SID
    assert winapp.sid_from_whoami("") is None


def test_the_task_name_matches_the_installers():
    # `LoginItem.TaskName` in launcher/windows/Sources/LoginItem.cs.
    assert winapp.task_name(SID) == f"GammonView Helper {SID}"


def test_start_runs_the_task_when_nothing_is_running():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return _done()

    assert winapp.start(run=run, running=lambda: False, sid=lambda: SID) == winapp.STARTED
    assert calls == [["schtasks", "/Run", "/TN", f"GammonView Helper {SID}"]]


def test_start_leaves_a_running_helper_alone_and_says_where_it_is():
    def run(argv, **kw):
        raise AssertionError("started a second copy")

    assert winapp.start(run=run, running=lambda: True, sid=lambda: SID) == winapp.ALREADY_RUNNING
    assert "^" in winapp.ALREADY_RUNNING[1], "Windows 11 hides new icons behind the arrow"


def test_start_without_a_task_sends_the_person_to_the_installer():
    assert winapp.start(run=lambda argv, **kw: _done(1), running=lambda: False,
                        sid=lambda: SID) == winapp.NOT_SET_UP
    assert winapp.start(run=lambda argv, **kw: _done(), running=lambda: False,
                        sid=lambda: None) == winapp.NOT_SET_UP


def test_the_install_root_is_found_from_the_venv(tmp_path):
    venv = tmp_path / "tools" / "gammonview-helper"
    venv.mkdir(parents=True)
    assert winapp.install_root(str(venv)) is None, "not without the installer's uv"
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "uv.exe").write_bytes(b"")
    assert winapp.install_root(str(venv)) == tmp_path


def test_an_update_names_this_users_login_item(tmp_path):
    launched = []

    def launch(*args):
        launched.append(args)
        return True

    assert winapp.start_update(tmp_path, "0.9.1", "3.13", sid=lambda: SID, launch=launch)
    assert launched == [(tmp_path, f"GammonView Helper {SID}", "0.9.1", "3.13")]
    assert not winapp.start_update(tmp_path, "0.9.1", None, sid=lambda: None, launch=launch), \
        "no SID, no task to restart"


# --- the menu's words --------------------------------------------------------


def test_the_menu_line_leaves_the_percentage_to_the_tooltip():
    from gvhelper.runner import Progress

    c = Control()
    c.state = "busy"
    c.progress = Progress()
    c.progress.set(37, 100)
    assert wintray.tooltip(c) == "GammonView Helper: Analysing a match… 37%"
    from gvhelper.tray import status_line

    assert status_line(c, percent=False) == "Analysing a match…"
    c.pause()
    assert status_line(c, percent=False) == "Pausing after this match…"


def test_link_is_offered_only_when_it_can_do_something():
    c = Control()
    c.state = "unlinked"
    assert wintray.link_label(c, linking=False) == "Link This Computer…"
    assert wintray.link_enabled(c, linking=False)
    assert not wintray.link_enabled(c, linking=True)
    c.state = "busy"
    assert wintray.link_label(c, linking=False) == "Unlink This Computer…"
    assert not wintray.link_enabled(c, linking=False), "not mid-match"
    c.state = "idle"
    assert wintray.link_enabled(c, linking=False)


def test_the_icons_ship_with_the_package():
    from pathlib import Path

    for paused in (False, True):
        assert Path(wintray.icon_path(paused)).is_file()


# --- on Windows itself -------------------------------------------------------

windows = pytest.mark.skipif(sys.platform != "win32", reason="Win32 calls")


@windows
def test_start_sees_the_menus_mutex():
    assert not winapp.menu_running()
    handle = winapp.hold_mutex()
    try:
        assert handle
        assert winapp.menu_running()
    finally:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    assert not winapp.menu_running()


@windows
def test_the_menu_builds_in_every_state(monkeypatch):
    """pystray evaluates every callable in the menu when it is built."""
    import threading

    from gvhelper.config import Config

    # A development build is never offered an update, and CI builds one.
    monkeypatch.setattr(tray, "__version__", "0.8.0")
    menu = wintray.HelperIcon(Config(), Control(), threading.Thread(target=lambda: None))
    for state in ("starting", "unlinked", "idle", "busy", "retrying"):
        menu.control.state = state
        menu.release = {"version": "99.0.0"}
        menu.icon.update_menu()
        texts = [str(item) for item in menu.icon.menu.items if item.visible]
        assert texts[0] == wintray.status_line(menu.control, percent=False)
        assert "Update to 99.0.0…" in texts


@windows
def test_whoami_answers_with_a_sid():
    assert (winapp.current_sid() or "").startswith("S-1-5-")


@pytest.mark.parametrize("answer, opened", [(True, ["https://x.test/#/link-helper?code=c"]), (False, [])])
def test_link_offers_the_browser_rather_than_opening_it(monkeypatch, answer, opened):
    asked, went = [], []
    monkeypatch.setattr(winapp, "ask", lambda title, message: asked.append(message) or answer)
    monkeypatch.setattr(wintray.webbrowser, "open", went.append)
    wintray.Narrator.ask_to_open("HERON", "https://x.test/#/link-helper?code=c")
    assert "HERON" in asked[0] and "https://x.test/#/link-helper?code=c" in asked[0]
    assert went == opened
