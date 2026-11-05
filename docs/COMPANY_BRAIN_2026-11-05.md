# Company Brain real revision-grounding check — November 5

**Live indexing and source grounding passed for Flip master
`5bd88bdc803cc5d30d361923b3dfce62e93bfe65`.** This is a dedicated acceptance
deployment, separate from the coding pipeline's PM configuration.
The [sanitized report](evidence/company-brain-2026-11-05.json) contains the actual
probe outcomes, source references and excerpt digests; it contains no credentials.

## Setup and observed results

The configured Company Brain server's existing registry contained other
repositories, and its graph endpoint on port 6379 was unavailable. Its tools were
not exposed in the current Codex tool inventory. No other repository's graph was
used as Flip context.

Created a clean detached Flip worktree outside the application repository at the
master revision above. Started the already-installed real FalkorDB Lite runtime
on loopback port 6399 with its own persisted private database. Redis reports
8.6.2; the graph module reports 41803. A separate single-repository registry and
operator config leave the existing registry/databases unchanged. The first
tenant-mode configuration correctly refused MCP startup without an authorization
catalog; this dedicated local instance uses the supported standalone trusted
operator mode, with no tenant memory extension.

Ran the installed Company Brain operator CLI against that real instance:

- `brain register` registered the canonical clean checkout.
- `brain sync` indexed 181 files, 1,520 symbols, 2,233 calls and 527 statically
  resolved imports. It also reports 10,609 unresolved calls; these remain unknown.
- `brain ingest-docs` indexed the actual `APPROVAL_RELEASE.md`,
  `ENGINEERING_ISOLATION.md` and `DEPLOYMENT_ACCEPTANCE.md` from that checkout:
  18 sections, one verified symbol link, zero unresolved document references.

Used the real MCP SDK stdio transport to start the actual installed Company Brain
server, not a controlled response provider. `list_repositories` and
`repository_status` confirmed root, clean state and matching local/graph SHA.
The existing SwitchCrew adapter's acceptance probe passed with query `atomically`,
returning one anchored source at the expected revision. Its warnings explicitly
identify keyword retrieval and untrusted source text, not approved decisions.

A separate persistent MCP session searched real code and obtained two
`read_code` citations: `src/switchcrew/approval.py:1–40` and
`docs/APPROVAL_RELEASE.md:1–19`. Compared their returned excerpts byte-for-byte
against the clean checkout. Reopened both using `resolve_evidence` in the same
MCP session; revision, digest and bytes matched again. These citations verify
actual files and commit rather than merely accepting a claimed revision string.
Citation IDs are process-local and require fresh retrieval after reconnect.

## Failures, changes and limits

The initial broad acceptance query returned unknown/no sources after code-only
indexing. An exact file node had identity but no source anchor. After document
ingestion, the broad `approval` query included credential-shaped documentation
examples and was omitted by SwitchCrew's sensitive-content filter. The filter
was preserved. A narrower non-sensitive query returned grounded evidence.

The acceptance function already accepted an explicit query, but its CLI did not.
Added `--query` and a regression test to support an indexed keyword or symbol
without weakening identity, revision, source or credential checks.

`check_change` for the tested approval file returned no recorded historical source
anchors. The full file-change acceptance probe therefore stayed BLOCKED, as
required. Source/code grounding is verified; broad change-impact retrieval is
not. No historical relationships, approved decisions or missing edges were
invented to make it pass.

The index covers the committed master baseline, not the later uncommitted
integration fixes. PM/Research were not reconfigured to attach this new registry
to live Switch tasks. Fresh-machine reproduction and persistent service hosting
remain open. This does not establish Waggle lesson approval, retrieval-driven
behavior change or the requested two-task learning demonstration.

Focused Company Brain tests passed 31/31; the final full application suite passed
592 tests with three opt-in live tests skipped in that run. Those live Switch
recovery probes separately passed 3/3.
