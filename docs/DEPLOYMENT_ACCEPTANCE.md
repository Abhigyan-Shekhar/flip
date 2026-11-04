# Verification-host deployment (code-only readiness)

**State: live deployment and acceptance pending.** The checks below are operator
actions to run on the already identified verification Mac or a fresh isolated
host. The repository alone cannot provision Discord, switch agents, human
approvers, authenticated Codex sessions, or a protected GitHub target branch.

## Static preflight

Keep the existing `.env` (mode `0600`) and role-owned registrations on the
verification host. Never paste tokens into a PR, issue, CI log or chat.

```bash
uv sync --frozen
uv run python -m switchcrew.deployment_preflight
```

The static check validates configured PM/Research IDs, distinct independent
Engineering/QA/Reviewer/Release identities, approval operators, private config
permissions, API credentials and the target repository. A clean result says
`ready_for_live_checks`, **not** that deployment succeeded. Service health,
networking, CLI authentication, actual GitHub branch policies, least-privilege
security runners, and externally observable Discord behavior need live proof.

Use the existing [Switch provisioning](../deploy/switch/README.md) and
[recovery acceptance](../deploy/switch/RECOVERY_ACCEPTANCE.md) instructions.
The API/PM/Research Compose processes are not a complete 17-agent deployment:
specialists have separate registrations and must be provisioned individually.

## Live acceptance contract

Run all of these on the host with a dedicated **disposable test repository** and
distinct human approvers. Capture trace IDs, timestamps, before/after versions,
exact git commit SHA and sanitized stdout/screen captures; redact secrets.

1. `switch_health`: Switch server/gateway healthy with real registered identities.
2. `discord_original_thread_roundtrip`: Discord request and reply in the original thread.
3. `pm_research_handoff`: PM → Research → PM workflow with persisted IDs and evidence.
4. `restart_replay`: stop/restart a worker and verify ack/cursor idempotency.
5. `engineering_approved_execution`: separate human approval, matching assignment and pinned SHA.
6. `independent_qa`: isolated QA commands executed at the implementation SHA.
7. `independent_review`: independent Reviewer accepts/rejects that exact SHA.
8. `release_draft_pr`: separate human Release approval and a published draft PR receipt.

The manifest below is a **reference index only**. It does not attest the
referenced artifacts, nor verify the remote service history. An operator must
independently inspect every receipt. All eight keys are mandatory:

```json
{
  "host": "verification-mac",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "checks": {
    "switch_health": {"status": "not_run", "receipt": ""},
    "discord_original_thread_roundtrip": {"status": "not_run", "receipt": ""},
    "pm_research_handoff": {"status": "not_run", "receipt": ""},
    "restart_replay": {"status": "not_run", "receipt": ""},
    "engineering_approved_execution": {"status": "not_run", "receipt": ""},
    "independent_qa": {"status": "not_run", "receipt": ""},
    "independent_review": {"status": "not_run", "receipt": ""},
    "release_draft_pr": {"status": "not_run", "receipt": ""}
  }
}
```

Store it in a **private operator folder** and run:

```bash
uv run python -m switchcrew.deployment_preflight --evidence /private/path/manifest.json
```

The validator rejects missing checks, unbounded/absolute traversal paths and
unsubstantiated `passed` claims without a receipt reference. Even an all-passed
manifest is labelled `operator_claims_complete`; actual independent acceptance
remains a manual or CI-integrated verification task.
