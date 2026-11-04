"""Configured, isolated security scanner execution with honest blocked states."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from .change_intelligence import _bounded_communicate, _git, _revision

_MAX_OUTPUT = 2_000_000
_MAX_FINDINGS = 500
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)[\"']?\s*[:=]\s*[\"']?\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)
_SEVERITIES = {
    "critical": "Critical",
    "crit": "Critical",
    "high": "High",
    "error": "High",
    "medium": "Medium",
    "moderate": "Medium",
    "warning": "Medium",
    "low": "Low",
    "info": "Low",
    "informational": "Low",
}
_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


def _redact(value: Any, limit: int = 1200) -> str:
    return _SECRET.sub("[REDACTED]", str(value))[:limit]


@dataclass(frozen=True)
class ScannerConfig:
    name: str
    argv: tuple[str, ...]
    timeout: float = 120
    success_codes: tuple[int, ...] = (0,)


@dataclass(frozen=True)
class SecurityFinding:
    severity: str
    file: str
    component: str
    evidence: str
    explanation: str
    remediation: str
    verification_status: str


@dataclass(frozen=True)
class SecurityReport:
    status: str
    revision: str
    findings: tuple[SecurityFinding, ...] = ()
    scanners: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finding(raw: Any) -> SecurityFinding | None:
    if not isinstance(raw, dict):
        return None
    severity = _SEVERITIES.get(str(raw.get("severity", "")).lower())
    if severity is None:
        return None
    file = str(raw.get("file", raw.get("path", raw.get("Target", ""))))
    path = PurePosixPath(file.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        file = "[invalid-path]"
    return SecurityFinding(
        severity=severity,
        file=_redact(file, 500),
        component=_redact(
            raw.get(
                "component", raw.get("package", raw.get("PkgName", raw.get("RuleID", "unknown")))
            ),
            300,
        ),
        evidence=_redact(
            raw.get(
                "evidence",
                raw.get("matched_text", raw.get("VulnerabilityID", raw.get("Match", ""))),
            ),
            1000,
        ),
        explanation=_redact(
            raw.get("explanation", raw.get("message", raw.get("Description", ""))), 1000
        ),
        remediation=_redact(
            raw.get("remediation", raw.get("recommendation", raw.get("FixedVersion", ""))),
            1000,
        ),
        verification_status=_redact(raw.get("verification_status", "unverified"), 100),
    )


def _extract_findings(data: Any) -> list[Any] | None:
    """Accept only the documented normalized JSON contract or common scanner arrays."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("findings"), list):
        return data["findings"]
    if isinstance(data.get("issues"), list):
        return data["issues"]
    results = data.get("Results")
    if isinstance(results, list):
        items = []
        for result in results:
            if isinstance(result, dict):
                items.extend(result.get("Misconfigurations", []))
                items.extend(result.get("Vulnerabilities", []))
                items.extend(result.get("Secrets", []))
        return items
    return None


