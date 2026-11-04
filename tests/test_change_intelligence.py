import hashlib
import subprocess

import pytest

from switchcrew.change_intelligence import ChangeReportError, build_change_report


def repo_with_two_revisions(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "dev@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Dev"], check=True)
    module = path / "module.py"
    module.write_text("def value():\n    return 1\n")
    subprocess.run(["git", "-C", str(path), "add", "module.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "before"], check=True, capture_output=True
    )
    before = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()
    module.write_text("def value():\n    return 2\n\ndef added():\n    return True\n")
    subprocess.run(["git", "-C", str(path), "add", "module.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "after"], check=True, capture_output=True
    )
    after = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()
    return path.resolve(), before, after


@pytest.mark.asyncio
async def test_report_uses_actual_git_diff_symbols_attribution_and_drafts(tmp_path):
    root, before, after = repo_with_two_revisions(tmp_path / "repo")
    report = await build_change_report(
        root,
        before,
        after,
        task_id="task-1",
        requester="Ria",
        assigned_agent="eng-1",
        tests=["pytest tests/test_module.py"],
        reviewer_findings=["none"],
        final_outcome="accepted",
        workflow_evidence={"request_event": "event-1", "qa_handoff": "handoff-qa"},
    )
    assert report.before_revision == before
    assert report.after_revision == after
    assert report.git_author == "Dev <dev@example.com>"
    assert report.files == [{"status": "modified", "path": "module.py"}]
    assert {item["symbol"]: item["change"] for item in report.symbols} == {
        "value": "modified",
        "added": "added",
    }
    assert "return 1" in report.code_diff and "return 2" in report.code_diff
    assert len(report.diff_sha256) == 64
    assert report.source_evidence["workflow"]["qa_handoff"] == "handoff-qa"
    assert "Company Brain coverage: unknown" in report.documentation_draft
    assert "unknown" not in " ".join(report.tests)


@pytest.mark.asyncio
async def test_brain_evidence_requires_checkout_and_after_revision(tmp_path):
    root, before, after = repo_with_two_revisions(tmp_path / "repo")

    class Brain:
        async def get_context(self, query):
            return type(
                "Result",
                (),
                {
                    "context": {
                        "repo_root": str(root),
                        "revision": after,
                        "evidence": {"sources": []},
                    },
                    "warning": None,
                },
            )()

        async def check_change(self, files, symbols):
            return type(
                "Result",
                (),
                {
                    "context": {
                        "repo_root": str(root),
                        "revision": after,
                        "evidence": {"callers": []},
                    },
                    "warning": None,
                },
            )()

    report = await build_change_report(root, before, after, company_brain=Brain())
    assert report.company_brain["status"] == "consistent"
    assert report.company_brain["coverage"] == "available"

    class StaleBrain(Brain):
        async def check_change(self, files, symbols):
            return type(
                "Result",
                (),
                {"context": {"repo_root": str(root), "revision": before}, "warning": "stale"},
            )()

    report = await build_change_report(root, before, after, company_brain=StaleBrain())
    assert report.company_brain["coverage"] == "unknown"
    assert "stale" in report.warnings


@pytest.mark.asyncio
async def test_rejects_invalid_revision_and_unrelated_history(tmp_path):
    root, before, after = repo_with_two_revisions(tmp_path / "repo")
    with pytest.raises(ValueError, match="full Git object IDs"):
        await build_change_report(root, "HEAD", after)
    with pytest.raises(ChangeReportError):
        await build_change_report(root, after, before)


@pytest.mark.asyncio
async def test_sensitive_diff_values_are_redacted_but_hash_remains_real(tmp_path):
    root, before, after = repo_with_two_revisions(tmp_path / "repo")
    module = root / "module.py"
    module.write_text("def value():\n    return 'token=ghp_AAAAAAAAAAAAAAAAAAAA'\n")
    subprocess.run(["git", "-C", str(root), "add", "module.py"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "credential fixture"],
        check=True,
        capture_output=True,
    )
    after = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    report = await build_change_report(root, before, after)
    assert report.diff_redacted is True
    assert "ghp_AAAAAAAAAAAAAAAAAAAA" not in report.code_diff
    assert "REDACTED" in report.code_diff
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
            before,
            after,
        ]
    )
    assert report.diff_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
async def test_diff_output_limit_fails_without_returning_partial_artifact(tmp_path):
    root, before, _ = repo_with_two_revisions(tmp_path / "repo")
    content = "line = 1\n" * 100_000
    (root / "large.py").write_text(content)
    subprocess.run(["git", "-C", str(root), "add", "large.py"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "large"], check=True, capture_output=True
    )
    after = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ChangeReportError, match="output bound"):
        await build_change_report(root, before, after)
