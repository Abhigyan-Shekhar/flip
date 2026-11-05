import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from switchcrew.company_brain_acceptance import verify_company_brain
from switchcrew.settings import Settings


def git_repo(path: Path) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "acceptance@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Acceptance"], check=True)
    (path / "module.py").write_text("def work():\n    return 1\n")
    subprocess.run(["git", "-C", str(path), "add", "module.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "fixture"], check=True, capture_output=True
    )
    revision = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()
    return path.resolve(), revision


def settings(root: Path, revision: str, **kwargs) -> Settings:
    return Settings(
        _env_file=None,
        company_brain_enabled=True,
        company_brain_repo_root=root,
        company_brain_revision=revision,
        company_brain_max_results=3,
        **kwargs,
    )


def make_transport(root: Path, revision: str, *, excerpt: str = "source body"):
    calls = []

    async def transport(name, args):
        calls.append((name, args))
        if name == "list_repositories":
            return [{"repo_root": str(root), "repo_id": "fixture"}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "status": "ok",
                "local_revision": revision,
                "graph_revision": revision,
                "working_tree_dirty": False,
                "warnings": [],
            }
        if name == "get_context":
            return {
                "status": "ok",
                "repository": {"repo_root": str(root), "repo_id": "fixture"},
                "graph_revision": revision,
                "untrusted_source_excerpts": [{"source_uid": "source-1", "excerpt": excerpt}],
            }
        if name == "check_change":
            return {
                "status": "ok",
                "repository": {"repo_root": str(root), "repo_id": "fixture"},
                "graph_revision": revision,
                "must_preserve": [{"source_uid": "preserve-1", "excerpt": "do not emit"}],
            }
        raise AssertionError(f"unexpected tool: {name}")

    return transport, calls


@pytest.mark.asyncio
async def test_probe_passes_with_identity_revision_and_optional_change_check(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    transport, calls = make_transport(root, revision)
    result = await verify_company_brain(
        settings(root, revision), transport=transport, files=["module.py"]
    )
    assert result["status"] == "PASS"
    assert result["identity_match"] is True
    assert result["configured_revision"] == revision
    assert result["returned_revision"] == revision
    assert result["source_count"] == 1
    assert result["change_source_count"] == 1
    assert "source body" not in str(result)
    assert "do not emit" not in str(result)
    change_call = next(args for name, args in calls if name == "check_change")
    assert change_call["files"] == ["module.py"]
    assert change_call["repo_root"] == str(root)


@pytest.mark.asyncio
async def test_disabled_or_missing_configuration_is_blocked(tmp_path):
    disabled = Settings(_env_file=None)
    result = await verify_company_brain(disabled)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "company_brain_disabled"

    incomplete = Settings(_env_file=None, company_brain_enabled=True)
    result = await verify_company_brain(incomplete)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "repository_root_or_revision_missing"


@pytest.mark.asyncio
async def test_empty_context_does_not_pass_as_source_grounded(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    calls = []

    async def transport(name, args):
        calls.append(name)
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "status": "ok",
                "local_revision": revision,
                "graph_revision": revision,
                "working_tree_dirty": False,
            }
        return {"status": "ok", "repository": {"repo_root": str(root)}, "graph_revision": revision}

    result = await verify_company_brain(settings(root, revision), transport=transport)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "no_source_grounded_evidence_returned"
    assert calls == ["list_repositories", "repository_status", "get_context"]


@pytest.mark.asyncio
async def test_identity_or_revision_mismatch_is_blocked(tmp_path):
    root, revision = git_repo(tmp_path / "repo")

    async def transport(name, args):
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "status": "ok",
                "local_revision": revision,
                "graph_revision": revision,
                "working_tree_dirty": False,
            }
        return {
            "status": "ok",
            "repository": {"repo_root": str(root)},
            "graph_revision": "different",
            "untrusted_source_excerpts": [{"source_uid": "x"}],
        }

    result = await verify_company_brain(settings(root, revision), transport=transport)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "retrieval_identity_or_revision_not_confirmed"


@pytest.mark.asyncio
async def test_probe_redacts_secret_warnings_and_never_emits_excerpts(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    secret = "super-secret-token"
    transport, _ = make_transport(root, revision, excerpt=f"token={secret}; raw private excerpt")
    config = settings(root, revision, company_brain_token=SecretStr(secret))
    result = await verify_company_brain(config, transport=transport)
    assert result["status"] == "BLOCKED"
    assert "private excerpt" not in str(result)
    assert secret not in str(result)
    assert "sensitive" in result["warnings"][0]


@pytest.mark.asyncio
async def test_change_files_must_be_repo_relative(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    result = await verify_company_brain(
        settings(root, revision),
        files=["../outside.py"],
        transport=make_transport(root, revision)[0],
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "change_files_must_be_repository_relative"


async def test_cli_requires_private_config_and_never_overwrites_report(tmp_path, capsys):
    import json
    import stat

    from switchcrew.company_brain_acceptance import main

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"company_brain_enabled": False}))
    config.chmod(0o644)
    report = tmp_path / "report.json"
    with pytest.raises(ValueError, match="private"):
        await main(str(config), str(report))
    assert not report.exists()

    config.chmod(0o600)
    assert await main(str(config), str(report)) == 1
    assert json.loads(report.read_text())["status"] == "BLOCKED"
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    original = report.read_text()
    with pytest.raises(FileExistsError):
        await main(str(config), str(report))
    assert report.read_text() == original
    assert json.loads(capsys.readouterr().out)["reason"] == "company_brain_disabled"


async def test_operator_probe_forwards_explicit_indexed_query(tmp_path, monkeypatch, capsys):
    import json

    from switchcrew import company_brain_acceptance as probe

    config = tmp_path / "config.json"
    config.write_text("{}")
    config.chmod(0o600)
    calls = []

    async def verify(settings, **kwargs):
        calls.append(kwargs)
        return {"status": "PASS"}

    monkeypatch.setattr(probe, "verify_company_brain", verify)
    assert await probe.main(str(config), files=["module.py"], query="exact indexed symbol") == 0
    assert calls == [{"files": ["module.py"], "query": "exact indexed symbol"}]
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
