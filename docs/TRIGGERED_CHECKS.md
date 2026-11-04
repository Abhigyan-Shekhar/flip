# Triggered QA and Security Checks

`switchcrew.triggers.accept_github_webhook` accepts signed GitHub `push` and
selected `pull_request` events. It checks the SHA-256 HMAC over the exact request
body, event type, stable delivery ID, configured repository name, action, and a
full commit SHA before writing anything. Invalid, duplicate-ID-with-changed-
payload, out-of-scope, and deletion deliveries do not create a new check task.

An accepted event creates a deterministic durable Store task and two handoffs:
one to the configured QA identity and one to Security. Store assignments and
outbox records are the durable source of truth; the existing Switch outbox
worker is responsible for sending them. Retrying an accepted delivery recreates
neither tasks nor assignments. The delivery ID is fingerprinted in Store inbox,
and a stable repository/event/ref-or-PR/revision key deduplicates replayed signed
bodies even if the delivery header changes. No Engineering assignment or
release action is created by this trigger.

The webhook handler performs no network request inside a Store transaction.
The application route must keep its request-size limit and rate limit, pass the
operator-configured secret/repository/recipient identities and PM sender. It may
also pass operator-owned Switch room and Discord thread IDs via `source_context`;
these keys are allowlisted and values are never read from GitHub payload fields.
Return only safe receipt fields. Never log the signature or webhook secret.

## QA

`TriggeredQAExecutor` runs only operator-provided argv. Commands are never read
from the webhook body, task text, or Discord. It requires a persisted handoff
addressed to the configured QA identity and a task marked as a GitHub trigger.
Each configured command receives a fresh local clone checked out detached at the
event SHA. Commands run without a shell, with a minimal environment and a
timeout. Missing configuration, authorization failures, clone/revision errors,
timeouts, and failed commands return blocked or failed statuses, never a pass.

The executor accepts `${revision}` and `${repo_root}` placeholders in individual
argv elements. They are expanded only from the verified persisted assignment.
For example, an operator may configure
`["python", "-m", "pytest", "${repo_root}/tests"]`; the path placeholder is
expanded to the isolated checkout. Review every scanner and test command before
provisioning it.

## Security

`SecurityRunner` requires one or more operator-configured `ScannerConfig` entries.
Each scanner receives argv directly, without a shell, in a temporary source
archive extracted from the exact commit. Accepted output is JSON with a
`findings` or `issues` array (or the supported Trivy result arrays). A scanner
must exist, finish before its timeout, return valid structured output, and
report no findings before the result can be `verified_success`. Missing scanners
produce `blocked_configuration`; timeouts, invalid output, and scanner errors
produce `scanner_failed`. Findings are normalized into Critical, High, Medium,
or Low with file/component, bounded evidence, explanation, remediation, and
verification status. Sensitive-looking values are redacted. Operator config may
include scanner-specific success exit codes (for example, a tool that uses exit
code 1 to report findings), but its JSON must still match the supported schema.

```python
runner = SecurityRunner([
    ScannerConfig("operator-scanner", ("/absolute/path/scanner", "--format", "json",
                                       "--input", "${repo_root}"), timeout=120),
])
report = await runner.run(repo_root, exact_revision)
```

`TriggeredSecurityExecutor(store, repo_root, recipient, repository, runner)` binds
this runner to the persisted GitHub trigger task and Security handoff, just as
`TriggeredQAExecutor` does for QA. The callback receives a Switch state with a
persisted `handoff_id`; the event itself cannot supply a scanner command.

Do not use arbitrary webhook text as scanner configuration. Operators own the
scanner argv, repository mapping, and allowed test commands. No test command or
scanner performs a release, merge, deployment, or external write.

## Verification boundary

Tests use local repositories, controlled signed request bodies, and small
operator-configured fixture commands. They verify signature handling,
repository/action/revision validation, deduplication across SQLite reopen,
Switch outbox assignment creation, isolated exact-SHA execution, scanner
normalization, and blocked failure paths. These are controlled local tests,
not live GitHub, Switch, QA-suite, or scanner acceptance.
