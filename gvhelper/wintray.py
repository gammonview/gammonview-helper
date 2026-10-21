# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The notification-area icon: `tray.py`'s menu, on Windows.

Same items, same words, same reasons -- read `tray.py` for those. What is
written here is only where Windows made something different:

* **Drawn with `pystray`**, which speaks the Win32 notification-area API through
  `ctypes` and needs no compiler. Its menu is built ahead of time rather than
  when it is opened, so a refresh thread rebuilds it -- but only when what it
  says has changed. Rebuilding destroys the old menu, and doing that once a
  second would sooner or later destroy one somebody has open. The percentage a
  match has reached therefore goes in the tooltip, which is free to change,
  and the menu says only *Analysing a match…*.
* **Dialogs are `MessageBoxW`**, each on a thread of its own: a callback runs
  on the icon's message loop, and a dialog blocking it would freeze the icon.
* **Quit just exits.** The task restarts the helper only when it *fails*, so a
  clean exit stays stopped until the next sign-in or the Start menu, which is
  what Quit promises. No `launchctl` equivalent is needed.
* **Update hands off to a process outside the venv** (`winmaint`), which
  stops this one; nothing inside it can replace the files it is running from.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from importlib.resources import files
from typing import Callable

from . import __version__, update, winapp
from .config import Config, machine_name
from .daemon import Control, sign_off
from .store import load_token
from .tray import (
    QUIT_GRACE,
    REFRESH_INTERVAL,
    account_line,
    link_enabled,
    link_label,
    offered,
    status_line,
)

#: Windows truncates a notification-area tooltip at 127 characters.
TOOLTIP_LIMIT = 127


def icon_path(paused: bool) -> str:
    name = "tray-paused.png" if paused else "tray.png"
    return str(files("gvhelper") / "resources" / name)


def tooltip(control: Control) -> str:
    return f"GammonView Helper: {status_line(control)}"[:TOOLTIP_LIMIT]


def later(fn: Callable, *args) -> None:
    """Off the icon's message loop, which a dialog would otherwise freeze."""
    threading.Thread(target=fn, args=args, daemon=True).start()


class Narrator:
    """`cli.pair`'s narrator, in dialogs."""

    def offer(self, machine: str, platform: str, word: str, url: str, expires_in: int) -> None:
        later(self.ask_to_open, word, url)

    @staticmethod
    def ask_to_open(word: str, url: str) -> None:
        """OK opens it here; the box's text, link and all, copies with Ctrl+C."""
        from .cli import offer_text

        if winapp.ask(
            "Link this computer",
            offer_text(word, url) + "\n\nClick OK to open the page in this computer's browser.",
        ):
            webbrowser.open(url)

    def linked(self, store_name: str, worker_id: str | None) -> None:
        later(winapp.tell, (
            "This computer is linked",
            "Analyses you start on gammonview.com will now run here.",
        ))

    def failed(self, reason: str, retry: bool) -> None:
        later(winapp.tell, ("Linking did not finish", reason), True)


