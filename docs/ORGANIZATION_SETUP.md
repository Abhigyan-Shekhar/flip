# Organization workflow setup

This extends the existing SwitchCrew deployment. Use the same shared application
SQLite database and independent Switch identities. Keep operator configuration
outside the repository with mode `0600`. No channels, bots, registrations, issues,
PRs or deployments are created by these setup examples.

## Human identities and permissions

`SWITCHCREW_HUMAN_CREDENTIALS` is a JSON mapping from participant/employee ID to a
unique random API credential (at least 32 characters). It authenticates meeting
consent, scoped knowledge access and employee assignment requests. It grants no
Engineering or Release approval privilege. Keep authorized privileged humans in
`SWITCHCREW_APPROVAL_CREDENTIALS` only. Each identity belongs in one map, and all
credentials must be independent from service/provider credentials. Scope approval
rights further through the private knowledge actor map and employee assigner list.

The existing service API token remains the administrator credential for task and
trace APIs. Human-specific routes use human identity credentials. Discord identity
comes from existing operator-verified bridge bindings; JSON fields never identify
a caller or grant permissions.

## Private organization configuration

Set `SWITCHCREW_ORGANIZATION_CONFIG=/private/switchcrew/organization.json`. All
features are optional; omitted sections remain disabled. A minimal meeting config:

```json
{
  "meetings": {
    "input_root": "/private/switchcrew/recordings",
    "notes_agent_id": "NOTES_SWITCH_REGISTRATION_ID",
    "voice_agent_id": "VOICE_SWITCH_REGISTRATION_ID",
    "context_actor_id": "AUTHORIZED_NOTES_ACTOR",
    "origins": {
      "meeting-1": {
        "owner_id": "ORGANIZER_USER_ID",
        "room_id": "SWITCH_ROOM_ID",
        "channel_id": "DISCORD_CHANNEL_ID",
        "thread_id": "VERIFIED_SWITCH_THREAD_ID",
        "message_id": "VERIFIED_SWITCH_ROOT_MESSAGE_ID",
        "guild_id": "DISCORD_GUILD_ID"
      }
    },
    "recording_manifest": {
      "meeting.wav": {
        "meeting_id": "meeting-1",
        "owner_id": "ORGANIZER_USER_ID",
        "participants": ["ORGANIZER_USER_ID", "SECOND_PARTICIPANT_USER_ID"]
      }
    },
    "provider": {"api_key_env": "ELEVENLABS_API_KEY"},
    "max_seconds": 3600,
    "max_audio_bytes": 104857600
  }
}
```

HTTP meeting creation requires an operator-registered origin with its permitted
organizer, room, channel, thread and root message. Caller-supplied destination
fields must match; HTTP participants cannot redirect private notes to a different
thread. Discord meeting commands instead derive origin from authenticated Switch
bridge events. Only the separate Voice process needs `ELEVENLABS_API_KEY`. API and PM build
provider-free meeting services. Put uploaded WAV files inside `input_root`; they
must meet the PCM/duration/size contract. Production recording always requires
an operator-owned manifest entry binding the exact relative file, meeting,
owner and participant roster. A participant cannot select another meeting's WAV
from a shared upload root. Missing/mismatched allocations block before audio
processing. Configuring a manifest never grants participant consent: every listed
person must still grant current explicit consent. Configure PM, Research, Notes and Voice
IDs plus their exact Switch target names in the existing private registries and
`SWITCHCREW_SPECIALIST_TARGETS`. The optional `context_actor_id` must have access in the private knowledge actor
map; it enables approved lesson context for Notes. Durable task facts stay
restricted to the meeting's room/channel/project, and Company Brain evidence
remains untrusted with coverage warnings. When bridge participant IDs differ from agent
registration IDs, set `participant_id` on each specialist target. Each specialist
registry maps only trusted sender participant IDs and target names.

For Notes/Voice generic runtime capability files, add these fields to the existing
private role/registry configuration:

