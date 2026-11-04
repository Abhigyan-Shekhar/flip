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
