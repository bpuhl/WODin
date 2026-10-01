# CLAUDE.md — WODin

- **What this app does:** Turns a structured workout (JSON) into a phone-first PWA the
  athlete logs against, then emits what they actually did as structured data an agent can
  read. The protocol — `AGENT.md` plus the two schemas — is the actual product; the PWA is
  the reference implementation and the CLI is convenience. Replaces a Google Apps Script +
  Sheets original that only Apps Script could drive.
- **Live:** https://wod.imav8n.com (OCI: private buckets, a Function behind an API Gateway)
- **This is a fork that diverged.** Upstream is serverless by design — the workout travels
  in the URL fragment and touches no server. This one added a backend: device-key and PIN
  sign-in, per-athlete workouts, stored history, roles. No updates are pushed or pulled.
  `DEPLOYMENT.md` is the contract; `AGENT.md` is upstream's protocol, still correct about
  the schemas and wrong about delivery.
- **Model:** vanilla (no bundler, no framework, no runtime deps; `sharp` is dev-only for icons)
- **Token deviations from app-template:** `--accent` is `#cdf24a` (lime), ground is `#0d1011`
  with a faint green bias. Dark-committed on purpose — no light theme, no
  `prefers-color-scheme` block. The original app was dark and this lives in a garage gym.
  Type is Barlow Condensed / Barlow / JetBrains Mono rather than the template default.

## Things that aren't obvious from the code

- **There is no published npm package.** `package.json` is `private: true` and was never
  published, so never write `npx wodin` in docs — that name on npm belongs to an unrelated
  package and would run someone else's code. Every documented command is `node cli/wodin.mjs`
  from a clone.

- **Odin's taxonomy is load-bearing.** A **set** is one prescription row (`Set 1`, `Set 2`);
  an **exercise** is the movement containing them. The athlete's note attaches to the
  *exercise*, one per movement. This was gotten wrong once — an earlier pass put notes on
  the row — so don't "fix" it back.
- **`kind` cannot be inferred.** Which fields to draw can't be derived from which plan values
  are non-null, because a field the athlete is meant to fill is null in the plan too. Agents
  must set it; `wodin validate` fails without it.
- **Bodyweight is `loadType: "bodyweight"`, never `load: 0`.** The original app showed
  `0 × 12` for band pull-aparts, which is the wart this replaces.
- **Prefill rule:** prescribed values (load, reps, distance, pace) prefill; subjective values
  (session RPE, notes, summary) start blank with the Rx shown only as a ghost hint. Never
  prefill RPE — an answered 7 and a defaulted 7 must stay distinguishable in the result.
- **`log` in a result is complete, not sparse.** Every non-skipped set appears, including
  `asPlanned: true` ones. Absence means skipped, never compliance.
- **Listeners bind once, outside `render()`.** `render()` replaces `#app`'s innerHTML but not
  the element, so binding inside it stacks a listener per render — one tap then fires all of
  them. That shipped once and added hundreds of rows per click.
- **Every path is relative.** Only `main` deploys here (no PR previews), but the app still
  comes from app-template's one-build-any-base-path shape, and `index.html`'s SW
  registration assumes it. Don't introduce root-absolute paths.
- **Two ways a workout arrives.** Signed in, the app fetches `/wods/<date>.json`; the
  server resolves the athlete from the session, never from the URL. A shared `#w=`
  (deflate-raw + base64url) or `#wj=` (plain base64url JSON) link still works without
  sign-in, and the fragment never reaches the server. Share strips `sink` before
  re-encoding, so a forwarded link can't log to anyone's history. A fragment workout
  therefore has no sink, and "Log workout" opens the Share/Copy sheet (`hasSink()` in
  `src/submit.js`).
- **Offline is a requirement, not a nice-to-have.** Gyms have no signal. Loading and
  logging a workout must work with no network once it has been opened. The POST to
  `/api/log` can't, and there is no retry queue: a failed send falls back to the
  Share/Copy sheet so the session still gets out.
- **This deployment's sink carries no credential.** Workouts point at
  `https://wod.imav8n.com/api/log`; the athlete's device-key session cookie authenticates
  it, and the server attributes the result from the session (`submittedBy` differs from
  `athleteId` when a coach logs for someone). Upstream's per-workout Bearer token pattern
  and `sink.mode: "blind"` (no-cors, opaque response, "delivery not confirmed") remain in
  `AGENT.md`, the schema, and `postResult()` for workouts from other publishers. They
  aren't used here, so don't add a token to the sink.
- **Never report a send failure we cannot observe** (this matters for cross-origin sinks;
  ours is same-origin). A cors-mode `fetch` rejects identically
  whether the request never left or it landed and the response merely omitted
  `Access-Control-Allow-Origin` — a very common server-side miss, since people set it on the
  preflight and forget the POST. WODin shipped claiming "Send failed" there, while payloads
  were arriving fine; a real smoke test caught it. Only `navigator.onLine === false` lets us
  say "nothing sent". Everything else that throws is "Sent — delivery not confirmed".

## Before changing infrastructure

Read `~/projects/PATTERNS.md`. It collects the OCI, Actions, service-worker
and testing traps that already cost real debugging time on this project and
its two siblings (`~/projects/pulse`, `~/projects/perch`), which run the same
stack. Several of those failures recurred *because* the lesson lived only in
one project's history.

The sibling repos' `infra/` and `functions/` carry the same reasoning inline,
next to the code it constrains — worth reading before writing new Terraform.