class HelperIcon:
    """The icon and its menu, apart from running them: built in a test too."""

    def __init__(self, cfg: Config, control: Control, worker: threading.Thread) -> None:
        import pystray
        from PIL import Image

        self.cfg, self.control, self.worker = cfg, control, worker
        self.images = {paused: Image.open(icon_path(paused)) for paused in (False, True)}
        self.linking = False
        self.release: dict | None = None
        # One question at a time: a second Quit while the first is still
        # asking would ask twice and act twice.
        self.asking = threading.Lock()
        Item = pystray.MenuItem
        self.icon = pystray.Icon(
            "GammonView Helper",
            self.images[False],
            tooltip(self.control),
            menu=pystray.Menu(
                Item(lambda _: status_line(self.control, percent=False), None, enabled=False),
                Item(lambda _: account_line(self.control) or "", None, enabled=False,
                     visible=lambda _: account_line(self.control) is not None),
                Item(lambda _: self.og_line() or "", None, enabled=False,
                     visible=lambda _: self.og_line() is not None),
                pystray.Menu.SEPARATOR,
                Item(lambda _: "Resume" if self.control.paused else "Pause", self.on_toggle),
                Item("Open GammonView", self.on_open, default=True),
                Item(lambda _: link_label(self.control, self.linking), self.on_link,
                     visible=lambda _: self.control.state != "starting",
                     enabled=lambda _: link_enabled(self.control, self.linking)),
                pystray.Menu.SEPARATOR,
                Item(lambda _: f"Update to {offered(self.release)}…", self.on_update,
                     visible=lambda _: offered(self.release) is not None,
                     enabled=lambda _: self.control.state != "busy"),
                Item("About GammonView Helper", self.on_about),
                Item("Quit GammonView Helper", self.on_quit),
            ),
        )

    def og_line(self) -> str | None:
        return self.control.og_line if self.control.state != "unlinked" else None

    # --- drawing ---------------------------------------------------------

    def signature(self) -> tuple:
        """Everything the menu says. It is rebuilt only when this changes."""
        return (
            status_line(self.control, percent=False), account_line(self.control), self.og_line(),
            self.control.paused, self.control.state, self.linking, offered(self.release),
        )

    def refresh(self) -> None:
        drawn: tuple | None = None
        paused = False
        title = ""
        while True:
            if not self.worker.is_alive() and not self.control.stopping:
                # The loop died, and an icon over a dead loop would claim
                # to be ready while doing nothing.
                self.icon.stop()
                return
            if self.control.stopping:
                return
            if self.control.paused != paused:
                paused = self.control.paused
                self.icon.icon = self.images[paused]
            if tooltip(self.control) != title:
                title = tooltip(self.control)
                self.icon.title = title
            if self.signature() != drawn:
                drawn = self.signature()
                self.icon.update_menu()
            time.sleep(REFRESH_INTERVAL)

    def watch_releases(self) -> None:
        while not self.control.stopping:
            release = update.latest(self.cfg)
            if release is not None:
                self.release = release
            self.control.sleep(update.CHECK_INTERVAL)

    def ask(self, title: str, message: str) -> bool:
        if not self.asking.acquire(blocking=False):
            return False
        try:
            return winapp.ask(title, message)
        finally:
            self.asking.release()

    # --- Pause / Resume, Open, About ------------------------------------

    def on_toggle(self, _icon, _item) -> None:
        if self.control.paused:
            self.control.resume()
        else:
            self.control.pause()
            later(self.sign_off)

    def sign_off(self) -> None:
        from .client import WorkerClient

        token = load_token(self.cfg)
        if not token:
            return
        client = WorkerClient(self.cfg, token)
        try:
            sign_off(client)
        finally:
            client.close()

    def on_open(self, _icon, _item) -> None:
        webbrowser.open(self.cfg.site)

    def on_about(self, _icon, _item) -> None:
        later(winapp.tell, (
            "About GammonView Helper",
            f"GammonView Helper {__version__}\n\n"
            "Analyses your GammonView matches on this computer.",
        ))

    # --- Link / Unlink ---------------------------------------------------

    def on_link(self, _icon, _item) -> None:
        if self.control.state == "unlinked":
            if self.linking:
                return
            self.linking = True
            later(self.link)
        else:
            later(self.unlink)

    def link(self) -> None:
        try:
            from .cli import pair

            pair(self.cfg, Narrator(), machine_name())
        finally:
            self.linking = False

    def unlink(self) -> None:
        whose = f" from {self.control.account}" if self.control.account else ""
        if not self.ask(
            f"Unlink this computer{whose}?",
            "It will stop analysing matches for that account, and be removed "
            "from the account's settings. You can link it again from this menu.",
        ):
            return
        self.control.state = "unlinked"
        self.control.account = None
        from .cli import unlink

        account, reason = unlink(self.cfg)
        if account == "left":
            winapp.tell((
                "This computer is unlinked",
                f"It could not be removed from your account's settings ({reason}). "
                "Use Unlink there to finish.",
            ))

    # --- Update ----------------------------------------------------------

    def on_update(self, _icon, _item) -> None:
        later(self.update)

    def update(self) -> None:
        version = offered(self.release)
        if version is None:
            return
        root = winapp.install_root()
        if root is None:
            winapp.tell((
                f"GammonView Helper {version} is available",
                "This copy was not installed by the GammonView installer. Quit "
                "it, then run `gammonview-helper update` in a terminal.",
            ))
            return
        if not self.ask(
            f"Update GammonView Helper to {version}?",
            "It takes a minute or two. The helper's icon goes away while it "
            "updates, and comes back when it is done.",
        ):
            return
        if self.control.state == "busy":
            # A match arrived while the question was open; the update
            # would end it half-analysed.
            winapp.tell((
                "A match is being analysed",
                "Choose Update again once it has finished.",
            ))
            return
        if not winapp.start_update(root, version, (self.release or {}).get("python")):
            winapp.tell((
                "The update did not start",
                "To update, download the installer from gammonview.com, open it, "
                "and choose Update.",
            ), True)

    # --- Quit ------------------------------------------------------------

    def on_quit(self, _icon, _item) -> None:
        later(self.quit)

    def quit(self) -> None:
        if not self.ask(
            "Quit GammonView Helper?",
            "It will stop analysing matches until you next sign in to Windows, "
            "or until you open GammonView Helper from the Start menu.\n\n"
            "To stop for a while, choose Pause instead.",
        ):
            return
        self.control.stop()
        # The loop fails a running job itself within a second; waited for
        # so the job has an answer before the process is gone.
        self.worker.join(QUIT_GRACE)
        self.sign_off()
        self.icon.stop()


def run(cfg: Config, control: Control, loop: Callable[[], int]) -> int:
    """Run `loop` on a background thread and the icon on this one.

    Returns the loop's exit code if it ends by itself, and 0 on Quit.
    """
    # Held until the process ends, so `start` can see the helper is running.
    mutex = winapp.hold_mutex()  # noqa: F841

    result: dict = {}

    def work() -> None:
        try:
            result["code"] = loop()
        except BaseException as e:  # noqa: BLE001 - reported, then exited
            print(f"helper loop failed: {type(e).__name__}: {e}", flush=True)
            result["code"] = 1

    worker = threading.Thread(target=work, name="helper-loop", daemon=True)
    menu = HelperIcon(cfg, control, worker)

    def setup(icon) -> None:
        icon.visible = True
        threading.Thread(target=menu.refresh, name="helper-menu", daemon=True).start()
        threading.Thread(target=menu.watch_releases, name="helper-update", daemon=True).start()

    worker.start()
    menu.icon.run(setup)
    return result.get("code", 0) if not control.stopping else 0
