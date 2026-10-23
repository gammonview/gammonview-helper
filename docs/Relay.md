# The relay protocol, revision 4

What this package talks to, and the one document that has to stay true when the
two halves of it are maintained apart.

The server is `accounts/gvaccounts/relay.py` in the GammonView repository,
which is **private**. This client is public. That asymmetry is intentional — a
program that runs on other people's computers should be readable by them, and a
service that holds other people's accounts should not be — but it has a cost:
neither side can be edited with the other on screen, and a mismatch does not
announce itself. A helper against a changed relay pairs successfully, polls
happily, and never claims a job.

So: a contract revision, `RELAY_CONTRACT` in `gvhelper/client.py`, sent in the
`User-Agent` on every request as `gammonview-helper/<version> (relay/<n>)`.
Bump it when a route, a field, or a status code changes meaning. Do not bump it
for a release — the package version is on its own line for exactly that reason.

## Base

Every path below is relative to `{site}{api_path}/relay`, which by default is
`https://gammonview.com/accounts/relay`. See `gvhelper/config.py`.

## Pairing — no credential, and two secrets that must not be confused

| Method | Path | Who calls it |
| --- | --- | --- |
| POST | `/pair/start` | helper |
| GET | `/pair/{code}` | browser |
| POST | `/pair/{code}/confirm` | browser |
| POST | `/pair/poll` | helper |

`start` returns **two** distinct strings. `code` goes in the URL the helper
opens in a browser; `secret` never leaves the helper and is what `poll`
presents. Handing one value to both jobs would mean anyone who saw the link
could collect the token it was about to mint. `PairingClient` keeps them apart
by construction and `tests/test_client.py` asserts the secret is never put in a
URL.

A pairing lives **five minutes** (`PAIR_TTL_SECONDS`). The browser is shown three
words and must pick the one the helper printed; a wrong pick fails the pairing
rather than offering another go.

