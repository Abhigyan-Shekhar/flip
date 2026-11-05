# Optional Company Brain context

The [November 5 real indexing and grounding check](COMPANY_BRAIN_2026-11-05.md)
passed on a dedicated registry at the recorded master SHA. Main PM integration
and broad change-impact acceptance remain unverified.

Company Brain is an opt-in, read-only evidence provider. Research results include
bounded graph evidence, repository root, indexed revision and coverage warnings.
For Engineering requests, the PM or API retrieves change impact for file paths
mentioned in the request, or keyword context otherwise. Orchestrated and incident
Engineering children receive their own context snapshot when their dependencies
are ready. The approval record and
`company_brain.context_attached` event retain that snapshot. After human approval,
the addressed Engineering handoff includes it as untrusted context to verify
against source. Existing QA, Reviewer and Release gates still apply.

The integration does not promote lessons, approve decisions, refresh an index,
or publish anything.
Graph context supplements the existing research result; it does not independently
satisfy acceptance criteria or change a blocked research result into success.

## Configuration

Keep `SWITCHCREW_COMPANY_BRAIN_ENABLED=false` until the operator has registered
and indexed the intended checkout in Company Brain. Configure these values in each PM/Research worker and API process that creates
Engineering tasks:

- `SWITCHCREW_COMPANY_BRAIN_ENABLED=true`
- `SWITCHCREW_COMPANY_BRAIN_REPO_ROOT`: absolute registered checkout path
- `SWITCHCREW_COMPANY_BRAIN_REVISION`: full Git HEAD expected for that checkout
- Exactly one transport: `SWITCHCREW_COMPANY_BRAIN_COMMAND` as a JSON argv array,
  or `SWITCHCREW_COMPANY_BRAIN_URL` as an MCP Streamable HTTP endpoint
- Optional `SWITCHCREW_COMPANY_BRAIN_TOKEN` for HTTP authentication
- `SWITCHCREW_COMPANY_BRAIN_MAX_RESULTS` to bound returned evidence entries

For stdio, use a wrapper executable that configures Company Brain's registry and
runtime. The child inherits only PATH, HOME, LANG and TMPDIR; application and
Discord credentials are excluded. Remote HTTP requires HTTPS. Credentials belong
in private environment configuration, never in URLs or repository files.

The adapter requires `list_repositories`, `repository_status`, `get_context` and
`check_change` from a registry-aware Company Brain server. It checks the exact
canonical root and local Git checkout against both server and graph revisions.
Dirty, stale, unindexed, syncing, mismatched or unavailable graphs yield a warning
instead of usable evidence. Evidence remains untrusted source material, and graph
relationships do not establish approved constraints. Missing edges remain unknown.

Compose API and worker processes receive the settings but do not automatically mount repositories
or install a Company Brain server. The configured checkout must be accessible
inside the worker at the same canonical root used by the server. Provision a
read-only mount or run the worker in the appropriate host environment.

## Verification boundary

Local tests use temporary real Git repositories and controlled MCP responses.
They verify routing, revision checks, failure handling, provenance and the human
approval boundary. They are not live Company Brain or Switch acceptance evidence.
On November 3, 2026, the connected registry contained other repositories and did
not register this checkout. No registry or index was changed. A live acceptance
run still needs an operator-provisioned registry, a clean indexed checkout and
recorded retrieval plus Switch handoff evidence.

## Engineering children and recovery

Context retrieval runs outside the SQLite transaction. The final transaction
rechecks the active parent and dependency state before creating an Engineering
child and its approval request with the retrieved snapshot. A cancelled parent
cannot dispatch a new child while a graph request is in flight. The snapshot
survives reopening SQLite and is reused on replay. Graph outages remain explicit
warnings and do not grant approval or bypass the existing gates.

With Company Brain configured, synchronous `Orchestrator.reconcile` safely defers
an Engineering child until it has a snapshot. Production async PM/API paths call
`reconcile_with_context`; integrations using Orchestrator directly should do the
same. Read-only child dispatch still uses the existing Switch handoffs.

## Read-only acceptance probe

Create a private JSON configuration file (mode `0600`) using Settings field names:

```json
{
  "company_brain_enabled": true,
  "company_brain_repo_root": "/absolute/registered/checkout",
  "company_brain_revision": "full-git-head-sha",
  "company_brain_command": ["/absolute/path/to/company-brain-wrapper"]
}
```

For HTTP, replace `company_brain_command` with `company_brain_url` and optionally
`company_brain_token`. Keep that file outside the repository. From a checkout:

```sh
PYTHONPATH=src .venv/bin/python -m switchcrew.company_brain_acceptance \
  --config /private/company-brain.json \
  --query "indexed keyword or exact symbol" \
  --file src/switchcrew/orchestration.py \
  --report /private/company-brain-report.json
```

Use `--query` for a keyword or exact indexed symbol known to exist in this
repository; an empty broad keyword result cannot establish acceptance.
The optional `--file` can be repeated. The report path must be new; the probe
creates it with mode `0600` and refuses to overwrite an existing report. It exits
zero for `PASS`, or nonzero with `BLOCKED` for absent configuration, unavailable or
mismatched evidence, or responses without source anchors. Output includes
identity, revision and source counts, with bounded warnings; excerpts are omitted.
A pass proves the configured MCP path for that run, not live Discord/Console
execution, approval, or release acceptance. Incomplete or truncated coverage
remains visible as warnings.
