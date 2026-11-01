# PM/Research recovery acceptance

Run on the verification Mac with its existing private `.env`, `.switch/`, Docker
volume and Discord bot. The November 1 demo ran on that Mac; localhost addresses
on another computer do not reach it. Keep the deployed identities and data.

## Status and scope

| Gate | Evidence so far | Remaining acceptance |
| --- | --- | --- |
| Real original-thread workflow | Recorded in [VERIFICATION.md](VERIFICATION.md) | Repeat after deploying this branch |
| Worker/API restart and disconnected workers | Recorded on verification Mac | Repeat after deploying this branch |
| Gap callback/replay | Real injected missing-ticket gap recorded | Request submitted while PM is absent must complete after the forced gap |
| Heartbeat/refusal handling | Controlled regressions; live probes below | Run all three probes on the real pinned server |
| Natural retention expiry | Not exercised | Keep separate from injected missing-ticket evidence |
| Fresh provisioning | Existing deployment used | Reproduce with new credentials/data on an isolated stack |
| Desktop Console connection | Not established | Verify separately; authenticated gateway provisioning worked |

Automated controlled tests do not establish external acceptance. A skipped live
test is an open gate. Record actual timestamps, commit/server versions, task and
message IDs, exit/restart observations and limitations before marking a gate done.

## Deploy without restarting an evicted holder

From this branch's checkout on the verification Mac, define a helper for the
local same-engine deployment:

```sh
crew() {
  docker compose -f compose.yaml -f deploy/compose.switch-local.yaml \
    -f deploy/switch/worker-recovery.yaml --profile workers "$@"
}
crew up --build -d api pm research
python3 deploy/switch/preflight.py
crew logs --tail 50 pm research
```

