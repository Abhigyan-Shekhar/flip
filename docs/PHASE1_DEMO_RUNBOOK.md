# Phase 1 demo runbook

## Current recovery status

See [the recovery report](PHASE1_RECOVERY_2026-11-05.md). The old task remains
FAILED. Use a fresh task, thread and disposable target; never overwrite its result.
The recovery host has healthy storage and a new clean baseline, but the original
private seven-role runtime, signed app setup and editable video project were not
transferred. Existing PM/Research health is not seven-role acceptance.

## Operator setup

Use a disposable `codex/demo-*` branch in the explicitly selected FLIP repository.
Never target `main`/`master`, weaken checks, delete task state or reuse a changed
revision's evidence. The pinned baseline is
`c26948e4d4205b45d54a2faad86fb486d78576f3`.

1. Follow `deploy/switch/README.md` and `LOCAL_MAC_RUNTIME.md` for the existing
   Switch/PostgreSQL/Discord bridge. Verify human guild/user bindings and separate
   PM, Security, Knowledge, Engineering, QA, Reviewer and Release identities.
2. Create a **new** clean checkout with
   `git worktree add --detach /absolute/demo/source c26948e4d4205b45d54a2faad86fb486d78576f3`.
   Preserve that commit with `git update-ref refs/demo/phase1-vulnerable SHA`.
   Create a new disposable remote target at that SHA; never reset an existing target.
3. Reuse the installed Company Brain environment. Run its supported `brain register`
   with a **separate** private registry/config pointing to this checkout, then
   `brain sync --registry /absolute/registry.json --repo-root /absolute/demo/source`.
   Run its real FalkorDB/Lite instance on a free loopback port. Do not register
   another checkout as if it were FLIP. Set the worker's `company_brain_revision`
   to the full baseline SHA. Preserve warnings and resolve byte-checked citations.
4. Add this route to a private organization config, retaining other required policy:

   ```json
   {"green_check":{"Abhigyan-Shekhar/flip":{
     "repo_root":"/absolute/demo/source",
     "security_id":"REGISTERED_SECURITY_ID",
     "knowledge_id":"REGISTERED_KNOWLEDGE_ID"
   }}}
   ```

5. Configure two existing `switchcrew.runtime` specialist processes. Each has its
   own `AgentConnection` registry, PM-only authenticated sender mapping, shared
   application database, own checkpoints, role and private capability config.
   Knowledge mode is `green_check_knowledge`; Security is `green_check_security`.
   Both capabilities require operator `repository`, `repo_root`, and `pm_id`.
   Security additionally requires `artifact_dir`, `require_isolation: true` and
   a digest-pinned Docker `container` with Python and Git. Configure its nested
   `gate` with the **existing** engineering Security capability (real offline
   scanners, separate scanner image/provenance, isolation required). PM needs the
   corresponding `security_gate` policy from `SECURITY_SCANNERS.md`/`GUARDED_MERGE.md`.
6. Set a `green-check` Engineering job template in the existing private
   Engineering/QA/Reviewer configs. Source and `base_revision` must match the
   investigation checkout. QA should run the new regression and existing scanner
   and gate tests. Use an image containing all required dependencies. Reviewer
   must inspect the real diff and emit its established accepted/findings schema.
   `require_isolation` must stay enabled. Preserve independent identities and
   exact-SHA invalidation/correction behavior. On Docker Desktop, use a runner
   image trusting only its fixed `/workspace` mount. For a large Python suite,
   inspect actual temporary-space and PID needs; `tmpfs_mb` is bounded and part
   of the validation identity. Changing it requires a fresh handoff. Host free
   space alone does not establish container capacity. A full suite with host
   executable fixtures is not automatically compatible with restricted `/tmp`.
7. Set `SWITCHCREW_ENGINEERING_JOB_KEY=green-check`, the explicit Release repository
   and disposable base, and the registered identities. Reuse the existing managed
   Console provisioning **after** human Engineering approval; preserve its
   network/mount/image attestation and operator job boundaries. No local Codex
   subprocess may substitute for a managed Engineering session.
8. Host the signed interaction endpoint with a dedicated Discord app or a proper
   command dispatcher. Register `slash_manifest()` for `/flip`; preserve Switch
   built-in commands. Configure its public key, verified original-thread mapping,
   guild/user allowlists and human approvers. Never replace an existing application
   endpoint without preserving its other commands. Phase 1 plain-text approvals
   are intentionally refused.
9. Configure Release's private repository-scoped credential and API-only guarded
   merge credential/required checks. Provide actual GitHub check publishers; local
   passing tests alone do not create required GitHub checks. Signed
   `/flip ready-for-review` is implemented and automatically tested. Configure its
   verified human/thread mapping and service before attempting it live. It is a
   separate action from merge approval and has not completed this live story.
