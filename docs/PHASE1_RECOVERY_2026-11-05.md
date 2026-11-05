# Green Check recovery — second Mac, November 5, 2026

**The Discord-to-merge demonstration is unfinished.** This recovery adds safe QA
failure details, prepares isolated tools and records new operator diagnostics.
It does not supply independent live QA, Reviewer, publication, merge or a final
video. The original failed task and its receipts remain unchanged.

## Source and preserved state

Work started from integration commit
`2b2ee8081ef0ddbb1e6414ec2f890f3287a2b534` on
`codex/green-check-qa-recovery`. The checkout was clean on `master`; branch ancestry
was inspected before switching. The divergent managed fix was not merged.
`master` remains at `5bd88bdc803cc5d30d361923b3dfce62e93bfe65`.

The host had approximately 264 GiB free before builds; no unrelated files were
deleted. SQLite's backup API copied the available older acceptance application,
execution and checkpoint databases into a new private directory. All three
copies returned `ok` from `PRAGMA quick_check`. The original Phase 1 database,
ledger, private setup and video project from the other Mac are absent here.
Their tracked sanitized receipts are retained as historical evidence, not
assumed to be current live state.

Historical task: `e9f51d66-3f50-5203-9cd8-c27befe4214c`, **FAILED**.
Historical official managed session:
`eecbff67-cd08-4056-9bbf-0f40756603b1`.
Managed fix: `d8e0562038d9f412edc20d24873da85853dec5bd`.
See [the original immutable checkpoint](evidence/phase1/live-engineering-checkpoint.json)
for the actual Engineering approval, handoffs and secret-only Security receipt.
There is no new human decision or workflow task in this recovery.

## QA changes and actual findings

QA failure details use fixed stage/category labels and a recognized OS error
name or bounded exit code. They never serialize exception messages, sensitive
argv, paths, stderr or chained exceptions. Discord renders recognized fields
only. The original failure reason and fail-closed behavior remain; failures do
not become successful retries. Directory preparation is now inside that failure
boundary. Finished command evidence is saved before container cleanup, so a
cleanup failure cannot erase the completed command receipt.

Regression coverage includes clone/checkout/launch/cleanup failures, safe display,
OS and process code bounds, preservation of earlier receipts, completed failure
replay after restart, changed-command refusal and a fresh attempt retaining the
old failure. Existing stale-revision, interrupted-execution, task revocation and
isolation tests remain required.

The new **operator** attempts all clone the full historical managed SHA into new
folders; they do not write to an application task database or execution ledger.
[Sanitized diagnostic receipts](evidence/phase1/recovery-diagnostics.json) preserve
successful and failed attempts, output digests, exact commands and scope.

- The first restricted retry hit Git's ownership refusal at `/workspace`.
  The runner image now trusts exactly that fixed mount, not every host repository.
- The original bug assertion then passed: incomplete Trivy output was rejected.
- Broad suite attempts exhausted the 64 MiB container temporary filesystem.
  The new operator `tmpfs_mb` setting defaults to 64, accepts 16–1024 MiB up to
  the memory quota, and changes validation identity. Prior evidence cannot be
  reused after a capacity change. No network access, credentials, host socket
  or additional host mounts were added.
- With 512 MiB, broader fixtures still encounter executable-temporary-file and
  process-capacity restrictions. Moving fixtures into the disposable mount
  exposed nested Git ownership refusals. All failures remain recorded.
  **There is no complete isolated historical-SHA suite pass.**
- Fresh isolated validation of the original assertion plus graph-selected
  `tests/test_security.py` and `tests/test_security_gate.py` passed: **102 tests**.
  This is a parser/release-gate regression, not a new Trivy secret or dependency
  scan, and not a live independent QA receipt.

These observations diagnose new second-Mac runner problems. They do **not** prove
what caused the original `validation_launch_or_checkout_failed`: its exception
was not retained. The original low-storage hypothesis remains unconfirmed.

## Baseline and Company Brain

