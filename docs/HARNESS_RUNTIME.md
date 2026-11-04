# Flip Harness Runtime v1 — Codex + Antigravity

## What it does

Use the **existing signed-in CLI subscriptions/credits** during a local Flip demo,
without configuring an OpenAI or Google inference API key. One provider can power the
PM planner while another powers Research and other nonprivileged specialists. This is
model reasoning, not keyword routing or a deterministic Python answer generator.

The implementation is **opt-in**. Without `SWITCHCREW_HARNESS_CONFIG`, existing
deterministic capabilities remain backward compatible; nothing silently starts a
paid inference service or changes an existing workflow.

| Boundary | Runtime v1 behavior |
| --- | --- |
| PM | Authenticated local CLI generates structured 1–12 step plans; Python validates roles and dependencies, then stores a plan in SQLite. |
| Research | Existing LangGraph, optional bounded research retrieval, and Company Brain context feed a CLI reasoning step as untrusted evidence. |
| Other specialists | Each existing specialist LangGraph may use `capability.mode: harness`, with the role bound in private operator config. |
| Switch | Existing independent bot identities, original-thread handoffs, retries, and delivery persist unchanged. |
| SQLite / memory | Task truth, plan state, human draft reviews, checkpoints, and optional Waggle memory remain on the existing paths. |
| Privileged actions | Engineering, QA, Reviewer, Release, Voice, and Notes cannot be assigned a generic harness executor. Existing approval, consent, and execution gates apply. |

## 1. Authenticate the CLIs on the demo machine

Install the official `codex` and/or `agy` CLIs in the **same OS user environment that
runs the Flip workers**. Sign in once interactively, using your existing accounts:

```bash
codex login
agy
```

Verify `codex --version` and `agy --version`, and run a small CLI-only prompt before
starting the demo. **Both CLIs use their own account quotas, model availability and
terms**; the harness does not create free unlimited inference or guarantee credits.
The runtime never uses provider SDK/API keys as an inference fallback.

For a first demo, run Flip **natively on that host**. The existing Docker images do
not contain Codex/Antigravity binaries or your CLI login state. Merely setting an
environment variable in Docker Compose does **not** enable this feature there.
Provision a private runner image and authenticated identity explicitly if a later
containerized deployment is needed.

## 2. Create a private operator-owned harness config

Create the JSON file outside the Git checkout, e.g. at an absolute path under your
user's private configuration directory, and set mode `0600` (`chmod 600 FILE`).
The workspace must already exist and should be a separate, **non-sensitive demo
folder**. It is validated as an operator-selected location, but the CLI itself
runs inside a new temporary working directory for each invocation; it does not
receive the configured workspace as its current working directory. Do not put
production credentials, secrets, or unreviewed hooks in the CLI user's reachable
files. Example file contents (replace every absolute path):

```json
{
  "roles": {
    "pm": {
      "provider": "codex",
      "workspace": "/absolute/path/to/flip-demo-workspace",
      "timeout_seconds": 180
    },
    "research": {
      "provider": "antigravity",
      "workspace": "/absolute/path/to/flip-demo-workspace",
      "timeout_seconds": 180
    },
    "documentation": {
      "provider": "codex",
      "workspace": "/absolute/path/to/flip-demo-workspace",
      "timeout_seconds": 180
    }
  }
}
```

Provider choices are per role, and can be swapped without changing graphs. Supported
roles: `pm`, `research`, `community`, `documentation`, `knowledge`, `onboarding`,
`moderation`, `security`, `scheduling`, `analytics`, and `incident`. There is **no**
Kiro adapter in v1. For optional local grounding, add `source_root` (existing source
directory) and `sources` (1–20 explicitly allowed relative files) to a role.
The original source scanner rejects private paths, limits bytes, and redacts likely
secrets. Approved snapshots are supplied in the prompt; the CLI is not started
from the source tree. This snapshot is untrusted context, not a source of new
instructions or verified facts. **Snapshot allowlisting is not a filesystem
security boundary**: the subprocess retains its signed-in OS identity, so use an
account/container with least-privilege filesystem access.

In the worker/API `.env` (also private), set:

```dotenv
SWITCHCREW_HARNESS_CONFIG=/absolute/path/to/private-harness.json
```

Both the PM and Research workers and the API read this exact path at startup.
Existing Switch/Discord identity, token, SQLite and memory settings stay as they
are. Check the config without invoking a paid model:

```bash
uv run python -c 'from switchcrew.settings import Settings; from switchcrew.harnesses import load_harness_config; print(sorted(load_harness_config(Settings().harness_config)))'
```

## 3. Run model-driven PM and Research

Start the existing API, PM worker and Research worker using the normal native host
setup and independent Switch registrations (see [operations](OPERATIONS.md) and
[Switch provisioning](../deploy/switch/README.md)). PM orchestration can be started
via an API task with `kind: "orchestrate"`, or an existing Discord/Switch PM
request. When PM has a configured harness, a request with **no** `kind` is also
treated as orchestration. A Research assignment invokes the configured model
instead of the old deterministic final-processing branch.

