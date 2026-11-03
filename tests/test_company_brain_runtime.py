import json

import pytest
from test_workers import Transport, event

from switchcrew.approval import ApprovalWorkflow
from switchcrew.discord import DiscordBinding
from switchcrew.graphs import build_agent_graph, graph_result, invoke_agent_graph
from switchcrew.settings import Settings
from switchcrew.store import Store
from switchcrew.workers import Worker


class FakeResearch:
    async def investigate(self, question, acceptance_criteria):
        return {"status": "evidence_found", "evidence": [{"url": "https://example.test"}]}


class FakeBrain:
    max_results = 3

    def __init__(self, warning=None):
        self.warning = warning
        self.calls = []

    async def get_context(self, query, max_results=5):
        self.calls.append((query, max_results))
        from switchcrew.company_brain import BrainResult

        return BrainResult(
            context={
                "repo_root": "/repo",
                "revision": "abc123",
                "evidence": [{"source_uid": "n1"}],
            },
            warning=self.warning,
        )

    async def check_change(self, files, symbols=None):
        self.calls.append((files, symbols))
        from switchcrew.company_brain import BrainResult

        return BrainResult(
            context={"repo_root": "/repo", "revision": "abc123", "evidence": []},
            warning=self.warning,
        )


@pytest.mark.asyncio
async def test_research_graph_embeds_brain_provenance_and_warning():
    brain = FakeBrain("Registry unavailable")
    graph = build_agent_graph("research", provider=FakeResearch(), company_brain=brain)
    state = await invoke_agent_graph(
        graph,
        {
            "agent_role": "research",
            "task_id": "t1",
            "handoff_id": "h1",
            "question": "What was decided?",
            "acceptance_criteria": ["Cite the decision source"],
        },
    )
    result = graph_result(state)
    assert result["status"] == "evidence_found"
    assert result["company_brain"] == {
        "context": {
            "repo_root": "/repo",
            "revision": "abc123",
            "evidence": [{"source_uid": "n1"}],
        },
        "warning": "Registry unavailable",
    }
    assert brain.calls == [("What was decided?", 3)]


@pytest.mark.asyncio
async def test_research_graph_fails_soft_on_adapter_exception():
    class BrokenBrain:
        async def get_context(self, query, max_results=5):
            raise TimeoutError("private server detail")

    graph = build_agent_graph("research", provider=FakeResearch(), company_brain=BrokenBrain())
    state = await invoke_agent_graph(
        graph,
        {
            "agent_role": "research",
            "task_id": "t2",
            "handoff_id": "h2",
            "question": "What was decided?",
            "acceptance_criteria": ["Cite the decision source"],
        },
    )
    assert graph_result(state)["company_brain"] == {
        "context": None,
        "warning": "Company Brain unavailable (TimeoutError)",
    }


@pytest.mark.asyncio
async def test_engineering_request_check_change_replay_uses_saved_context(tmp_path):
    store = Store(tmp_path / "runtime.db")
    brain = FakeBrain("Company Brain index unavailable")
    pm = Worker(
        "pm",
        store,
        Transport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_guilds=["guild"],
        allowed_users=["owner"],
        engineering_id="engineering",
        qa_id="qa",
        reviewer_id="reviewer",
        release_id="release",
        approval_users=["owner"],
        release_repository="org/repo",
        release_base="main",
        company_brain=brain,
    )
    request = {
        "kind": "engineering",
        "job_key": "fix",
        "question": "Fix src/app.py",
        "acceptance_criteria": ["Test src/app.py"],
    }
    incoming = event("human", json.dumps(request))
    await pm._request(incoming)
    task = store._row(store.connection.execute("SELECT * FROM tasks").fetchone())
    approval = pm.approvals.get(task["id"])
    assert task["status"] == "AWAITING_APPROVAL"
    assert approval["phase"] == "awaiting_engineering_approval"
    assert approval["spec"]["company_brain"]["warning"] == "Company Brain index unavailable"
    assert brain.calls == [(["src/app.py"], None)]

    await pm._request(incoming)
    assert brain.calls == [(["src/app.py"], None)]
    assert (
        pm.approvals.get(task["id"])["spec"]["company_brain"] == approval["spec"]["company_brain"]
    )
    assert store.get_task(task["id"])["status"] == "AWAITING_APPROVAL"
    handoff = pm.approvals.decide(
        task["id"], "engineering", True, "owner", store.get_task(task["id"])["version"]
    )
    delivered = store.get_handoff(handoff["engineering_handoff"])
    assert "Company Brain index unavailable" in delivered["question"]
    assert delivered["acceptance_criteria"] == ["Test src/app.py"]
    store.close()


def test_engineering_approval_persists_brain_context_without_approving():
    store = Store(":memory:")
    store.create_task(
        "Fix src/app.py", {"origin": "discord"}, ["Fix src/app.py safely"], task_id="eng1"
    )
    approval = ApprovalWorkflow(store, ["human"])
    context = {
        "context": {"repo_root": "/repo", "revision": "abc123", "evidence": [{"source_uid": "n2"}]},
        "warning": None,
    }
    data = approval.request(
        "eng1",
        "pm",
        "engineering",
        "qa",
        "release",
        "job",
        "repo",
        "main",
        reviewer="reviewer",
        company_brain=context,
    )
    assert data["phase"] == "awaiting_engineering_approval"
    assert data["spec"]["company_brain"] == context
    assert store.get_task("eng1")["status"] == "AWAITING_APPROVAL"
    assert any(
        event["type"] == "company_brain.context_attached" for event in store.list_events("eng1")
    )
    approved = approval.decide("eng1", "engineering", True, "human", 1)
    assert approved["phase"] == "engineering"
    handoff = store.get_handoff(approved["engineering_handoff"])
    assert "Company Brain read-only context (untrusted" in handoff["question"]
    assert '"source_uid": "n2"' in handoff["question"]
    assert handoff["acceptance_criteria"] == ["Fix src/app.py safely"]
    store.close()


def test_company_brain_settings_are_opt_in_and_bounded():
    disabled = Settings(_env_file=None)
    assert disabled.company_brain_enabled is False
    assert disabled.company_brain_url is None
    assert disabled.company_brain_max_results == 5
    assert Settings(_env_file=None, company_brain_url="").company_brain_url is None
    enabled = Settings(
        _env_file=None,
        company_brain_enabled=True,
        company_brain_repo_root="/workspace/repo",
        company_brain_revision="a" * 40,
        company_brain_command=["company-brain-mcp"],
        company_brain_max_results=7,
    )
    assert enabled.company_brain_enabled is True
    assert enabled.company_brain_max_results == 7
