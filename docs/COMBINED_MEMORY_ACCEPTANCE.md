# Waggle + Company Brain: revision-pinned acceptance

Existing probes already cover source-backed Company Brain retrieval, a scoped
real Waggle MCP cross-role observation/recall, and a two-task lesson recovery
fixture. The combined command enforces an **exact clean checkout revision**
before running them together, and produces one bounded evidence index.

## Host preparation

1. On the verification host, check out the real target repository at a full
   40-character SHA. Ensure `git status --porcelain` is clean.
2. Using the **operator-owned Company Brain indexer**, index/sync that same
   checkout and revision, then independently record the indexer receipt and
   symbol/file counts. This application MCP adapter is intentionally read-only;
   the combined probe does not pretend to perform indexing.
3. Configure Company Brain (`company_brain_enabled`, absolute repo root, exact
   SHA, a private MCP command/HTTPS URL) and Waggle (`memory_enabled`, installed
   MCP connector). Use a **dedicated `/acceptance/` project scope**, not
   production memory. Use private, mode `0600` JSON configuration.
4. Execute:

```bash
uv run python -m switchcrew.combined_acceptance \
  --config /private/path/acceptance.json \
  --file src/relevant_module.py \
  --report /private/path/acceptance-report.json
```

The `--file` option is required and may be repeated for multiple repository-relative
files. Omitting it blocks acceptance so `check_change` cannot be skipped.

The report requires exact local git HEAD and a clean checkout, revision/root
identity matching in returned Company Brain evidence, source anchors from
`get_context` and `check_change`, cross-role persisted Waggle recall with a
fresh client/session, and an independently controlled two-task learning probe.
If the external tools are unavailable or return unsupported evidence, the
result is blocked, not a hallucinated success.

Even when all three probes pass, the final status is
`integration_probes_passed`, while `live_checkout_indexing` and `live_learning`
remain `not_verified`. This distinction is essential: a controlled fixture
showing an approved lesson fixing a second test task is **not** proof that a
real approved lesson altered behavior on a second live Switch assignment.

## Code-only verification

On the final merged checkout, run the full repository tests and packaging checks.
The unit tests intentionally use a temporary Git repository and controlled
adapters; they verify fail-closed behavior but do not connect to a production
Waggle graph or a deployed Company Brain index. Retain the exact-SHA evidence
requirements above for the later host-side acceptance run.

## Final live acceptance remains pending

Record a real task with a revision-anchored graph lookup, a failed regression,
and an approved reusable lesson. Start a distinct second real task, show the
Waggle recall and the resulting different model action, then independently
verify its QA/Review receipts. Store sanitized message IDs, graph source UIDs,
lesson ID and exact SHAs. Do not write synthetic test facts to production memory.
