# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The Linux login item, app entry and status icon -- everything but a panel.

No panel can be drawn in a test, so what is tested is what the desktop files
say, how a running helper is found, and what the menu would say and do. The
D-Bus layout is checked where `dbus-fast` is installed (Linux, where it is a
dependency), and the icon where Pillow is.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from gvhelper import linuxapp, linuxtray, tray
from gvhelper.config import Config
from gvhelper.daemon import Control
from gvhelper.runner import Progress


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    return tmp_path


# --- desktop files ---------------------------------------------------------


def test_the_login_item_runs_and_the_app_entry_starts():
    autostart = linuxapp.desktop_entry("/home/a/.local/bin/gammonview-helper", autostart=True)
    entry = linuxapp.desktop_entry("/home/a/.local/bin/gammonview-helper", autostart=False)
    assert "Exec=/home/a/.local/bin/gammonview-helper run\n" in autostart
    assert "NoDisplay=true" in autostart
    assert "Exec=/home/a/.local/bin/gammonview-helper start\n" in entry
    assert "NoDisplay" not in entry
    assert "Icon=gammonview-helper\n" in entry


def test_a_path_with_a_space_is_quoted_the_desktop_entry_way():
    entry = linuxapp.desktop_entry("/home/a b/bin/gammonview-helper", autostart=False)
    assert 'Exec="/home/a b/bin/gammonview-helper" start\n' in entry


def test_install_writes_three_files_and_remove_takes_them_away(xdg):
    linuxapp.install("/usr/bin/gammonview-helper")
    assert linuxapp.installed()
    for path in (linuxapp.autostart_path(), linuxapp.entry_path(), linuxapp.icon_target()):
        assert path.is_file() and str(path).startswith(str(xdg))
    linuxapp.remove()
    assert not linuxapp.installed()
    assert not linuxapp.entry_path().exists()


def test_a_helper_run_by_hand_does_not_make_itself_a_login_item(xdg):
    linuxapp.ensure("/usr/bin/gammonview-helper")
    assert not linuxapp.installed()


# --- finding a running helper ---------------------------------------------


@pytest.mark.parametrize("argv, expected", [
    (["/usr/bin/python3", "/home/a/.local/bin/gammonview-helper", "run"], True),
    (["/home/a/.local/bin/gammonview-helper", "run"], True),
    (["python", "-m", "gvhelper", "--site", "https://beta.gammonview.com", "run"], True),
    (["/home/a/.local/bin/gammonview-helper", "status"], False),
    (["vim", "run"], False),
    (["python", "-m", "pytest", "run"], False),
])
def test_a_running_helper_is_told_by_its_command_line(argv, expected):
    assert linuxapp.is_helper_run(argv) is expected


@pytest.mark.skipif(os.name == "nt", reason="reads /proc as this user's uid, which Windows has neither of")
def test_running_reads_proc_and_leaves_out_itself(tmp_path):
    def proc(pid: int, *argv: str) -> None:
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    proc(10, "/x/bin/gammonview-helper", "run")
    proc(11, "/x/bin/gammonview-helper", "run")
    proc(12, "bash")
    (tmp_path / "self").mkdir()
    assert sorted(linuxapp.running(tmp_path, me=11)) == [10]


def test_start_leaves_a_running_helper_alone(xdg):
    linuxapp.install("/x/gammonview-helper")
    started = []
    said = linuxapp.start("/x/gammonview-helper", popen=lambda *a, **k: started.append(a),
                          pids=lambda: [42])
    assert said == linuxapp.ALREADY_RUNNING and not started


def test_start_runs_the_helper_detached_into_its_log(xdg):
    linuxapp.install("/x/gammonview-helper")
    calls = []
    said = linuxapp.start("/x/gammonview-helper", popen=lambda argv, **k: calls.append((argv, k)),
                          pids=lambda: [])
    assert said == linuxapp.STARTED
    argv, kwargs = calls[0]
    assert argv == ["/x/gammonview-helper", "run"]
    assert kwargs["start_new_session"]
    assert linuxapp.log_path().exists()


