# WODin on OCI — the deployment contract

Everything specific to running WODin at **https://wod.imav8n.com**.

**If you are an agent writing workouts, read
[`AGENT.md`](https://wod.imav8n.com/AGENT.md) instead** — it is the complete,
versioned guide to this deployment, and it is served from the live site so
it always matches what the page renders. This file is the operator's half:
the buckets, what the site serves, and how to run it.

---

## The layout

```
wodin-site/                  the function only
  index.html  AGENT.md  src/  styles/  fonts/  icons/  schema/  examples/  sw.js
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
| `/AGENT.md` | no — the agent guide, fetched each session |
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
