import subprocess

import pytest

import switchcrew.combined_acceptance as combined
from switchcrew.combined_acceptance import check_checkout, run_combined_acceptance
from switchcrew.settings import Settings


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def setup_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    (root / "code.py").write_text("value = 1\n")
    git(root, "add", "code.py")
    git(root, "commit", "-qm", "initial")
    return root, git(root, "rev-parse", "HEAD")


def test_checkout_gate_requires_clean_exact_revision(tmp_path):
    root, sha = setup_repo(tmp_path)
    settings = Settings(_env_file=None, company_brain_repo_root=root, company_brain_revision=sha)
    assert check_checkout(settings)["status"] == "PASS"
    (root / "code.py").write_text("value = 2\n")
    assert check_checkout(settings)["status"] == "BLOCKED"
    (root / "code.py").write_text("value = 1\n")
    settings = Settings(
        _env_file=None, company_brain_repo_root=root, company_brain_revision="f" * 40
    )
    assert check_checkout(settings)["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_combined_gate_does_not_call_memory_for_mismatched_revision(tmp_path):
    root, sha = setup_repo(tmp_path)
    settings = Settings(
        _env_file=None,
        company_brain_enabled=True,
        company_brain_repo_root=root,
        company_brain_revision="0" * 40,
        memory_enabled=True,
        memory_project="testing/acceptance/isolated",
    )

    def forbid():
        raise AssertionError("Memory must not be invoked")

    result = await run_combined_acceptance(settings, memory_factory=forbid)
    assert result["status"] == "BLOCKED"
    assert result["live_learning"] == "not_verified"
    settings.company_brain_revision = sha
    settings.memory_project = "production"
    result = await run_combined_acceptance(settings, memory_factory=forbid)
    assert result["reason"] == "isolated_waggle_acceptance_project_required"


@pytest.mark.asyncio
async def test_successful_probes_still_label_live_learning_pending(tmp_path, monkeypatch):
    root, sha = setup_repo(tmp_path)
    settings = Settings(
        _env_file=None,
        company_brain_enabled=True,
        company_brain_repo_root=root,
        company_brain_revision=sha,
        memory_enabled=True,
        memory_project="demo/acceptance/isolation",
    )

    async def brain(*args, **kwargs):
        return {"status": "PASS", "source_count": 3}

    async def memory(factory):
        return {"status": "PASS", "fresh_client": True}

    async def lesson():
        return {"status": "PASS", "live_waggle_called": False}

    monkeypatch.setattr(combined, "verify_company_brain", brain)
    monkeypatch.setattr(combined, "verify_shared_memory", memory)
    result = await run_combined_acceptance(settings, learning_probe=lesson)
    assert result["status"] == "integration_probes_passed"
    assert result["live_learning"] == "not_verified"
    assert result["live_checkout_indexing"] == "not_verified"
