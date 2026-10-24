# gammonview-helper

Analyze your backgammon matches on your own computer, and see the results on
[gammonview.com](https://gammonview.com).

GammonView analyses matches inside your browser, which stops at 2-ply. The
helper runs the deeper presets — up to *World Class*, with rollouts behind it —
on your own computer instead: you keep using the website exactly as before, and
the analysis happens at home.

Nothing listens on a port. The helper dials out to gammonview.com, waits for
work **you** queued from your own browser, runs it, and sends the result back.
There is no inbound connection, no router setting, no firewall rule.

## Install

Needs [uv](https://docs.astral.sh/uv/) (or pipx, or a plain `pip install` into
a virtualenv).

```bash
uv tool install gammonview-helper
```

That pulls in the analysis engine and its neural networks — about 80 MB, and it
takes a minute or two the first time.

On a Linux desktop, which has no installer, a second command does what the
macOS and Windows installers do — links the computer, makes it start whenever
you log in, adds **GammonView Helper** to your applications, and starts it:

```bash
uv tool install gammonview-helper && gammonview-helper install
```

The engine is built for x86-64 Linux only. Update later with `gammonview-helper
update` (below).

Two other forms, for whoever prefers them:

```bash
# the exact wheel from a release, no index involved. Pinned by definition:
# re-run it with a newer version to upgrade, since `uv tool upgrade` has
# nothing to ask about a package installed from a URL
uv tool install https://github.com/ngvlamis/gammonview-helper/releases/download/v0.1.1/gammonview_helper-0.1.1-py3-none-any.whl

# straight from a tag — needs `git`, and builds from source rather than
# installing the wheel that was tested
uv tool install git+https://github.com/ngvlamis/gammonview-helper@v0.1.1
```

## Link this computer

```bash
gammonview-helper link
```

It prints a word and a link. Open the link in any browser — on this computer,
or on your phone or another computer if this one is across the room or at the
end of an SSH connection. Sign in if you are asked, and pick the word it printed
out of the three you are shown. You will get an email saying the computer was
linked.

At a terminal on the computer itself, you can press Enter to open the link in
its default browser. Over SSH, or on Linux with no display, it does not offer
to; `--no-browser` turns the offer off anywhere.

If the three words do not include the one on your screen, **something is
wrong** — close the page and start again. That is the check working.

## Run it

```bash
gammonview-helper run
```

Leave it running. Analyses you start on gammonview.com will now go to this
computer, and the deeper presets appear in the menu. If the helper is not
running, the website analyses in your browser instead — you never get an error
for having closed it.

You can start it before linking. An unlinked helper waits for a credential
rather than giving up, so the usual order — install, let it start, then link in
the browser — needs no third step telling it to look again; it picks the link
up within a few seconds. The same is true if you unlink and re-link later.

### The menu-bar item (macOS)

On a Mac, `run` puts a doubling cube showing 2 in the menu bar. Its menu says
what the helper is doing — ready, analysing (with a percentage beside the
icon), paused, or not linked — and which account it is linked to, and has
these controls:

- **Pause / Resume.** A match already being analysed finishes first; after that
  the helper takes no more until you resume. The website stops offering this
  computer at once.
- **Open GammonView.**
- **Link This Computer…** — while it is not linked. Shows the word to pick
  and the link in a dialog, with a button to open the link in your browser,
  as `link` does in a terminal.
  Once linked, the same place offers **Unlink This Computer…**, which asks
  first and does what `unlink` does. Not while a match is being analysed.
- **Update to …** — only when gammonview.com offers a newer helper. Installed
  by the installer or with `uv tool`, it updates itself and restarts; installed
  any other way (`pipx`, a checkout), it tells you to update it the same way.
- **About GammonView Helper**, with the version.
- **Quit GammonView Helper.** Asks first, then stops it until you next log in
  or open the app below. A match being analysed at that moment is stopped, and
  the website says why.

`run --no-menu` leaves the item out. It is never shown over SSH.

On Windows the same menu is a notification-area icon, and on a Linux desktop a
status icon in the panel — at the top right on Ubuntu. Linux status icons need a
panel that hosts them: KDE, Cinnamon, XFCE and Ubuntu's GNOME do; GNOME
elsewhere needs the AppIndicator extension. Without one the helper runs as
usual with no icon. GNOME also hides status icons while the screen is locked;
the helper's comes back when it unlocks.

Run as the login item, the helper also keeps **GammonView Helper** in
Applications (in `~/Applications` if it cannot write to `/Applications`).
Opening it starts the helper again after a Quit, or says it is already running.
It is written on the Mac itself rather than downloaded, so it opens with no
Gatekeeper warning, and the installer's Remove deletes it.

### Keeping it running (macOS)

`run` stops when you close the terminal. To have it start at login and stay up:

```bash
deploy/install-login-item.sh
```

That registers a launchd agent pointing at the installed `gammonview-helper`,
logging to `~/Library/Logs/GammonView/helper.log`. Re-run it after an upgrade —
it boots the old job out first, so you never end up with two pollers sharing one
worker id, and it keeps whatever site the installed agent already pointed at.
To move a machine between sites, say so explicitly:

```bash
GAMMONVIEW_SITE=https://gammonview.com deploy/install-login-item.sh
```

It says when it moves one, because a credential is per site: the machine has to
be linked to the new one separately.

```bash
deploy/install-login-item.sh stop     # stop it, and leave it stopped
tail -f ~/Library/Logs/GammonView/helper.log
```

Installing always leaves the helper running, so a `stop` does not survive the
next install. After stopping, the website keeps saying **Connected** for up to
two minutes — a helper stops by going quiet, and the relay waits that long
before believing a silence.

The eventual installer does this for you; the script exists because the machine
this is developed on needed it first, and `deploy/com.gammonview.helper.plist`
records the four choices in it that are not boilerplate.

## The other commands

```bash
gammonview-helper update     # install the version gammonview.com has released
gammonview-helper status     # what is configured, and is the link still alive
gammonview-helper pause      # the menu's Pause: finish the current match, take no more
gammonview-helper resume     # the menu's Resume
gammonview-helper unlink     # unlink this computer from its account
gammonview-helper install    # Linux: link, start at log-in, add to applications
gammonview-helper uninstall  # Windows and Linux: remove the helper
```

`update` installs exactly the version gammonview.com offers, not simply the
newest on PyPI, and on a Mac restarts the login item onto it. On Linux it
restarts every running helper onto it. On Windows, an
installer install is updated in the background and its login item restarted,
since nothing can replace the files a running helper holds; elsewhere, stop the
running helper and start it again afterwards. It works for an installer install
and a `uv tool` one; for anything else it says to update the way you installed
it.

`unlink` does both halves: it tells the site to drop this computer, then
forgets the credentials here. If it cannot reach the site it still forgets the
credentials and says so, so use **Unlink** in your account settings to finish.
`--local-only` skips the site entirely.

The helper still cannot reach your account — a worker credential is refused by
every route that takes a sign-in. The one exception is the route that unlinks
*the caller itself*, which names no computer and so cannot name another one.

## Uninstalling

Remove the login item, the directory, and the account row:

```bash
gammonview-helper unlink                      # the account row, and the credentials
launchctl bootout gui/$(id -u)/com.gammonview.helper   # macOS login item
rm ~/Library/LaunchAgents/com.gammonview.helper.plist
rm -rf ~/Library/"Application Support"/GammonView
```

Order matters: `unlink` spends the credential that the last line deletes.

Installed with the macOS installer app instead? Open it again and press
**Uninstall** — it does all four, in that order. If you added the terminal
command from the menu, choose **Remove Terminal Command…** first, or delete
`~/.local/bin/gammonview-helper` afterwards; the installer leaves it behind.

### The terminal command, for an installer install

The installer keeps the `gammonview-helper` command inside its own folder and
off your PATH. To use it from a terminal too, choose **Add Terminal Command…**
in the menu: it links `~/.local/bin/gammonview-helper` to it, and says what to
add to your shell profile if `~/.local/bin` is not on your PATH. It never
replaces a `gammonview-helper` that is already there, such as a `uv tool`
install of your own. Run only one of them as the helper.

On Windows, use **Uninstall** in Settings > Apps. On Linux,
`gammonview-helper uninstall` unlinks, stops the helper, removes what `install`
added, and runs `uv tool uninstall`. Your settings in `~/.config/gammonview/`
are left behind; delete that directory too to forget the computer completely.

## Settings

Everything lives in one directory:

| | |
|---|---|
| macOS | `~/Library/Application Support/GammonView/` |
| Windows | `%LOCALAPPDATA%\GammonView\` |
| Linux | `~/.config/gammonview/` |

The credential is kept in your platform's credential store (Keychain, Credential
Manager, Secret Service), not in that directory. `status` says which store it
actually got — on a machine with no credential store it falls back to a
`0600` file, and tells you so.

`config.json` takes a few optional keys:

| key | default | what it does |
|---|---|---|
| `jobs` | `0` — the engine decides | how many of a match's decisions run in parallel |
| `threads` | `0` — the engine decides | engine threads inside each of those |
| `nice` | 10 | how hard the helper tries to stay out of your way |

The parallelism is sized by the engine, from measurements it keeps for the
purpose; set either to a number if you want a hard cap, and it will be kept —
over the site's "how much of this computer" below, too.
`nice` is what makes the helper something you forget is running — it yields to
whatever you are actually doing, and every engine worker inherits it.

### This computer's settings

A few settings are this computer's, and are chosen on gammonview.com under
**My Computers → Helper settings**, one card per linked computer:

- **How much of this computer to use** — all of it (the engine's own sizing),
  about half, or about a quarter of its cores. A `jobs` or `threads` in
  `config.json` wins over it.
- **Analyse while running on battery** — offered on a computer with a battery.
  With it off, an unplugged laptop takes no matches until it is plugged in.
- **When** — any time, or only between two times on this computer's clock.
- **Fetch and analyse OpenGammon matches on this computer** — shown once
  OpenGammon sync is set up — and, beneath it, when this computer does the
  analysing,
  **wait until I'm away from this computer**: synced matches are analysed only
  once the keyboard and mouse have been left alone for five minutes, so the
  sync never slows the computer while you use it. Offered where the helper can
  tell. Matches you analyse yourself never wait — sending one means you are at
  a computer, very often this one.

The helper hears a change on its next poll, within half a minute. Outside its
hours or on battery it behaves exactly as if paused — it takes no matches, and
one already being analysed is finished — and the menu says why: *Not taking
matches: on battery*. It picks up again by itself.

### Your own presets

Analysis presets of your own are built on gammonview.com, under **My Computers →
Helper settings**, not here. They belong to your account, so every computer
linked to it can run them: a match sent with one carries the preset's definition
to whichever computer takes it, and nothing is stored on this one. The analysis
is labelled `custom/<name>@<digest>`, so it says exactly which definition made
it. This needs an engine new enough to take a definition; `hello` tells the site
whether this computer's engine can, and an older helper is never sent one. The
same goes for the OpenGammon sync, whose level may be one of your presets.

**Judging accepted resignations** is an option on the same page, also your
account's. A match sent with it on is analysed judging them and labelled
`<preset>+resign`; only a computer whose engine can do that is given one.

## Licence

MIT. The engine ([bgsage](https://pypi.org/project/bgsage/)) is MPL-2.0 and is
installed as an ordinary dependency.

## Development

```bash
uv run pytest -q -m "not slow and not contract"
```

Two markers are deselected there. `slow` runs a real analysis and needs a
minute of CPU. `contract` talks to a **live** relay and is opt-in:

```bash
GAMMONVIEW_CONTRACT_BASE=https://beta.gammonview.com uv run pytest -m contract
```

The other end of that protocol — the relay itself — is part of the
gammonview.com service and is not open source. `docs/Relay.md` documents the
seam from this side, says which revision this client implements, and explains
why the contract carries a number of its own.