## Work — a worker session, and nothing else

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/worker/hello` | name, version, engine, presets, features, available |
| GET | `/worker/next-job?wait=` | long poll; server clamps `wait` to **25s** |
| POST | `/worker/jobs/{id}/progress` | also the lease renewal |
| POST | `/worker/jobs/{id}/result` | raw `application/octet-stream` body |
| POST | `/worker/jobs/{id}/error` | message truncated to 500 chars |
| POST | `/worker/offline` | not taking work for now; the next poll undoes it |

These carry `Authorization: Bearer <worker token>`, a session whose `scope` is
`worker`. The account routes reject it — the partition between `current_user`
and `current_worker` is the security boundary the whole design rests on, which
is why `unlink` here is local-only: this token cannot delete its own row.

Three server timings this client is built around. A job's lease is **300s**
(`LEASE_SECONDS`) and a progress report renews it, so progress *is* the
heartbeat and `daemon.py` posts one every 5 seconds whether or not the number
moved. A worker unheard from for **120s** (`WORKER_FRESH_SECONDS`) stops being
`live`, which is what the site's menu reads — and **an empty poll counts as
being alive**, which it did not until revision 2; a helper waiting quietly for
work is the normal state of a working helper. A claim is first-wins under one
`UPDATE … WHERE status='queued'`, so two helpers on one account race safely.

**Long polls live under other people's proxies.** The server holds `next-job`
for at most 25 seconds and this client asks for 20, both of them under the 30
that read timeouts commonly default to. A poll held for exactly as long as the
proxy will wait is a coin flip between an answer and an HTML 504 from
something the helper has never heard of. So `next_job` also treats 502, 503,
504, 408 and 524 as *no work* rather than as errors — nothing was claimed, so
nothing is lost by asking again — with a five-second floor, because a proxy
whose upstream is down answers instantly and the alternative is a busy loop.

The match arrives base64-encoded inside JSON; the result goes back as a raw
gzipped `.gvab` body. Not symmetric, and deliberately: the job envelope has
fields beside the bytes, the result is only bytes.

**What a helper says it can do, and what it is then given.** `hello` carries
`features`, words for what this helper can do beyond running a named preset
(`machine.helper_features`):

| Word | Meaning | What the relay then does |
| --- | --- | --- |
| `custom_presets` | the engine takes a preset as a definition | may hand it a job with `preset_name` and `preset_spec` |
| `resign_errors` | the engine can judge accepted resignations | may hand it a job with `resign_errors: true` |
| `machine_settings` | the helper applies its computer's settings | the settings page offers them for this computer |
| `battery`, `idle` | this computer can tell either | the page offers the settings that need them |

A job needing a word goes only to a helper that sent it, so an old helper
beside a new one takes the jobs it can run and leaves the rest. Both optional
handout fields are absent when not needed, so an old helper sees the handout it
always did.

**This computer's settings** come back on `hello` and on every `next-job`
answer, empty or not, as `settings` (`gvhelper/machine.py` says what each
means). On the poll, so a change on the settings page reaches a running helper
within one wait. A helper that rests by them — on battery, outside its hours —
signs off with `/worker/offline` as Pause does, and says hello now and then to
hear them change.

**`release`**, on the same answers, is the helper version the site's
`helper-release.json` names, or null when the relay could not read it. A
helper that hears one it has not seen rereads the manifest straight away
rather than at its next six-hourly check (`update.wait_for_release`), so its
menu offers an update about when the site's banner starts asking for one. The
manifest still decides what is offered. Added within revision 4: an older
helper ignores it, and against an older relay a helper hears nothing and
keeps the six-hourly check.

**`outdated`**, on the same answers, is true when this helper is older than
the manifest's `minimum`, which a release raises when it fixes what analyses
say. The relay then gives it no jobs — a poll waits and answers `job: null` —
refuses a synced match it analysed itself (409, so it says hello again), and
says sync is off in `hello` when sync analyses on that computer. Nothing else
changes; it updates the way any helper does. A helper from 0.12.2 shows it in
its menu (`update.held`). Added within revision 4: an older helper ignores the
key and simply gets no work.

**451** is the site, not the relay: nginx answers it to every `/accounts/` route
for a computer in a region accounts and analysis are not offered in (the
site's `docs/RegionRestriction.md`), with a `detail` sentence saying so. The
helper treats it as neither weather nor unlinking: it logs the sentence once,
shows "Not available in your region" in its menu, and asks again hourly
(`daemon.REGION_RETRY`), since the answer changes only when the computer is
somewhere else. Pairing from such a region fails with the same sentence.
An older helper counts it as weather and retries every minute.

**`available`** in `hello` is false from a helper that is running but not
taking work: paused, or resting. The relay then leaves the machine offline
where a hello used to clear it — which mattered, because the OpenGammon sync
thread says hello every five minutes whether or not the loop is paused.

## What this client does not use

`/workers`, `/workers/{id}`, `/analyze`, `/analyze/{id}`, and
`/analyze/{id}/result` are the browser's half of the relay, taking
`current_user`. They are listed here so that "the helper does not call this"
is written down rather than inferred.

## Revisions

**1** — the protocol as first deployed to beta, 2026-09-16. Pairing with
word-confirmation, the five worker routes above, `scope='worker'` sessions.

**2** — 2026-09-16, the same day, after the first live pairing found two things
no local test could. `next-job` now refreshes `last_seen_at`, so an idle helper
stays `live` instead of vanishing from the site after two minutes while its own
`status` command still reported a working link. And `wait` is **clamped**
rather than validated: it used to be `le=MAX_WAIT_SECONDS`, so lowering that
cap — which revision 2 does, 30 to 25 — would have answered every helper still
asking for 30 with a 422. A server upgrade must not break clients on machines
nobody here can update.

A revision-1 client works unchanged against a revision-2 relay. The number is
for reading logs and for knowing what to check, not a handshake.

**3** — 2026-10-02, for the menu-bar item's Pause and Quit. `POST
/worker/offline` marks this machine not `live` at once, instead of after the
120 seconds of silence it otherwise takes — two minutes in which the site said
Connected and queued matches for a helper that had stopped asking. It sets an
`offline_at` beside `last_seen_at` rather than ageing `last_seen_at`, so the
settings page's "last seen" stays true. Only `next-job` and `hello` clear it:
progress and results on a match the helper is finishing *after* Pause renew the
lease without making the machine available again.

A revision-3 client against a revision-2 relay gets a 404 from that route and
treats it as success: going quiet is what pausing did before the route existed.

**4** — 2026-10-21, for the settings page. `hello` gains `features` and
`available`; its answer and every `next-job` answer gain `settings`; a job may
carry `preset_name` and `preset_spec` (one of the account's own presets) and
`resign_errors`, but only to a helper whose `features` say it can run them.
Every addition is optional in both directions. A revision-3 helper sends
neither field, is never given a job needing a feature, and ignores `settings`;
a revision-4 helper against a revision-3 relay hears no settings and runs as an
unset computer does.
