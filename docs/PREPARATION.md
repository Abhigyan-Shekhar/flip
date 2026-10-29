# Technical preparation — October 29, 2026

Follow-up: [voice dependency probes](../research/voice/README.md) investigate
released py-cord and a pinned upstream receive fix. Docker work is deferred at
the user's request. Podman 5.7.1 is installed, but its `company-brain` VM is
stopped and the container socket is unreachable; it was left unchanged.

## Scope and evidence

The working directory initially contained only an unborn Git repository, with
no files, commits, or configured remote. `git ls-remote` against the supplied
GitHub repository succeeded and returned no refs. Its URL is now the local
`origin`. No commits or pushes were made during this investigation.

The official [hackathon rules](https://www.wemakedevs.org/hackathons/switch)
state November 4, 12:01 AM IST through November 7, 11:59 PM IST, 2026. Existing
agents and libraries are permitted; the Switch setup and workflow must be built
during the event. This preparation does not implement either workflow.

Switch source inspected at commit
`557b62b23002548baa678339a97c8140c1ccaa8b` (core package reports 0.31.0).
Voice-receive extension source inspected at
`ac04ea7b0941112e83767cf1c1469b408fa06748`.
These are research snapshots, not production dependency pins. Recheck on Nov 4.

The Switch documentation root failed through the web reader, but its published
documentation is available in the official source tree. The
[documentation MCP endpoint](https://docs.switchagents.ai/mcp) returned its
discovery description; it is documentation search, not a runtime agent bridge.

## Simplest viable architecture

Use three application process types: FastAPI, independently supervised Python
specialist workers, and a dedicated Python Discord voice/command bot. Switch and
its infrastructure run separately. Codex is managed by Switch Console or its
sidecar. Begin with one worker per specialist identity for clear ownership.

| Component | Owns |
| --- | --- |
| Discord | Conversations, threads, native interactions, voice membership |
| Switch | Rooms, participant identity, messaging, addressing policies, role leases, Discord text bridge |
| FastAPI | Task records, authorization, proposals, approvals, evidence, receipts |
| Specialist workers | One Switch identity and internal LangGraph workflow per role |
| Codex through Switch | Repository implementation in scoped execution environments |
| QA runner | Independently controlled tests on an exact implementation revision |
| Release service | Restricted GitHub operations after backend authorization |
| Application SQLite | Task event log, inbox/outbox, handoffs, approvals, action ledger |
| LangGraph SQLite saver | Recoverable internal execution checkpoints |

**SQLite applies to SwitchCrew. Switch itself uses PostgreSQL.** Do not try to
replace Switch's persistence with our SQLite database. See the official
[Switch internals](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/docs/official/internals/index.md).

Keep cross-specialist communication visible through Switch. There is no master
LangGraph secretly running the workforce: each custom specialist uses its own
graph, keyed by task and assignment, while Switch carries addressed work and
human collaboration. Share narrow task-service interfaces instead of sharing
unrestricted database access or publication credentials.

## Verified Switch interfaces

Evidence: [agent protocol](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/docs/official/internals/agent-protocol.md),
[registration schemas](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/core/switch_core/bridges/agent/api/schemas.py),
and [operation definitions](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/core/switch_core/bridges/agent/operations/definitions.py).

- Agents receive SSE and send HTTP requests. Runtime requests use an agent API
  key; the separate registration token is only for registration.
- Built-in agent types use `POST /agents/register-known`. Custom workers use
  `POST /agents` with `name`, `description`, `connector_type`, and
  `integration_profile`. The profile requires `connection_model`,
  `message_exchange`, pre/post invocation mediation lists, and event reporting.
  An always-running worker should describe itself as `always_on`. Do not invent
  a built-in `langgraph` agent type. Registration returns `id` and a one-time
  `api_key`; protect it outside prompts and logs.
- Open `GET /agents/{agent_id}/events` with a persistent `connection_id`, scope,
  filter, and initial rooms. Read the initial `connection_state`. The default
  heartbeat is every two seconds with a six-second TTL, but honor the server's
  advertised interval. The bridge URL differs from the Gateway URL.
- Discover operation signatures with `GET /agents/{agent_id}/ops`. Invoke
  `POST /agents/{agent_id}/ops/{operation}` with the arguments object directly
  and `X-Switch-Connection-Id` for connection-scoped operations.
- `connect_to_room` obtains context and claims the room. `post_message` accepts
  `body` and optional `thread_id`, with room inferred from the connection.
  `send_targeted_message` accepts `body`, `target_names` and/or `target_roles`,
  and optional `thread_id`. Name targets are participant names, not IDs.
- Roles are room-scoped and may be exclusive or shared. Role membership and
  addressability do not authorize GitHub publication.
- **The task protocol is explicitly documented as not ready for use.** Use
  supported targeted messages for handoffs with an application-owned typed
  envelope: task ID, handoff ID, sender/recipient, expected revision, acceptance
  criteria, and artifact references. Validate sender identity and membership;
  never treat arbitrary message JSON as a trusted backend operation.
- An addressed message being sent does not prove the recipient accepted work.
  Inspect `target_statuses` and wait for an explicit application acknowledgment.
  Set appropriate addressing policies: default owner-only agents refuse work
  from other agents. Link the human Discord account to the Switch owner.
- Reconnect with the same connection ID and last processed sequence. Declare
  rooms when opening the stream. A `gap` requires rereading history. The SSE
  buffer is not durable enough to replace the application event log. Persist
  validated inbox records before acknowledging progress; deduplicate using
  stable message/handoff IDs as well as stream position across server restarts.

The [session runtime documentation](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/docs/official/internals/connectors-and-runtime.md)
explains Codex integration: Console/sidecar launches a session host, supplies the
Switch skill and a loopback MCP server, and a watcher translates tool calls to
HTTP operations and incoming SSE into session input. Use that existing route.
Do not replace Codex with an LLM that emits code strings. Validate actual CLI
execution, worktree selection, interruption, and returned revisions during M4.

The [Discord bridge documentation](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/docs/official/deploy/messaging-apps/discord.md)
describes one bot application and per-channel webhooks for distinct agent names
and avatars. It connects outward; text bridging needs no public inbound server.
Message Content and Server Members intents are required. A normal first channel
message creates the room; a slash command cannot create it as the first event.
Discord DMs are not supported by this bridge.

Our separate bot owns `/crew` commands and voice. Use a separate application
token to avoid two processes competing over one bot connection. It needs only
its relevant channel and voice permissions. Route its confirmed tasks into
Switch, and avoid duplicating Switch's ordinary text relay. Gateway interactions
provide authenticated Discord user/guild data; role authorization is checked by
the backend through a trusted bot-to-service path, never caller-supplied IDs or
plain-text approval. HTTP-delivered interactions would additionally require
Discord signature verification and a reachable endpoint.

## State, approvals, and external action design

Plan Pydantic contracts for TaskContract, TaskEvent, AgentAssignment, Handoff,
ActionProposal, Approval, Evidence, CompletionReceipt, and VoiceSession. Preserve
all specified task states. Enforce transitions centrally with an expected state
version to reject racing/stale writes. Store state updates and events in one
transaction; place outgoing handoffs in an outbox in that same transaction.

Bind each approval to the authenticated approver, authorized role, task,
proposal digest, repository, base/head revisions, exact action parameters, and
expiry. A changed revision invalidates authorization. QA must pass on that same
revision before publication. Resolve current authorization at action time.
Keep GitHub write credentials with the release service, unavailable to general
agents and test processes. Codex should have no publication/merge credentials.

Use unique external action keys and an action ledger, but do not promise
exactly-once GitHub effects from a local uniqueness constraint. A crash after
GitHub accepts a PR but before SQLite records it requires reconciliation by
repository/head/base and a stable task marker. An uncertain outcome must be
reconciled before retrying. Never merge or deploy automatically.

QA records argv/command, cwd, timeout, exit code, relevant output, revision, and
criterion results. Run tests in a separate sandbox/worktree with no write token.
Git worktrees alone are not a security sandbox. Completion requires verified
external artifact links, QA evidence, human authorization, and source request.

## Python voice feasibility

[Discord's voice documentation](https://docs.discord.com/developers/topics/voice-connections)
requires DAVE for calls from March 1, 2026. RTP transport decryption and DAVE
media-frame decryption are distinct layers. UDP reachability is also necessary.

[discord.py's changelog](https://discordpy.readthedocs.io/en/stable/whats_new.html)
records DAVE support in 2.7.0 and additional dependency diagnostics in 2.7.1.
Candidate transmission stack: `discord.py[voice]`, `davey`, PyNaCl, Opus, and
FFmpeg. This is not proof of receive support.

The [voice receive extension](https://github.com/imayhaveborkedit/discord-ext-voice-recv)
labels itself experimental. Its published version is `0.5.2a179`, dated June 18,
2025. At the inspected source revision, `reader.py` decrypts RTP and `opus.py`
passes those bytes directly into the Opus decoder. No DAVE references were found
in its package source. **Inference: this receive path is unsuitable for DAVE
media as inspected, despite importing with discord.py 2.7.1.** A maintained
Python receive implementation with demonstrated DAVE support, or a reviewed
compatibility patch, is required. Do not claim the voice feature is feasible
end-to-end until a real receive test passes; do not bypass encryption or silently
replace VC input with uploaded recordings.

The isolated transport spike must prove: two consenting human participants,
per-speaker decoded PCM, a spoken bot reply, a membership/key transition, a
reconnect, and immediate stop/disconnect without further capture. Then test
Whisper-compatible STT and TTS adapters, bounded queues, bot-audio filtering,
consent withdrawal, and explicit human confirmation before any task submission.
New participants must pause capture until consent is established. Retention and
transcript handling must be explicit. No Discord credentials were supplied or
live call performed in this preparation.

## Dependency and local environment check

Registry versions observed on October 29; these are candidate versions, not a
committed lockfile. Use Python 3.12 initially, matching Switch's minimum.

| Package | Observed version | Assessment |
| --- | --- | --- |
| FastAPI | 0.162.0 | Suitable candidate; isolated request passed |
| Pydantic | 2.14.1 | Structured contracts; installed with candidates |
| LangGraph | 1.2.18 | Graph and checkpoint smoke check passed |
| langgraph-checkpoint-sqlite | 3.1.2 | Separate SQLite saver dependency |
| discord.py | 2.7.1 | DAVE documented; live voice untested |
| davey | 0.1.7 | Installed native binding; live encryption untested |
| discord-ext-voice-recv | 0.5.2a179 | Experimental; DAVE receive gate fails source review |
| Ruff / pytest | 0.17.3 / 9.2.0 | Candidate lint/test tools |

Evidence is the respective `https://pypi.org/pypi/<package>/json` registry
response and the upstream sources linked above. Recent releases alone do not
prove stability. Resolve and lock dependencies on event day after rerunning
checks on the target Docker platform. Use httpx for the narrow HTTP/SSE adapter;
the SSE parser must support chunk boundaries, multiline data, IDs, and comments.

An isolated `/tmp/switchcrew-dependency-check` environment on CPython 3.12.13
resolved and installed all candidates. Checks passed: FastAPI TestClient request;
a trivial LangGraph invocation and SQLite checkpoint read; discord.py,
voice-receive, davey, and PyNaCl imports. This was a disposable dependency probe,
not SwitchCrew implementation or a live integration test. No repository test
suite exists yet.

Local executables found: Python 3.12 via uv, uv, FFmpeg, Codex CLI, and gh.
Docker was not found on PATH. CLI presence does not establish authentication,
Docker daemon availability, or provider access. Switch Console availability was
not inspected. No credentials were printed or requested.

## Risks and remaining gates

| Risk | Priority | Required resolution |
| --- | --- | --- |
| Python DAVE receive compatibility | Critical | Live isolated receive/transmit spike before promising voice |
| Switch protocol churn | High | Pin tested release/source; discover ops; test heartbeat/gap/reconnect |
| Native task protocol unavailable | High | Durable application handoffs over supported targeted messages |
| Docker missing from PATH | High | Install/verify Docker and Compose before event setup |
| Discord/agent identity mapping | High | Owner link, addressing policy, membership and role checks |
| Codex unattended execution/isolation | High | Actual scoped run, interruption, revision evidence, no release secrets |
| Publication crash ambiguity | High | Action ledger and GitHub reconciliation test |
| Seven roles plus voice in four days | High | Complete text slice first; keep optional agents/integrations deferred |

Outstanding live prerequisites: Switch server/Console, test Discord guild and
bot configuration, model access, a disposable GitHub target, release credentials
with narrow permissions, and consenting voice participants. These are event-day
integration prerequisites, not reasons to guess APIs or simulate completion.
