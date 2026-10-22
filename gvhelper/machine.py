# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""This computer's settings, as chosen on gammonview.com, and what they mean.

The site keeps a few settings per linked computer (`MachineSettings` in the
GammonView repo's `accounts/gvaccounts/helpersettings.py`) and hands them down
on `hello` and on every poll. None of them changes what an analysis finds --
that is the account's presets and options, which travel with each job -- only
when, and how hard, this computer works:

* **cores** -- how much of the computer to use: all of it (the engine's own
  sizing), about half, or about a quarter.
* **on_battery** -- whether to work while unplugged.
* **window_start / window_end** -- hours to work in, on this computer's clock.
* **og_sync** -- whether this computer runs the OpenGammon sync. The site
  applies that one itself, by telling a computer with it off that sync is off.
* **sync_when_idle** -- hold synced OpenGammon matches until nobody is using
  the computer. Synced matches only: a match sent from the browser means
  somebody is at a computer, very often this one.

**`config.json` wins.** A `jobs` or `threads` written there is a hard cap the
person set on this computer, by hand, and the README promises it is kept; the
site's `cores` applies only where neither is set.

Everything here is pure, so the daemon's loop and the tests ask the same
functions; the probes it needs are `power.py`'s.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, fields

from .ogsync import window_wait

#: The feature word for a helper that applies these settings.
MACHINE_SETTINGS = "machine_settings"

#: How long the keyboard and mouse must have been left alone before the
#: computer counts as idle. Five minutes is the screensaver's usual default:
#: long enough that reading a page is not idle, short enough that a computer
#: left for lunch is.
IDLE_AFTER = 5 * 60

#: How much of the computer each `cores` setting gives the engine, as a share
#: of its cores.
SHARES = {"half": 2, "quarter": 4}


@dataclass(frozen=True)
class Settings:
    """The site's settings for this computer. The defaults are what a helper
    did before there were any, so an unset computer behaves as it always has."""

    cores: str = "all"
    on_battery: bool = True
    window_start: int | None = None
    window_end: int | None = None
    og_sync: bool = True
    sync_when_idle: bool = False

    @classmethod
    def from_dict(cls, raw) -> "Settings":
        """Leniently: unknown keys are a newer site's, and a value of the wrong
        type is the default rather than an error -- the work loop must not stop
        over a setting."""
        if not isinstance(raw, dict):
            return cls()
        out = {}
        for f in fields(cls):
            if f.name not in raw:
                continue
            value = raw[f.name]
            default = getattr(cls, f.name)
            if f.name == "cores":
                ok = value in ("all", *SHARES)
            elif f.name in ("window_start", "window_end"):
                ok = value is None or (isinstance(value, int) and not isinstance(value, bool)
                                       and 0 <= value < 24 * 60)
            else:
                ok = isinstance(value, bool)
            out[f.name] = value if ok else default
        return cls(**out)


class Current:
    """The latest settings, shared by the work loop and the sync thread.

    Either may be the one that hears a change -- the loop on its poll, the
    sync thread on its `hello` -- and both act on it, so one holder with a
    lock rather than two copies drifting apart.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._settings = Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def update(self, raw) -> None:
        """Take a `settings` from the site. An answer without one -- a site
        older than them -- changes nothing."""
        if raw is None:
            return
        settings = Settings.from_dict(raw)
        with self._lock:
            self._settings = settings


_capabilities: list[str] | None = None


def helper_features() -> list[str]:
    """Everything `hello` says this helper can do: what its engine can run
    (`runner.engine_features`), that it applies these settings, and which of
    battery and idle this computer can tell (`power.capabilities`). The last
    is probed once a process -- it can cost a subprocess, and a computer does
    not grow a battery while the helper runs."""
    global _capabilities
    from . import power
    from .runner import engine_features

    if _capabilities is None:
        _capabilities = power.capabilities()
    return [*engine_features(), MACHINE_SETTINGS, *_capabilities]


def parallelism(jobs: int, threads: int, settings: Settings,
                cpu: int | None = None) -> tuple[int, int]:
    """`(jobs, threads)` for one analysis.

    `jobs` and `threads` are `config.json`'s; either one set is the person's
    own cap and is used as it stands. Otherwise "all" is the engine's sizing
    (both 0), and a share is one decision at a time with that share of the
    cores inside it -- the one shape that keeps the engine to a number of
    cores, since its parallel mode gives every worker all of them.
    """
    if jobs or threads or settings.cores == "all":
        return jobs, threads
    cpu = cpu or os.cpu_count() or 1
    return 1, max(1, cpu // SHARES[settings.cores])


def resting(settings: Settings, *, at: float, on_battery: bool | None) -> str | None:
    """Why this computer should take no work right now, or None if it may.

    The answer is a sentence for the menu. `on_battery` is `power.on_battery()`;
    None (cannot tell) is plugged in.
    """
    if not settings.on_battery and on_battery:
        return "On battery"
    if window_wait(settings.window_start, settings.window_end, at):
        return f"Outside its hours, back at {_clock(settings.window_start)}"
    return None


def holds_sync(settings: Settings, *, idle: float | None) -> bool:
    """Whether a synced OpenGammon match should wait: only while somebody is
    using the computer, and only when that was asked for. `idle` is
    `power.idle_seconds()`; None (cannot tell) is idle."""
    return settings.sync_when_idle and idle is not None and idle < IDLE_AFTER


def _clock(minutes: int | None) -> str:
    minutes = minutes or 0
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
