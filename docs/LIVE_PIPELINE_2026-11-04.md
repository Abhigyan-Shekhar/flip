# Live pipeline acceptance preparation — November 4, 2026 IST

**Historical preparation checkpoint: incomplete at the time recorded.**
The subsequent [November 5 live acceptance](LIVE_PIPELINE_2026-11-05.md) resolved
the Console, execution, approval and draft-PR blockers below.
This follow-up started from fetched `origin/master`
`5bd88bdc803cc5d30d361923b3dfce62e93bfe65`. The existing workforce,
infrastructure and workflow implementations predate this run. This run provisions
test identities, tests deployed boundaries and fixes scanner interoperability;
it does not reclassify earlier work as hackathon-built.

## Actual deployment work

- Restarted the existing Colima `flip` VM, PostgreSQL, pinned Switch server and
  gateway. Switch readiness returned healthy. Restored API, PM and Research host
  processes without deleting their state. Retained the documented non-editable
  Python installation.
- Registered four distinct real Switch workers, joined the existing verified
  Discord-associated room, and established protocol-6 connections. Authenticated
  `list_participants` confirmed each registration's participant ID. Credentials
  are separate private files outside Git. These are specialist workers, **not
  Console-managed Codex agents**.
- Installed room-scoped addressing rules for required inbound peers. Extended
  PM's existing policy with a separate specialist rule, preserving prior rules.
- Prepared private per-role registries and immutable `fix-answer` job templates.
  Started separate QA and Reviewer processes sharing application state and a
  separate execution ledger. Engineering remains stopped with no invented
  Console identity; Release remains stopped without a scoped credential.
- Configured PM/API with specialist identities and the disposable destination.
  Static preflight now reports `ready_for_live_checks`, not live acceptance.

| Role | Real Switch registration |
| --- | --- |
| Engineering | `2e847c37-8dae-4b4e-bbed-a99d7ca126de` |
| QA | `f9812933-ee44-4c43-9605-b97d0ff86310` |
| Reviewer | `78fb4961-dd93-439a-8beb-6e581e9fcd87` |
| Release | `5443ac94-bb3c-454a-87ee-248603f66727` |

Room: `1d8c9f05-5e7e-41b9-aead-bfd1b10b927d`.

Private runtime directory: `~/.local/share/flip-acceptance/2026-11-04/`.
It contains protected registrations/configurations, preparation scripts, logs
and component evidence. Engineering configuration deliberately lacks the three
managed-agent identity fields; Release configuration deliberately lacks a token.
Neither is ready to execute. QA/Reviewer commands inspect only this small
answer-file fixture; they do not establish general code-review coverage.

## Real GitHub setup

