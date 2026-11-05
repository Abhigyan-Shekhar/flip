import json
import subprocess
import sys

import pytest

from switchcrew.security import ScannerConfig, SecurityRunner


def trivy_envelope():
    return {
        "SchemaVersion": 2,
        "ArtifactName": "/workspace",
        "ArtifactType": "filesystem",
        "Trivy": {"Version": "0.76.1"},
    }


def committed_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    (path / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    return path.resolve(), subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


@pytest.mark.asyncio
async def test_scanner_runs_on_isolated_exact_sha_and_reports_clean_only_for_valid_output(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    code = (
        "import json,pathlib,sys; "
        "findings=[] if pathlib.Path('app.py').read_text() == \"print('hello')\\n\" else "
        "[{'severity':'critical','file':'app.py'}]; "
        "print(json.dumps({'findings':findings,'revision':sys.argv[1]}))"
    )
    report = await SecurityRunner(
        [ScannerConfig("fixture", (sys.executable, "-c", code, "${revision}"))]
    ).run(root, revision)
    assert report.status == "verified_success"
    assert report.revision == revision
    assert report.scanners[0]["name"] == "fixture"
    assert report.scanners[0]["status"] == "completed"
    assert report.scanners[0]["exit_code"] == 0
    assert (
        subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True) == ""
    )


@pytest.mark.asyncio
async def test_findings_are_structured_normalized_and_redacted(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    output = json.dumps(
        {
            "findings": [
                {
                    "severity": "critical",
                    "file": "app.py",
                    "component": "app",
                    "evidence": "password=do-not-leak",
                    "explanation": "Credential exposure",
                    "remediation": "Rotate the credential",
                }
            ]
        }
    )
    code = "import json; print(json.dumps(json.loads(" + repr(output) + ")))"
    report = await SecurityRunner([ScannerConfig("fixture", (sys.executable, "-c", code))]).run(
        root, revision
    )
    assert report.status == "findings"
    assert report.findings[0].severity == "Critical"
    assert report.findings[0].verification_status == "unverified"
    assert "do-not-leak" not in str(report.to_dict())
    assert "REDACTED" in report.findings[0].evidence


@pytest.mark.asyncio
async def test_operator_can_configure_scanner_finding_exit_code(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    payload = json.dumps({"findings": [{"severity": "high", "file": "app.py"}]})
    code = "import json,sys; print(json.dumps(json.loads(" + repr(payload) + "))); sys.exit(1)"
    report = await SecurityRunner(
        [ScannerConfig("scanner", (sys.executable, "-c", code), success_codes=(0, 1))]
    ).run(root, revision)
    assert report.status == "findings"
    assert report.findings[0].severity == "High"


@pytest.mark.asyncio
async def test_trivy_native_finding_preserves_severity_file_and_redaction(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    payload = {
        **trivy_envelope(),
        "Results": [
            {
                "Target": "app.py",
                "Secrets": [
                    {
                        "RuleID": "github-pat",
                        "Severity": "CRITICAL",
                        "Title": "GitHub Personal Access Token",
                        "Match": "ghp_" + "a" * 36,
                    }
                ],
            }
        ],
    }
    code = "print(" + repr(json.dumps(payload)) + ")"
    report = await SecurityRunner(
        [ScannerConfig("trivy-contract", (sys.executable, "-c", code))]
    ).run(root, revision)
    assert report.status == "findings"
    finding = report.findings[0]
    assert finding.severity == "Critical"
    assert finding.file == "app.py"
    assert finding.component == "github-pat"
    assert finding.explanation == "GitHub Personal Access Token"
    assert "a" * 36 not in str(report.to_dict())
    assert "REDACTED" in finding.evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"Secrets": {}}, {"Secrets": [None]}])
async def test_malformed_trivy_results_fail_closed(tmp_path, result):
    root, revision = committed_repo(tmp_path / "repo")
    code = "print(" + repr(json.dumps({**trivy_envelope(), "Results": [result]})) + ")"
    report = await SecurityRunner(
        [ScannerConfig("trivy-contract", (sys.executable, "-c", code))]
    ).run(root, revision)
    assert report.status == "scanner_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("native_envelope", [True, False])
async def test_clean_trivy_report_requires_native_envelope_when_results_omitted(
    tmp_path, native_envelope
):
    root, revision = committed_repo(tmp_path / "repo")
    payload = (
        {
            "SchemaVersion": 2,
            "ArtifactName": "/workspace",
            "ArtifactType": "filesystem",
            "Trivy": {"Version": "0.76.1"},
        }
        if native_envelope
        else {}
    )
    code = "print(" + repr(json.dumps(payload)) + ")"
    report = await SecurityRunner(
        [ScannerConfig("trivy-contract", (sys.executable, "-c", code))]
    ).run(root, revision)
    assert report.status == ("verified_success" if native_envelope else "scanner_failed")


@pytest.mark.asyncio
async def test_missing_scanner_and_unconfigured_scanners_are_blocked(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    no_scanners = await SecurityRunner([]).run(root, revision)
    assert no_scanners.status == "blocked_configuration"
    missing = await SecurityRunner(
        [ScannerConfig("missing", (str(tmp_path / "missing-scanner"),))]
    ).run(root, revision)
    assert missing.status == "blocked_configuration"
    assert "unavailable" in missing.warnings[0]


@pytest.mark.asyncio
async def test_timeout_malformed_output_and_missing_revision_never_report_clean(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    sleepy = (sys.executable, "-c", "import time; time.sleep(5)")
    timed = await SecurityRunner([ScannerConfig("slow", sleepy, timeout=0.02)]).run(root, revision)
    assert timed.status == "scanner_failed"
    assert "timed out" in timed.warnings[0]
    malformed = await SecurityRunner(
        [ScannerConfig("bad-json", (sys.executable, "-c", "print('not json')"))]
    ).run(root, revision)
    assert malformed.status == "scanner_failed"
    missing_sha = await SecurityRunner(
        [ScannerConfig("fixture", (sys.executable, "-c", "print('{}')"))]
    ).run(root, "f" * 40)
    assert missing_sha.status == "blocked_configuration"


@pytest.mark.asyncio
async def test_excessive_scanner_output_is_bounded_and_blocked(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    code = "print('x' * 4000000); print('{\\\"findings\\\": []}')"
    report = await SecurityRunner([ScannerConfig("noisy", (sys.executable, "-c", code))]).run(
        root, revision
    )
    assert report.status == "scanner_failed"


async def test_repository_snapshot_has_separate_bound_from_rendered_git_diff(tmp_path):
    root, _ = committed_repo(tmp_path / "repo")
    (root / "large.txt").write_text("a" * (1024 * 1024))
    subprocess.run(["git", "-C", str(root), "add", "large.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@example.test",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "large source",
        ],
        check=True,
        capture_output=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    runner = SecurityRunner(
        [
            ScannerConfig(
                "controlled-size-probe",
                (
                    sys.executable,
                    "-c",
                    "import json,pathlib; "
                    "assert pathlib.Path('large.txt').stat().st_size==1048576; "
                    "print(json.dumps({'findings': []}))",
                ),
            )
        ]
    )
    result = await runner.run(root, revision)
    assert result.status == "verified_success"
    assert result.revision == revision


async def test_native_semgrep_finding_ids_are_stable_ranked_and_preserve_rule_lines(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")

    def entry(rule, severity, line):
        return {
            "check_id": rule,
            "path": "app.py",
            "start": {"line": line},
            "extra": {
                "severity": severity,
                "message": "Inspect this call",
                "metadata": {"confidence": "HIGH"},
            },
        }

    entries = [entry("low-rule", "INFO", 1), entry("high-rule", "ERROR", 2)]

    async def scan(items):
        payload = {"results": items, "version": "1.1.0", "errors": [], "skipped_rules": []}
        return await SecurityRunner(
            [
                ScannerConfig(
                    "semgrep", (sys.executable, "-c", "print(" + repr(json.dumps(payload)) + ")")
                )
            ]
        ).run(root, revision)

    first, second = await scan(entries), await scan(list(reversed(entries)))
    assert first.status == "findings"
    assert first.findings == second.findings
    assert first.findings[0].rule_id == "high-rule"
    assert first.findings[0].line == 2
    assert first.findings[0].category == "source"
    assert first.findings[0].finding_id.startswith("F-")
    assert first.scanners[0]["version"] == "1.1.0"


@pytest.mark.parametrize(
    "extra", [{"errors": [{"message": "rule failed"}]}, {"skipped_rules": ["missing-rule"]}]
)
async def test_partial_semgrep_scan_never_reports_clean(tmp_path, extra):
    root, revision = committed_repo(tmp_path / "repo")
    payload = {"results": [], "version": "1", **extra}
    report = await SecurityRunner(
        [
            ScannerConfig(
                "semgrep", (sys.executable, "-c", "print(" + repr(json.dumps(payload)) + ")")
            )
        ]
    ).run(root, revision)
    assert report.status == "scanner_failed"


async def test_dependency_findings_keep_unknown_severity_and_require_fresh_database(tmp_path):
    from datetime import UTC, datetime

    root, revision = committed_repo(tmp_path / "repo")
    payload = {
        "dependencies": [
            {
                "name": "example",
                "version": "1.0",
                "vulns": [
                    {
                        "id": "PYSEC-EXAMPLE",
                        "description": "Controlled advisory",
                        "fix_versions": ["2"],
                    }
                ],
            }
        ]
    }
    argv = (sys.executable, "-c", "print(" + repr(json.dumps(payload)) + ")")
    stale = await SecurityRunner([ScannerConfig("pip-audit", argv)]).run(root, revision)
    assert stale.status == "degraded_coverage"
    assert stale.findings[0].severity == "Unknown"
    assert stale.findings[0].component == "example@1.0"
    fresh = await SecurityRunner(
        [ScannerConfig("pip-audit", argv, database_updated_at=datetime.now(UTC).isoformat())]
    ).run(root, revision)
    assert fresh.status == "findings"
    assert fresh.findings[0].rule_id == "PYSEC-EXAMPLE"


async def test_scan_artifact_is_bounded_redacted_and_private(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    code = 'print(\'{"findings": [], "note": "password=hidden-value"}\')'
    report = await SecurityRunner(
        [ScannerConfig("fixture", (sys.executable, "-c", code))],
        artifact_dir=tmp_path / "artifacts",
    ).run(root, revision)
    from pathlib import Path

    artifact = Path(report.scanners[0]["artifact_path"])
    assert "hidden-value" not in artifact.read_text()
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert report.scanners[0]["exit_code"] == 0
    assert report.scanners[0]["revision"] == revision
    assert len(report.scanners[0]["output_sha256"]) == 64


async def test_timeout_records_failed_scanner_receipt(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    report = await SecurityRunner(
        [
            ScannerConfig(
                "sleep", (sys.executable, "-c", "import time; time.sleep(10)"), timeout=0.01
            )
        ]
    ).run(root, revision)
    assert report.status == "scanner_failed"
    assert report.scanners[0]["status"] == "timed_out"
    assert report.scanners[0]["timed_out"] is True
    assert report.scanners[0]["finished_at"]


async def test_clean_native_dependency_report_still_requires_database_freshness(tmp_path):
    root, revision = committed_repo(tmp_path / "repo")
    payload = {"dependencies": [{"name": "example", "version": "1", "vulns": []}]}
    report = await SecurityRunner(
        [
            ScannerConfig(
                "pip-audit", (sys.executable, "-c", "print(" + repr(json.dumps(payload)) + ")")
            )
        ]
    ).run(root, revision)
    assert report.status == "degraded_coverage"
    assert report.scanners[0]["status"] == "degraded"


@pytest.mark.parametrize("results", ["omitted", [], [{}]])
@pytest.mark.parametrize(
    "field,value",
    [
        ("SchemaVersion", None), ("SchemaVersion", 1), ("SchemaVersion", 2.0),
        ("ArtifactType", None), ("ArtifactType", []), ("ArtifactType", "unknown"),
        ("ArtifactName", None), ("ArtifactName", ""), ("ArtifactName", "  "),
        ("Trivy", None), ("Trivy", {}), ("Trivy", {"Version": 1}),
        ("Trivy", {"Version": ""}), ("Trivy", {"Version": "  "}),
    ],
)
async def test_incomplete_trivy_envelope_fails_closed(tmp_path, results, field, value):
    root, revision = committed_repo(tmp_path / "repo")
    payload = trivy_envelope()
    if value is None:
        del payload[field]
    else:
        payload[field] = value
    if results != "omitted":
        payload["Results"] = results
    code = "print(" + repr(json.dumps(payload)) + ")"
    report = await SecurityRunner(
        [ScannerConfig("trivy-contract", (sys.executable, "-c", code))]
    ).run(root, revision)
    assert report.status == "scanner_failed"
    assert not report.findings


@pytest.mark.parametrize("payload", [{"Results": []}, {"Results": [{}]}])
async def test_results_alone_never_report_clean(tmp_path, payload):
    root, revision = committed_repo(tmp_path / "repo")
    code = "print(" + repr(json.dumps(payload)) + ")"
    report = await SecurityRunner(
        [ScannerConfig("trivy-contract", (sys.executable, "-c", code))]
    ).run(root, revision)
    assert report.status == "scanner_failed"


@pytest.mark.parametrize("artifact_type", ["filesystem", "repository", "container_image"])
@pytest.mark.parametrize("results", ["omitted", [], [{}], None, {}])
async def test_native_trivy_results_require_valid_shape(tmp_path, artifact_type, results):
    root, revision = committed_repo(tmp_path / "repo")
    payload = {**trivy_envelope(), "ArtifactType": artifact_type}
    if results != "omitted":
        payload["Results"] = results
    code = "print(" + repr(json.dumps(payload)) + ")"
    report = await SecurityRunner(
        [ScannerConfig("trivy-contract", (sys.executable, "-c", code))]
    ).run(root, revision)
    expected = "scanner_failed" if results is None or results == {} else "verified_success"
    assert report.status == expected


@pytest.mark.parametrize("payload", [[], {"findings": []}, {"issues": []}])
async def test_normalized_clean_reports_do_not_require_trivy_envelope(tmp_path, payload):
    root, revision = committed_repo(tmp_path / "repo")
    code = "print(" + repr(json.dumps(payload)) + ")"
    report = await SecurityRunner(
        [ScannerConfig("normalized", (sys.executable, "-c", code))]
    ).run(root, revision)
    assert report.status == "verified_success"
