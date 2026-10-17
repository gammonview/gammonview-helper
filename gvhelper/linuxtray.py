# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The status icon: `tray.py`'s menu, on a Linux desktop.

Same items, same words, same reasons -- read `tray.py` for those, and
`wintray.py` for the Windows twin this follows most closely. What is written
here is only where Linux made something different:

* **Spoken over D-Bus directly**, as a StatusNotifierItem with its menu as a
  `com.canonical.dbusmenu`. That pair is what KDE, Ubuntu's GNOME (through the
  AppIndicator extension it ships enabled), Cinnamon, XFCE and Budgie all
  host. `pystray` reaches it only through PyGObject and the system's
  AppIndicator typelib, which `uv tool install` cannot bring; `dbus-fast`
  is a wheel. Stock GNOME elsewhere hosts no status icons at all, and there
  the helper runs with no icon -- as it would over SSH.
* **The icon is registered whenever a host appears**, not only at start. A
  helper started at log-in can win the race with the panel, and a host can
  restart (or be switched on in GNOME's Extensions) under a running helper.
* **The menu is asked for when it is opened**, so it can say the live
  percentage that the Windows menu leaves to its tooltip; a change is
  announced with `LayoutUpdated` and the host fetches it again.
* **Dialogs are `zenity` or `kdialog`** (`linuxapp.py`), each on a thread of
  its own, as on Windows.
* **Update restarts in place.** Linux replaces files a running process has
  open, so the update runs here, and the helper then `exec`s itself.

No `from __future__ import annotations` here: `dbus-fast` reads the D-Bus
signatures out of the annotations, and must see them as written.
"""

import asyncio
import os
import sys
import threading
import webbrowser
from importlib.resources import files
from typing import Callable

from . import __version__, linuxapp, update
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

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


def session_bus_address() -> str | None:
    """The session bus, as the desktop session would give it.

    A login item started by the session has it in its environment. Fall back
    to the systemd location so `available` and the tests agree with it.
    """
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if address:
        return address
    path = f"/run/user/{os.getuid()}/bus"
    return f"unix:path={path}" if os.path.exists(path) else None


def available() -> bool:
    """A desktop to put an icon in: a display, a session bus, and the library.

    Not whether a host is up *now* -- see the module's note on registering.
    """
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    if session_bus_address() is None:
        return False
    try:
        import dbus_fast  # noqa: F401
    except ImportError:
        return False
    return True


def has_host(timeout: float = 3.0) -> bool:
    """Whether a panel will show the icon right now: what `install` warns about."""
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    async def ask() -> bool:
        bus = await MessageBus(bus_type=BusType.SESSION, bus_address=session_bus_address()).connect()
        try:
            reply = await bus.call(Message(
                destination=WATCHER, path="/StatusNotifierWatcher",
                interface="org.freedesktop.DBus.Properties", member="Get",
                signature="ss", body=[WATCHER, "IsStatusNotifierHostRegistered"],
            ))
            return reply.message_type == MessageType.METHOD_RETURN and bool(reply.body[0].value)
        finally:
            bus.disconnect()

    try:
        return asyncio.run(asyncio.wait_for(ask(), timeout))
    except Exception:  # noqa: BLE001 - no bus, no watcher: no host
        return False


def pixmap(path: str) -> list:
    """`a(iiay)`: the PNG as ARGB32 in network byte order, which the spec wants."""
    from PIL import Image

    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        width, height = rgba.size
        data = rgba.tobytes()
    argb = bytearray(len(data))
    argb[0::4] = data[3::4]
    argb[1::4] = data[0::4]
    argb[2::4] = data[1::4]
    argb[3::4] = data[2::4]
    return [[width, height, bytes(argb)]]


def icon_path(paused: bool) -> str:
    name = "tray-paused.png" if paused else "tray.png"
    return str(files("gvhelper") / "resources" / name)


def later(fn: Callable, *args) -> None:
    """Off the bus's event loop, which a dialog would otherwise freeze."""
    threading.Thread(target=fn, args=args, daemon=True).start()


class Narrator:
    """`cli.pair`'s narrator, in dialogs."""

    def offer(self, machine: str, platform: str, word: str, url: str, expires_in: int) -> None:
        later(linuxapp.tell, (
            "Link this computer",
            "Your browser is opening GammonView. Sign in if you are asked, "
            f"then choose this word:\n\n{word}\n\n"
            "If the word is not one of the three you are shown, close the "
            f"page: something is wrong.\n\nIf no browser opened, go to:\n{url}",
        ))

    def linked(self, store_name: str, worker_id: str | None) -> None:
        later(linuxapp.tell, (
            "This computer is linked",
            "Analyses you start on gammonview.com will now run here.",
        ))

    def failed(self, reason: str, retry: bool) -> None:
        later(linuxapp.tell, ("Linking did not finish", reason), True)


# Stable ids for the menu's items: the host refers to an item by its id.
STATUS, ACCOUNT, OG, SEP1, TOGGLE, OPEN, LINK, SEP2, UPDATE, ABOUT, QUIT = range(1, 12)


class HelperMenu:
    """The menu's contents and actions, apart from D-Bus: built in a test too."""

    def __init__(self, cfg: Config, control: Control, worker: threading.Thread,
                 stop: Callable[[], None] = lambda: None) -> None:
        self.cfg, self.control, self.worker = cfg, control, worker
        self.linking = False
        self.release: dict | None = None
        #: Set by Update, read by `run` once the loop is down: exec the new code.
        self.restart = False
        #: Ends the bus's event loop; `run` replaces it once the loop exists.
        self.stop = stop
        # One question at a time: a second Quit while the first is still
        # asking would ask twice and act twice.
        self.asking = threading.Lock()

    def og_line(self) -> str | None:
        return self.control.og_line if self.control.state != "unlinked" else None

    def items(self) -> list[dict]:
        """Every item, in order: id, label, enabled, visible, and what it does."""
        c = self.control
        account, og, version = account_line(c), self.og_line(), offered(self.release)

        def item(id_, label, action=None, enabled=True, visible=True):
            return {"id": id_, "label": label, "action": action,
                    "enabled": enabled, "visible": visible}

        return [
            item(STATUS, status_line(c), enabled=False),
            item(ACCOUNT, account or "", enabled=False, visible=account is not None),
            item(OG, og or "", enabled=False, visible=og is not None),
            {"id": SEP1, "separator": True},
            item(TOGGLE, "Resume" if c.paused else "Pause", self.on_toggle),
            item(OPEN, "Open GammonView", self.on_open),
            item(LINK, link_label(c, self.linking), self.on_link,
                 enabled=link_enabled(c, self.linking), visible=c.state != "starting"),
            {"id": SEP2, "separator": True},
            item(UPDATE, f"Update to {version}…" if version else "", self.on_update,
                 enabled=c.state != "busy", visible=version is not None),
            item(ABOUT, "About GammonView Helper", self.on_about),
            item(QUIT, "Quit GammonView Helper", self.on_quit),
        ]

    def signature(self) -> tuple:
        """Everything the menu says. The host is told when this changes."""
        return tuple(
            (i["id"], i.get("label"), i.get("enabled"), i.get("visible")) for i in self.items()
        )

    def clicked(self, id_: int) -> None:
        for i in self.items():
            if i["id"] == id_ and i.get("action") and i["enabled"] and i["visible"]:
                i["action"]()
                return

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
            return linuxapp.ask(title, message)
        finally:
            self.asking.release()

    # --- Pause / Resume, Open, About ------------------------------------

    def on_toggle(self) -> None:
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

    def on_open(self) -> None:
        later(webbrowser.open, self.cfg.site)

    def on_about(self) -> None:
        later(linuxapp.tell, (
            "About GammonView Helper",
            f"GammonView Helper {__version__}\n\n"
            "Analyses your GammonView matches on this computer.",
        ))

    # --- Link / Unlink ---------------------------------------------------

    def on_link(self) -> None:
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
            linuxapp.tell((
                "This computer is unlinked",
                f"It could not be removed from your account's settings ({reason}). "
                "Use Unlink there to finish.",
            ))

    # --- Update ----------------------------------------------------------

    def on_update(self) -> None:
        later(self.update)

    def update(self) -> None:
        version = offered(self.release)
        if version is None or self.release is None:
            return
        where = update.target(os.path.abspath(sys.argv[0]))
        if where is None:
            linuxapp.tell((
                f"GammonView Helper {version} is available",
                "This copy was not installed with `uv tool`, so update it the "
                "way you installed it.",
            ))
            return
        if not self.ask(
            f"Update GammonView Helper to {version}?",
            "It takes a minute or two, and the helper starts again when it is done.",
        ):
            return
        if self.control.state == "busy":
            # A match arrived while the question was open; the restart
            # would end it half-analysed.
            linuxapp.tell((
                "A match is being analysed",
                "Choose Update again once it has finished.",
            ))
            return
        problem = update.install(where, self.release)
        if problem is not None:
            linuxapp.tell(("The update did not finish", problem), True)
            return
        self.restart = True
        self.shut_down()

    # --- Quit ------------------------------------------------------------

    def on_quit(self) -> None:
        later(self.quit)

    def quit(self) -> None:
        if not self.ask(
            "Quit GammonView Helper?",
            "It will stop analysing matches until you next log in, or until "
            "you open GammonView Helper from your applications.\n\n"
            "To stop for a while, choose Pause instead.",
        ):
            return
        self.shut_down()

    def shut_down(self) -> None:
        self.control.stop()
        # The loop fails a running job itself within a second; waited for
        # so the job has an answer before the process is gone.
        self.worker.join(QUIT_GRACE)
        self.sign_off()
        self.stop()


def layout(menu: HelperMenu) -> list:
    """`(ia{sv}av)`: the root and its items, as dbusmenu wants them."""
    from dbus_fast import Variant

    children = []
    for i in menu.items():
        if i.get("separator"):
            props = {"type": Variant("s", "separator")}
        else:
            props = {
                "label": Variant("s", i["label"].replace("_", "__")),
                "enabled": Variant("b", i["enabled"]),
                "visible": Variant("b", i["visible"]),
            }
        children.append(Variant("(ia{sv}av)", [i["id"], props, []]))
    return [0, {"children-display": Variant("s", "submenu")}, children]


def _interfaces(menu: HelperMenu):
    """The two D-Bus objects. Built in a function so that importing this
    module does not need `dbus-fast`, and `available` can say so."""
    from dbus_fast.constants import PropertyAccess
    from dbus_fast.service import ServiceInterface, dbus_property, method, signal

    class Item(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.kde.StatusNotifierItem")
            self.paused = False
            self.pixmaps = {p: pixmap(icon_path(p)) for p in (False, True)}

        @dbus_property(access=PropertyAccess.READ)
        def Category(self) -> "s":
            return "ApplicationStatus"

        @dbus_property(access=PropertyAccess.READ)
        def Id(self) -> "s":
            return linuxapp.APP_ID

        @dbus_property(access=PropertyAccess.READ)
        def Title(self) -> "s":
            return "GammonView Helper"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "Active"

        @dbus_property(access=PropertyAccess.READ)
        def IconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def IconPixmap(self) -> "a(iiay)":
            return self.pixmaps[self.paused]

        @dbus_property(access=PropertyAccess.READ)
        def ToolTip(self) -> "(sa(iiay)ss)":
            return ["", [], "GammonView Helper", status_line(menu.control)]

        @dbus_property(access=PropertyAccess.READ)
        def ItemIsMenu(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def Menu(self) -> "o":
            return MENU_PATH

        @method()
        def Activate(self, x: "i", y: "i"):
            pass

        @method()
        def SecondaryActivate(self, x: "i", y: "i"):
            pass

        @method()
        def ContextMenu(self, x: "i", y: "i"):
            pass

        @method()
        def Scroll(self, delta: "i", orientation: "s"):
            pass

        @signal()
        def NewIcon(self) -> None:
            pass

        @signal()
        def NewToolTip(self) -> None:
            pass

    class Menu(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("com.canonical.dbusmenu")
            self.revision = 1

        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> "u":
            return 3

        @dbus_property(access=PropertyAccess.READ)
        def TextDirection(self) -> "s":
            return "ltr"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "normal"

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "as":
            return []

        @method()
        def GetLayout(self, parent: "i", depth: "i", names: "as") -> "u(ia{sv}av)":
            return [self.revision, layout(menu)]

        @method()
        def GetGroupProperties(self, ids: "ai", names: "as") -> "a(ia{sv})":
            whole = {c.value[0]: c.value[1] for c in layout(menu)[2]}
            return [[i, whole[i]] for i in ids if i in whole]

        @method()
        def GetProperty(self, id_: "i", name: "s") -> "v":
            from dbus_fast import Variant

            for c in layout(menu)[2]:
                if c.value[0] == id_ and name in c.value[1]:
                    return c.value[1][name]
            return Variant("s", "")

        @method()
        def Event(self, id_: "i", event: "s", data: "v", timestamp: "u"):
            if event == "clicked":
                menu.clicked(id_)

        @method()
        def EventGroup(self, events: "a(isvu)") -> "ai":
            for id_, event, _data, _ts in events:
                if event == "clicked":
                    menu.clicked(id_)
            return []

        @method()
        def AboutToShow(self, id_: "i") -> "b":
            return False

        @method()
        def AboutToShowGroup(self, ids: "ai") -> "aiai":
            return [[], []]

        @signal()
        def LayoutUpdated(self) -> "ui":
            return [self.revision, 0]

        @signal()
        def ItemsPropertiesUpdated(self) -> "a(ia{sv})a(ias)":
            return [[], []]

    return Item(), Menu()


async def _serve(menu: HelperMenu) -> None:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SESSION, bus_address=session_bus_address()).connect()
    item, dbusmenu = _interfaces(menu)
    bus.export(ITEM_PATH, item)
    bus.export(MENU_PATH, dbusmenu)
    name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
    await bus.request_name(name)

    async def register() -> None:
        reply = await bus.call(Message(
            destination=WATCHER, path="/StatusNotifierWatcher", interface=WATCHER,
            member="RegisterStatusNotifierItem", signature="s", body=[name],
        ))
        if reply.message_type == MessageType.METHOD_RETURN:
            print("status icon registered", flush=True)

    def on_message(message) -> None:
        # A host that starts (or restarts) after us: register with it.
        if (message.member == "NameOwnerChanged" and message.body
                and message.body[0] == WATCHER and message.body[2]):
            asyncio.ensure_future(register())

    await bus.call(Message(
        destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus", member="AddMatch", signature="s",
        body=[f"type='signal',member='NameOwnerChanged',arg0='{WATCHER}'"],
    ))
    bus.add_message_handler(on_message)
    await register()

    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    menu.stop = lambda: loop.call_soon_threadsafe(done.set)

    drawn = menu.signature()
    tip = status_line(menu.control)
    while not done.is_set():
        if not menu.worker.is_alive() and not menu.control.stopping:
            # The loop died, and an icon over a dead loop would claim to
            # be ready while doing nothing.
            break
        if menu.control.paused != item.paused:
            item.paused = menu.control.paused
            item.NewIcon()
        if status_line(menu.control) != tip:
            tip = status_line(menu.control)
            item.NewToolTip()
        if menu.signature() != drawn:
            drawn = menu.signature()
            dbusmenu.revision += 1
            dbusmenu.LayoutUpdated()
        try:
            await asyncio.wait_for(done.wait(), REFRESH_INTERVAL)
        except asyncio.TimeoutError:
            pass
    bus.disconnect()


def run(cfg: Config, control: Control, loop: Callable[[], int]) -> int:
    """Run `loop` on a background thread and the icon on this one.

    Returns the loop's exit code if it ends by itself, and 0 on Quit. After
    Update, does not return: it becomes the new version.
    """
    if linuxapp.installed():
        # Refreshed on every start, so an update reaches it.
        linuxapp.ensure(os.path.abspath(sys.argv[0]))

    result: dict = {}

    def work() -> None:
        try:
            result["code"] = loop()
        except BaseException as e:  # noqa: BLE001 - reported, then exited
            print(f"helper loop failed: {type(e).__name__}: {e}", flush=True)
            result["code"] = 1

    worker = threading.Thread(target=work, name="helper-loop", daemon=True)
    menu = HelperMenu(cfg, control, worker)
    worker.start()
    threading.Thread(target=menu.watch_releases, name="helper-update", daemon=True).start()
    try:
        asyncio.run(_serve(menu))
    except Exception as e:  # noqa: BLE001 - no icon is not a reason to stop analysing
        print(f"no status icon: {type(e).__name__}: {e}", flush=True)
        worker.join()
    if menu.restart:
        linuxapp.restart()
    return result.get("code", 0) if not control.stopping else 0
