"""Real MCP SDK transport against a controlled registry server, not live graph evidence."""

import subprocess
import sys

from switchcrew.company_brain import CompanyBrain


async def test_real_stdio_registry_routing_and_schema(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    server = tmp_path / "server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        f"ROOT = {str(repo.resolve())!r}\nREV = {revision!r}\n"
        'mcp = FastMCP("controlled-company-brain")\n'
        "@mcp.tool()\n"
        "def list_repositories() -> dict:\n"
        '    return {"repositories": [{"repo_root": ROOT}]}\n'
        "@mcp.tool()\n"
        "def repository_status(repo_root: str) -> dict:\n"
        "    assert repo_root == ROOT\n"
        '    return {"repo_root": ROOT, "local_revision": REV, "graph_revision": REV, '
        '"working_tree_dirty": False, "status": "ok", "warnings": []}\n'
        "@mcp.tool()\n"
        "def get_context(query: str, repo_root: str) -> dict:\n"
        '    assert repo_root == ROOT and query == "source decision"\n'
        '    return {"repository": {"repo_root": ROOT}, "status": "ok", '
        '"graph_revision": REV, "warnings": [], "untrusted_source_excerpts": '
        '[{"source_uid": "S1", "excerpt": "Keep the public API"}]}\n'
        'mcp.run(transport="stdio")\n'
    )
    brain = CompanyBrain(
        repo_root=str(repo.resolve()),
        revision=revision,
        command=[sys.executable, str(server)],
        timeout=10,
    )
    result = await brain.get_context("source decision")
    assert result.warning is None
    assert result.context["revision"] == revision
    assert result.context["evidence"]["untrusted_source_excerpts"][0]["source_uid"] == "S1"
