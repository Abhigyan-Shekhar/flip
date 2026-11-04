# PM harness planning: budget and fallback policy

The existing model-assisted PM emits a structured 1–12 step proposal. The
Orchestrator, not the model, validates every role, criterion and dependency,
and commits the immutable plan to SQLite. Engineering still requires a
separate human authorization, QA, independent review and Release approval.

The operator can now configure these limits in the private `.env`:

```dotenv
SWITCHCREW_HARNESS_PLANNER_MAX_CALLS=2
SWITCHCREW_HARNESS_PLANNER_MAX_STEPS=12
SWITCHCREW_HARNESS_PLANNER_MAX_PROMPT_CHARS=24000
SWITCHCREW_HARNESS_PLANNER_FALLBACK=false
```

- `MAX_CALLS` is 1–3. Prompt-length validation happens before a call is
  reserved; oversized prompts consume zero model attempts. A model attempt is
  then reserved transactionally in SQLite, so a crash **consumes** it. Concurrent
  requests for the same task share a renewable SQLite claim and reuse the
  persisted plan. A crashed claim becomes recoverable after its lease expires.
- `MAX_STEPS` is 1–12 and applies to both model and fallback plans.
- `MAX_PROMPT_CHARS` is 1,000–32,000. This is a **character budget**, not an
  independently measured token/billing budget; provider quotas remain external.
- `FALLBACK` is off by default. If explicitly enabled, a failed/invalid model
  plan, oversized prompt, or exhausted invocation budget may use the existing
  deterministic criterion router **without spending an additional model call**,
  but only if its selected roles are configured and its steps fit the budget.
  The stored planner label explicitly records the fallback. No fallback can
  introduce an unregistered specialist, bypass approval, or hide an unavailable
  provider. Without any PM harness configured, the pre-existing deterministic
  planner remains the default; this flag controls failures of an enabled harness.

These controls do not guarantee a particular invoice or subscription credit
count: neither CLI exposes dependable billed-token metadata in this adapter.
Human authorization and existing Switch sender verification remain separate.

## Post-merge verification boundary

Run the repository CI suite on the merged checkout before supplying a signed-in
Codex or Antigravity CLI to the PM role. The isolated unit tests cover schema
rejection, claim races, budget exhaustion and fallback gating; they do not prove
remaining subscription credits, external model quality, or real task outcomes.
Keep those host-side observations explicitly pending.
