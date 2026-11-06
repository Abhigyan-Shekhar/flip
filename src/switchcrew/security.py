"""Configured, isolated security scanner execution with honest blocked states."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import tarfile
import tempfile
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
    "unknown": "Unknown",
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
    version: str = "unknown"
    category: str = "unspecified"
    database_updated_at: str | None = None
    database_max_age_hours: float = 24


@dataclass(frozen=True)
class SecurityFinding:
    severity: str
    file: str
    component: str
    evidence: str
    explanation: str
    remediation: str
    verification_status: str
    finding_id: str = ""
    category: str = "unspecified"
    rule_id: str = ""
    line: int | None = None
    confidence: str = "unknown"


@dataclass(frozen=True)
class SecurityReport:
    status: str
    revision: str
    findings: tuple[SecurityFinding, ...] = ()
    scanners: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finding(raw: Any, scanner: ScannerConfig | None = None) -> SecurityFinding | None:
    if not isinstance(raw, dict):
        return None
    severity = _SEVERITIES.get(str(raw.get("severity", raw.get("Severity", ""))).lower())
    if severity is None:
        return None
    file = str(raw.get("file", raw.get("path", raw.get("Target", ""))))
    path = PurePosixPath(file.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        file = "[invalid-path]"
    rule = str(raw.get("rule_id", raw.get("VulnerabilityID", raw.get("RuleID", ""))))
    line = raw.get("line", raw.get("StartLine"))
    if line is not None and (type(line) is not int or line < 1):
        return None
    component = str(raw.get("component", raw.get("package", raw.get("PkgName", "unknown"))))
    category = str(raw.get("category", scanner.category if scanner else "unspecified"))
    identity = json.dumps(
        [scanner.name if scanner else "", rule, file, line, component], separators=(",", ":")
    )
    return SecurityFinding(
        finding_id="F-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
        category=_redact(category, 100),
        rule_id=_redact(rule, 300),
        line=line,
        confidence=_redact(raw.get("confidence", "unknown"), 100),
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
            raw.get(
                "explanation", raw.get("message", raw.get("Description", raw.get("Title", "")))
            ),
            1000,
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
    # Native Semgrep JSON. Errors and skipped rules must never become a clean verdict.
    if "results" in data and "version" in data:
        if not isinstance(data["results"], list) or data.get("errors") or data.get("skipped_rules"):
            return None
        items = []
        for result in data["results"]:
            if not isinstance(result, dict) or not isinstance(result.get("extra"), dict):
                return None
            extra = result["extra"]
            metadata = extra.get("metadata", {})
            start = result.get("start", {})
            if not isinstance(metadata, dict) or not isinstance(start, dict):
                return None
            items.append(
                {
                    "severity": extra.get("severity"),
                    "file": result.get("path", ""),
                    "rule_id": result.get("check_id", ""),
                    "line": start.get("line"),
                    "explanation": extra.get("message", ""),
                    "remediation": extra.get("fix", "Inspect the rule and affected code."),
                    "confidence": metadata.get("confidence", "unknown"),
                    "category": "source",
                }
            )
        return items
    # pip-audit does not supply severity. Preserve Unknown; the release gate blocks it.
    if "dependencies" in data:
        if not isinstance(data["dependencies"], list):
            return None
        items = []
        for dependency in data["dependencies"]:
            if (
                not isinstance(dependency, dict)
                or dependency.get("skip_reason")
                or not isinstance(dependency.get("vulns"), list)
            ):
                return None
            for vulnerability in dependency["vulns"]:
                if (
                    not isinstance(vulnerability, dict)
                    or not isinstance(vulnerability.get("id"), str)
                    or not isinstance(vulnerability.get("fix_versions", []), list)
                ):
                    return None
                items.append(
                    {
                        "severity": "unknown",
                        "category": "dependency",
                        "component": str(dependency.get("name", "unknown"))
                        + "@"
                        + str(dependency.get("version", "unknown")),
                        "rule_id": vulnerability["id"],
                        "explanation": vulnerability.get("description", ""),
                        "remediation": "Upgrade to: "
                        + ", ".join(map(str, vulnerability.get("fix_versions", []))),
                        "evidence": vulnerability["id"],
                    }
                )
        return items
    # Trivy omits Results entirely when a successful scan has no findings.
    # Require its native envelope whether Results is present or omitted.
    if not (
        data.get("SchemaVersion") == 2
        and data.get("ArtifactType") in {"filesystem", "repository", "container_image"}
        and isinstance(data.get("ArtifactName"), str)
        and bool(data["ArtifactName"])
        and isinstance(data.get("Trivy"), dict)
        and isinstance(data["Trivy"].get("Version"), str)
        and bool(data["Trivy"]["Version"])
    ):
        return None
    if "Results" not in data:
        return []
    results = data.get("Results")
    if isinstance(results, list):
        items = []
        for result in results:
            if not isinstance(result, dict):
                return None
            for key in ("Misconfigurations", "Vulnerabilities", "Secrets"):
                entries = result.get(key, [])
                if not isinstance(entries, list):
                    return None
                for entry in entries:
                    if not isinstance(entry, dict):
                        return None
                    items.append(
                        {
                            "file": result.get("Target", ""),
                            "category": {
                                "Misconfigurations": "configuration",
                                "Vulnerabilities": "dependency",
                                "Secrets": "secret",
                            }[key],
                            **entry,
                        }
                    )
        return items
    return None


class SecurityRunner:
    """Run operator configured argv only, in temporary source extracted at one SHA."""

    def __init__(
        self,
        scanners: list[ScannerConfig] | tuple[ScannerConfig, ...],
        *,
        isolation=None,
        artifact_dir: str | Path | None = None,
    ):
        self.scanners = tuple(scanners)
        self.isolation = isolation
        self.artifact_dir = Path(artifact_dir).resolve() if artifact_dir is not None else None

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
                or not isinstance(scanner.version, str)
                or not isinstance(scanner.category, str)
                or isinstance(scanner.database_max_age_hours, bool)
                or not isinstance(scanner.database_max_age_hours, (int, float))
                or not math.isfinite(scanner.database_max_age_hours)
                or scanner.database_max_age_hours <= 0
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
            if (
                self.isolation is None
                and not (Path(scanner.argv[0]).is_absolute() and Path(scanner.argv[0]).is_file())
                and not shutil.which(scanner.argv[0])
            ):
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
                                {
                                    "repo_root": "/workspace"
                                    if self.isolation is not None
                                    else checkout,
                                    "revision": revision,
                                }[match.group(1)]
                            ),
                            arg,
                        )
                        for arg in scanner.argv
                    ]
                    started = datetime.now(UTC)
                    clock = time.monotonic()
                    receipt = {
                        "name": _redact(scanner.name, 100),
                        "status": "running",
                        "version": _redact(scanner.version, 100),
                        "category": scanner.category,
                        "revision": revision,
                        "repository": _redact(str(root), 1000),
                        "argv": [_redact(a, 16000) for a in scanner.argv],
                        "started_at": started.isoformat(),
                        "database_updated_at": scanner.database_updated_at,
                        "isolation": "docker" if self.isolation is not None else "host",
                    }
                    scanner_statuses.append(receipt)
                    try:
                        status, stdout, warning = await self._execute(
                            command,
                            checkout,
                            temp,
                            scanner.timeout,
                            scanner.success_codes,
                            receipt=receipt,
                        )
                        receipt["status"] = status
                        receipt["output_sha256"] = hashlib.sha256(stdout).hexdigest()
                    except TimeoutError:
                        receipt.update(status="timed_out", timed_out=True)
                        raise
                    except asyncio.CancelledError:
                        receipt.update(status="cancelled", cancelled=True)
                        raise
                    except Exception:
                        receipt["status"] = "failed"
                        raise
                    finally:
                        receipt["finished_at"] = datetime.now(UTC).isoformat()
                        receipt["duration_seconds"] = round(time.monotonic() - clock, 3)
                    if self.artifact_dir is not None:
                        self.artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                        safe_output = _redact(stdout.decode("utf-8", errors="replace"), _MAX_OUTPUT)
                        artifact = self.artifact_dir / (
                            hashlib.sha256(
                                (revision + scanner.name + receipt["started_at"]).encode()
                            ).hexdigest()
                            + ".json.txt"
                        )
                        with artifact.open("x", encoding="utf-8") as stream:
                            artifact.chmod(0o600)
                            stream.write(safe_output)
                        receipt["artifact_path"] = str(artifact)
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
                    if isinstance(decoded, dict):
                        native_version = decoded.get("version")
                        if isinstance(decoded.get("Trivy"), dict):
                            native_version = decoded["Trivy"].get("Version")
                        if isinstance(native_version, str) and native_version:
                            receipt["version"] = _redact(native_version, 100)
                            receipt["version_source"] = "scanner_report"
                    dependency_report = False
                    if isinstance(decoded, dict):
                        results = decoded.get("Results", [])
                        dependency_report = "dependencies" in decoded or (
                            isinstance(results, list)
                            and any(
                                isinstance(result, dict)
                                and (
                                    "Vulnerabilities" in result
                                    or result.get("Class") in {"os-pkgs", "lang-pkgs"}
                                )
                                for result in results
                            )
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
                        normalized = _finding(item, scanner)
                        if normalized is None:
                            return SecurityReport(
                                "scanner_failed",
                                revision,
                                tuple(findings),
                                tuple(scanner_statuses),
                                ("Scanner output included an unrecognized finding.",),
                            )
                        findings.append(normalized)
                    if (
                        scanner.category == "dependency"
                        or dependency_report
                        or any(item.get("category") == "dependency" for item in raw_findings)
                    ):
                        try:
                            updated = datetime.fromisoformat(scanner.database_updated_at or "")
                            age = (started - updated).total_seconds() / 3600
                            if age < 0 or age > scanner.database_max_age_hours:
                                raise ValueError("stale database")
                        except (ValueError, TypeError):
                            receipt["status"] = "degraded"
                            return SecurityReport(
                                "degraded_coverage",
                                revision,
                                tuple(findings),
                                tuple(scanner_statuses),
                                ("Dependency database freshness is unverified.",),
                            )

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
            tuple(
                sorted(
                    findings,
                    key=lambda f: (
                        -{"Critical": 4, "High": 3, "Unknown": 3, "Medium": 2, "Low": 1}[
                            f.severity
                        ],
                        f.finding_id,
                    ),
                )
            ),
            tuple(scanner_statuses),
        )

    async def _execute(
        self,
        command: list[str],
        checkout: Path,
        temp: str,
        timeout: float,
        success_codes: tuple[int, ...],
        *,
        receipt: dict | None = None,
    ):
        home = Path(temp) / ("home-" + str(len(list(Path(temp).glob("home-*")))))
        home.mkdir()
        env = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
        context = (
            self.isolation.launch(command, checkout)
            if self.isolation is not None
            else nullcontext(command)
        )
        with context as execution:
            process = await asyncio.create_subprocess_exec(
                *execution,
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
        if receipt is not None:
            receipt.update(
                exit_code=process.returncode,
                output_exceeded=exceeded,
                display_logs=_redact(stderr.decode("utf-8", errors="replace"), 2000),
            )
        if exceeded or process.returncode not in success_codes:
            return "failed", stdout, "Scanner exited unsuccessfully or exceeded output limits."
        return "completed", stdout, ""
