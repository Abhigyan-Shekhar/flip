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

- `MAX_CALLS` is 1–3. An attempt is reserved transactionally in SQLite before
  calling an external model; a crash **consumes** that attempt. Replays of an
  already persisted plan never call a model again.
- `MAX_STEPS` is 1–12 and applies to both model and fallback plans.
- `MAX_PROMPT_CHARS` is 1,000–32,000. This is a **character budget**, not an
  independently measured token/billing budget; provider quotas remain external.
- `FALLBACK` is off by default. If explicitly enabled, a failed/invalid model
  plan may use the existing deterministic criterion router, but **only** if its
  selected roles are all configured and its steps fit the budget. The stored
  planner label explicitly records the fallback. No fallback can introduce an
  unregistered specialist, bypass approval, or hide an unavailable provider.

These controls do not guarantee a particular invoice or subscription credit
count: neither CLI exposes dependable billed-token metadata in this adapter.
Human authorization and existing Switch sender verification remain separate.