def test_start_without_install_says_how_to_set_it_up(xdg):
    assert linuxapp.start("/x/gammonview-helper", pids=lambda: []) == linuxapp.NOT_SET_UP


# --- dialogs ---------------------------------------------------------------


def test_dialogs_prefer_zenity_then_kdialog():
    zenity = linuxapp.dialog_argv("question", "T", "M", which=lambda n: n == "zenity")
    assert zenity[:2] == ["zenity", "--question"] and "--no-markup" in zenity
    kde = linuxapp.dialog_argv("question", "T", "M", which=lambda n: n == "kdialog")
    assert kde == ["kdialog", "--title", "T", "--yesno", "M"]
    assert linuxapp.dialog_argv("info", "T", "M", which=lambda n: None) is None


# --- the status icon -------------------------------------------------------


def test_no_icon_without_a_display_or_a_bus(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not linuxtray.available()


def _menu(monkeypatch, state: str = "idle") -> linuxtray.HelperMenu:
    monkeypatch.setattr(tray, "__version__", "0.8.0")
    control = Control()
    control.state = state
    return linuxtray.HelperMenu(Config(), control, threading.Thread(target=lambda: None))


def _shown(menu: linuxtray.HelperMenu) -> list[str]:
    return [i["label"] for i in menu.items() if not i.get("separator") and i["visible"]]


def test_the_menu_says_the_live_percentage(monkeypatch):
    menu = _menu(monkeypatch, "busy")
    menu.control.progress = Progress()
    menu.control.progress.set(37, 100)
    assert _shown(menu)[0] == "Analysing a match… 37%"


def test_the_menu_offers_an_update_only_when_there_is_one(monkeypatch):
    menu = _menu(monkeypatch)
    assert not any(label.startswith("Update") for label in _shown(menu))
    menu.release = {"version": "99.0.0"}
    assert "Update to 99.0.0…" in _shown(menu)


def test_a_change_in_what_the_menu_says_changes_its_signature(monkeypatch):
    menu = _menu(monkeypatch)
    before = menu.signature()
    menu.control.pause()
    assert menu.signature() != before
    assert "Resume" in _shown(menu)


def test_a_click_reaches_only_an_enabled_item(monkeypatch):
    menu = _menu(monkeypatch, "busy")
    acted = []
    monkeypatch.setattr(menu, "on_link", lambda: acted.append("link"))
    monkeypatch.setattr(menu, "on_toggle", lambda: acted.append("toggle"))
    menu.clicked(linuxtray.LINK)  # Unlink is disabled mid-match
    menu.clicked(linuxtray.STATUS)  # a line of text, not a control
    menu.clicked(linuxtray.TOGGLE)
    assert acted == ["toggle"]


def test_the_layout_is_what_dbusmenu_expects(monkeypatch):
    pytest.importorskip("dbus_fast")
    menu = _menu(monkeypatch)
    root_id, props, children = linuxtray.layout(menu)
    assert root_id == 0 and props["children-display"].value == "submenu"
    assert all(c.signature == "(ia{sv}av)" for c in children)
    ids = [c.value[0] for c in children]
    assert ids == list(range(1, 12))
    separators = [c.value[0] for c in children if "type" in c.value[1]]
    assert separators == [linuxtray.SEP1, linuxtray.SEP2]


def test_the_icon_is_argb_in_network_order(tmp_path):
    image = pytest.importorskip("PIL.Image")
    path = tmp_path / "pixel.png"
    image.new("RGBA", (1, 1), (10, 20, 30, 40)).save(path)
    assert linuxtray.pixmap(str(path)) == [[1, 1, bytes([40, 10, 20, 30])]]
    for paused in (False, True):
        assert Path(linuxtray.icon_path(paused)).is_file()
