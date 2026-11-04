# External knowledge and verified lessons

SwitchCrew has two separate local records for reusable context:

* **External knowledge** captures bounded Markdown, text, or JSON snapshots from operator-configured import roots. Imported notes are untrusted, pending, and hash-checked. They can inform Engineering only after an authorized person approves the captured snapshot.
* **Verified lessons** capture a correction that has an exact failed attempt, correction, passing QA command, and Reviewer receipt tied to Git revisions. A configured approver must approve the lesson. Hypotheses cannot be approved as verified lessons.

Both services keep scope, provenance, approval, retrieval, and relations in the SwitchCrew SQLite store. Waggle has project-level conversational memory only; it does not provide the ACLs or structured relation APIs needed for private, team, or organization records. Only approved project lessons for a matching configured project are projected to Waggle. Local SQLite remains authoritative.

## Actor policy

The host supplies `trusted_actors` from authenticated operator configuration. Caller-provided actor IDs must come from the host's authenticated identity, never from note contents or task input. An example policy is:

```python
trusted_actors = {
    "alice": {
        "projects": ["github.com/acme/service"],
        "teams": ["backend"],
        "orgs": ["acme"],
    },
    "reviewer": {
        "projects": ["github.com/acme/service"],
        "approve_projects": ["github.com/acme/service"],
        "approve_teams": ["backend"],
        "approve_orgs": ["acme"],
        "approve_personal": ["alice"],
    },
}
```

The host must omit untrusted actors and must not derive approval rights from a document's JSON fields, claimed author, or source path. Personal records are readable only by their owner; team and organization records require membership; project records require project membership. Approval rights are separately mapped.

## External note ingestion

Construct `KnowledgeService(store, trusted_actors=..., import_roots=...)`. Import roots must exist and be configured by the operator. `ingest_file` accepts a relative file inside one of those roots or an absolute path under a root. It rejects traversal, symlinks, unsupported formats, invalid UTF-8/JSON, secret-like material, empty files, and files larger than the configured limit (at most 1 MB).

```python
pending = service.ingest_file(
    "design/normalization.md",
    actor_id=authenticated_actor_id,
    project_id="github.com/acme/service",
    scope="project",
    source_owner="Platform team",
    source_timestamp="2026-10-28T10:00:00Z",
    related_task_id="task-123",  # optional; must exist in the same project
)
approved = service.approve_document(pending["id"], actor_id=reviewer_actor_id)
context = service.retrieve("normalization convention", actor_id=authenticated_actor_id,
                           project_id="github.com/acme/service")
```

Approval rechecks the stored snapshot digest. Retrieval returns only approved, scope-visible documents whose digest still verifies, with source path, source owner, source timestamp, import actor/time, hash, and optional related task. Retrieved note content remains explicitly untrusted input to the model; it cannot grant tool access or alter approval policy.

## Verified lessons

`LessonService.propose_from_task` accepts a bounded summary, failure signature, root cause and status (`verified`, `unknown`, or `hypothesis`), correction, exact regression command argv, applicability, and optional supersession/contradiction links. The service independently checks the task's persisted workflow: rejected attempt evidence, exact revision-bound final QA and Reviewer receipts, and a successful named regression command on the final revision. An unknown root cause must be stated as `unknown`; hypotheses may be stored as hypotheses but cannot pass approval.

`approve_lesson` revalidates the evidence fingerprint, checks scope-specific approver rights, and records the approving actor and time. Supersession and contradiction links are explicit local relations between approved lessons in the same scope. Superseded lessons are omitted from retrieval; contradictory lessons remain visible with their relation so callers can resolve the conflict. These local relations are not represented as Waggle graph edges.

`retrieve_lessons` returns only locally approved and authorized lessons, and may use project-scoped Waggle recall as a candidate hint. A Waggle result is never trusted by itself: its lesson ID must map back to an approved local row the caller can read. Lesson text is guidance, not executable authority.

When retrieval is requested with a persisted consuming `task_id`, the service stores a retrieval receipt for each returned lesson. After that task has its own exact QA and Reviewer receipts and passes the lesson's named regression command, the runtime can call `record_use(task_id, lesson_id, actor_id=...)`. The method checks the retrieval receipt, scope authorization, matching project, and verified task evidence before storing an `applied_and_verified` use receipt. It rejects a lesson's source task and does not count retrieval alone as reuse.

## Waggle write outcomes

For matching project configuration, approval records a write-in-flight state before the Waggle call. Confirmed persistence becomes `stored`; a timeout or ambiguous result becomes `unknown`. The service does not blindly retry an uncertain write, avoiding duplicate observations. Private, team, and organization lessons remain local. The `Memory` adapter's existing project setting must exactly match the lesson project before any projection.

## Controlled acceptance experiment

Run the reproducible two-task regression experiment with:

```sh
PYTHONPATH=src .venv/bin/python -m switchcrew.learning_acceptance
```

It creates a temporary Git repository and SQLite workflow store, reproduces a failing normalization regression in task one, records a correction with revision-bound local QA/Reviewer fixtures, explicitly approves the lesson through a controlled in-process memory adapter, then demonstrates task two failing before retrieval and passing after applying the retrieved correction. The report says `live_waggle_called: false`. This verifies local behavior and the memory-service contract; it is not evidence of live Waggle integration. The temporary repository and database are removed at process exit. An optional `--report PATH` writes a new report file with exclusive creation and mode `0600`.
