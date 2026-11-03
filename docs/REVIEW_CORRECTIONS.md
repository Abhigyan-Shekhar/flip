# Independent review and bounded correction

Implemented November 3, 2026 IST at the user's explicit instruction to start
immediately. This predates the November 4–7 event and must not be described as
event-built. Existing implementation history is preserved.

The approved pipeline is PM → Engineering → QA → Reviewer → human Release
approval → Release. Each specialist has its own Switch registration, secret,
process, and durable assignment. The coordinator schedules handoffs through the
existing inbox/outbox; it does not invoke another specialist's graph.

## Required configuration

Add `SWITCHCREW_REVIEWER_AGENT_ID` to PM/API and optionally set
`SWITCHCREW_MAX_CORRECTIONS` (0–5, default 2). All five PM/Engineering/QA/Reviewer/
Release identities must differ. Existing workflows without a reviewer cannot
publish; create a new complete request rather than editing approval records.

Start Reviewer with its own private registry using the existing privileged entry
point:

```sh
uv run python -m switchcrew.engineering_qa --config /private/crew/reviewer.json
```

Use the same application database, separate execution ledger, room and Switch
URL as the other workers. Reviewer needs its own `checkout_root` and
`job_templates` using the same job key. Each job requires `commands` (argv arrays)
and optional `timeout`. Commands run in fresh detached clones of the exact
implementation SHA, with a minimal environment and separate HOME. They must
perform a meaningful independent inspection, not print an unconditional verdict.
Keep review programs and policy outside the implementation author's control.

For example, an operator-owned review program can accept the verified commit
arguments:

```json
{"commands": [["/absolute/python", "/private/crew/review_patch.py", "{base_revision}", "{revision}"]], "timeout": 120}
```

The program must inspect the actual changes and emit a JSON object on stdout:
`accepted` (boolean) and `findings` (list of changed relative file paths, positive
line numbers, messages, and optional severity). An acceptance requires an empty
findings list. Rejection requires actionable findings. Nonzero/timeout/mutation
results cannot accept work. Malformed output blocks configuration. No model or
command is silently substituted when the review capability is missing.

The reviewer verifies the actual cumulative diff digest against Engineering's
persisted artifact. QA and review evidence bind the current Engineering
assignment and exact commit; old results cannot authorize a corrected revision.
A review verdict represents only the configured review policy's coverage.

## Switch addressing

Engineering permits PM, QA and Reviewer senders; QA permits Engineering; Reviewer
permits QA. Their outbound target names must include their handoff peers:
Engineering → QA/PM, QA → Engineering/Reviewer, Reviewer → Engineering/QA.
Each mapping uses actual transport participant IDs and registration IDs; names
are exact Switch machine names. The Release worker continues to permit PM.
Never reuse credentials between identities.

## Correction and recovery

A genuine QA failure or Reviewer rejection queues a new immutable Engineering
assignment from the rejecting specialist. Its prompt includes bounded, untrusted
findings; the original objective, acceptance criteria, and human Engineering
approval remain in force. Corrective work cannot authorize publication.

Each attempt starts in a new scoped checkout at the previous implementation's
exact commit. Its final diff is measured from the original task base so earlier
changes remain subject to review. Use the private Engineering `console_agents`
map keyed by `assignment_id(new_handoff_id)` to supply a dedicated Console agent
identity for that attempt. Each Console location must match its actual new
assignment checkout. Missing/mismatched onboarding blocks execution; it does not
fall back to a local CLI or another agent's checkout.

Attempts and rejection evidence persist in `retry_history`. Exhausted correction
budgets and repeated failures escalate. Replayed events and coordinator restarts
reuse the same assignment. Cancellation prevents new execution and dispatch;
already-running external requests cannot be recalled. Review command execution
claims are persisted before launch; an interrupted uncertain command is not
blindly repeated. An operator must reconcile it.

Human Release approval is still required after current QA and Reviewer acceptance.
Receipts include task/objective, assigned identities, full changed-file list,
exact commit/test/review evidence, approvals, correction history, draft PR URL,
and remaining risks. There is no merge or production deployment operation.

## Verification boundaries

Tests use real temporary Git repositories, commits, isolated checkouts and
subprocess review/test programs. Console, Switch transport and GitHub doubles
are explicitly controlled fixtures; their results are not live acceptance.

For live acceptance, provision the independent Reviewer identity and meaningful
review program, prepare Console agents for each assignment, and use the existing
separate Engineering and Release human approvals. Record actual Switch message,
task/handoff/session IDs, original/corrected SHAs, QA command evidence, review
digest/verdict, and fetched draft PR head. Demonstrate both QA and Reviewer
rejections, retry exhaustion, cancellation and restart without duplicate effects.
Do not use a missing provider or credential as a reason to fabricate success.
