# The launcher

The thing a person downloads. It finds `uv`, installs `gammonview-helper` from
PyPI into a private directory, registers a login item, and hands pairing to the
package it just installed. Then it stops.

That list is the whole specification, and its shortness is the design. The
launcher ships **unsigned**, so somebody gets past Gatekeeper exactly once —
which means anything that might change has to live on the other side of PyPI,
where updating it costs nobody a dialog. See `docs/DesktopHelper.md` in the
GammonView repository, *"The native surface is a launcher, and it is frozen"*.

    launcher/macos/    Swift + AppKit, universal binary, no Xcode project
    launcher/windows/  C# + WinForms on .NET Framework 4.8, one .exe with uv inside

## Building

```sh
launcher/macos/checks.sh          # the decisions, in a few seconds
launcher/macos/build.sh           # -> launcher/macos/build/GammonView Installer.app
VERSION=1.0.1 launcher/macos/build.sh
```

Needs the Command Line Tools and `curl`. No project file, no package manager,
no dependency to resolve: this artefact is meant to be rebuilt rarely and
identically, sometimes years apart, and every one of those is a thing that rots
in between.

**A full Xcode is needed for a release build**, and the script says so rather
than quietly producing something narrower. Swift needs back-deployment shim
libraries to target an OS older than the runtime features it links against, and
the Command Line Tools ship those for arm64 only — Apple stopped building the
x86_64 slices. With only the CLT the script still builds, with the Intel floor
raised from macOS 11 to macOS 13, behind a banner saying that is not the
shipping configuration. `sudo xcodebuild -license accept` is usually all that
is missing.

## Releasing

```sh
git tag launcher-v1.0.3 && git push origin launcher-v1.0.3
```

`launcher.yml` builds it on a macOS runner and attaches the zip to the Release.

**The tag namespace is its own, and that is the whole point of it.** hatch-vcs
takes a `v*` tag as the Python package's version, so sharing one would make
every launcher rebuild publish a wheel nobody asked for — two artefacts with
deliberately opposite release cadences, since the package updates itself
through `uv` and this one has to be downloaded again by hand. `launcher-v*`
does not match `v*`, so neither workflow can be started by the other's tag.

The runner is not a convenience. It has a full Xcode, so it can reach the
macOS 11 Intel floor a machine with only the Command Line Tools cannot — and
the workflow *asserts* the floor it got rather than trusting the banner, which
is the difference between a build that warns and a release that cannot ship
something narrower than what was tested.

The version in the tag is stamped into `Info.plist`. It is **not** the
generation: that is `launcherGeneration` in `Manifest.swift`, and it moves only
when the protocol does.

## What it is allowed to know

Three things, and each of them is fetched rather than compiled in:

| question | answered by |
| --- | --- |
| which helper version to install | `helper-release.json` on gammonview.com |
| which Python to install it on | the same file's `python` field |
| which site the helper talks to | the same file's `site` field, or the package |

That last row used to read "the helper package's own default", which was right
in principle and wrong in practice: the package's default comes from
`config.json` in the install root, and the install root is a directory an
earlier hand-install may already have written. See `Manifest.site`.

`launcher_min` in that file is the escape hatch, and the only one. A launcher
whose generation is below it stops and sends the user to the download page,
which is how an already-installed copy can be told the protocol moved. It is
worthless unless read from the first release, so it exists at generation 1 with
nothing yet to reject.

## What it installs, and where

Everything under `~/Library/Application Support/GammonView/` — `uv`, a private
CPython, the venv, the cache — plus one login item. Never the user's PATH,
never their `uv`, never their Python. Uninstall:

```sh
launchctl bootout gui/$(id -u)/com.gammonview.helper
rm ~/Library/LaunchAgents/com.gammonview.helper.plist
rm -rf ~/Library/"Application Support"/GammonView
```

Or open the installer again and press **Uninstall**, which is the same list
plus the half no shell command here can do: `gammonview-helper unlink` first,
so the computer stops being listed in the user's account settings. That step
has to come first, because it spends the credential the third line deletes.

`uv` is copied *out* of the app bundle rather than run from it, so the
installer itself is disposable: dragging it to the Trash afterwards — the
normal thing to do with an installer — must not take the working install away.

## Four things that will surprise you

