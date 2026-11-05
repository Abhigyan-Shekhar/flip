"""Real parser regression: incomplete native reports must never be verified clean."""

import json
import sys

import pytest
from test_security import committed_repo

from switchcrew.security import ScannerConfig, SecurityRunner, _extract_findings


@pytest.mark.parametrize(
    "payload",
    [
        {"Results": []},
        {"Results": [{"Target": "app.py"}]},
        {"SchemaVersion": 2, "Results": []},
    ],
)
async def test_incomplete_trivy_cannot_report_verified_success(tmp_path, payload):
    root, revision = committed_repo(tmp_path / "repo")
    report = await SecurityRunner(
        [
            ScannerConfig(
                "trivy-contract", (sys.executable, "-c", "print(" + repr(json.dumps(payload)) + ")")
            )
        ]
    ).run(root, revision)
    assert report.status == "scanner_failed", report.to_dict()


@pytest.mark.parametrize("results", [None, [], [{"Target": "app.py", "Secrets": []}]])
def test_valid_native_clean_report(results):
    report = {
        "SchemaVersion": 2,
        "ArtifactName": "/workspace",
        "ArtifactType": "filesystem",
        "Trivy": {"Version": "0.76.1"},
    }
    if results is not None:
        report["Results"] = results
    assert _extract_findings(report) == []


@pytest.mark.parametrize("payload", [[], {"findings": []}, {"issues": []}])
def test_normalized_empty_output_remains_compatible(payload):
    assert _extract_findings(payload) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"Results": [], "findings": []},
        {"Results": [], "issues": []},
        {
            "SchemaVersion": 2,
            "ArtifactName": "repo",
            "ArtifactType": [],
            "Trivy": {"Version": "0.76.1"},
            "Results": [],
        },
        {"SchemaVersion": True, "Results": []},
        {
            "SchemaVersion": 2,
            "ArtifactName": " ",
            "ArtifactType": "filesystem",
            "Trivy": {"Version": "0.76.1"},
            "Results": [],
        },
    ],
)
def test_unexpected_native_envelopes_cannot_produce_false_clean(payload):
    assert _extract_findings(payload) is None
