# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""What the helper can tell about the computer it is on: battery, and idle.

Two questions, asked by the per-computer settings (`machine.py`): is this
computer running on battery right now, and how long since anybody touched it.
Each platform answers them differently, and some cannot answer one at all --
a Linux desktop under Wayland with no GNOME has no portable idle time. So each
answer may be None, meaning "cannot tell", and `capabilities()` says which a
computer can answer, for `hello`: the settings page offers only the options a
computer can honour.

**Cannot tell never stops work.** A computer that cannot say it is on battery
is treated as plugged in, and one that cannot say how long it has been idle as
idle. The settings these feed are about being considerate, and a helper that
silently never worked because a probe failed would be the opposite of that.

Every probe is cheap -- a sysfs read, a ctypes call, or one short subprocess on
macOS -- and the daemon asks at most once a poll.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

#: How long a subprocess probe may take before it counts as "cannot tell".
PROBE_TIMEOUT = 2.0


def _run(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


# --- battery -----------------------------------------------------------------


def _mac_on_battery() -> bool | None:
    # "Now drawing from 'Battery Power'" or "... 'AC Power'". A desktop Mac
    # says AC Power and lists no battery.
    out = _run(["pmset", "-g", "batt"])
    if out is None:
        return None
    if "'Battery Power'" in out:
        return True
    if "'AC Power'" in out or "'UPS Power'" in out:
        return False
    return None


def _windows_on_battery() -> bool | None:  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    # 0 offline (on battery), 1 online, 255 unknown. BatteryFlag 128 is "no
    # system battery", which is a desktop: never on battery.
    if status.BatteryFlag & 0xFF == 128:
        return False
    return {0: True, 1: False}.get(status.ACLineStatus & 0xFF)


def _linux_on_battery(root: Path = Path("/sys/class/power_supply")) -> bool | None:
    """From sysfs: plugged in if any mains supply is online, on battery if
    there are mains supplies and none is, and cannot tell with none at all --
    a desktop, or a container with no view of the hardware."""
    try:
        supplies = list(root.iterdir())
    except OSError:
        return None
    mains = []
    for supply in supplies:
        try:
            kind = (supply / "type").read_text().strip()
        except OSError:
            continue
        if kind == "Mains":
            try:
                mains.append((supply / "online").read_text().strip() == "1")
            except OSError:
                continue
    if not mains:
        return None
    return not any(mains)


def on_battery() -> bool | None:
    """True on battery, False plugged in, None when this computer cannot say."""
    try:
        if sys.platform == "darwin":
            return _mac_on_battery()
        if sys.platform == "win32":  # pragma: no cover
            return _windows_on_battery()
        if sys.platform.startswith("linux"):
            return _linux_on_battery()
    except Exception:  # noqa: BLE001 - a probe never takes the helper down
        return None
    return None


def has_battery() -> bool:
    """Whether this computer has a battery the helper can see, for `hello`.

    Asked once at start. A Mac with no battery reports AC Power, which is a
    perfectly good answer to "on battery?" -- False, for ever -- and no reason
    to offer the setting, so the battery itself is asked about.
    """
    try:
        if sys.platform == "darwin":
            out = _run(["pmset", "-g", "batt"])
            return out is not None and "InternalBattery" in out
        if sys.platform == "win32":  # pragma: no cover
            return _windows_has_battery()
        if sys.platform.startswith("linux"):
            root = Path("/sys/class/power_supply")
            return any((s / "type").read_text().strip() == "Battery"
                       for s in root.iterdir() if (s / "type").exists())
    except Exception:  # noqa: BLE001
        return False
    return False


def _windows_has_battery() -> bool:  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return False
    return status.BatteryFlag & 0xFF not in (128, 255)


# --- idle --------------------------------------------------------------------


def _mac_idle() -> float | None:
    # IOHIDSystem's HIDIdleTime, in nanoseconds since the last keyboard or
    # mouse event, from any user.
    out = _run(["ioreg", "-c", "IOHIDSystem", "-d", "4"])
    if out is None:
        return None
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


def _windows_idle() -> float | None:  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    # Both in milliseconds since boot, and both wrap at 49.7 days -- together,
    # so the difference is right modulo 2**32.
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _linux_idle() -> float | None:
    """GNOME's idle monitor (Wayland or X11), then `xprintidle` (X11). Both
    in milliseconds. Anything else -- KDE under Wayland, a headless box --
    cannot tell, and says so."""
    if shutil.which("gdbus"):
        out = _run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                    "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                    "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"])
        # "(uint64 61000,)": the number after the type, not the 64 in it.
        m = re.search(r"uint64\s+(\d+)", out or "")
        if m:
            return int(m.group(1)) / 1000.0
    if shutil.which("xprintidle"):
        out = _run(["xprintidle"])
        if out and out.strip().isdigit():
            return int(out.strip()) / 1000.0
    return None


def idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input, or None if unknown."""
    try:
        if sys.platform == "darwin":
            return _mac_idle()
        if sys.platform == "win32":  # pragma: no cover
            return _windows_idle()
        if sys.platform.startswith("linux"):
            return _linux_idle()
    except Exception:  # noqa: BLE001 - a probe never takes the helper down
        return None
    return None


# --- for hello ---------------------------------------------------------------

#: The feature words `hello` sends for what this computer can tell.
BATTERY = "battery"
IDLE = "idle"


def capabilities() -> list[str]:
    """Which of the two questions this computer can answer, for `hello`."""
    out = []
    if has_battery():
        out.append(BATTERY)
    if idle_seconds() is not None:
        out.append(IDLE)
    return out
