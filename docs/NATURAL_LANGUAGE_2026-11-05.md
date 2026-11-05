# Readable agent conversation — November 5, 2026

**New normal Switch/Discord messages use prose rather than JSON envelopes.**
The human's real plain-text request completed through PM → Research → PM with
zero raw JSON handoff blocks in the observed thread.

- [Live Discord thread](https://discord.com/channels/1565566745622872115/1567733439984042149)
- Task `eb714d1c-e95c-5c31-aeb8-fa4a140b05aa`: COMPLETED, version 4.
- Research handoff `df973814-6c86-5170-9fd8-de26d8f2d62f`: completed, evidence found.
- [Sanitized message/evidence record](evidence/natural-language-2026-11-05.json).

## What changed

PM and specialist runtimes now render delegation, acceptance, completion,
correction, review and Security outcomes as readable messages. A single audit
reference identifies the durable outbox record; receivers resolve the original
structured intent from the existing shared SQLite store. Display text never
grants authority or changes the assignment. Legacy JSON remains readable for
history and compatibility. APIs, explicit debug traces and private artifacts
retain their structured schemas.

Human requests can use prose with explicit success criteria:

```text
Please fix answer.txt.
Success criteria:
- The file contains exactly correct followed by a newline.
- README stays unchanged.
```

Coding verbs route to Engineering only through an operator-configured default
job, `SWITCHCREW_ENGINEERING_JOB_KEY`. This test host maps it to the existing
disposable `fix-answer` template. Request text cannot supply a shell command,
filesystem source, repository destination or credential. Other requests with
criteria route to Research. Missing criteria produce a plain-language question;
they are not invented. This is deterministic prose intake, not unrestricted
conversational planning or an inferred approval.

Approval prompts provide an exact readable response, for example:

```text
Approve engineering for task TASK_UUID version 1
Approve release for task TASK_UUID version 13
Reject engineering for task TASK_UUID version 1
```

An optional leading "I" and final period are accepted. Task UUID, stage and
positive version remain mandatory. Casual agreement never authorizes execution.
The existing authenticated human identity, allowlist, original-thread scope,
optimistic version, independent QA/Reviewer/Security and separate Release gates
still apply. JSON decisions continue to work for existing callers.

The actual Console Codex assignment is also prose. Its full objective, criteria,
untrusted correction/context and result contract are placed in a mode-600 brief
under the checkout's ignored `.switch/config/` directory. The public message
points the agent to that local brief. Symlinked brief directories and tracked
brief-name collisions are refused. Engineering still claims delivery durably
and checks human approval before delivery; no alternate model launch was added.

PM Research results now render both extractive-provider results and model-backed
artifacts/string evidence. Limitations are readable bullets. Default workflow
and meeting summaries also avoid JSON dumps. The release receipt summarizes
configured check results and includes the execution receipt, while complete
commands/hashes remain in durable evidence.

## Live failures and fixes

1. The first coordinated reload encountered an existing pending approval prompt
   with old wording. Reusing its immutable outbox key with new text raised a
   conflict and stopped PM. Frozen presentation replies now preserve the original
   saved prompt on replay; new prompts use the updated wording. No task or
   execution was approved, reset or duplicated to repair this.
2. The real model-backed Research result had an `artifact`, string evidence and
   list limitations. The first renderer handled only extractive evidence objects,
   incorrectly displaying no evidence and a Python list. Added both schema paths
   and a regression. The operator queued a clearly labeled display correction
   from the unchanged completed result through PM's normal durable outbox.
   Research was not rerun; the extra Discord messages are a presentation
   correction, not a second investigation.

The real Switch/Discord messages were independently read back through Discord's
API. The stored human request is plain text, the authenticated human identity
matches the configured binding, and the completed task has one Research handoff.
The Engineering/QA/Reviewer/Release ledger remains six rows with unchanged digest
`754848f487a7fe5cb52bf5d0891674a248936bd8973bc81ca883f893a4bdc1c7`.
No Engineering execution or publication occurred in this read-only test.

## Validation boundary

Final suite: **610 passed, 3 skipped** (31.29 seconds). Ruff lint/format checks for
`src tests`, Node syntax checks for the controller relay sources and
`git diff --check` pass. Regressions cover natural request/decision parsing,
authenticated same-thread decisions, stale decision replay, readable-reference
resolution, sender rejection, unchanged receipt digests, startup prompt migration,
model-backed Research formatting, private briefs and symlink rejection.

The three skips are the opt-in Switch recovery tests; their earlier separate
real run passed 3/3, before this presentation update. The
[earlier complete coding-to-draft-PR run](LIVE_PIPELINE_2026-11-05.md) is retained
as evidence of that pipeline. Its historic JSON messages were not rewritten.
The entire Engineering → QA correction → Reviewer → human Release → PR sequence
has not been rerun with the new presentation; that portion has controlled
regression coverage here. Live VC, recording/provider consent, two-task learning,
fresh-machine setup and persistent hosting retain their existing acceptance gaps.
