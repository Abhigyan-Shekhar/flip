# Guarded GitHub merge

Merge is disabled by default. Publication still creates a draft PR. The PR must
be explicitly marked ready for review in GitHub before this service will merge.
The service never marks drafts ready or approves its own PR.

Configure these values on the **API process only**:

```dotenv
SWITCHCREW_MERGE_ENABLED=true
SWITCHCREW_MERGE_GITHUB_TOKEN=CONFIGURE_PRIVATE_REPOSITORY_SCOPED_TOKEN
SWITCHCREW_MERGE_REQUIRED_CHECKS=["test"]
```

The repository and base come from `SWITCHCREW_RELEASE_REPOSITORY` and
`SWITCHCREW_RELEASE_BASE`. Use a repository-scoped token with Contents write,
Pull requests read, Checks read and Commit statuses read. Keep it separate from
human API credentials; never pass it to Engineering, untrusted execution, PM,
or repository tools. Give the bot no branch-protection bypass permission.
Names in `MERGE_REQUIRED_CHECKS` must exactly match GitHub check-run names or
commit-status contexts. Empty policy fails startup. The API masks this token in
responses and does not accept it as a human approval credential.

After publication, the following API calls are available:

1. `GET /tasks/{task_id}/pull-request` with the service credential reads current
   GitHub state and revalidates persisted independent QA, Reviewer and an independent, passing Security gate at the current revision.
   Security is mandatory for merge even where legacy draft publication has no
   configured Security policy. It returns `revision`, `base_revision`, checks,
   PR number and `merge.version` (initially zero).
2. `POST /tasks/{task_id}/merge-approval` with a separate allowlisted human
   credential and `{ "revision": "FULL_HEAD_SHA", "expected_version": 0 }`
   records a new, explicit human merge approval. The version is the returned
   **merge version**, not the task lifecycle version.
3. `POST /tasks/{task_id}/merge` with a human credential and
   `{ "revision": "FULL_HEAD_SHA" }` revalidates all current conditions and
   executes one GitHub merge request with the expected head SHA. Agent/service
   credentials cannot approve or execute this endpoint.

Signed Discord controls use the same service when configured; see the generated
`slash_manifest()` for exact command options. The signature, allowlisted human,
original task thread and explicit revision are checked before a deferred,
ephemeral response. HTTP webhook reply tokens remain in memory and are never
stored as task artifacts.

An independent Security identity distinct from PM, Engineering, QA, Reviewer
and Release must have executed a passing current-SHA gate. Missing, pending or
stale Security evidence blocks merge even when optional legacy publication
policy would allow creating a draft. Schedule Security before publication.

An open, non-draft, clean and definitely mergeable PR is required. All returned
checks and statuses must succeed, and every operator-required name must be
present. Missing/unknown state, skipped checks, unavailable APIs, excessive
pagination (over 100 records), changed destination, changed head or changed base
blocks merging. GitHub base must still match Engineering's verified base commit;
otherwise rebase and obtain fresh validation. A changed head invalidates approval and requires fresh
Engineering, QA, Security and review evidence. GitHub branch protection remains
the final server-side guard against check/base races after inspection.

Approval and execution are separate durable audit events. A merge claim is
persisted before the network mutation. If the response is lost, the next request
only reads GitHub: a confirmed merged PR produces a receipt; otherwise it reports
`pending_reconciliation`. It never blindly repeats the merge PUT. Do not erase
the claim to retry. Reconcile manually if the original request failed before it
reached GitHub. The receipt posts to the original task's Discord outbox and marks
the task complete.

`uv run pytest -q tests/test_guarded_merge.py tests/test_reviewer.py tests/test_approval_release.py` exercises real temporary Git repositories and
real QA/review subprocesses with a controlled GitHub HTTP adapter. It covers
unauthorized actors, forged actor fields, stale head/base/checks, pending
mergeability, drafts, bounded pagination, rejected internal evidence, replay and
lost-response recovery. Reviewer Docker mounts are read-only and subprocess
logs are bounded. These are automated controlled tests, **not live GitHub merge
acceptance**. A disposable GitHub repository and separately authorized human
are required for that acceptance. No live merge has been performed by this change.

The host-process Reviewer fallback remains available for trusted operator test
fixtures; it is not a security sandbox. Configure Docker isolation for untrusted
repository review. This change enforces the read-only repository mount in that
configured Docker path.
