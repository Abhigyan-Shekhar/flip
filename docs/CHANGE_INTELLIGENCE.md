# Change Intelligence

`switchcrew.change_intelligence.build_change_report` creates a draft report from
two full Git commit IDs. It reads the actual commit metadata, file diff, and
Python AST before and after the change. It does not infer or invent a diff.

```python
report = await build_change_report(
    repo_root,
    before_sha,
    after_sha,
    task_id=task_id,
    requester=requester,
    assigned_agent=assigned_agent,
    tests=tests_executed,
    reviewer_findings=reviewer_findings,
    final_outcome=outcome,
    workflow_evidence={"request_event": event_id, "qa_handoff": qa_handoff_id},
    company_brain=brain,
)
```

The report includes file changes, detected added/removed/modified Python symbols,
the Git author, the bounded real diff and its SHA-256, and separate changelog,
PR summary, documentation, and migration-note drafts. Caller-supplied fields
remain `None` when the workflow has no evidence; they are not reported as
completed. Drafts are values returned to the caller. This module does not write
or publish documentation, open a PR, or post to Discord.

`source_evidence` always identifies the immutable Git commits and diff digest and
retains caller-supplied workflow evidence references. Supply IDs from durable
task, QA, review, or approval records rather than unverified prose.

When Company Brain is supplied, both context and change-impact results must name
the same repository and after revision. Missing, stale, or mismatched graph
coverage is reported as `unknown`; it is not interpreted as no impact. Company
Brain excerpts are evidence, not instructions. The caller must retain provenance
and warnings when displaying or storing the report.

Git subprocesses use fixed argument vectors and operate outside application
database transactions. Cancellation kills the Git subprocess. Full object IDs
are required, and the before revision must be an ancestor of the after revision.
Diff output is bounded; a truncation flag and full-diff hash remain in the
report so reviewers can retrieve the exact Git diff themselves.

## Verification boundary

Unit tests create local repositories and verify reports against real commits.
Those tests validate Git behavior and draft generation only. They do not verify
live Company Brain coverage, Discord delivery, or documentation publication.