Require `worker_connected` and `worker_role_assumed` for each worker. Roles `pm`
and `research` must already be defined and addressing must admit the verified
owner and peer worker. The recovery override is required for takeover tests:
the base `unless-stopped` policy restarts clean exits too. Workers now return
exit 0 with `worker_ownership_lost` after terminal displacement, and exit 1 for
ordinary failures. `on-failure` restarts only failures; explicitly run `crew up
-d pm research` after Docker/host restart. Do not add another always-restarting
supervisor. See [Docker's restart policy reference](https://docs.docker.com/engine/containers/start-containers-automatically/).

For a remote Switch server, omit `deploy/compose.switch-local.yaml` and set its
HTTPS URL in private configuration; keep the worker recovery override. Use a
separate room, identities and database for the concurrent Engineering/QA work.

## Real heartbeat probes (isolated identity)

Register an additional disposable agent, never reuse PM or Research credentials.
Load `SWITCH_REGISTRATION_TOKEN` privately as in [provisioning](README.md), then:

```sh
umask 077
mkdir -p .switch
chmod 700 .switch
uv sync --frozen
uv run python scripts/register_agents.py --url http://127.0.0.1:8080 \
  --name recovery-probe --display-name 'Recovery Probe' \
  --description 'Isolated heartbeat acceptance probe; no task work' \
  --output .switch/recovery-probe.json
unset SWITCH_REGISTRATION_TOKEN

SWITCHCREW_LIVE_RECOVERY=1 \
SWITCHCREW_LIVE_AGENT_FILE="$PWD/.switch/recovery-probe.json" \
SWITCHCREW_LIVE_SWITCH_URL=http://127.0.0.1:8080 \
uv run pytest -q -m live tests/test_switch.py
```

The three opt-in tests use fresh random connection IDs and no PM/Research room
claims. They verify:

1. Real heartbeat TTL expiry reopens the stream with the same connection ID and
   a newer generation. The probe intentionally omits beats; allow roughly the
   pinned server's 30-second TTL plus reconnect overhead.
2. Closing a real stream produces HTTP 409 `no_stream`; fenced reattachment
   succeeds and the new incarnation's heartbeat is accepted. This endpoint
   evidence complements the controlled automatic-refusal/reconnect tests.
3. A second probe stream deliberately takes over the first. The old generation's
   beat receives HTTP 409 `taken_over`; the losing adapter stops without another
   open, while the winner can still heartbeat.

The latter proves adapter behavior, not Docker's supervision policy. To verify
the deployed worker policy too, use an isolated worker deployment with the
recovery override, deliberately replace its connection, and require exit 0,
`worker_ownership_lost`, no restart-count increase and a healthy winning holder.
Do not take over a production worker to perform this check. Revoke the disposable
probe registration after acceptance and retain its key only until cleanup.

Without explicit opt-in, the tests skip. Missing/bad private probe configuration
after opt-in fails. Credentials are not supplied as command arguments or printed.

## Request submitted during a forced gap

Use the approved test room and a thread attached to an already-bridged human
message. Standalone bot-created threads can lose their thread association on
this pinned upstream. Observe the actual Discord destination of every reply.

This procedure changes only one stopped worker's application SQLite cursor. It
does not rename/delete Switch tickets, history or task records. It injects an
unavailable replay ticket, not natural retention expiry. The pinned source
[resolves unknown tickets](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/core/switch_core/bridges/agent/api/handlers.py)
and [emits an opening gap](https://github.com/sandbox-quantum/switch/blob/557b62b23002548baa678339a97c8140c1ccaa8b/core/switch_core/bridges/agent/protocol/stream.py).
The test passes only if the server actually emits a gap.

1. Record the existing task/handoff counts and completed results. Ensure no
   unrelated test is in flight. Start from workers that have persisted cursors.
2. Stop both workers, leaving API and Switch running:

   ```sh
   crew stop pm research
   crew ps -a pm research
   ```

3. Set `PM_AGENT_ID` to the existing PM registration ID from private local
   configuration (not its sender ID). Choose a new backup name for each run:

   ```sh
   PM_AGENT_ID='<actual PM registration ID>'
   GAP_BACKUP="gap-$(date -u +%Y%m%dT%H%M%SZ).json"
   crew exec -T api python - arm \
     --database /app/data/switchcrew.sqlite3 --agent-id "$PM_AGENT_ID" \
     --backup "/app/data/$GAP_BACKUP" --workers-stopped \
     < deploy/switch/gap_cursor.py
   ```

   The script requires an existing cursor and an exclusive private backup file.
   It fsyncs rollback evidence before changing the cursor. It prints
   `live_gap_verified: false` because cursor injection alone proves nothing.

4. While PM remains stopped, have the verified human send one new, uniquely
   identifiable JSON request in the anchored Discord thread. Use configured
   source terminology, for example `Gateway intents`, and nonempty criteria.
   Record its actual source message ID and Discord thread ID.
5. Start Research first; wait for `worker_role_assumed`. Then start PM:

   ```sh
   crew start research
   crew logs --tail 30 research
   crew start pm
   crew logs --tail 100 pm research
   ```

6. Require PM's `history_gap_received` then `history_gap_replayed`, with the
   cursor updated from the injected sentinel. Missing either log is a failed
   injection/recovery gate. The replayed log means durable inbox ingestion;
   still wait for application acknowledgment and completion.
7. Verify exactly one task and one Research handoff for the request, one logical
   acknowledgment and result, `COMPLETED` state, and no remaining task delivery.
   Match stored source context and actual Discord replies to the original thread.
   Previously completed research must remain completed without another execution.
8. Restart PM and Research, then verify those same logical records and results
   remain unchanged. Do not count duplicate visible posts as a new execution:
   outgoing sends remain at-least-once after uncertain transport responses.

If aborting before replay has advanced the cursor, stop both workers and restore:

```sh
crew stop pm research
crew exec -T api python - restore \
  --database /app/data/switchcrew.sqlite3 --agent-id "$PM_AGENT_ID" \
  --backup "/app/data/$GAP_BACKUP" --workers-stopped \
  < deploy/switch/gap_cursor.py
```

Restore refuses if the worker has changed its cursor, rather than overwriting
real progress. After successful replay, retain the backup as evidence and keep
the new cursor; do not restore it. Keep backups private and preserve the volume.

## Fresh setup and remaining evidence

Reproduce [provisioning](README.md) on an isolated Docker project with separate
ports, data, bot/bridge or approved test channels and agent identities. Never
reset the existing volume to make the test pass. Record actual health, owner
link, sender bindings, roles/addressing and one anchored-thread result.
`local.sh` fixes the project name to `switch-server`; running it again on the
same Docker engine reuses that deployment. Prefer a separate clean machine, or
use the upstream Compose commands with an explicit different project name,
ports and matching external-network override for a same-host fresh-stack check.

Append each real run to [VERIFICATION.md](VERIFICATION.md), distinguishing the
injected gap from natural retention, live heartbeat from controlled mocks and
in-process reconnect from supervisor recovery. If a gate cannot run, record its
specific missing prerequisite rather than marking the collaboration milestone
fully accepted.
