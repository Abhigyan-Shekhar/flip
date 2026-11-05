# FLIP — The Green Check That Lied

Positioning: **Fix your codebase without opening your laptop.**

## Implementation checklist

- [x] Audit existing Discord, Switch, LangGraph, SQLite, Company Brain,
  managed Console, QA/Reviewer, Security and guarded merge paths.
- [x] Verify clean baseline `c26948e4d4205b45d54a2faad86fb486d78576f3`.
- [x] Reproduce the real incomplete-native-output defect before editing the parser.
- [x] Preserve vulnerable and patched references; fix the parser and retain generic formats.
- [x] Add narrowly scoped investigation/follow-up intents and durable Switch handoffs.
- [x] Require real isolated reproduction and exact-revision Company Brain context.
- [x] Carry reproduction, graph evidence and selected tests into an Engineering proposal.
- [x] Add signed Engineering/Release controls, retaining existing independent verification.
- [ ] Complete this story through managed Engineering, PR and human-approved guarded merge live.

The repository's original remote remains `ria-kayy/Discord-server`. The brief's
`Abhigyan-Shekhar/flip` contains the same baseline SHA; native Git verified it
before using that identity for the separate demonstration checkout. Existing
configuration, task databases, volumes and branches were preserved.

## What changed

`security.py` previously validated the Trivy metadata only when `Results` was
absent. With `Results: []`, it accepted incomplete output as a clean scan.
Both paths now require the native schema, artifact identity and Trivy version.
Malformed and ambiguous mixed native/normalized envelopes fail closed. The
separate documented normalized `findings`, `issues` and list formats still work.

`green_check.py` recognizes only the scanner investigation story. An authenticated
request creates one SQLite task with a pinned source revision, requesting user,
room and original thread. PM creates two addressed Switch outbox handoffs:
Security reproduces; Knowledge retrieves Company Brain context. Independent
`AgentRuntime` processes execute their own LangGraphs under distinct credentials.
PM never invokes either specialist executor directly. Replay reuses task/handoff
IDs. Follow-ups require exactly one investigation belonging to the requesting
user in the same room/thread. Configuration failures produce explicit blockers.

Security clones the real vulnerable revision, then runs an operator-owned Python
regression assertion with `DockerIsolation` and `capture_command`. The container
has no network, credentials or host Docker socket; CPU, memory, PIDs, time and
output are bounded. A failing assertion counts as reproduction only when the
actual report says `verified_success` and the specific assertion fails. Other
errors do not count. Receipts retain actual argv, exit, revision, duration,
redacted log artifact and digest. This is a deterministic parser-contract probe,
not a real vulnerability-database scan.

Knowledge reuses the existing `CompanyBrain` MCP adapter and operator CLI, with
a separate clean checkout and registry. It validates root, clean state and index
revision, retrieves the parser/consumer relationship, and requests bounded
byte-checked source citations. The evidence selects the scanner regression tests;
where gate source is grounded, it also selects the gate tests. Missing graph edges
remain unknown. Commands, destinations, identities and authority come only from
local operator policy, never graph prose.

“Fix it” creates an ordinary existing `ApprovalWorkflow` proposal carrying that
real reproduction and graph snapshot. It does not start Engineering. Repeated
fix requests reuse the existing proposal. Phase 1 chat approval messages are
refused: signed `/flip approve-engineering` and `/flip approve-release` controls
require allowlisted authenticated humans, original thread and current task
version. Release additionally binds the full head SHA. Bot/system identities
are rejected. Existing APIs retain their separate human credential boundary.

The existing managed Console Engineering harness, independent exact-SHA QA,
Reviewer, bounded correction loop, Security gate, draft publication, guarded
merge claims and uncertain-response reconciliation are reused. No new agent
framework, direct orchestration fallback or merge-on-success was introduced.
A Security process can handle both investigation and its separately configured
existing `engineering_security_gate` capability.

## Reproduction references

- Vulnerable: `refs/demo/phase1-vulnerable` → `c26948e4d4205b45d54a2faad86fb486d78576f3`.
- Patched: `refs/demo/phase1-patched` → `89c103f` (full SHA: `git rev-parse refs/demo/phase1-patched`).
- Original regression run: `docs/evidence/phase1/vulnerable-pytest.txt`, **3 failed / 6 passed**.
- Focused patched run: `docs/evidence/phase1/patched-pytest.txt`, **34 passed**.

Use a detached worktree for each revision. Copy the regression test into the
vulnerable worktree for testing; do not change production code to recreate the bug.
Run `PYTHONPATH=src uv run --no-sync pytest -q tests/test_phase1_trivy.py tests/test_security.py`
in the prepared development environment. Tests invoke real subprocesses and Git.

All live status and unresolved work are in `PHASE1_ACCEPTANCE.md`. A historical
successful answer-file pipeline is not acceptance of this scanner workflow.

## Recovery status

The [second-Mac recovery report](PHASE1_RECOVERY_2026-11-05.md) records safe QA
failure details, preserved completed-command evidence, bounded temporary storage,
a clean baseline index and prepared Python-equipped official controller image.
Signed `ready-for-review` is implemented and tested. These changes do not complete
the unchecked live story above: the original task remains FAILED, the broad
isolated historical suite has no passing receipt, and no workflow PR, merge or
final narrated video is claimed.
