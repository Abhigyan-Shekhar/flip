"""Independent revision-pinned validation; commands come only from the operator."""

import errno
import os
import subprocess
import tempfile
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from .engineering import assignment_id, git, outcome
from .execution import capture_command


def generated_inputs(checkout):
    """Reject new worktree files except known Python/pytest cache artifacts."""
    paths = set()
    for args in (
        ("--others", "--exclude-standard"),
        ("--others", "--ignored", "--exclude-standard"),
    ):
        paths.update(git(checkout, "ls-files", *args).splitlines())
    pytest_cache = {
        ".pytest_cache/.gitignore",
        ".pytest_cache/CACHEDIR.TAG",
        ".pytest_cache/README.md",
        ".pytest_cache/v/cache/nodeids",
        ".pytest_cache/v/cache/lastfailed",
        ".pytest_cache/v/cache/stepwise",
    }
    return [
        path
        for path in paths
        if path not in pytest_cache
        and not ("__pycache__" in Path(path).parts and path.endswith(".pyc"))
    ]


def failure_detail(stage, error, command_index):
    """Only fixed labels and bounded numeric codes may leave the worker.

    Exception messages, argv, paths, stderr and chained exceptions are private.
    Even an operator command or a custom exception can contain credentials.
    """
    detail = {"stage": stage, "category": "validation_error"}
    if command_index is not None:
        detail["command_index"] = command_index
    if isinstance(error, subprocess.CalledProcessError):
        detail["category"] = "subprocess_error"
        if type(error.returncode) is int and -255 <= error.returncode <= 255:
            detail["exit_code"] = error.returncode
    elif isinstance(error, OSError):
        detail["category"] = "os_error"
        if type(error.errno) is int and error.errno in errno.errorcode:
            detail["code"] = errno.errorcode[error.errno]
    return detail


class QAExecutor:
    def __init__(
        self,
        ledger,
        root,
        implementation_assignment,
        revision,
        commands,
        timeout=120,
        authorize=None,
        isolation=None,
    ):
        self.ledger, self.root = ledger, Path(root).resolve()
        self.implementation = implementation_assignment
        self.revision, self.commands, self.timeout = revision, commands, timeout
        self.authorize = authorize
        self.isolation = isolation

    def check_approval(self, state):
        if self.authorize is not None and self.authorize(state) is False:
            raise PermissionError("QA task authorization denied")

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
        try:
            self.check_approval(state)
        except PermissionError:
            return outcome("blocked", identity, reason="qa_task_no_longer_active")
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
        spec = {
            "implementation": self.implementation,
            "revision": self.revision,
            "commands": self.commands,
            "timeout": self.timeout,
            **({"isolation": asdict(self.isolation)} if self.isolation is not None else {}),
        }
        try:
            data = self.ledger.reserve(identity, spec)
        except ValueError:
            return outcome("blocked_configuration", identity, reason="qa_assignment_inputs_changed")
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
        checkout = self.root
        records = []
        stage, index = "prepare_run_directory", None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            run_dir = Path(tempfile.mkdtemp(prefix=identity + "-", dir=self.root))
            revision_unchanged = True
            for index, command in enumerate(self.commands):
                stage = "authorization"
                self.check_approval(state)
                stage = "clone"
                checkout = run_dir / f"command-{index}"
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
                stage = "checkout"
                git(checkout, "checkout", "--detach", self.revision)
                if git(checkout, "rev-parse", "HEAD") != self.revision:
                    raise ValueError("QA checkout revision mismatch")
                # HOME is outside the checkout and is fresh for each command.
                stage = "prepare_environment"
                home = run_dir / f"home-{index}"
                home.mkdir()
                env = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
                stage = "record_command_intent"
                self.ledger.put(
                    identity,
                    {
                        "phase": "running",
                        "commands": records,
                        "current_command": command,
                        "tested_revision": self.revision,
                    },
                )
                stage = "authorization"
                self.check_approval(state)
                stage = "isolation_setup"
                context = (
                    self.isolation.launch(command, checkout, writable=True)
                    if self.isolation is not None
                    else nullcontext(command)
                )
                with context as execution:
                    stage = "command_execution"
                    receipt = await capture_command(
                        execution,
                        cwd=checkout,
                        env=env,
                        timeout=self.timeout,
                        artifact=run_dir / f"command-{index}.log",
                    )
                    records.append(
                        {
                            **receipt,
                            "command": command,
                            "revision": self.revision,
                            "task_id": state["task_id"],
                            "sandbox_id": checkout.name,
                            "working_directory": "/workspace" if self.isolation else str(checkout),
                            "isolation": "docker" if self.isolation else "host_process",
                        }
                    )
                    stage = "record_command_result"
                    self.ledger.put(
                        identity,
                        {"phase": "running", "commands": records, "tested_revision": self.revision},
                    )
                    stage = "isolation_cleanup"
                stage = "verify_worktree"
                if (
                    git(checkout, "rev-parse", "HEAD") != self.revision
                    or git(checkout, "diff", "HEAD")
                    or generated_inputs(checkout)
                ):
                    revision_unchanged = False
                    break
            status = (
                "verified_success"
                if revision_unchanged
                and all(
                    r["exit_code"] == 0 and not r["timed_out"] and not r["output_limit_exceeded"]
                    for r in records
                )
                else "qa_failed"
            )
            stage = "authorization"
            self.check_approval(state)
            stage = "implementation_currency"
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
        except PermissionError as error:
            result = outcome(
                "blocked",
                identity,
                tested_revision=self.revision,
                commands=records,
                reason="qa_task_no_longer_active",
                failure_detail=failure_detail(stage, error, index),
            )
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            result = outcome(
                "qa_failed",
                identity,
                tested_revision=self.revision,
                commands=records,
                reason="validation_launch_or_checkout_failed",
                failure_detail=failure_detail(stage, error, index),
            )
        self.ledger.put(identity, {"phase": "finished", "result": result})
        return result