**It looks finished before it is.** ~80 MB of Python, numpy, bgsage and weights
arrive after the window appears, which the plan flags as the one place this
flow can confuse somebody. Hence a sentence that always says what is happening,
and a progress bar that does not stop until something has actually ended.

**A migrating user gets one Keychain prompt.** `keyring` binds a stored item's
ACL to the binary that created it. Somebody who installed the helper by hand
has a token owned by `~/.local/share/uv/tools/...`, and the launcher's copy is
a different binary, so macOS asks once — *Always Allow* is the answer. A new
user has no such item and sees nothing. Changing the manifest's `python` pin
rebuilds the interpreter and can re-ask, which is one more reason that field
moves rarely and deliberately.

**A leftover credential is not a link.** `status --porcelain` reports both
`linked` (is there a token in the Keychain) and `link` (what the relay said
when asked). Only the second one means anything: unlinking in account settings,
deleting the account, letting a worker session expire, or restoring a login
keychain onto a new Mac all leave the first one true. The installer reads
`link`, so a machine in that state is paired again rather than told it is fine
-- which makes "run the installer again" the remedy for a link that stopped
working, and is why it has to be the remedy: a user of this has no terminal to
be given a command for.

**Running it twice asks a question.** Every other decision this program makes
it makes for itself, which is what a one-button installer is. But a second
double-click is genuinely two different intentions -- update the thing, or take
it off this computer -- and there is no way to infer which. So an install it
finds already present gets a screen with **Update** and **Uninstall** on it,
Update being the default because it is the one that undoes nothing. Uninstall
is never the Return key.

Uninstalling runs `gammonview-helper unlink` *first*, before the login item and
before the files, and the order is forced rather than tidy: unlinking is the
only step that needs the credential, and the credential lives in a store that
only the installed helper knows how to reach. Done last it could not be done at
all, and the account would go on listing a computer that no longer has the
software on it -- which is the state that made an uninstall worth building.

## Testing it

`checks.sh` covers every place the launcher decides something from data it did
not produce: the two JSON shapes the helper emits and the plist it writes
itself. Those are the parts worth pinning, because this ships frozen — a
misread field is not a bug that gets fixed next week, it is a bug sitting on
other people's computers. Both readers it exercises are in their current form
because an earlier one was wrong in exactly that way. It runs on every push.

The rest is irreducibly about seizing a machine. A real run takes over
`com.gammonview.helper` from whatever is already serving it, so there is one
affordance for testing and it is not a supported interface:

```sh
export GAMMONVIEW_INSTALLER_ROOT=/tmp/gv-test
"build/GammonView Installer.app/Contents/MacOS/GammonViewInstaller"
```

That relocates the install root *and* makes the login-item step a no-op — one
variable, because they are one decision: a sandboxed install is a real install
of everything except the part shared with the rest of the system. Only when it
is set is `GAMMONVIEW_INSTALLER_MANIFEST` honoured as well. That gating is
deliberate: the manifest decides which code lands on somebody's computer, so an
environment variable able to repoint it would be the softest part of the whole
install, and a shipped run has no path to it at all.

The pairing half can be driven without a relay by building a stub wheel that
speaks the two porcelain shapes (`{"event": "offer", ...}` and
`{"event": "linked"}`) and pointing a local manifest at its version.

## Windows

The same program, step for step, and its source comments point back at the
Swift ones rather than repeat them. `launcher/windows/`:

```sh
launcher/windows/checks.sh                      # the decisions, any OS
launcher/windows/build.sh                       # -> build/GammonView-Installer.exe
VERSION=1.0.0 launcher/windows/build.sh
```

Needs the .NET SDK (8 or later) and `curl`, on **any** OS: the exe targets .NET
Framework 4.8, which ships with Windows 10 and 11 so the user installs nothing
first, and the Framework's reference assemblies come from NuGet, so a Mac can
build it. Releases are built on a Windows runner anyway, because only there can
the smoke install run (below).

Released on its own tag namespace, which matches neither of the others:

```sh
git tag launcher-win-v1.0.0 && git push origin launcher-win-v1.0.0
```

A `workflow_dispatch` of `launcher-windows.yml` builds, checks and
smoke-installs without releasing, and leaves the exe as a workflow artifact.

### Where Windows differs

**One `.exe`, not a zip.** `uv` is embedded as a resource and written out on
every run. A downloaded zip has to be extracted first, and on Windows the
failure when it is not is silent: Explorer runs the exe from a temporary copy
that disappears.

