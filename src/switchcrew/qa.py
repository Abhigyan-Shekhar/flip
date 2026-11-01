"""Independent revision-pinned validation; commands come only from the operator."""

import asyncio
import hashlib
import os
import signal
import subprocess
import tempfile
from pathlib import Path

from .engineering import assignment_id, git, outcome


class QAExecutor:
    def __init__(self, ledger, root, implementation_assignment, revision, commands, timeout=120):
        self.ledger, self.root = ledger, Path(root).resolve()
        self.implementation = implementation_assignment
        self.revision, self.commands, self.timeout = revision, commands, timeout

    def current(self):
        data = self.ledger.get(self.implementation)
        return (data or {}).get("result", {})

    def stale(self):
        current = self.current()
        if current.get("final_revision") != self.revision:
            return True
        try:
            return git(current["checkout"], "rev-parse", "HEAD") != self.revision
        except (OSError, subprocess.CalledProcessError):
            return True

    async def __call__(self, state):
        identity = "qa-" + assignment_id(state["handoff_id"])
        if (
            self.timeout <= 0
            or not self.commands
            or any(
                not isinstance(c, list) or not c or any(not isinstance(a, str) for a in c)
                for c in self.commands
            )
        ):
            return outcome("blocked_configuration", identity, reason="validation_commands_required")
        current = self.current()
        if current.get("status") != "implemented":
            return outcome("blocked_configuration", identity, reason="implementation_not_available")
        if self.stale():
            return outcome("stale_qa", identity, tested_revision=self.revision)
        data = self.ledger.reserve(
            identity,
            {
                "implementation": self.implementation,
                "revision": self.revision,
                "commands": self.commands,
                "timeout": self.timeout,
            },
        )
        if "result" in data:
            return data["result"]
        if not self.ledger.claim(identity, "reserved", "running"):
            return outcome(
                "blocked_configuration",
                identity,
                tested_revision=self.revision,
                reason="qa_interrupted_use_new_handoff_after_operator_reconciliation",
                commands=data.get("commands", []),
            )
        self.root.mkdir(parents=True, exist_ok=True)
        checkout = Path(tempfile.mkdtemp(prefix=identity + "-", dir=self.root))
        records = []
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--no-hardlinks",
                    "--no-checkout",
                    current["checkout"],
                    str(checkout),
                ],
                check=True,
                capture_output=True,
            )
            git(checkout, "checkout", "--detach", self.revision)
            if git(checkout, "rev-parse", "HEAD") != self.revision:
                raise ValueError("QA checkout revision mismatch")
            # No application/API credentials or caller PYTHONPATH are inherited.
            env = {"PATH": os.defpath, "HOME": str(checkout / ".qa-home"), "LANG": "C.UTF-8"}
            Path(env["HOME"]).mkdir()
            revision_unchanged = True
            for command in self.commands:
                self.ledger.put(
                    identity,
                    {
                        "phase": "running",
                        "commands": records,
                        "current_command": command,
                        "tested_revision": self.revision,
                    },
                )
                with tempfile.TemporaryFile() as output:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=checkout,
                        env=env,
                        stdout=output,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                    timed_out = False
                    try:
                        await asyncio.wait_for(process.wait(), self.timeout)
                    except (TimeoutError, asyncio.CancelledError) as exc:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                        timed_out = True
                    output.seek(0)
                    digest = hashlib.sha256()
                    size = 0
                    preview = b""
                    while chunk := output.read(65536):
                        digest.update(chunk)
                        size += len(chunk)
                        if len(preview) < 4096:
                            preview += chunk[: 4096 - len(preview)]
                records.append(
                    {
                        "command": command,
                        "exit_code": process.returncode,
                        "timed_out": timed_out,
                        "output_summary": preview.decode("utf-8", errors="replace"),
                        "output_bytes": size,
                        "output_sha256": digest.hexdigest(),
                        "revision": self.revision,
                    }
                )
                self.ledger.put(
                    identity,
                    {"phase": "running", "commands": records, "tested_revision": self.revision},
                )
                if git(checkout, "rev-parse", "HEAD") != self.revision or git(
                    checkout, "diff", "HEAD"
                ):
                    revision_unchanged = False
                    break
            clean = revision_unchanged and not git(checkout, "diff", "HEAD")
            status = (
                "verified_success"
                if clean and all(r["exit_code"] == 0 and not r["timed_out"] for r in records)
                else "qa_failed"
            )
            if self.stale():
                status = "stale_qa"
            result = outcome(
                status,
                identity,
                implementation_assignment=self.implementation,
                tested_revision=self.revision,
                commands=records,
                checkout=str(checkout),
            )
        except (OSError, ValueError, subprocess.CalledProcessError):
            result = outcome(
                "qa_failed",
                identity,
                tested_revision=self.revision,
                commands=records,
                reason="validation_launch_or_checkout_failed",
            )
        self.ledger.put(identity, {"phase": "finished", "result": result})
        return result
