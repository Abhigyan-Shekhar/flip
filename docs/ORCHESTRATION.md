# Bounded autonomous PM orchestration

PM now decomposes an explicit request into a durable dependent plan and dispatches
its steps to independent Switch workers. The planner is **deterministic criterion
routing**, not an LLM or a claim of general semantic planning. It selects roles
from inspectable keyword rules over the supplied criteria. Unknown wording falls
back to configured Research, rather than granting a privileged capability.

A request supports 1–12 nonblank criteria and an objective up to 10,000 characters.
Each criterion becomes a step. Steps run sequentially: a later step starts only
after its predecessor completes. Evidence is forwarded as bounded, explicitly
untrusted text; it never carries authority. A blocked/failed step blocks the
parent. Completing a plan means the configured analyses/drafts completed, not
that a human or independent QA approved every acceptance criterion.

## Public routing configuration

PM already knows Research and optionally Engineering from existing settings.
Configure additional targets in both PM and API:

```dotenv
SWITCHCREW_SPECIALIST_TARGETS={"documentation":{"agent_id":"DOCS_REGISTRATION_ID","name":"documentation-agent"},"security":{"agent_id":"SECURITY_REGISTRATION_ID","name":"security-agent"}}
SWITCHCREW_ORCHESTRATION_JOB_KEY=OPERATOR_OWNED_ENGINEERING_TEMPLATE
```

Names are exact Switch machine names; IDs are registration IDs, not transport
participant IDs. The specialist registry permits PM's actual participant ID and
maps it to PM's registration ID. Its target_names permits replies to PM. Source
paths, snapshot files, model/provider settings and commands belong in protected
operator configuration, not in request JSON or memory.

Configure the specialist executor using [SPECIALISTS.md](SPECIALISTS.md). Example
private generic-runtime configuration (chmod 600):

```json
{
  "role": "documentation",
  "capability": {
    "root": "/private/crew/public-snapshots",
    "sources": ["gateway-notes.md", "support-guide.md"]
  },
  "memory": {
    "memory_enabled": false,
    "checkpoint_path": "/private/crew/checkpoints.sqlite3"
  }
}
```

Launch an independent process with its private registry and the same application
DB as PM:

```sh
uv run python -m switchcrew.runtime documentation \
  --registry /private/crew/documentation-registry.json \
  --database /private/crew/application.sqlite3 \
  --url http://127.0.0.1:8080 \
  --capability-config /private/crew/documentation-config.json
```

If this host needs a noneditable install, refresh it after source changes with
`uv sync --frozen --no-editable --reinstall-package switchcrew`.

Registry and capability files must be private. With capability config, the CLI
skips the root `.env` file; optional memory/checkpoint settings come from its
`memory` section. Without explicit capability config, default specialist graphs
remain blocked. The generic CLI refuses to substitute a capability file for the
approval-gated Engineering/QA/Release entry point. Voice has its own consent/job
schema and separate execution ledger; see [VOICE.md](VOICE.md).

## Requests and inspection

In a verified Discord thread, send:

```json
{
  "kind": "orchestrate",
  "question": "Prepare a source-backed reconnect guide",
  "acceptance_criteria": [
    "Find reconnect evidence from configured official sources",
    "Write documentation covering reconnect behavior"
  ]
}
```

The API equivalent uses `POST /tasks` with `kind: "orchestrate"`, `objective`
and `acceptance_criteria`, using the existing service bearer credential. Include
original room/thread source context if you want replies in that thread.
`GET /tasks/{id}/plan` returns the stored planner type, steps, dependencies,
child task/handoff identities, state and results. `GET /tasks/{id}/events`
contains dispatch events. API transitions cannot mark the plan successful.

Plans and children use stable IDs. Replaying a request or restarting PM reuses
the same plan, existing children and handoffs. The plan snapshots its operator
route identities and Engineering template/destination so a later config reload
cannot silently redirect existing undispatched steps.

## Engineering and cancellation

An Engineering step creates an **AWAITING_APPROVAL child task**, not an executable
handoff. PM announces the child task ID and version in the original thread.
A real authorized human must approve that child separately. Its implementation,
independent QA and release approval use the existing [approval workflow](APPROVAL_RELEASE.md).
The step completes only after that child's actual PR receipt exists. A planner,
source excerpt, parent approval or remembered decision cannot approve a child.

Cancellation blocks new dispatch and new child approvals. Runtime/executor checks
also reject inactive parents, including the legacy Research worker and each new
QA command. A command or external request already in flight cannot be recalled
by a SQLite state change. If a child has transactionally entered `PUBLISHING`,
parent cancellation is refused until publication is reconciled, preserving the
same in-flight-publication boundary as direct Engineering tasks.

## Validation limits

Controlled tests cover dynamic role selection, dependency ordering, restart
without duplicate handoffs, source evidence forwarding, missing specialists,
failed dependencies, parent cancellation, pending child approval prompts and
API lifecycle restrictions. A generic runtime test runs actual local document
analysis through a controlled Switch transport. These are not evidence that all
specialists are deployed in the verification Discord server.

A live acceptance run must provision the new identities/configurations and show
one Research → Documentation plan, one Engineering child stopping for both human
approval stages, a failed dependency and a restart with unchanged child counts.
