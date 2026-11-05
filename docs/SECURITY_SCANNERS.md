# Revision-bound Security scanner configuration

The existing `SecurityRunner` accepts operator-owned argument vectors in the
`triggered_security` and `engineering_security_gate` capabilities. Discord and
model text cannot supply scanner commands. Set `require_isolation: true` and
configure a digest-pinned `container` as described in
[Engineering isolation](ENGINEERING_ISOLATION.md). Unisolated mode remains an
explicit operator execution mode; a temporary checkout alone does not isolate
host filesystem or network access.

Supported report formats are normalized `findings`, Trivy filesystem JSON,
Semgrep JSON, and pip-audit JSON with `dependencies`. Trivy covers dependencies,
secrets and configuration when those scanners are enabled; Semgrep covers source
rules. These parsers do not install or silently enable scanners. Install reviewed
versions and offline rules/databases in the pinned image. There is no online
network exception in the restricted runner.

A scanner entry accepts `name`, `argv`, `timeout`, `success_codes`, `version`,
`category`, `database_updated_at`, and `database_max_age_hours`. For example:

```json
{
  "name": "trivy-dependencies",
  "argv": ["trivy", "fs", "--scanners", "vuln", "--skip-db-update", "--format", "json", "${repo_root}"],
  "version": "OPERATOR_VERIFIED_VERSION",
  "category": "dependency",
  "database_updated_at": "REPLACE_WITH_VERIFIED_UTC_DATABASE_TIMESTAMP",
  "database_max_age_hours": 24,
  "timeout": 120,
  "success_codes": [0]
}
```

Use the actual database metadata timestamp, not the time a task was requested.
Missing, naive, future or stale database timestamps produce `degraded_coverage`
for a configured dependency scan or detected dependency finding; they cannot pass
release. A scanner whose scope is unspecified proves only that configured command's
result. Set the category explicitly even when a dependency report can be empty.
Versions from native Trivy/Semgrep reports are retained; other scanners retain the
operator version, defaulting honestly to `unknown`. Publisher signatures are not
verified by this adapter.

Semgrep `errors` and `skipped_rules`, pip-audit skipped dependencies, invalid JSON,
unknown findings, output overflow and nonallowlisted exits fail closed. pip-audit
reports have no severity: these findings retain `Unknown`, which blocks release
until independent severity validation; the adapter never invents a CVSS score.
Detection is labeled unverified and is not evidence of exploit reproduction.

Finding IDs are stable hashes of scanner identity, rule/advisory, path, line and
component. Reports sort by descending severity and stable ID, so contextual selection
can refer to the persisted ranked list. IDs remain tied to the report revision;
a changed dependency version or shifted line can intentionally produce a new ID.

Execution receipts include repository, revision, configured argv, timestamps,
duration, exit code, redacted stderr preview, raw-output SHA-256 and isolation
mode. Timeout receipts record `timed_out`. The optional capability/`SecurityRunner` constructor
`artifact_dir` writes bounded redacted scanner output files with mode 600 in an
operator-managed directory. Retention and disk quotas remain operator policy;
artifact persistence is disabled unless configured. Artifacts are redacted text,
not byte-identical raw reports; the SHA-256 identifies the original bounded output.
No credential-bearing raw report is retained.

Controlled parser/process tests are automated integration evidence, not real
Semgrep, pip-audit, vulnerability-database or network-isolation acceptance.
The earlier [live Trivy secret-only run](LIVE_PIPELINE_2026-11-05.md) remains the
only historical scanner evidence described there. Fresh dependency/source/config
scanner acceptance still requires the actual configured tools on the worker.


Discord scan intake uses a separate private organization policy:

```json
{
  "security_scans": {
    "ria-kayy/Discord-server": {
      "repo_root": "/absolute/operator/checkout",
      "security_id": "REGISTERED_SECURITY_AGENT_ID"
    }
  }
}
```

The Security worker must use `triggered_security` mode with the same `repo_root`
and `repository`. PM chooses only these configured bindings; `scan this repository`
requires exactly one binding, and explicit `scan ria-kayy/Discord-server for
vulnerabilities` matches that repository. Request context is authenticated by the
existing Discord/Switch intake. The scheduler snapshots the operator checkout's
HEAD SHA, preserves it across request replays, and creates one durable independent
Security handoff through the existing Store outbox. Source message fields cannot
override the checkout, revision or command. `show me the findings` reads the
persisted report; a scan does not authorize a repair or publish operation.

Native format references: [Semgrep output schema](https://github.com/semgrep/semgrep-interfaces/blob/main/semgrep_output_v1.jsonschema)
and [pip-audit documentation](https://github.com/pypa/pip-audit).
