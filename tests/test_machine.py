# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""This computer's settings, and what each one means for the loop."""

from __future__ import annotations

import time

from gvhelper import machine
from gvhelper.machine import Settings


def test_an_unset_computer_behaves_as_it_always_has():
    s = Settings.from_dict(None)
    assert s == Settings()
    assert machine.parallelism(0, 0, s, cpu=8) == (0, 0)
    assert machine.resting(s, at=time.time(), on_battery=True) is None
    assert machine.holds_sync(s, idle=0.0) is False


def test_settings_are_read_leniently():
    s = Settings.from_dict({"cores": "most", "on_battery": "no", "window_start": 99999,
                            "og_sync": False, "from_a_newer_site": 1})
    assert (s.cores, s.on_battery, s.window_start, s.og_sync) == ("all", True, None, False)


def test_a_share_is_one_decision_at_a_time_on_that_share_of_cores():
    assert machine.parallelism(0, 0, Settings(cores="half"), cpu=8) == (1, 4)
    assert machine.parallelism(0, 0, Settings(cores="quarter"), cpu=8) == (1, 2)
    assert machine.parallelism(0, 0, Settings(cores="quarter"), cpu=2) == (1, 1)


def test_a_cap_written_in_config_json_wins():
    assert machine.parallelism(3, 0, Settings(cores="quarter"), cpu=8) == (3, 0)
    assert machine.parallelism(0, 2, Settings(cores="half"), cpu=8) == (0, 2)


def test_battery_rests_only_when_asked_and_only_on_battery():
    off = Settings(on_battery=False)
    now = time.time()
    assert machine.resting(off, at=now, on_battery=True) == "On battery"
    assert machine.resting(off, at=now, on_battery=False) is None
    # Cannot tell is plugged in: a probe that fails never stops the helper.
    assert machine.resting(off, at=now, on_battery=None) is None


def _at(hour, minute=0):
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, -1))


def test_hours_that_cross_midnight():
    night = Settings(window_start=22 * 60, window_end=7 * 60)
    assert machine.resting(night, at=_at(23), on_battery=None) is None
    assert machine.resting(night, at=_at(6, 59), on_battery=None) is None
    assert machine.resting(night, at=_at(12), on_battery=None) == "Outside its hours, back at 22:00"


def test_synced_matches_wait_only_while_the_computer_is_in_use():
    idle_only = Settings(sync_when_idle=True)
    assert machine.holds_sync(idle_only, idle=10.0) is True
    assert machine.holds_sync(idle_only, idle=machine.IDLE_AFTER + 1) is False
    # Cannot tell is idle, for the same reason.
    assert machine.holds_sync(idle_only, idle=None) is False


def test_the_latest_settings_are_shared():
    current = machine.Current()
    current.update({"cores": "half"})
    current.update(None)  # an answer without settings changes nothing
    assert current.get().cores == "half"
