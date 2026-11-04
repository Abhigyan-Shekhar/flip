# OSS Maintainer Mode

OSSMaintainer is an opt-in issue intake lane that uses the existing Store and PM
Orchestrator. It accepts only repository names and local Git roots explicitly
supplied in its operator-owned allowlist. It does not accept repository URLs,
issue-body commands, or test commands as runtime configuration.

## Configuration

The application constructs one instance with:

- pm_id: configured PM Switch identity.
- targets: existing role-to-Switch-identity map, including research and engineering.
- repo_config: a map from canonical owner/repository names to an enabled flag,
  absolute local repo_root, base branch, and optionally a per-repository
  Engineering route override.
- engineering_route: the existing QA, Reviewer, Release, job-key, and base
  configuration needed by Orchestrator.
- source_context: optional operator-owned Switch room and Discord thread IDs.
- self_bot_logins: GitHub actor logins whose issue events should be ignored.
- company_brain: optional existing read-only Company Brain adapter.

The GitHub webhook secret must be at least 32 bytes. Configure the webhook for
the issues event. Only opened, reopened, and edited actions are used. The
application route should bound request bytes and rate, and must not log the
secret or signature.

## Issue flow

After signature, repository, event, delivery, issue identity, and size checks,
an accepted issue creates a stable Store task. The PM Orchestrator starts its
existing criterion plan and dispatches the Research handoff first. The plan
then has an Engineering step whose task uses the existing human approval,
independent QA, Reviewer, bounded correction, and human Release gates.
Research reports are evidence; they do not approve Engineering.

Issue title and body are bounded, secret-like text is redacted, and the content
is explicitly marked as untrusted in the task. Reproduction status begins as
not_run; intake never claims that a bug was executed or reproduced.

Similar issue candidates use a bounded lexical comparison against at most 500
prior issues in the same repository. A match is only
possible_duplicate_requires_human_review. Issues are never merged,
auto-closed, commented on, or treated as confirmed duplicates.

GitHub delivery IDs are stored in the existing inbox with a body fingerprint.
Task identity is stable per repository and issue number. Duplicate deliveries
and restarts therefore reuse the existing task and Orchestrator plan. Editing
or reopening an existing issue appends a source-evidence event and a PM outbox
message; it does not rewrite the task objective, approval inputs, or prior
issue snapshot. Duplicate edited deliveries are idempotent.

## Polling and cancellation

After the Research handoff completes, call await maintainer.reconcile(task_id)
to reuse the existing Orchestrator reconciliation path. Cancellation is read
from the parent Store task; a cancelled plan does not advance to an Engineering
approval request. Switch delivery continues through the existing durable
outbox worker.

## External-action boundary

This module performs no GitHub API call and cannot close issues, post comments,
merge pull requests, deploy, or release. A signed event proves webhook
authenticity, not that a report is correct. Local tests use controlled signed
fixtures and a temporary Git repository; they do not prove live GitHub, Switch,
Company Brain, Research, Engineering, or release acceptance.