Created the private disposable repository
[flip-acceptance-20261104](https://github.com/Abhigyan-Shekhar/flip-acceptance-20261104).
Pushed only the **operator-prepared baseline**, commit
`1ca12f82a6c8c4df8f1f6e79467643101cceac95`, to `master`. It contains the
intentionally broken `answer.txt` and explanatory README. This is setup,
not an Engineering implementation or a human-approved release.

GitHub accepted and returned branch protection requiring `flip/qa` and
`flip/security` checks, an approving review, dismissal of stale reviews and
administrator enforcement. Force pushes and deletion are disabled. This run
does not configure check publishers or prove the rules using a final PR.

The existing `gh` OAuth login has broad `repo`/`workflow` access. It was used for
repository setup, not copied into Release or Engineering. No repository-scoped
Release token or GitHub App credential is configured. No draft PR was created.

## Real Security scanner and isolation components

Pulled the official Trivy image and ran it with the production `SecurityRunner`
and `DockerIsolation`, without network or credentials:

`aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`

The binary reports `Version: 0.76.1`. The configured scanner is `trivy fs
--scanners secret --format json --cache-dir /tmp/trivy --quiet /workspace`.
This tests **secret scanning only**, not vulnerability database coverage.
Image digest pinning and the version output were checked; independent publisher
signature verification remains open. See the upstream
[secret-scanner contract](https://github.com/aquasecurity/trivy/blob/main/docs/guide/scanner/secret.md).

Two integration defects were observed and fixed in `src/switchcrew/security.py`:

1. Native Trivy findings use `Severity`, and file paths are on the enclosing
   `Results[].Target`. The adapter rejected a genuine finding and lost its path.
   Normalization now preserves severity, file, rule and title while redacting
   matched credential-shaped text. Malformed nested results fail closed.
2. A clean native Trivy filesystem report omits `Results` (and can omit
   `Metadata`). Such reports were rejected. The adapter now accepts the native
   schema/version/artifact envelope without Results. Arbitrary empty JSON still
   cannot pass.

The first failures were `scanner_failed`, never false clean verdicts. After the
fixes, the actual container detected one Critical finding in `intentional.txt`,
then returned `verified_success` for a later clean commit. The marker was random,
nonfunctional, never authenticated, and never pushed. Reports redact its value.
That Critical finding exceeds the intended High-or-above policy threshold.
The deployed independent Security identity, persisted policy and release gate
are not configured or verified by this component check.

Separate real container probes passed read-only root/workspace enforcement,
network denial, credential exclusion, Docker-socket exclusion, host-home
exclusion, writable temporary storage and forced cleanup following timeout.
No `switchcrew-*` container remained. The daemon runs in Colima's VM; these
checks do not prove isolation of Console's host/provider process.

## Approval denial and restart component

Created API denial-probe task `ff35ca0c-d58f-46b2-9c87-d897ad8df426` using the
service credential. It remained `AWAITING_APPROVAL`, version 1. Attempts to
approve Engineering and Release with that service credential returned HTTP 403.
There was no Engineering approval or handoff. Cancelled the probe through the
ordinary cancellation API, then restarted API/PM/Research. It remained
`CANCELLED`, version 2, with exactly one task row and zero handoffs.

This proves those deployed denial/cancellation/restart cases only. It does not
prove human rejection, failed managed-job recovery, uncertain Console creation,
Switch handoff replay or GitHub publication reconciliation in this pipeline.
No human credential was used to approve or reject a task.

## Real transport recovery failure and fix

The first rerun of the three opt-in recovery probes against the real Switch
server returned **2 passed, 1 failed** in 41.04 seconds. The displaced client
opened its SSE endpoint twice. The existing EOF guard did not cover a stream
ending with `httpx.TransportError`, which is how the real server terminated it.

`src/switchcrew/switch.py` now checks the old connection generation before
opening another stream after a transport failure. A `taken_over` response stops
the loser. A transient ownership-probe failure keeps the gate closed while
retrying; `no_stream` permits a generation-fenced reconnect. Added regressions
cover truncated transport and an unavailable first ownership probe. Recovery
rerun results are recorded in the component evidence index. This is transport
ownership evidence, not proof of exactly-once Engineering or PR publication.

After the fix, **all 3 real recovery probes passed** in 30.93 seconds. The final
ordinary suite passed **564 tests, 3 skipped** in 53.97 seconds. Those three
ordinary-run skips are the opt-in recovery probes run separately above. Ruff
lint, formatting and `git diff --check` passed. Updated code was installed with
the non-editable package procedure and API/PM/Research/QA/Reviewer were reloaded;
API health and the cancelled denial-probe state were checked afterward.

## Remaining blockers and next acceptance

1. Console control API returns HTTP 200 with `agents: []`. First-run onboarding
   was reached and an existing-server connection was attempted. Native UI actions
   failed repeatedly with `noWindowsAvailable` / `elementHasNoFrame`; no server
   attachment or agent creation was confirmed. The control API supports listing
   agents and creating sessions, not onboarding agents. No direct Console DB
   writes or local-CLI substitute was used.
2. No dedicated isolated Console execution host has been established. The local
   adapter requires Console's location to match the assignment checkout and
   rejects SSH locations. QA container flags do not isolate local managed Codex.
3. No scoped Release credential is available. The existing OAuth token cannot
   mint a fine-grained PAT through the GitHub REST API. Do not grant the generated
   Coding agent the broad setup credential.
4. Need the actual human Discord coding request and separate authenticated
   Engineering approval, followed by exact-SHA QA failure/correction/rerun,
   independent review, human Release approval and a real draft PR. Provision the
   per-attempt Console location/agent after the approved handoff exists, before
   starting Engineering. Capture handoff/session/test/review/PR receipts.
5. Need real rejected-human-approval, failed-job and publication-restart cases
   with execution counts. Controlled regressions are not substitutes.

Recorded audio/provider consent, live two-task Waggle learning, exact-revision
Company Brain indexing and operational follow-up remain unrun in this follow-up.
Company Brain's connected registry lists other repositories but not Flip; no
other repository's graph was used as Flip evidence. Conversational Waggle recall
was used, with stale deployment memories checked against current runtime state.

Test totals and sanitized component evidence are recorded in the accompanying
`docs/evidence/live-pipeline-2026-11-04.json`. No complete acceptance manifest is
claimed.
