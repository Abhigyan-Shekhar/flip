# Optional Company Brain context

Company Brain is an opt-in, read-only evidence provider. Research results include
bounded graph evidence, repository root, indexed revision and coverage warnings.
For direct PM Engineering requests, PM retrieves change impact for file paths
mentioned in the request, or keyword context otherwise. The approval record and
`company_brain.context_attached` event retain that snapshot. After human approval,
the addressed Engineering handoff includes it as untrusted context to verify
against source. Existing QA, Reviewer and Release gates still apply.

This first integration does not enrich orchestration-created Engineering child
tasks, promote lessons, approve decisions, refresh an index, or publish anything.
Graph context supplements the existing research result; it does not independently
satisfy acceptance criteria or change a blocked research result into success.

## Configuration

Keep `SWITCHCREW_COMPANY_BRAIN_ENABLED=false` until the operator has registered
and indexed the intended checkout in Company Brain. Configure these worker values:

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

Compose workers receive the settings but do not automatically mount repositories
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
