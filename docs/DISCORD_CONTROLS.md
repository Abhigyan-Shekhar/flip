# Discord task controls and finding selection

`/crew` remains supported. `/flip status TASK_UUID`, `/flip logs TASK_UUID`,
`/flip findings TASK_UUID`, `/flip tasks` and `/flip help` are also available as
signed slash commands and ordinary bridged text. Text additionally supports
`/flip cancel TASK_UUID`, using the existing owner/approver cancellation checks.

The natural phrases `show me the logs`, `what's the status`, `what did Security
find?`, and `stop the current task` resolve durable task records in the original
Switch room/thread. Multiple matching tasks require an explicit task UUID.
Restarting the worker does not reset this context. Logs show bounded, redacted
recorded command previews, exit codes and revisions; full log download, interactive
PTY access and live log streaming remain unavailable.

After a completed configured Security scan:

```text
Show me the findings.
Investigate the first finding.
Fix the first one.
Investigate the second finding for task SCAN_TASK_UUID.
```

Finding selection preserves the report's order, stable finding ID, scan task,
Security handoff, repository and exact revision in the new task's source.
Multiple matching scans require the explicit scan task form. Incomplete, stale
revision receipts, unsupported ranks and older reports without stable IDs cannot
start a follow-up. User-supplied JSON cannot inject trusted selection metadata.
The fixed Engineering destination must match the scanned repository; every fix
still waits for human Engineering approval. Findings remain untrusted evidence,
and detection is not proof of exploit reproduction. Investigation uses the
existing Research path and configured revision-grounded Company Brain context.
No new live acceptance is claimed by these controlled tests.

## Guarded merge from Discord

Enable the separately configured guarded merge service and register the updated
manifest returned by the existing Discord commands API. Keep the GitHub credential
in the API/Release environment. Configure verified thread bindings and explicitly
allowlisted Discord approver user IDs. All signed merge commands require the
original task thread, a published verified task and current GitHub gates.

1. `/flip merge-status task_id:TASK_UUID` checks current readiness and returns
   the exact revision and **merge-state version** (initially zero).
2. `/flip approve-merge task_id:TASK_UUID revision:FULL_SHA version:MERGE_VERSION`
   records separate human authorization for that head/base/check policy.
3. `/flip merge task_id:TASK_UUID revision:FULL_SHA` rechecks current gates and
   attempts the guarded merge. A changed head invalidates approval.

Merge commands acknowledge immediately with an ephemeral deferred response and
update that response after GitHub verification. Interaction callback credentials
are transient; they are not stored in task records. The release service's durable
claim prevents an uncertain GitHub merge response from triggering an automatic
second merge. A callback failure can hide the display result; inspect merge status
before retrying. Ordinary phrases such as “looks good” never authorize a merge.
The service requires a ready, non-draft PR; marking a PR ready is currently an
operator GitHub action. Live Discord merge acceptance is still pending.