A clean separate clone is pinned to
`c26948e4d4205b45d54a2faad86fb486d78576f3`. A newly created remote target,
`codex/demo-green-check-20261105-parth-recovery`, was verified at that same SHA.
No existing target was reset. Application development remains on the fixed
integration lineage.

Company Brain's supported `init`, `register` and `sync` commands used a separate
private registry and loopback-only pinned FalkorDB instance. The existing plugin
registry still targets its original project and was not modified. Real MCP
queries were made against the separate registry. Both full revisions agree and
the baseline is clean: **224 files, 1,677 symbols, 2,449 calls, 606 statically
resolved imports, 12,009 unresolved calls**.

[Retrieved and reopened graph evidence](evidence/phase1/recovery-baseline-graph.json)
contains `SecurityRunner.run → _extract_findings`, with exact lexical resolution.
The parser citation (`security.py:210–253`) identifies the incomplete `Results`
envelope path. The gate citation (`security_gate.py:314–365`) shows the consumer
using `SecurityRunner` on the bound revision. Both citations were reopened in the
same MCP session and byte-compared with local source. This selected the two
scanner/gate test files for the 102-test isolated run above. No missing graph
edge or historical file association was invented. This is operator test selection;
no fresh Knowledge-to-Engineering workflow handoff is claimed.

## Prepared tools and runtime limits

Starting installed Docker Desktop restored the existing Switch, gateway,
PostgreSQL, API and older PM/Research containers under their restart policies.
The deployment preflight authenticated both configured workers and returned
HTTP 200 for all three services. It does not verify seven distinct story roles,
Discord membership, original-thread mapping or signed commands.

The installed official controller bundles match `bundle-provenance.json` exactly.
The existing controller-combination recipe produced:

`flip-green-check-python-controller@sha256:91e3f5c44772cab0ee3b334e156a99143dc63fcf886fd3901694a8b74dea3113`

A network-disabled, read-only version probe verified Python 3.12.16, pytest 9.2.0,
Node 22.23.4, Codex 0.166.0 and both official bundle digests. Codex reported that
PATH aliases could not be created on the read-only filesystem; the installed
executable returned its version successfully. No provider login was copied and
no managed assignment was started. Image readiness is not controller enrollment,
provider authentication or assignment attestation.

The available private worker configuration is the older `fix-answer` setup. It
has no `green-check` job or verified full PM/Knowledge/Security/Engineering/QA/
Reviewer/Release deployment. The original signed app configuration, verified
thread mapping, human allowlists, release credentials and editable video project
must be provisioned locally or transferred through an authorized private channel.
Do not run the old host-specific preparation script or weaken immutable policy.

## Verification and next acceptance

The frozen environment installed successfully with `uv sync --frozen`; installed
source import works on this Mac without the non-editable workaround. An initial
sandboxed full suite failed with macOS subprocess warnings and dependent fixture
failures; it is not a successful run. Outside that sandbox, the pre-capacity-change
suite passed **712 tests, 3 opt-in live tests skipped**. Later capacity regressions
add coverage; that earlier count must not be reused as final-SHA verification.

After the capacity regressions, the pre-commit full suite passed **720 tests,
3 opt-in live tests skipped**. Archive inspection also found ignored nested demo
files and staged official bundles in the source distribution. An explicit source
allowlist now excludes local runtime state and those separately staged bundles;
both rebuilt archive member lists must be checked before delivery.

The branch's final-SHA full pytest, Ruff, format, package build and whitespace
results are recorded in the final verification manifest under the private recovery
directory and in its GitHub `checks / test` run. Local output is not a GitHub check.
The existing push-triggered workflow already runs the complete required checks;
no canned commit status is used.

Next live acceptance requires a fresh task and thread, verified identities and
addressing, signed human Engineering approval, a new official managed assignment,
fresh independent exact-SHA gates, separate human release approval, a real draft
PR, signed `ready-for-review`, actual GitHub checks, a separate human merge
approval and a Discord guarded merge receipt. Capture the final video only after
that actual ending. No historical PR, manual checkpoint push or operator test
result can substitute for it.
