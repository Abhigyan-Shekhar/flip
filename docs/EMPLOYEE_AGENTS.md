# Personal Employee Codex Agents and Watchdog

This slice reuses SwitchCrew's existing `ConsoleCodex` adapter. It does not
control an arbitrary desktop window, create Codex accounts, provision Console,
or establish an operating-system sandbox. The trusted runtime must use the
employee directory to authorize each addressed task handoff before constructing
the existing Console adapter.

## Operator registrations

Create a private JSON file outside the repository and set mode `0600`. Keep one
dedicated Console control file and one read-only Console database per employee.
The control file contains the local Console token and must also have mode
`0600`; never include either file's contents in task state, event details, logs,
or user-facing responses.

```json
{
  "employees": [
    {
      "employee_id": "ria",
      "actor_id": "VERIFIED_DISCORD_USER_ID",
      "switch_agent_id": "RIA_SWITCH_AGENT_ID",
      "codex_switch_agent_id": "RIA_DEDICATED_CONSOLE_PROVIDER_SWITCH_ID",
      "workspace_root": "/private/ria/authorized-workspace",
      "allowed_roots": ["/private/ria/authorized-workspace"],
      "control_file": "/private/ria/console-control.json",
      "console_agent_id": "RIA_DEDICATED_CONSOLE_AGENT_ID",
      "codex_name": "Ria Codex",
      "console_database": "/private/ria/console.sqlite3",
      "enabled": true
    }
  ]
}
```

The configured `actor_id` must come from a verified Discord/API credential
mapping; request JSON cannot supply or override it. Employee IDs, actor IDs,
employee Switch identities, Console provider Switch identities, Console
control files, Console databases, and workspace roots must be distinct across
registrations. The employee Switch identity must differ from the dedicated
Console provider Switch identity. Each employee root must be within
that employee's configured allowlist, and allowlists cannot contain another
employee's workspace.

Initialize the directory from trusted runtime configuration:

```python
directory = EmployeeDirectory.from_file(
    store,
    "/private/switchcrew/employees.json",
    allowed_assigners={"TRUSTED_PM_SWITCH_AGENT_ID"},
)
```

The actual constructor for an already parsed operator mapping is
`EmployeeDirectory(store, registrations, allowed_assigners=...)`.
Registration identities and scope are pinned in SQLite. Changing an employee's
actor, Switch identity, workspace, or Console profile requires an explicit
operator migration; a restart will reject changed identity instead of silently
re-binding old assignments.

## Assignment and Console session boundary

The trusted PM/runtime calls `assign_task(task_id, employee_id,
assigned_by=...)` only after its own authorization and approval checks. The
directory stores an assignment row and a task event; it does not mutate the
task's immutable `source` JSON. `assigned_employee(task_id)` provides the
persisted route for task views. `revoke_assignment` blocks future authorization
and marks active session records blocked.

Before starting or resuming an employee session, call
`authorize_assignment(employee_id, task_id, workspace_root, actor_id=...,
handoff_id=...)`. The caller must provide the verified actor from runtime
credentials, not a handoff field. Authorization checks the assignment, active
task, exact Switch handoff recipient, employee identity, and exact approved
workspace. It returns a trusted Console profile for constructing the existing
`ConsoleCodex` adapter. Keep the returned control-file path in process memory;
do not serialize it. Sessions are keyed to the task and handoff for restart
recovery; unexpected Console session changes require operator reconciliation.

The directory prevents SwitchCrew from intentionally routing an employee
assignment outside its configured root. It cannot stop a local Console process
or Codex provider from reading paths allowed by the host OS. For genuine
employee privacy, run separate employee-owned OS accounts or isolated Console
processes, restrict each process's filesystem and credential permissions, and
use separate Console control/database files. The code does not claim that
prompt instructions or workspace paths alone enforce host isolation.

The parent runtime must also re-check assignment state after external Console
I/O and before accepting a result. Revoking an assignment prevents later
authorization; it cannot recall a prompt already sent to a provider.

## Watchdog

The watchdog is a separate Python process over the same SQLite database. It
observes open handoffs, pending outbox deliveries, configured worker heartbeats,
and an optional read-only Engineering execution ledger. It writes durable,
bounded findings and recovery state. When configured, it adds an idempotent
Discord notification to the existing outbox for an owning worker to deliver.
It never retries handoffs, replays a Console request, changes task state, or
approves privileged work. An uncertain Console outcome requires operator
reconciliation.

Save a private mode-`0600` watchdog config:

```json
{
  "database": "/private/switchcrew/switchcrew.sqlite3",
  "monitored_agents": ["PM_SWITCH_AGENT_ID", "ENGINEERING_SWITCH_AGENT_ID"],
  "alert_sender": "PM_SWITCH_AGENT_ID",
  "alert_after_seconds": 60,
  "handoff_timeout_seconds": 900,
  "outbox_timeout_seconds": 300,
  "execution_timeout_seconds": 300,
  "execution_ledger": "/private/switchcrew/engineering-ledger.sqlite3",
  "execution_ledger_required": true,
  "max_findings": 1000,
  "max_pending_alerts": 100,
  "poll_interval_seconds": 5
}
```

Run it as its own process:

```sh
PYTHONPATH=src uv run python -m switchcrew.watchdog --config /private/switchcrew/watchdog.json
```

The trusted worker runtime must call
`record_worker_heartbeat(store, agent_id, status=..., worker_id=...)` from its
actual lifecycle callbacks. A `Watchdog` instance also provides
`record_heartbeat` with an expected-agent allowlist. This module provides the
durable heartbeat endpoint; the parent runtime owns wiring those callbacks.
Missing heartbeat integration is not interpreted as evidence that a worker is
healthy. Handoff ages use their recorded event time when available; pending
outbox ages start at first watchdog observation because the current outbox
schema has no creation timestamp.

For recovery inspection, query `watchdog_findings` and
`employee_assignments` / `employee_sessions` in the shared database. These
records report observations and authorization state; they are not proof of
external delivery or successful Codex work. No live Console or Discord call is
made by the tests.
