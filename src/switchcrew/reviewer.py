"""Independent, revision-pinned review using operator supplied commands."""

import asyncio
import hashlib
import inspect
import json
import math
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .engineering import assignment_id, git, outcome
from .qa import generated_inputs

_MAX_OUTPUT = 256 * 1024
_MAX_MESSAGE = 1200
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)[\"']?\s*[:=]\s*[\"']?\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)


def _redact(value):
    return _SECRET.sub("[REDACTED]", value)[:4096]


class ReviewerExecutor:
    """Run configured review commands against an immutable engineering revision."""

    def __init__(
        self,
        ledger,
        root,
        implementation_assignment,
        revision,
        commands,
        timeout=120,
        authorize=None,
    ):
        self.ledger, self.root = ledger, Path(root).resolve()
        self.implementation = implementation_assignment
        self.revision, self.commands, self.timeout = revision, commands, timeout
        self.authorize = authorize

    def _approved(self, state):
        if self.authorize is not None:
            decision = self.authorize(state)
            if inspect.isawaitable(decision):
                if inspect.iscoroutine(decision):
                    decision.close()
                raise PermissionError("Review authorization must be synchronous")
            if decision is not None and decision is not True:
                raise PermissionError("Review authorization denied")

    def _engineering(self):
        return (self.ledger.get(self.implementation) or {}).get("result", {})

    def _current(self):
        result = self._engineering()
        if result.get("status") != "implemented" or result.get("final_revision") != self.revision:
            return None
        checkout = Path(result.get("checkout", "")).resolve()
        if (
            not checkout.is_dir()
            or git(checkout, "rev-parse", "HEAD") != self.revision
            or git(checkout, "status", "--porcelain", "--untracked-files=all")
        ):
            return None
        base = result.get("base_revision")
        if not base:
            return None
        base = git(checkout, "rev-parse", f"{base}^{{commit}}")
        git(checkout, "merge-base", "--is-ancestor", base, self.revision)
        diff = git(checkout, "diff", "--binary", base, self.revision).encode()
        persisted_diff = result.get("diff")
        if (
            not isinstance(persisted_diff, str)
            or hashlib.sha256(persisted_diff.encode()).digest() != hashlib.sha256(diff).digest()
        ):
            return None
        names = (
            subprocess.check_output(
                ["git", "-C", str(checkout), "diff", "--name-only", "-z", base, self.revision]
            )
            .decode()
            .split("\0")
        )
        files = [x for x in names if x]
        return result, checkout, base, diff, files

    async def __call__(self, state):
        identity = "review-" + assignment_id(state["handoff_id"])
        try:
            self._approved(state)
        except PermissionError:
            return outcome("blocked", identity, reason="review_task_no_longer_active")
        if (
            not isinstance(self.timeout, (int, float))
            or isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
            or not isinstance(self.commands, (list, tuple))
            or not self.commands
            or any(
                not isinstance(c, (list, tuple)) or not c or any(not isinstance(a, str) for a in c)
                for c in self.commands
            )
        ):
            return outcome("blocked_configuration", identity, reason="review_commands_required")
        try:
            verified = self._current()
        except (OSError, ValueError, subprocess.CalledProcessError):
            verified = None
        if verified is None:
            return outcome(
                "stale_review",
                identity,
                implementation_assignment=self.implementation,
                reviewed_revision=self.revision,
            )
        engineering, source, base, diff, files = verified
        diff_hash = hashlib.sha256(diff).hexdigest()
        spec = {
            "implementation": self.implementation,
            "revision": self.revision,
            "base": base,
            "commands": self.commands,
            "timeout": self.timeout,
        }
        try:
            data = self.ledger.reserve(identity, spec)
        except ValueError:
            return outcome(
                "blocked_configuration", identity, reason="review_assignment_inputs_changed"
            )
        if "result" in data:
            try:
                self._approved(state)
                again = self._current()
            except PermissionError:
                return outcome("blocked", identity, reason="review_task_no_longer_active")
            except (OSError, ValueError, subprocess.CalledProcessError):
                again = None
            if again is None or hashlib.sha256(again[3]).hexdigest() != diff_hash:
                return outcome(
                    "stale_review",
                    identity,
                    implementation_assignment=self.implementation,
                    reviewed_revision=self.revision,
                )
            return data["result"]
        if data.get("phase") != "reserved" or not self.ledger.claim(
            identity, "reserved", "running"
        ):
            return outcome(
                "pending_execution",
                identity,
                implementation_assignment=self.implementation,
                reviewed_revision=self.revision,
                reason="review_interrupted_operator_reconciliation_required",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix=identity + "-", dir=self.root))
        records, findings, command_findings, rejected = [], [], [], False
        try:
            for i, raw_command in enumerate(self.commands):
                self._approved(state)
                command = [
                    part.replace("{base_revision}", base).replace("{revision}", self.revision)
                    for part in raw_command
                ]
                checkout = run_dir / f"command-{i}"
                subprocess.run(
                    ["git", "clone", "--no-hardlinks", "--no-checkout", str(source), str(checkout)],
                    check=True,
                    capture_output=True,
                )
                git(checkout, "checkout", "--detach", self.revision)
                home = run_dir / f"home-{i}"
                home.mkdir()
                env = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
                self.ledger.put(
                    identity, {"phase": "running", "commands": records, "current_command": i}
                )
                with tempfile.TemporaryFile() as output:
                    self._approved(state)
                    proc = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=checkout,
                        env=env,
                        stdout=output,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                    timed_out = False
                    try:
                        await asyncio.wait_for(proc.wait(), self.timeout)
                    except (TimeoutError, asyncio.CancelledError) as exc:
                        os.killpg(proc.pid, signal.SIGKILL)
                        await proc.wait()
                        if isinstance(exc, asyncio.CancelledError):
                            self.ledger.put(
                                identity,
                                {"phase": "uncertain", "commands": records, "current_command": i},
                            )
                            raise
                        timed_out = True
                    output.seek(0)
                    digest_builder = hashlib.sha256()
                    size = 0
                    captured = bytearray()
                    while chunk := output.read(65536):
                        digest_builder.update(chunk)
                        size += len(chunk)
                        if len(captured) < _MAX_OUTPUT + 1:
                            captured.extend(chunk[: _MAX_OUTPUT + 1 - len(captured)])
                raw = bytes(captured)
                digest = digest_builder.hexdigest()
                too_large = size > _MAX_OUTPUT
                preview = _redact(raw[:_MAX_OUTPUT].decode("utf-8", errors="replace"))
                rec = {
                    "command": command,
                    "revision": self.revision,
                    "exit_code": proc.returncode,
                    "timed_out": timed_out,
                    "output_sha256": digest,
                    "output_summary": preview,
                }
                records.append(rec)
                if proc.returncode != 0 or timed_out:
                    rejected = True
                    command_findings.append(
                        {
                            "command_index": i,
                            "message": (
                                f"Review command failed (exit={proc.returncode}, "
                                f"timeout={timed_out}): {preview[:500]}"
                            ),
                        }
                    )
                    continue
                try:
                    doc = json.loads(raw.decode("utf-8")) if not too_large else None
                    if (
                        not isinstance(doc, dict)
                        or not isinstance(doc.get("accepted"), bool)
                        or not isinstance(doc.get("findings"), list)
                    ):
                        raise ValueError
                    if len(doc["findings"]) > 100 or (
                        doc["accepted"] is False and not doc["findings"]
                    ):
                        raise ValueError
                    for finding in doc["findings"]:
                        path = finding.get("path") if isinstance(finding, dict) else None
                        pure = PurePosixPath(path) if isinstance(path, str) else None
                        if (
                            not isinstance(finding, dict)
                            or path not in files
                            or pure is None
                            or pure.is_absolute()
                            or ".." in pure.parts
                            or any(
                                part.startswith(".")
                                or part.lower()
                                in {".env", ".switch", ".ssh", ".aws", "credentials", "secrets"}
                                for part in pure.parts
                            )
                            or (checkout / path).is_symlink()
                            or not isinstance(finding.get("line"), int)
                            or isinstance(finding.get("line"), bool)
                            or finding["line"] < 1
                            or not isinstance(finding.get("message"), str)
                            or not finding["message"].strip()
                            or len(finding["message"]) > _MAX_MESSAGE
                            or (
                                "severity" in finding
                                and (
                                    not isinstance(finding["severity"], str)
                                    or len(finding["severity"]) > 32
                                )
                            )
                        ):
                            raise ValueError
                        item = {
                            "path": finding["path"],
                            "line": finding["line"],
                            "message": _redact(finding["message"]),
                            **({"severity": finding["severity"]} if "severity" in finding else {}),
                        }
                        findings.append(item)
                    if bool(doc["findings"]) and doc["accepted"]:
                        raise ValueError
                    rejected |= not doc["accepted"] or bool(doc["findings"])
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self.ledger.put(
                        identity,
                        {
                            "phase": "finished",
                            "result": outcome(
                                "blocked_configuration",
                                identity,
                                implementation_assignment=self.implementation,
                                reviewed_revision=self.revision,
                                base_revision=base,
                                diff_sha256=diff_hash,
                                files_changed=files,
                                findings=findings,
                                commands=records,
                                reason="review_command_output_invalid",
                            ),
                        },
                    )
                    return self.ledger.get(identity)["result"]
                if (
                    git(checkout, "rev-parse", "HEAD") != self.revision
                    or git(checkout, "diff", "HEAD")
                    or git(checkout, "status", "--porcelain")
                    or generated_inputs(checkout)
                ):
                    rejected = True
                    command_findings.append(
                        {
                            "command_index": i,
                            "message": "Review command modified its isolated checkout",
                        }
                    )
            self._approved(state)
            fresh = self._current()
            if fresh is None or hashlib.sha256(fresh[3]).hexdigest() != diff_hash:
                status = "stale_review"
            else:
                status = "review_rejected" if rejected else "review_accepted"
            result = outcome(
                status,
                identity,
                implementation_assignment=self.implementation,
                reviewed_revision=self.revision,
                base_revision=base,
                diff_sha256=diff_hash,
                files_changed=files,
                findings=findings,
                command_findings=command_findings,
                commands=records,
            )
        except PermissionError:
            result = outcome(
                "blocked",
                identity,
                implementation_assignment=self.implementation,
                reviewed_revision=self.revision,
                base_revision=base,
                diff_sha256=diff_hash,
                files_changed=files,
                findings=findings,
                commands=records,
                reason="review_task_no_longer_active",
            )
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, subprocess.CalledProcessError):
            result = outcome(
                "review_rejected",
                identity,
                implementation_assignment=self.implementation,
                reviewed_revision=self.revision,
                base_revision=base,
                diff_sha256=diff_hash,
                files_changed=files,
                findings=findings,
                commands=records,
                reason="review_checkout_or_launch_failed",
            )
        self.ledger.put(identity, {"phase": "finished", "result": result})
        return result
