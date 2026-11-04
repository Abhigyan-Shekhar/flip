# Restricted execution for Engineering-related workflows

**Container runner and QA/Reviewer components verified; full managed-host and
end-to-end acceptance pending.** The [November 4 component run](VERIFICATION_2026-11-04.md)
checked read-only mounts, network denial, secret isolation, timeout cleanup and
exact-SHA QA/review using operator-prepared Git artifacts. It does not establish
Console host isolation or meaningful Security scanner acceptance. See the
[Mac-specific Docker configuration](LOCAL_MAC_RUNTIME.md).

The `DockerIsolation` runner is a strict optional policy for operator-owned QA,
Reviewer and Security commands. It is deliberately separate from Switch
Console's managed Codex sessions, which require an **independently isolated
host/VM**; this repository cannot prove Console's OS containment from its API.

## Configure the privileged workers

In the **private Engineering/QA/Reviewer integration config**, set
`"require_isolation": true` and add a container entry to each job template.
The same pinned image must provide the operator-configured QA and review binaries:

```json
{
  "require_isolation": true,
  "job_templates": {
    "demo": {
      "commands": [["python", "-m", "pytest", "-q"]],
      "review_commands": [["python", "-m", "pytest", "-q"]],
      "container": {
        "image": "registry.example/test-runner@sha256:REPLACE_WITH_64_HEX_DIGEST",
        "memory_mb": 768,
        "pids_limit": 128,
        "cpus": 1.0
      }
    }
  }
}
```

This is a **partial config example**, not a standalone integration worker
configuration. Retain the existing private registration, checkout, control
token, approval policy, identity and ledger fields. The image must be pulled
and verified by the operator in advance; the runner uses `--pull=never`.

For a **rootless Docker worker**, additionally set `"rootless": true` and
`"daemon_socket": "/run/user/<worker-uid>/docker.sock"` on each `container`
object (replace the placeholder with the actual worker UID). The runner
connects to that exact Unix socket and confirms the daemon reports rootless
mode. The container runs as UID 0 *inside its user namespace*, which maps to
the unprivileged worker UID on the host; rootful Docker continues to use an
unprivileged container UID. Do not set `rootless=true` for a rootful daemon.

Changing a pinned image or isolation setting invalidates cached QA and review
evidence for that handoff; create a fresh handoff before retrying. Every
container receives a unique temporary name and is forcibly reaped on command
failure, timeout or cancellation. Cleanup failures make the job fail closed.

For independent Security scanner capabilities (`triggered_security` and
`engineering_security_gate`), put the same `container` object and
`"require_isolation": true` inside the private `capability` section. Scanner
commands must be installed inside the image and emit the existing normalized
JSON findings contract. Offline vulnerability databases must already exist in
the pinned image: network access is disabled inside the container.

The restricted runner supplies:

- No container networking, dropped capabilities, `no-new-privileges`, read-only
  root filesystem, private IPC, pinned image digest and bounded CPU/PID/memory.
- Dedicated temporary `/tmp`, no extra mounts, no Docker socket and no inherited
  Discord, Switch, GitHub, approval or provider tokens.
- One temporary checkout as `/workspace`. Security scans get a **read-only**
  checkout; QA/review get a **writable disposable clone** and verify it was not
  changed after the command.
- Unavailable Docker/image or invalid isolation config is a failed/blocked
  result, never a successful security verdict.

**Important:** The host's Docker daemon can be privileged, and Docker flags
alone are not a complete defense. Use rootless Docker on a dedicated VM or
similar hardened worker, a reviewed image, explicit host egress policy, and no
secrets or trusted repositories accessible to untrusted code. A worker running
as root intentionally maps to an unprivileged container UID; this can cause
access failures in the disposable checkout and must be tested on the host.

## GitHub branch and scanner policy still needed

On the private target repository, configure branch protection/rulesets to
require a separate Reviewer, successful QA and Security checks, fresh approvals
after new commits, and a human Release approval. Do not give the generated
Codex agent a token capable of bypassing these rules. The application already
records SHA-pinned internal gates, but **these cannot replace GitHub branch
rules**. Set the project security policy with an explicit `block_at_or_above`
threshold (`high` recommended for the test repository) and allowlisted checkout
roots; it cannot be silently weakened after registration.

Acceptance still requires a real isolated Console agent, independently verified
scanner versions/signatures, a benign intentional finding test, and a complete
approved draft PR with durable task, QA, review and release receipts.
