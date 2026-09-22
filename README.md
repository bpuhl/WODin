# WODin

**An agent writes your workout. You log it on your phone at the gym. The agent reads back what you actually did.**

Live at **[wod.imav8n.com](https://wod.imav8n.com)**.

```
  agent                          athlete                        agent
  ─────                          ───────                        ─────
  reads results/<you>/           opens wod.imav8n.com           reads the result
  writes wods/<you>/<date>  ──>  signs in, logs the set  ──>    and prescribes
        (Object Storage)          (offline-capable PWA)          the next one
```

Each athlete gets their own workout, because the agent builds it from *their*
history — where their strengths are, and how that lines up with their goals.

## Getting in

Type the address and sign in:

```
wod.imav8n.com/brian          then a 6-digit PIN
```

That works on any device, which is the point: a workout should open on
whatever you are holding, without transferring a URL from somewhere else.

A long device-key link (`?key=…`) also still works and skips the PIN. Two
doors, one session — signing in lasts a year, so the app opens at a gym
with no signal.

## What's here

| | |
|---|---|
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | **the contract** — where the agent publishes, how it reads history, what the site serves to whom |
| `schema/` | the two JSON documents: the plan, and the result |
| `functions/server/` | the OCI Function serving the site and the API |
| `infra/` | Terraform: buckets, gateway, function, logging |
| `scripts/` | athlete and key management, IAM bootstrap, deploy helpers |
| `src/`, `styles/`, `public/` | the app itself — vanilla JS, no framework, no bundler |
| `cli/wodin.mjs` | `link`, `render`, `serve`, `parse`, `validate` |

## Running it

```sh
node scripts/build.mjs      # → dist/
python3 -m unittest discover -s functions/server -p "test_*.py"
node --test test/
```

Push to `main` and GitHub Actions builds, applies Terraform, syncs the
site, warms the function and verifies every asset byte-for-byte.

Issuing access:

```sh
python3 scripts/manage-athletes.py add --id brian --name "Brian"
python3 scripts/manage-athletes.py set-pin brian
python3 scripts/manage-athletes.py set-role doc coach --athletes brian sam
```

## Three decisions worth knowing

**The athlete is never in the URL.** `/wods/2026-09-22.json` resolves through
the signed-in session, so two people open the identical address and each get
their own workout. Isolation is structural — there is nothing in the URL to
tamper with.

**Nothing private is ever served as an object.** The key registry, the roster
and stored results are reachable only through the API, which scopes every
request to the caller. A signed-in athlete cannot fetch another's session by
guessing a path.

**Auth is a signed cookie, not a token exchange.** It lasts a year and needs
no network to keep working, because the one place this app has to work is a
gym with no signal.

## Credit

Forked from [BeachMonkey-AI/WODin](https://github.com/BeachMonkey-AI/WODin),
whose protocol — a workout as JSON, a result as JSON, and a link between
them — is the foundation this is built on. That project is deliberately
serverless: the workout travels in the URL fragment and touches no server.
This fork took the opposite turn, adding a backend so workouts can be
published per athlete and history kept, which is why the two have diverged
rather than one tracking the other.

Bundled typefaces (Barlow, Barlow Condensed, JetBrains Mono) are under the
SIL Open Font License 1.1 — see [`public/fonts/OFL.txt`](public/fonts/OFL.txt).
