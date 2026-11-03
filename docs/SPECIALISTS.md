# Configured artifact specialists

Nine formerly scaffold-only roles now have useful deterministic executors. They
read explicitly selected local snapshots and produce bounded results for review.
No model/provider login is needed for these analyses. They do not invent live
service access, issue mutations or independent QA approval.

| Role | Actual output | Input |
| --- | --- | --- |
| Community | Source-backed support reply draft and clarification flag | Public project guides/FAQ |
| Documentation | Markdown draft with cited notes and acceptance checklist | Public guides/change descriptions |
| Knowledge | Deduplicated cited knowledge entries | Public guides/decision summaries |
| Onboarding | Ordered cited checklist of relevant setup steps | Setup guide |
| Moderation | Message/rule review queue; no bans/deletions | JSON messages and configured literal-term rules |
| Security | Locations of possible secrets and unsafe execution/TLS patterns; no secret values | Explicit public code/text snapshots |
| Scheduling | Timezone-aware proposed-window conflicts; no calendar writes | JSON event snapshot and configured candidate |
| Analytics | Record count, status counts and sampled mean duration | JSON records |
| Incident | Chronological event summary, failure count and suggested checks | JSON timestamped events |

These are intentionally narrow executors, rather than claims of autonomous
support, policy interpretation, security certification, calendar integration or
incident remediation. Richer sources improve their outputs; irrelevant sources
produce an explicit no-evidence/clarification note. Human review remains required.

## Operator-owned configuration

The executor factory is `switchcrew.specialists.specialist_executor(role, config)`.
It returns the async callable accepted by `build_agent_graph(role, executor=...)`:

```python
from switchcrew.graphs import build_agent_graph, invoke_agent_graph
from switchcrew.specialists import specialist_executor

executor = specialist_executor("documentation", {
    "root": "/absolute/public-artifacts",
    "sources": ["setup.md", "recovery.md"],
})
graph = build_agent_graph("documentation", executor=executor)
result = await invoke_agent_graph(graph, {
    "agent_role": "documentation",
    "task_id": "stable-task-id",
    "handoff_id": "stable-assignment-id",
    "question": "Document Switch setup and recovery",
    "acceptance_criteria": ["Cite the setup and recovery instructions"],
})
```

Use operator-owned runtime configuration to select this executor; never read
`root`, `sources`, rules, candidate windows or executable commands from a handoff
body. The executor ignores attempted state-level overrides. The runtime's normal
trusted-sender and addressed-handoff checks still apply. The factory refuses PM,
Engineering, QA, Release and Voice, which have separate execution/approval paths.

All source paths must be relative to the configured root. A source cannot use
`..`, an absolute path, or a symlink resolving outside that root. Private paths
such as `.env`, `.git`, `.ssh`, `.aws`, `.switch`, credential/secret directories,
and key files are denied. Each source is limited to 64 KiB; up to 20 sources and
256 KiB total are accepted. Binary/NUL-containing sources are refused. JSON
analysis is limited to 500 records. Results include relative source paths,
SHA-256 digests, line citations where applicable and a maximum 24,000-character
Markdown artifact. Secret-looking lines are replaced rather than echoed;
security findings retain only source/line/rule, not credential values.

The root is an explicit trust boundary, not an OS sandbox. Select a directory
containing public review artifacts rather than a home directory or private
checkout configuration. Sources are treated as data; their text cannot grant
permissions. Do not use this pattern scan as a substitute for a real security
review. A sensitive line in structured JSON may make the redacted snapshot
unparseable, in which case the structured analysis blocks rather than exposing it.

## Structured snapshots

Analytics `metrics.json`:

```json
{
  "records": [
    {"status": "completed", "duration_seconds": 10},
    {"status": "failed", "duration_seconds": 30},
    {"status": "completed"}
  ]
}
```

This yields three records, two completed/one failed, two duration samples and a
mean duration of 20 seconds. Durations must be nonnegative finite numbers;
missing durations are excluded from that mean and counted separately.

Scheduling `calendar.json`:

```json
{
  "events": [
    {"title": "Review", "start": "2026-11-02T10:00:00+05:30", "end": "2026-11-02T11:00:00+05:30"}
  ]
}
```

Add a `candidate` object with `title`, `start`, `end` to the operator config.
Every datetime requires an explicit timezone. Comparisons account for offsets;
a meeting ending exactly when another starts does not overlap. Availability is
only against the supplied snapshot, never a claim about the live calendar.

Moderation `messages.json`:

```json
{"messages": [{"id": "message-1", "text": "Buy spam now"}]}
```

Add `rules: [{"id": "unsolicited", "terms": ["spam"]}]` to operator config.
Rules use escaped literal terms with word boundaries, not untrusted regular
expressions or instructions. Matching messages are recommended for human review;
their contents are not copied into the queue. Up to 50 rules, each with up to
30 terms of at most 200 characters, are accepted. No moderation action is sent.

Incident `events.json`:

```json
{
  "events": [
    {"timestamp": "2026-11-02T04:50:00Z", "severity": "info", "message": "Deployment started"},
    {"timestamp": "2026-11-02T05:00:00Z", "severity": "error", "message": "Worker timeout"}
  ]
}
```

Events are sorted by actual timezone-aware instants. Error/critical/fatal events
count as failures. Root cause is explicitly unconfirmed: chronological proximity
does not establish causation, and no restart/deployment/mitigation is attempted.

## Research quality

The existing configured HTTPS Research provider now ranks whole tokens from both
the question and acceptance criteria. Criteria have extra ranking weight. It
preserves source line boundaries, splits long paragraphs/code lines into bounded
snippets, skips code fences, deduplicates excerpts and URLs, and associates each
criterion with candidate evidence URLs. Source errors expose their exception
category rather than raw upstream messages. Existing public-address validation,
pinned DNS/TLS, byte limits and redirect refusal remain in place.

`criterion_evidence.status` stays `human_review_required`. A matching term or
`evidence_found` result does not prove that a criterion is satisfied. This is
still extractive research over configured sources, not autonomous web search or
an independent factual verifier.

## Validation and live gate

Controlled tests execute each role through actual source snapshots, including
compiled graph calls for the text roles. Tests verify computed metrics,
timezone conflict boundaries, literal moderation matches, sorted incident
chronology, secret-value suppression, root traversal/symlink rejection and
oversized/invalid snapshots. Research regression tests verify whole-word
relevance, criteria-aware ranking, deduplication and bounded Markdown excerpts.

For a live demonstration, configure a separate specialist identity and public
artifact root, send a trusted durable handoff, and record acknowledgment,
source digests and returned artifact in the original thread. No live specialist
Switch/Discord delivery is claimed by the controlled tests.
