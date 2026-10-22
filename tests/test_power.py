# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Reading battery and idle off each platform's own answer."""

from __future__ import annotations

from gvhelper import power


def _supply(root, name, kind, online=None):
    d = root / name
    d.mkdir()
    (d / "type").write_text(kind + "\n")
    if online is not None:
        (d / "online").write_text(f"{online}\n")


def test_linux_battery_from_sysfs(tmp_path):
    _supply(tmp_path, "BAT0", "Battery")
    _supply(tmp_path, "AC", "Mains", 1)
    assert power._linux_on_battery(tmp_path) is False
    (tmp_path / "AC" / "online").write_text("0\n")
    assert power._linux_on_battery(tmp_path) is True


def test_a_linux_box_with_no_mains_supply_cannot_tell(tmp_path):
    _supply(tmp_path, "BAT0", "Battery")
    assert power._linux_on_battery(tmp_path) is None
    assert power._linux_on_battery(tmp_path / "missing") is None


def test_mac_battery_from_pmset(monkeypatch):
    monkeypatch.setattr(power, "_run", lambda argv: "Now drawing from 'Battery Power'\n -InternalBattery-0")
    assert power._mac_on_battery() is True
    monkeypatch.setattr(power, "_run", lambda argv: "Now drawing from 'AC Power'\n")
    assert power._mac_on_battery() is False
    monkeypatch.setattr(power, "_run", lambda argv: None)
    assert power._mac_on_battery() is None


def test_mac_idle_from_ioreg(monkeypatch):
    monkeypatch.setattr(power, "_run", lambda argv: '  |   "HIDIdleTime" = 4500000000\n')
    assert power._mac_idle() == 4.5
    monkeypatch.setattr(power, "_run", lambda argv: "nothing useful")
    assert power._mac_idle() is None


def test_linux_idle_from_gnome_then_xprintidle(monkeypatch):
    monkeypatch.setattr(power.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(power, "_run", lambda argv: "(uint64 61000,)" if argv[0] == "gdbus" else None)
    assert power._linux_idle() == 61.0
    monkeypatch.setattr(power, "_run", lambda argv: None if argv[0] == "gdbus" else "2500\n")
    assert power._linux_idle() == 2.5
    monkeypatch.setattr(power.shutil, "which", lambda name: None)
    assert power._linux_idle() is None


def test_a_probe_that_breaks_cannot_tell(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no")

    monkeypatch.setattr(power, "_mac_on_battery", boom)
    monkeypatch.setattr(power, "_mac_idle", boom)
    monkeypatch.setattr(power.sys, "platform", "darwin")
    assert power.on_battery() is None
    assert power.idle_seconds() is None
