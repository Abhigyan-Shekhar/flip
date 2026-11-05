# Isolated official Console controller

These are the container build/relay sources used in the
[November 5 live acceptance](../../docs/LIVE_PIPELINE_2026-11-05.md).
They provide the execution boundary for the opt-in controller-v2 adapter;
they do not grant Engineering or Release approval.

## Build

Stage `agent-controller.mjs` and `shared-host.mjs` from the installed official
`Switch Console.app/Contents/Resources/dist-sidecar` into this directory.
They are intentionally ignored by Git. Compare their SHA-256 values with
`bundle-provenance.json`; a different Console build needs fresh verification.

```sh
docker build -t flip-console-codex:acceptance deploy/console-controller
docker build -f deploy/console-controller/Dockerfile.egress -t flip-codex-egress:acceptance deploy/console-controller
```

The build context uses an allowlist: do not add auth files, environment files,
registries or runtime directories. The pinned Node base, Codex 0.166.0, official
bundles, Git and procps are required. Record the resulting immutable image IDs;
APT dependencies are not fully reproducible from the base digest alone.

Create a dedicated internal network `flip-codex-internal`. Run the trusted
egress image on both that network (alias `flip-codex-egress`) and an external
bridge. Publish no ports. Its only outbound proxy destinations are the listed
OpenAI HTTPS hosts; its fixed Switch relay targets `host.docker.internal:8080`.
Other hosts/platforms must explicitly configure and revalidate that relay.

## Per-assignment operator preparation

The operator must first validate the persisted human Engineering approval and
current immutable handoff with `ApprovalWorkflow.require_engineering`. Prepare
the authorized checkout through `EngineeringExecutor(..., session=None)`.
Do not manually alter the execution ledger, approval records or Console DB.

Use Switch's official managed-controller enrollment and agent APIs to establish
one controller and Codex agent dedicated to this assignment. Keep one-time
enrollment JSON, controller state and provider auth in separate private directories
outside Git. Copy only the provider's required authentication, never the user's
whole Codex home or GitHub credentials. Wait for controller online/provider
installed/authenticated before placement. Join only the test room and allow
messages from the Engineering registration.

The approved agent definition must pin `provider=codex`, its exact checkout,
`isolation=isolated`, `auto_approve=false`, and a first instructions line
`switchcrew-assignment:<32-hex-assignment-id>`. Prompt contents remain bounded by
the approved objective and correction findings. Warm/send is performed later by
the Engineering runtime after its durable execution claim, not by provisioning.

Run the provider container with:

- Nonroot UID 501, read-only root, cap-drop ALL, no-new-privileges, private IPC,
  no published ports, memory/CPU/PID limits, and a bounded `/tmp` tmpfs.
- Only `flip-codex-internal`, DNS `127.0.0.1`, HTTP(S)_PROXY
  `http://flip-codex-egress:3128`, and appropriate local NO_PROXY entries.
- Exactly three writable bind mounts: the assignment checkout at its identical
  absolute path, dedicated state at `/state`, isolated provider home at
  `/home/agent`. Set workdir to that checkout. Do not mount the Docker socket.
- No GitHub, human approval, application or owner credentials in its environment.

Create a mode-600 descriptor only after `ManagedConsoleCodex.validate(checkout)`
passes and `find(assignment_id)` confirms no unexpected prior model session.
Descriptor keys are `agent_id`, `agent_name`, `controller_id`, `revision`,
`room_id`, `container_name`, `network_name`, `image_id` (immutable sha256),
`docker_binary`, `daemon_socket`, `state_root`, `home_root`.

Set `job_templates.<job>.managed_console_directory` in the private Engineering
config. Its descriptor filename is `<assignment_id>.json`. Without it the worker
prepares the authorized checkout but stays `pending_execution` with reason
`awaiting_managed_console_provisioning`. Corrections require a fresh approved
assignment-specific descriptor. Do not reuse a different assignment's session.
If a creation/delivery has already been claimed, reconcile the original outcome;
do not reprovision or resend to escape uncertainty.

The current host used a private operator preparation script implementing those
steps. This directory preserves the tested build sources and setup contract;
it is not yet a tested fresh-machine automated installer. Worker registrations,
peer policies, organization Security policy, application database and scoped
Release credential still need the separate workflow configuration described in
[APPROVAL_RELEASE.md](../../docs/APPROVAL_RELEASE.md).