10. Run the read-only, repeatable preflight:

    ```sh
    PYTHONPATH=src .venv/bin/python -m switchcrew.phase1_preflight \
      --config /absolute/private/preflight.json --report /absolute/new-report.json
    ```

    The config contains `settings` (Settings field names), `repo_root`, full
    `revision`, `container`, `require_isolation`, `security_id`, `knowledge_id`,
    `scanners` and scanner provenance. Missing dependencies are BLOCKED. Optional
    operator readiness declarations are setup attestations, not live evidence.
    A report must use a new path; the probe never overwrites evidence.

## Discord sequence and observable evidence

1. In the configured test channel send:
   **Hey Flip, I think our vulnerability scanner reports clean results even when
   Trivy output is incomplete. Can you investigate?**
2. Open its generated thread. Observe separate PM→Security and PM→Knowledge
   Switch references and specialist acceptance/results. Wait for the actual
   pinned-revision reproduction and graph receipt.
3. Send **What did you find?**, **Show me the evidence.**, or
   **Show me the reproduction logs.** Context is scoped to user/room/thread.
4. Send **Fix it. Add a regression test and create a PR. Don't merge without my approval.**
   Observe an Engineering proposal with no session before approval.
5. The actual human approver uses `/flip approve-engineering` with the displayed
   task and current task version. A test operator must not impersonate that decision.
6. Observe managed Engineering's commit SHA, independent QA, real Reviewer and
   Security receipts. A failed check must prevent release; corrections need
   fresh evidence at the new SHA. Inspect `/flip logs TASK_UUID`.
7. The actual human uses `/flip approve-release` with task, current task version
   and full verified SHA. Observe the real draft PR URL. The human then uses
   `/flip ready-for-review` with the task and exact full SHA in the original
   verified thread. Confirm GitHub actually reports `draft: false`; this action
   does not approve or execute a merge.
8. Inspect `/flip merge-status`; obtain real current required GitHub checks.
   Human uses `/flip approve-merge` with task, full SHA and **merge version**,
   then `/flip merge` with the same task/SHA. Observe GitHub's actual merge SHA.

Stop the recording at a blocker and label it accurately. Do not show a synthetic
merge receipt, fake scanner output or a historical PR as this task's result.

## Three-minute recording outline

- 0:00–0:20: title, phone-first premise and the actual incomplete-output report.
- 0:20–0:55: Discord request, PM plan and independently identified Switch handoffs.
- 0:55–1:25: genuine isolated failing assertion; source SHA and graph/test decision.
- 1:25–1:55: proposal and actual human approval; managed patch SHA and before/after test.
- 1:55–2:25: exact-SHA QA, Security and Reviewer receipts, real PR and required checks.
- 2:25–3:00: actual human merge approval and confirmed GitHub receipt, or clearly
  show the pending gate when the live run cannot proceed.

Long waits may be edited out only when disclosed. Capture actual browser frames;
never reconstruct agent results. A partial acceptance recording must say so.

## Recovery and repetition

Restart the same scoped workers with the same SQLite/checkpoint/ledger paths.
Completed inbox/outbox IDs prevent duplicate delivery; an interrupted read-only
reproduction can rerun safely. Persisted investigation/fix proposals survive restart.
Do not erase claims after an uncertain publication/merge response; use existing
read-only reconciliation. Use a **new** thread and disposable target for each run.
Never reset an already published/merged demo branch to replay the story.

## Actual artifacts from this host

The captioned acceptance-preview export is:
`/Users/abhigyanshekhar/.local/share/flip-phase1/2026-11-05/deliverables/FLIP-green-check-acceptance-preview.mp4`.
It is exactly 180 seconds, H.264, 1920×1080 at 30 fps. Actual browser captures,
frame timestamps, scene text, private configuration and media build script are
retained in the same private host directory. Captures are sampled browser frames;
idle gaps and one corrected UI check are disclosed edits. This is a partial
acceptance preview, not a successful end-to-end merge demonstration.

To repeat the **actual isolated comparison**, with a new output directory:

```sh
docker build -t flip-phase1-runner:YOUR_RUN deploy/phase1-runner
# Inspect RepoDigests, then put the exact digest/socket/binary in a private
# DockerIsolation JSON config. Do not use an unpinned tag for execution.
PYTHONPATH=src .venv/bin/python scripts/phase1_compare.py \
  --source /absolute/flip-checkout \
  --vulnerable refs/demo/phase1-vulnerable \
  --patched refs/demo/phase1-patched \
  --container-config /absolute/private/container.json \
  --output /absolute/new-comparison-directory
```

For this existing host's recorded preview, the exact media rebuild command is:

```sh
python3 /Users/abhigyanshekhar/.local/share/flip-phase1/2026-11-05/build_demo.py
```

That command requires the preserved captures/receipts, Pillow and FFmpeg; it
composes existing evidence and does not run or recreate a live workflow.
