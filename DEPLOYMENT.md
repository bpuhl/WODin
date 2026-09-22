# WODin on OCI — the deployment contract

Everything specific to running WODin at **https://wod.imav8n.com**.

`AGENT.md` is upstream's protocol — the two JSON documents and the link.
It is unchanged and still correct. This file is the other half: where
those documents live in *this* deployment, and how an agent reaches them.

---

## For the agent

You publish workouts and read history by talking to **Object Storage
directly**, not to an HTTP API. There is no endpoint to call, no token to
mint, and nothing to keep running.

### The bucket

| | |
|---|---|
| Bucket | `wodin-data` |
| Compartment | `WODin`, under `Projects` |
| Region | `us-sanjose-1` |
| Auth | instance principal — dynamic group `claudebot-agents` |

There is a second bucket, `wodin-site`, holding the app and `auth.json`.
**You have no grant on it and should not look for one.** It contains the
session signing secret and every athlete's key hash; anything holding the
signing secret can forge any athlete's session. The split exists so your
grant can be bucket-wide and still exclude it.

### 1. Who to build for

Read `roster.json`:

```json
{
  "version": 1,
  "generated": "2026-09-22",
  "athletes": [
    { "id": "brian", "name": "Brian", "disabled": false, "role": "athlete",
      "coaches": [], "wods": "wods/brian/", "results": "results/brian/" },
    { "id": "doc", "name": "Doc", "disabled": false, "role": "coach",
      "coaches": ["brian"], "wods": "wods/doc/", "results": "results/doc/" }
  ]
}
```

`role` is `athlete`, `coach` or `admin`. For a coach, `coaches` names
exactly the athletes they may act for. A coach is still an athlete in
their own right, with their own `wods/` and `results/`.

Skip anyone `disabled`. Each entry tells you both paths, so you never have
to construct them.

Do **not** try to discover athletes any other way. The authoritative list
is `auth.json`, which you cannot read, and this file is generated from it
on every change precisely so you do not need to.

### 2. What they have done

Read `results/<athlete>/_index.json` for the session list, newest first:

```json
{
  "athleteId": "brian",
  "sessions": [
    { "workoutId": "2026-09-20", "title": "Routine 2 — Back + Biceps",
      "duration": "47:12", "durationSec": 2832, "rpe": 9,
      "sets": 2, "skipped": 1,
      "submittedAt": "2026-09-20T04:45:02.839669+00:00" }
  ]
}
```

Then read `results/<athlete>/<workoutId>.json` for any session in full —
that is the complete result document from `result.schema.json`, plus two
fields the server adds: `athleteId`, and `receivedAt` (when the server
actually took it, as opposed to whatever the phone's clock claimed).

**Prefer the index to listing the prefix.** It exists so that reading
history is one object, and it stays correct because it is rewritten on
every submission.

#### Reading cardio: pace is always per 500m

For rowing, `pace` means **time per 500 metres**, whatever the interval's
actual distance is. A 250m rep at `2:15` pace is a 2:15/500m split, not a
2:15 rep.

This is a house convention, not a protocol rule — the schema deliberately
keeps `pace` generic ("the denominator becomes the field's unit suffix"),
so `/mile` and `/km` are equally valid for other movements. It is written
down here because **the stored result drops the denominator**: a plan says
`"pace": "2:15/500m"` and the logged entry comes back `"pace": "2:15"`.
Without the convention, history is genuinely ambiguous — the same string
could be a 500m split or a rep time.

Write the denominator in the plan; assume `/500m` when reading a rowing
result back.

#### Sessions logged by someone else

A coach may log a session on an athlete's behalf, so a result carries two
identities:

```json
{ "athleteId": "brian", "submittedBy": "doc" }
```

`athleteId` is whose training it is; `submittedBy` is who sent it. They
differ only when a coach logged it. Treat `submittedBy != athleteId` as
second-hand: the numbers were entered by someone who was not necessarily
holding the bar, which is worth weighing before prescribing from them.

#### asPlanned, and what it does not mean

`asPlanned: false` marks a set that diverged from the prescription. Fields
named in the plan's `athleteFills` are excluded from that comparison,
because the plan holds `null` there on purpose — a cardio set prescribing
distance and pace with `"athleteFills": "duration"` comes back
`asPlanned: true` when performed as written, however fast it was rowed.

An unanswered `rpe` or `athleteSummary` is `null`, which means the athlete
did not answer. It does not mean zero, and it does not mean agreement.

### 3. What they should do next

Write `wods/<athlete>/<date>.json`, conforming to `wod.schema.json`.

```
wods/brian/2026-09-22.json
```

One file per athlete. There is no shared workout and no fallback: if you
do not write a file for someone, they get a clean "nothing today" rather
than somebody else's session.

Set the sink so the result comes back on its own:

```json
{ "sink": { "type": "post", "url": "https://wod.imav8n.com/api/log" } }
```

No token in it. The athlete's browser is already authenticated to this
origin by their device-key session, so the POST carries their cookie and
the server attributes the result from the session — not from anything in
the link. A link that leaks therefore grants nothing.

---

## The layout

```
wodin-site/                  the function only
  index.html  src/  styles/  fonts/  icons/  schema/  examples/  sw.js
  auth.json                  signing secret + key hashes. Never served.

wodin-data/                  the function and the agent
  roster.json                who exists. Never served over HTTP.
  wods/<athlete>/<date>.json      you write these
  results/<athlete>/<workoutId>.json   the athlete's logged session
  results/<athlete>/_index.json        summaries, newest first
```

Both buckets are `NoPublicAccess` and versioned. Nothing is reachable
except through the gateway, and the gateway serves neither `auth.json`,
`roster.json` nor `results/` as objects at all.

## What the site serves, and to whom

| Path | Needs a device key? |
|---|---|
| `/`, `/src/*`, `/styles/*`, `/fonts/*`, `/icons/*`, `sw.js`, `manifest` | no |
| `/schema/*`, `/examples/*` | no — agents resolve `$id` to these |
| `#w=…` shared links | no — the workout travels in the fragment |
| `/wods/<date>.json` | yes — resolved to the caller's own athlete |
| `/api/history`, `/api/history/<id>`, `POST /api/log` | yes |
| `auth.json`, `roster.json`, `results/*` | never served, at any auth level |

`/wods/<date>.json` carries no athlete. The server supplies it from the
session, so two people opening the identical URL get their own workout and
neither can reach the other's by editing it.

## Running it

| | |
|---|---|
| Deploy | push to `main`; GitHub Actions builds, applies Terraform, syncs `dist/`, warms the function, and verifies every asset byte-for-byte |
| Issue a device key | `python3 scripts/manage-athletes.py add --id <id> --name "<name>"` |
| List / revoke | `... list`, `... disable <id>`, `... remove <id>` |
| Terraform locally | `scripts/tf.sh plan` — shares CI's state object |
| Refresh CI secrets | `scripts/sync-secrets.sh --repo bpuhl/WODin --compartment <ocid>` |

IAM is deliberately **not** in the Terraform. `manage policies` is
privilege escalation by definition, so the deploy identity does not have
it, and the tenancy-level grants live in two scripts a human runs:

- `scripts/bootstrap-iam.sh` — WODin's own components
- `scripts/bootstrap-agent-iam.sh` — the cross-compartment grant to the agent

A caution learned here: **OCI accepts a policy naming a dynamic group that
does not exist.** It is created successfully, grants nothing, and reports
no error. Renaming a dynamic group therefore breaks every policy naming it,
silently. Compartments are safe to rename — everything references them by
OCID.