```json
{
  "role": "notes",
  "settings": {
    "organization_config": "/private/switchcrew/organization.json",
    "pm_agent_id": "PM_ID",
    "research_agent_id": "RESEARCH_ID",
    "specialist_targets": {"notes": {"agent_id": "NOTES_ID", "name": "notes-agent"}}
  },
  "capability": {"mode": "meeting_notes"}
}
```

The Voice file uses `role: voice` and `mode: meeting_transcription`. It loads the
same organization configuration, the configured provider and the same application
database. These meeting modes use application recording claims; the older
single-file Voice mode retains its separate execution ledger. Run each process
with its own registry:

```sh
PYTHONPATH=src .venv/bin/python -m switchcrew.runtime notes \
  --registry /private/notes-registry.json \
  --capability-config /private/notes-capability.json
```

Use the existing CLI's `--database` and `--url` options when needed. Container
paths must exist inside the process; Compose passes config paths but does not
mount employee workspaces, recordings or secrets automatically. Add explicit
read-only configuration mounts and narrowly scoped data mounts in a private
operator override. See [Meetings](MEETINGS.md).

## Knowledge, learning and employees

Add `knowledge: {import_roots: [...], trusted_actors: {...}}` to the private
organization file. Actor profiles contain `projects`, `teams`, `orgs`, and
scope-specific approver lists. See [Knowledge and learning](KNOWLEDGE_LEARNING.md)
for the exact map and APIs. Personal/team/org content is enforced locally through
SQLite access checks; it is not projected into Waggle's shared project scope.
Only explicitly approved project lessons can be projected through the configured
Waggle adapter. Imported files never become executable instructions.

For Engineering retrieval, the private `engineering_qa` config may include
`learning: {actor_id: "ENGINEERING_ACTOR", trusted_actors: {...}}`. This actor must
have the consuming task's project permission. The runtime snapshots retrieved
lessons for the assignment, includes labeled guidance in its Console prompt, and
accepts optional `used_lesson_ids` from the actual implementation receipt. A use
receipt is verified only after the consuming task has fresh QA/Reviewer evidence
and the referenced regression command passes. An agent's use claim alone is not
proof of causal learning. The two-task local experiment demonstrates behavioral
reuse with controlled transport explicitly identified as such.

Add `employees: {registrations_file: "/private/employees.json",
allowed_assigners: ["AUTHORIZED_HUMAN"]}` for private employee registrations.
Assign before Engineering approval with `POST /tasks/{id}/employee` and an
expected task version. Assignment changes increment the version, invalidating
stale approvals. Each worker and Console provider needs a separate Switch ID.
Configure each employee runtime's private `employee` section as described in
[Employee agents](EMPLOYEE_AGENTS.md). Register the employee worker's exact target
name in PM specialist targets so PM can deliver its addressed handoff. Separate
OS accounts/process filesystem restrictions remain necessary for actual host-level
privacy; an application workspace allowlist is not an OS sandbox.

## Documentation and independent security

Use a separate Documentation runtime with `capability.mode: change_intelligence`
and `allowed_repo_roots: ["/private/qa-checkouts"]`. After final QA/Reviewer evidence,
PM creates a documentation assignment. The executor reads exact before/after Git
revisions and actual workflow receipts, produces drafts, and includes Company
Brain impact evidence only when repository/revision checks match. Access the
completed report at `GET /tasks/{engineering-id}/change-report` using the requester
or an authorized approval identity. See [Change intelligence](CHANGE_INTELLIGENCE.md).

For an optional mandatory security policy, add:

```json
{
  "security_gate": {
    "security_id": "INDEPENDENT_SECURITY_ID",
    "policy": {
      "block_at_or_above": "high",
      "allowed_repo_roots": ["/private/engineering-checkouts"]
    }
  }
}
```

