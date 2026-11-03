import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from switchcrew.company_brain import (
    CompanyBrain,
    NullCompanyBrain,
    company_brain_from_settings,
)


def make_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "x@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "X"], check=True)
    (path / "source.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "source.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True
    )
    return path.resolve(), subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


@pytest.mark.asyncio
async def test_validates_registry_and_status_before_context(tmp_path):
    root, revision = make_repo(tmp_path / "repo")
    calls = []

    async def transport(name, args):
        calls.append((name, args))
        if name == "list_repositories":
            return {"repositories": [{"repo_root": str(root)}]}
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": revision,
                "status": "ok",
            }
        return {
            "repository": {"repo_root": str(root), "repo_id": "repo-1"},
            "graph_revision": revision,
            "status": "ok",
            "must_preserve": ["public API"],
            "callers": [{"symbol": "run"}],
            "untrusted_source_excerpts": [{"source_uid": "src1", "excerpt": "fact"}],
        }

    brain = CompanyBrain(repo_root=str(root), revision=revision, transport=transport)
    result = await brain.get_context("decision about source.py")
    assert result.warning is None
    assert result.context["evidence"]["untrusted_source_excerpts"][0]["source_uid"] == "src1"
    assert result.context["revision"] == revision
    assert result.context["evidence"]["must_preserve"] == ["public API"]
    assert result.context["evidence"]["callers"][0]["symbol"] == "run"
    assert [name for name, _ in calls] == ["list_repositories", "repository_status", "get_context"]
    assert all(
        args.get("repo_root") == str(root) for name, args in calls if name != "list_repositories"
    )


@pytest.mark.asyncio
async def test_never_queries_context_when_registered_root_does_not_match(tmp_path):
    root, revision = make_repo(tmp_path / "repo")
    calls = []

    async def transport(name, args):
        calls.append(name)
        if name == "list_repositories":
            return {"repositories": [{"repo_root": str(tmp_path)}]}
        pytest.fail("status should not be called for a different registered root")

    result = await CompanyBrain(repo_root=str(root), transport=transport).check_change(
        ["source.py"]
    )
    assert result.context == ""
    assert "no registered repository" in result.warning
    assert calls == ["list_repositories"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stale", "syncing", "unavailable", "unindexed"])
async def test_status_blocks_graph_retrieval(tmp_path, status):
    root, revision = make_repo(tmp_path / "repo")
    calls = []

    async def transport(name, args):
        calls.append(name)
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": revision,
                "status": status,
            }

    result = await CompanyBrain(repo_root=str(root), transport=transport).get_context("topic")
    assert result.context == ""
    assert status in result.warning
    assert calls == ["list_repositories", "repository_status"]


@pytest.mark.asyncio
async def test_dirty_or_stale_revision_is_not_used(tmp_path):
    root, revision = make_repo(tmp_path / "repo")

    async def transport(name, args):
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": "different",
                "status": "ok",
            }
        pytest.fail("must not retrieve evidence")

    brain = CompanyBrain(repo_root=str(root), revision=revision, transport=transport)
    result = await brain.get_context("topic")
    assert "stale" in result.warning

    (root / "source.py").write_text("dirty\n")

    async def current_transport(name, args):
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": revision,
                "status": "ok",
            }
        pytest.fail("must not retrieve evidence")

    result = await CompanyBrain(repo_root=str(root), transport=current_transport).get_context(
        "topic"
    )
    assert "uncommitted" in result.warning


@pytest.mark.asyncio
async def test_stale_index_warning_blocks_before_retrieval(tmp_path):
    root, revision = make_repo(tmp_path / "repo")
    calls = []

    async def transport(name, args):
        calls.append(name)
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        return {
            "repo_root": str(root),
            "local_revision": revision,
            "graph_revision": revision,
            "status": "ok",
            "warnings": ["stale_index"],
        }

    result = await CompanyBrain(repo_root=str(root), transport=transport).get_context("topic")
    assert result.context == ""
    assert "stale_index" in result.warning
    assert calls == ["list_repositories", "repository_status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieval_revision", [None, "other"])
async def test_retrieval_requires_matching_graph_revision(tmp_path, retrieval_revision):
    root, revision = make_repo(tmp_path / "repo")

    async def transport(name, args):
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": revision,
                "status": "ok",
            }
        return {"status": "ok", "graph_revision": retrieval_revision, "evidence": ["claim"]}

    result = await CompanyBrain(repo_root=str(root), transport=transport).get_context("topic")
    assert result.context == ""
    assert "graph revision" in result.warning


@pytest.mark.asyncio
async def test_git_failure_and_timeout_are_fail_soft(tmp_path, monkeypatch):
    root, revision = make_repo(tmp_path / "repo")
    calls = []

    async def transport(name, args):
        calls.append(name)
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        return {
            "repo_root": str(root),
            "local_revision": revision,
            "graph_revision": revision,
            "status": "ok",
        }

    brain = CompanyBrain(repo_root=str(root), transport=transport)

    async def fail_git(*args):
        return None

    monkeypatch.setattr(brain, "_git", fail_git)
    result = await brain.get_context("topic")
    assert "revision could not be validated" in result.warning
    assert calls == ["list_repositories", "repository_status"]

    async def slow_transport(name, args):
        await asyncio.sleep(0.03)

    timeout_brain = CompanyBrain(repo_root=str(root), transport=slow_transport, timeout=0.001)
    result = await timeout_brain.get_context("topic")
    assert result.context == ""
    assert result.warning == "Company Brain unavailable; use local source as authoritative."


@pytest.mark.asyncio
async def test_result_limit_and_sensitive_content_are_enforced(tmp_path):
    root, revision = make_repo(tmp_path / "repo")

    async def transport(name, args):
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": revision,
                "status": "ok",
            }
        return {
            "status": "ok",
            "graph_revision": revision,
            "untrusted_source_excerpts": [{"n": n} for n in range(5)],
        }

    brain = CompanyBrain(repo_root=str(root), max_results=2, transport=transport)
    result = await brain.get_context("topic", max_results=2)
    assert len(result.context["evidence"]["untrusted_source_excerpts"]) == 2
    assert "truncated" in result.warning
    result = await brain.get_context("token=abc-secret-value")
    assert result.context == ""
    assert "sensitive" in result.warning

    async def sensitive_transport(name, args):
        if name == "list_repositories":
            return [{"repo_root": str(root)}]
        if name == "repository_status":
            return {
                "repo_root": str(root),
                "local_revision": revision,
                "graph_revision": revision,
                "status": "ok",
            }
        return {
            "status": "ok",
            "graph_revision": revision,
            "untrusted_source_excerpts": [{"excerpt": "password=private-value"}],
        }

    result = await CompanyBrain(repo_root=str(root), transport=sensitive_transport).get_context(
        "topic"
    )
    assert result.context == ""
    assert "sensitive" in result.warning


@pytest.mark.asyncio
async def test_factory_invalid_transport_is_fail_soft():
    settings = SimpleNamespace(
        company_brain_enabled=True,
        company_brain_repo_root="/tmp/repo",
        company_brain_revision="abc",
        company_brain_command=[],
        company_brain_url=None,
        company_brain_token=None,
        company_brain_max_results=5,
    )
    brain = company_brain_from_settings(settings)
    assert isinstance(brain, NullCompanyBrain)
    result = await brain.get_context("topic")
    assert "configuration is invalid" in result.warning


def test_requires_absolute_root():
    with pytest.raises(ValueError, match="absolute"):
        CompanyBrain(repo_root="relative", transport=lambda *_: None)