**The login item is a scheduled task**, not the `Run` registry key, because a
task can restart the helper after a crash (launchd's `KeepAlive`) and can be
stopped by name. It runs `pythonw.exe -m gvhelper run` out of the venv -- the
console entry point would put a window on the desktop at every sign-in -- with
`--site` on the command line where macOS sets `GAMMONVIEW_SITE`. The task
carries the user's SID in its name, needs no administrator rights, and has the
task defaults that would quietly break it turned off: no start on battery, stop
when unplugged, end after three days. `LoginItem.cs` has the full list.

**The running helper is stopped before anything is replaced.** On macOS, `uv
tool install --force` swaps files under a running helper and nothing notices.
Windows refuses to replace a loaded DLL, so Update and Uninstall both end the
task and then every process running out of the install root.

**It keeps no copy of itself.** An earlier build kept one in `bin` for Settings
and the menu to run, and Windows Defender quarantined it as
`Behavior:Win32/Persistence.A!ml`: an unsigned program copying itself into
AppData and registering the copy is what malware does. So the two things it was
kept for are the helper's own (`gvhelper/winmaint.py`). The menu's **Update**
and `gammonview-helper update` hand the update to a stdlib-only script run by
the base interpreter under `python\`, outside the venv, which ends the task,
reinstalls with the installer's `uv` and environment, and starts the task again.
The **Settings > Apps** entry runs `pythonw.exe -m gvhelper uninstall`, which
unlinks, removes the task, the entry and the shortcut, and leaves a hidden
`cmd` to delete the root once it has exited. The entry's icon is
`icon.ico`, written into the root. Opening the installer again still offers
Update and Uninstall, from wherever it was downloaded to.

**A Start menu shortcut**, "GammonView Helper", runs `pythonw.exe -m gvhelper
start`: start the task if it is stopped, and say where the icon is. Windows 11
hides a new notification-area icon behind the ^ arrow, so starting in silence,
as the macOS app does, would look like not starting. What `start` does lives in
the package (`gvhelper/winapp.py`); the installer only writes the shortcut.

**The menu is a notification-area icon** (`gvhelper/wintray.py`, on `pystray`),
with the macOS menu's items. Quit just exits: the task restarts only on
failure.

**Logs are inside the root**, at `%LOCALAPPDATA%\GammonView\logs\helper.log`,
written by the helper itself when it finds it has no console (`pythonw` has
none). Uninstall from Settings > Apps, or open the installer again and press
**Uninstall**; either unlinks first. By hand:

```bat
schtasks /End /TN "GammonView Helper <your SID>"
schtasks /Delete /TN "GammonView Helper <your SID>" /F
reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\GammonViewHelper /f
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\GammonView Helper.lnk"
rmdir /s /q "%LOCALAPPDATA%\GammonView"
```

**`app.manifest` is load-bearing.** Windows assumes an unmanifested program
with "Installer" in its name wants administrator rights and raises a UAC prompt;
`asInvoker` stops that. `dpiAware` stops the window, and the confirmation word,
arriving blurred on a scaled display.

**x64 only.** bgsage has Windows wheels for AMD64 alone, so an ARM64 PC runs
the helper under emulation regardless -- and an x64 `uv` asks for an x64
Python, where an ARM64 one would fetch an interpreter the engine cannot
install into.

### Testing it

`checks.sh` ports every macOS check and adds the two decisions only Windows
has: quoting an argument onto a command line, and the task document. On a
Windows machine it also registers that document with Task Scheduler under a
throwaway name, reads it back and deletes it -- the schema is Windows' and only
Windows can say whether it is a task.

The sandbox is the macOS one: `GAMMONVIEW_INSTALLER_ROOT` moves the root and
skips the task, the shortcut and the Settings entry, and only then is `GAMMONVIEW_INSTALLER_MANIFEST` honoured. A
sandboxed run also points the helper at the sandbox for its config. Add
`GAMMONVIEW_INSTALLER_HEADLESS=1` and it runs with no window, prints each step,
and stops before pairing; that is what `launcher-windows.yml` runs on every
build, against the live manifest and PyPI.

```bat
set GAMMONVIEW_INSTALLER_ROOT=%TEMP%\gv-test
GammonView-Installer.exe
```
