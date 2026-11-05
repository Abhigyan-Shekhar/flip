import json
import subprocess
import sys

import pytest

from switchcrew.security import ScannerConfig, SecurityRunner


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
    assert report.scanners == ({"name": "fixture", "status": "completed"},)
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
    code = "print(" + repr(json.dumps({"Results": [result]})) + ")"
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