The policy persists by project and cannot be weakened by removing environment
configuration. Release evidence requires a report for the current Engineering
assignment/SHA, successful configured scanners, and compliance with the severity
threshold. Missing scans and missing gate records block a configured project.
Configure the Security runtime with `mode: engineering_security_gate` and operator
scanner argv entries. Scanner processes receive isolated revision snapshots and a
restricted environment. They are not an OS/network sandbox. Scanner JSON formats
and explicit unavailable outcomes are documented in [Triggered checks](TRIGGERED_CHECKS.md).

## GitHub and OSS mode

Set private `github_triggers` fields `repository`, `qa_recipient`,
`security_recipient`, `secret_env`, and `source`. `source` contains operator-owned
`switch_room_id`, `discord_channel_id`, `discord_thread_id`, and
`original_message_id`. Store the webhook secret in the configured environment
variable (minimum 32 bytes), never in task state. Configure GitHub's signed push/PR
webhook to `POST /integrations/github/webhook` only after operator authorization.
Run independent QA/Security workers using modes `triggered_qa` and
`triggered_security`, a fixed local `repo_root`, exact repository slug, and
operator-owned commands/scanners. No repository fetch occurs automatically; the
operator must make the exact commit available locally.

For OSS issues use the separate `/integrations/github/issues` endpoint and private
`oss` section: `repositories` maps authorized slugs to absolute local `repo_root`,
`base`, and stable `project_id`; `source` supplies the canonical room/thread;
`secret_env` and optional `self_bot_logins` control intake. The same PM/Research/
Engineering/QA/Reviewer/Release core handles these tasks. Duplicate suggestions
remain uncertain and no external issue mutation occurs. See [OSS mode](OSS_MAINTAINER.md).

## Discord task interface and permissions

Existing text commands remain. Added commands are `/crew create objective |
criterion`, `/crew tasks [active|blocked|completed|all|employee ID]`, `/crew mine`,
`/crew completed`, `/crew task UUID`, `/crew dependencies UUID`, `/crew evidence
UUID`, and `/crew trace UUID`. These are text commands, not registered application
commands. Lists are channel-scoped; individual detail/cancel/trace views retain
the original room/thread boundary. `/crew mine` includes tasks requested by the
caller and assignments mapped through the verified employee-to-actor directory.

Meeting control also accepts verified Discord JSON messages with `type: meeting`,
`meeting_id`, and `operation`: create (participants), consent (expiry and explicit
disclosure acknowledgment), revoke, cancel, notes, or confirm (completed Notes
handoff ID and edited action list). Uploaded recording queueing is through the
human-authenticated API; Voice/Notes processing and subsequent assignments travel
through Switch. The original meeting thread remains the canonical reference.

The existing bridge needs View Channel, Send Messages, Read Message History, and
permission to send in the selected threads. Message Content intent must be enabled
for text intake where required. Creating new Discord channels is an operator task.
Live voice would additionally require View Channel/Connect/Speak and applicable
voice-state events; no live receive or permission setup is claimed by this change.

## Watchdog and traces

Run `python -m switchcrew.watchdog --config /private/watchdog.json` independently.
The private file identifies the application database, monitored agents, timeout
bounds and optional execution ledger. Existing runtimes now write heartbeat
records; the watchdog reports persisted stalls/uncertain executions without
repeating external effects. `GET /watchdog` requires a human listed in private
`watchdog.readers`. The independent CLI and API must use compatible policies.

Task traces are available through `/crew trace UUID` and authenticated
`GET /tasks/{id}/trace`. They include lineage, assignments, source references,
revisions, checks, decisions and recorded outcomes while omitting raw transcript,
memory, command output and diffs. Switch result envelopes carry hashes and stable
handoff references; full evidence stays in the access-controlled durable records.


Store SQLite, recordings, imported notes, checkpoints and execution ledgers on
appropriately restricted encrypted OS volumes (for example, organization-managed
FileVault or LUKS volumes). These existing paths are configurable. This code does
not introduce custom cryptography, SQLCipher, or a claim of per-record encryption.