The PM can assign only **configured, connected specialists** to a model-driven
plan (plus Engineering when its existing independent gates are all configured).
The model cannot invent an unregistered identity or route directly into a privileged
capability. If you configure only PM and Research, PM can plan Research work, but
it cannot silently invent a Documentation worker.

For other role workers (for example Documentation), retain the existing Switch
registration and add `"capability": {"mode": "harness"}` to that worker's
private capability config. Supply `"settings": {"harness_config":
"/absolute/path/to/private-harness.json"}` in that same private file, or set the
environment variable for the role's process. The role must exist in the operator
harness JSON. Launch the existing `switchcrew.runtime` specialist entry point with
its private `--registry` and `--capability-config` arguments; there is no new bot,
queue, or service to deploy.

## 4. Response shape and failure behavior

An unprivileged specialist returns a validated JSON object with:

```json
{
  "status": "evidence_found",
  "summary": "A short statement of the draft findings",
  "artifact": "The full generated report or draft",
  "evidence": ["An identifiable source reference"],
  "limitations": ["What still needs independent verification"]
}
```

The runtime adds `role`, `provider`, and
`trust: "model_generated_requires_human_review"` before handing the result back
to LangGraph. The result is stored and delivered through existing mechanisms.
Successful model-generated specialist steps remain `awaiting_review` in SQLite;
they do **not** complete their child task or release downstream dependencies until
an authenticated human accepts the exact persisted result. A rejection blocks
the parent plan. This also applies to nonprivileged outputs in model-driven plans.
No generated claim can itself approve code, pass QA, publish a PR, or bypass the
Switch sender/recipient checks. An unavailable CLI, timeout, invalid JSON,
oversized result, or failed schema validation **blocks** the task; it never
silently falls back to a deterministic fabricated result.

The PM result is separately validated: exactly 1–12 sequential steps, explicit
allowed roles, and bounded nonempty criteria. Only then is the plan committed to
SQLite. A failed model plan leaves the task in `DRAFT` for a safe retry.

### Human review of model-generated plan results

Configure an independent human approval credential (for example,
`SWITCHCREW_APPROVAL_CREDENTIALS='{"operator":"<long-private-token>"}'`).
Inspect the pending plan with `GET /tasks/{parent_task_id}/plan` using the API
service token, and inspect the full draft and its provenance before deciding.
Retrieve the parent's latest `version` from `GET /tasks/{parent_task_id}`. Then
send this request using the **human approval Bearer token**, not the API token:

```http
POST /tasks/{parent_task_id}/plan/reviews
Authorization: Bearer <human-approval-token>
Content-Type: application/json

{"step_id":"step-1","approved":true,"expected_version":2}
```

The version `2` is only illustrative; use the current version. Rejected and stale
decisions fail closed, and decisions are recorded in task events with the human
identity and a hash of the reviewed result. There is no automatic approval.

## Security and demo limitations

- **CLI processes can use the user's own login/session.** Workers pass a small
  allowlisted environment rather than Discord, approval, or service credentials.
  Codex runs `exec --sandbox read-only` with user config ignored and ephemeral
  sessions (prompt over stdin); Antigravity runs with `--mode=plan`, disabled
  slash commands, and `--sandbox` terminal restrictions. Both run from newly
  created disposable work directories, never the configured source checkout.
  Antigravity's terminal sandbox **is not a read-only file permission policy**,
  and plan mode is not an OS security boundary. A process can still access files
  its OS identity permits (including CLI credentials). Use a separate, tightly
  restricted demo OS account/container; do not rely on source filtering or prompt
  instructions for confidentiality or mutation safety.
- **Prompts leave the machine for provider inference.** The graph, plan store, and
  handoffs remain local/Switch as before, but Codex and Antigravity are hosted
  inference paths. Do not send secrets, confidential customer data, or sensitive
  memory into those prompts.
- **Antigravity receives its prompt on a command-line argument** for the currently
  documented `agy --print` mode; other local users with process-inspection rights
  may be able to observe it. Codex reads its prompt through stdin.
- **Tool policy may differ by installed CLI and host.** The temporary directory
  prevents edits to the configured checkout through relative workspace paths,
  but only OS permissions can reliably restrict arbitrary file reads/writes.
  The generated artifact is a draft and may hallucinate evidence.
- **Not yet live-accepted against your accounts.** Unit tests use fake CLIs to
  verify the contract without spending quota. The end-to-end Switch demo still
  needs a signed-in host, running workers, configured identities, and live smoke
  acceptance; quota exhaustion may also block a request.

## Verification

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests scripts
```

For an account-backed smoke test, first send a benign PM task and check that a
persisted plan has `planner: "harness_structured_v1"`; then check Research output
includes the configured `provider` and `trust` fields and an honest limitation.
Never interpret a fake-CLI test as proof that provider login or Switch delivery
worked on the demo machine.

