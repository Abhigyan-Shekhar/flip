# Autonomous incident intake and teams

Implemented November 3, 2026 IST at the user's explicit instruction to start
before the event. This work is not represented as November 4–7 implementation.

## Enable only permitted channels

PM must already have verified Discord bindings, guild/user allowlists and room
access. Add `SWITCHCREW_INCIDENT_CHANNEL_IDS` as a JSON list of the bound support
channel IDs. The default empty list disables proactive incident detection. Set
`SWITCHCREW_INCIDENT_MAX_EVENTS_PER_MINUTE` to bound admissions per channel and
human. Duplicate source events do not consume another admission.

This feature observes only events delivered to PM's configured Switch room and
accepted by the existing identity checks. It does not discover or join channels.
The permission check precedes deterministic incident classification. Reports,
repository text and evidence remain untrusted data and cannot grant approvals.

Unstructured reports such as “Payments are failing for some users” are classified
using inspectable phrase rules. SEV1/SEV2/SEV3 are preliminary labels, not verified
production impact. Chatter and rejected/budget-limited messages in opted-in
channels do not create tasks. JSON requests continue to use the explicit existing
workflow. No LLM call or token-spend claim is hidden behind classification.

Incident records persist the actual trigger, source IDs, preliminary severity,
unconfirmed hypothesis, synthetic-fixture label and correlated task. Similar
normalized reports deduplicate within the same guild/channel/room and configured
time window. Replies in the incident's original thread attach evidence from
multiple permitted humans. Cancellation and terminal-task checks prevent new
reports from silently restarting canceled execution. Deduplication uses bounded
rules rather than a claim of general semantic understanding.

## Teams and durable work

The deterministic capability registry chooses configured triage/evidence roles,
Security when indicated, Engineering when remediation is required, and available
Documentation for subsequent artifacts. The Engineering team includes independent
QA, Reviewer and Release, preserving both human gates. Missing required roles or
route configuration block with an explanation; missing capability never becomes
fabricated successful work.

A persisted plan records responsibilities, deliverables, selection reasons,
dependencies and identity snapshots. Independent investigation handoffs can be
ready together. Engineering waits for required evidence and creates an
AWAITING_APPROVAL child task. Actual specialist processes execute their own
LangGraphs after receiving addressed Switch handoffs. No coordinator runs their
capabilities directly, and no OS process/credential is provisioned automatically.
Completed assignments become idle; persistent specialist services remain running.

Trusted coordinator code can amend an active plan through `update_team`, with a
recorded reason. Only configured read-only roles may be added this way. Started
identities are immutable; downstream dependencies and active Engineering work
cannot be discarded. Plans are limited to twelve steps. Amendments do not confer
Engineering or publication permission.

Configured snapshot specialists produce cited analyses and draft documentation.
Their completion is not proof of a production diagnosis or of every human
acceptance criterion. Use genuinely available sources; label synthetic fixtures
and missing telemetry explicitly. Company Brain evidence and verified-lesson
promotion remain separate work, not simulated integrations in this feature.

## Inspect and cancel

Use the [crew text commands](CREW_COMMANDS.md) in the original task thread.
Status/team/why/replay show persisted state and allowlisted audit metadata. Owner
or authorized approver cancellation stops new scheduling. In-flight external
calls cannot be recalled; cancellation is refused during publication.

The authenticated task, plan, workflow and events APIs remain available. For
Engineering children, review the actual child ID, version and objective before
approval; parent task ownership and remembered text do not authorize a child.

## Acceptance

Local tests cover permitted natural intake, multiple humans, duplicate messages,
channel/user budgets, restart after committed intake, capability selection,
dependency ordering, team amendment bounds, approval denial, and cancellation.
Phase 1 tests separately exercise real Git, exact-revision QA/review and bounded
corrections. Controlled transports are not proof of live Discord/Switch delivery.

Live acceptance still requires actual configured incident/research/documentation
identities and sources, room permissions, Console agents, and separate human
approvals. Record source/task/handoff/session IDs, returned artifacts, actual
Switch messages and the final tested/reviewed SHA and draft PR URL. Do not claim
live incident remediation from an offline fixture run.
