# Incident room text commands

SwitchCrew recognizes these exact Discord message forms in the authenticated task's original room and thread:

```text
/crew status <task-uuid>
/crew team <task-uuid>
/crew why <task-uuid>
/crew replay <task-uuid>
/crew cancel <task-uuid>
```

These are text commands handled by the existing message bridge. They do not register Discord slash commands. Commands for unknown tasks and tasks from a different room or thread receive the same generic response without disclosing task details. A task's owner can cancel it; a caller already authorized as an approver may be passed through the trusted `can_cancel` argument. Authorization is never inferred from message text. Cancellation uses the normal version-checked task transition and cannot interrupt a task in `PUBLISHING`.

`status` reports task status, workflow phase, and approval phase/attempt when recorded. `team` reports selected roles, step IDs, and assignment states. `why` reports the recorded selection reason and capabilities. `replay` shows a bounded chronological audit view with allowlisted event names, transition states, handoff identifiers and senders, and approval actors/timestamps. It omits objectives, criteria, handoff payloads, tool results, prompts, diffs, tokens, paths, and private remembered context.