class SecurityRunner:
    """Run operator configured argv only, in temporary source extracted at one SHA."""

    def __init__(self, scanners: list[ScannerConfig] | tuple[ScannerConfig, ...]):
        self.scanners = tuple(scanners)

    async def run(self, repo_root: str | Path, revision: str) -> SecurityReport:
        root = Path(repo_root).resolve()
        if (
            not root.is_dir()
            or not _revision(revision)
            or not self.scanners
            or len(self.scanners) > 20
        ):
            return SecurityReport(
                "blocked_configuration",
                revision,
                warnings=("Valid repo, full revision, and configured scanners are required.",),
            )
        try:
            resolved = (
                (await _git(str(root), "rev-parse", f"{revision}^{{commit}}")).decode().strip()
            )
        except Exception:
            return SecurityReport(
                "blocked_configuration",
                revision,
                warnings=("Requested revision is not available in the configured repository.",),
            )
        if resolved != revision.lower():
            return SecurityReport(
                "blocked_configuration",
                revision,
                warnings=("Requested revision did not resolve exactly.",),
            )
        for scanner in self.scanners:
            if (
                not scanner.name
                or not scanner.argv
                or not isinstance(scanner.timeout, (int, float))
                or isinstance(scanner.timeout, bool)
                or not math.isfinite(scanner.timeout)
                or scanner.timeout <= 0
                or not scanner.success_codes
                or any(
                    not isinstance(code, int) or isinstance(code, bool)
                    for code in scanner.success_codes
                )
                or any(not isinstance(arg, str) or "\x00" in arg for arg in scanner.argv)
                or any(
                    key not in {"repo_root", "revision"}
                    for arg in scanner.argv
                    for key in _PLACEHOLDER.findall(arg)
                )
            ):
                return SecurityReport(
                    "blocked_configuration",
                    revision,
                    warnings=("Scanner configuration is invalid.",),
                )
            if not (
                Path(scanner.argv[0]).is_absolute() and Path(scanner.argv[0]).is_file()
            ) and not shutil.which(scanner.argv[0]):
                return SecurityReport(
                    "blocked_configuration",
                    revision,
                    warnings=(f"Configured scanner unavailable: {_redact(scanner.name, 100)}.",),
                )

        findings: list[SecurityFinding] = []
        scanner_statuses: list[dict[str, Any]] = []
        try:
            with tempfile.TemporaryDirectory(prefix="switchcrew-security-") as temp:
                archive = await _git(
                    str(root),
                    "archive",
                    "--format=tar",
                    revision,
                    timeout=60,
                    max_bytes=64 * 1024 * 1024,
                )
                checkout = Path(temp) / "checkout"
                checkout.mkdir()
                with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tar:
                    for member in tar.getmembers():
                        member_path = PurePosixPath(member.name)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            return SecurityReport(
                                "scanner_failed",
                                revision,
                                warnings=("Git archive contained an unsafe path.",),
                            )
                    tar.extractall(checkout, filter="data")
                for scanner in self.scanners:
                    command = [
                        _PLACEHOLDER.sub(
                            lambda match: str(
                                {"repo_root": checkout, "revision": revision}[match.group(1)]
                            ),
                            arg,
                        )
                        for arg in scanner.argv
                    ]
                    status, stdout, warning = await self._execute(
                        command, checkout, temp, scanner.timeout, scanner.success_codes
                    )
                    scanner_statuses.append({"name": _redact(scanner.name, 100), "status": status})
                    if status != "completed":
                        return SecurityReport(
                            "scanner_failed",
                            revision,
                            tuple(findings),
                            tuple(scanner_statuses),
                            (warning,),
                        )
                    try:
                        decoded = json.loads(stdout)
                    except (ValueError, TypeError):
                        return SecurityReport(
                            "scanner_failed",
                            revision,
                            tuple(findings),
                            tuple(scanner_statuses),
                            ("Scanner output was not valid JSON.",),
                        )
                    raw_findings = _extract_findings(decoded)
                    if raw_findings is None:
                        return SecurityReport(
                            "scanner_failed",
                            revision,
                            tuple(findings),
                            tuple(scanner_statuses),
                            ("Scanner output did not match the supported findings schema.",),
                        )
                    if len(raw_findings) > _MAX_FINDINGS:
                        return SecurityReport(
                            "scanner_failed",
                            revision,
                            tuple(findings),
                            tuple(scanner_statuses),
                            ("Scanner findings exceeded the report limit.",),
                        )
                    for item in raw_findings:
                        normalized = _finding(item)
                        if normalized is None:
                            return SecurityReport(
                                "scanner_failed",
                                revision,
                                tuple(findings),
                                tuple(scanner_statuses),
                                ("Scanner output included an unrecognized finding.",),
                            )
                        findings.append(normalized)
        except TimeoutError:
            return SecurityReport(
                "scanner_failed",
                revision,
                tuple(findings),
                tuple(scanner_statuses),
                ("Configured scanner timed out.",),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return SecurityReport(
                "scanner_failed",
                revision,
                tuple(findings),
                tuple(scanner_statuses),
                ("Security scan could not be completed.",),
            )
        return SecurityReport(
            "findings" if findings else "verified_success",
            revision,
            tuple(findings),
            tuple(scanner_statuses),
        )

    async def _execute(
        self,
        command: list[str],
        checkout: Path,
        temp: str,
        timeout: float,
        success_codes: tuple[int, ...],
    ):
        home = Path(temp) / ("home-" + str(len(list(Path(temp).glob("home-*")))))
        home.mkdir()
        env = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=checkout,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr, exceeded = await _bounded_communicate(
                process, timeout=timeout, limit=_MAX_OUTPUT
            )
        except (TimeoutError, asyncio.CancelledError):
            raise
        if exceeded or process.returncode not in success_codes:
            return "failed", b"", "Scanner exited unsuccessfully or exceeded output limits."
        return "completed", stdout, ""
