"""Controlled adapters only; actual Git and subprocess QA, no live Codex claim."""

import json
import sys
from pathlib import Path

import httpx
import pytest

from switchcrew.engineering import ConsoleCodex, EngineeringExecutor, Ledger, assignment_id, git
from switchcrew.graphs import build_agent_graph, invoke_agent_graph
from switchcrew.qa import QAExecutor


def commit(repo, content):
    (repo / "answer.txt").write_text(content)
    git(repo, "add", "answer.txt")
    git(
        repo,
        "-c",
        "user.name=Controlled Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "fixture change",
    )
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    base = commit(source, "broken")
    ledger = Ledger(tmp_path / "execution.sqlite3")
    yield source, base, ledger
    ledger.close()


def state(identity="engineering-1", role="engineering"):
    return {
        "agent_role": role,
        "handoff_id": identity,
        "task_id": "task",
        "question": "Correct answer.txt",
        "acceptance_criteria": ["Answer equals correct"],
    }


class ControlledSession:
    """Fault-injection contract double; never counted as managed Codex evidence."""

    def __init__(self, uncertain=None):
        self.sessions = {}
        self.creates = self.deliveries = 0
        self.uncertain = uncertain

    async def validate(self, checkout):
        self.checkout = checkout

    async def find(self, identity):
        return self.sessions.get(identity)

    async def create(self, identity):
        self.creates += 1
        self.sessions[identity] = "controlled-session"
        if self.uncertain == "create":
            raise httpx.ReadTimeout("private error")
        return self.sessions[identity]

    async def deliver(self, identity, checkout, question, criteria):
        self.deliveries += 1
        if self.uncertain == "deliver":
            raise httpx.ReadTimeout("private error")
        return "controlled-delivery"


def receipt(checkout, identity, revision, status="implemented"):
    (checkout / ".switchcrew-result.json").write_text(
        json.dumps({"assignment_id": identity, "final_revision": revision, "status": status})
    )


@pytest.mark.asyncio
async def test_missing_live_capability_is_blocked(setup, tmp_path):
    source, base, ledger = setup
    result = await EngineeringExecutor(ledger, tmp_path / "work", source, base, None)(state())
    assert result["status"] == "blocked_configuration"
    assert result["base_revision"] == base
    assert Path(result["checkout"]) != source


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["create", "deliver"])
async def test_uncertain_external_response_reconciles_without_duplicate(setup, tmp_path, fault):
    source, base, ledger = setup
    session = ControlledSession(fault)
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    assert (await executor(state()))["status"] == "pending_execution"
    # Reopen the durable ledger to simulate application restart.
    restarted = Ledger(tmp_path / "execution.sqlite3")
    executor.ledger = restarted
    try:
        assert (await executor(state()))["status"] == "pending_execution"
        assert session.creates == 1
        assert session.deliveries == 1
        identity = assignment_id("engineering-1")
        final = commit(session.checkout, "correct")
        receipt(session.checkout, identity, final)
        result = await executor(state())
        assert result["status"] == "implemented"
        assert result["final_revision"] == final
        assert "+correct" in result["diff"]
        assert await executor(state()) == result
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_unknown_create_does_not_relaunch(setup, tmp_path):
    source, base, ledger = setup
    session = ControlledSession("create")
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    await executor(state())
    session.sessions.clear()  # No authoritative answer yet.
    for _ in range(3):
        assert (await executor(state()))["status"] == "pending_execution"
    assert session.creates == 1


@pytest.mark.asyncio
async def test_actual_qa_failure_success_and_stale_result(setup, tmp_path):
    source, base, ledger = setup
    session = ControlledSession()
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    await executor(state())
    identity = assignment_id("engineering-1")
    receipt(session.checkout, identity, base)
    broken = await executor(state())
    assert broken["status"] == "implemented"  # Implementation completion is not QA success.
    commands = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'",
        ]
    ]
    qa = QAExecutor(ledger, tmp_path / "qa", identity, base, commands)
    result = await qa(state("qa-broken", "qa"))
    assert result["status"] == "qa_failed"
    assert result["commands"][0]["exit_code"] != 0
    assert result["tested_revision"] == base
    assert Path(result["checkout"]) != session.checkout
    assert git(result["checkout"], "rev-parse", "HEAD") == base

    # A correction is a new Engineering assignment, never mutating old inputs.
    corrected_session = ControlledSession()
    corrected_executor = EngineeringExecutor(
        ledger, tmp_path / "work", source, base, corrected_session
    )
    await corrected_executor(state("engineering-2"))
    corrected_id = assignment_id("engineering-2")
    final = commit(corrected_session.checkout, "correct")
    receipt(corrected_session.checkout, corrected_id, final)
    await corrected_executor(state("engineering-2"))
    good_qa = QAExecutor(ledger, tmp_path / "qa", corrected_id, final, commands)
    passed = await good_qa(state("qa-correct", "qa"))
    assert passed["status"] == "verified_success"
    assert passed["commands"][0]["revision"] == final
    commit(corrected_session.checkout, "changed after QA")
    assert (await good_qa(state("qa-correct", "qa")))["status"] == "stale_qa"


@pytest.mark.asyncio
async def test_qa_interruption_is_not_blindly_repeated(setup, tmp_path):
    source, base, ledger = setup
    identity = assignment_id("implementation")
    ledger.reserve(identity, {})
    ledger.put(
        identity,
        {
            "phase": "finished",
            "result": {"status": "implemented", "checkout": str(source), "final_revision": base},
        },
    )
    qa = QAExecutor(ledger, tmp_path / "qa", identity, base, [["/bin/true"]])
    qa_id = "qa-" + assignment_id("qa-interrupted")
    ledger.reserve(
        qa_id,
        {"implementation": identity, "revision": base, "commands": [["/bin/true"]], "timeout": 120},
    )
    ledger.claim(qa_id, "reserved", "running")
    result = await qa(state("qa-interrupted", "qa"))
    assert result["status"] == "blocked_configuration"
    assert result["tested_revision"] == base


@pytest.mark.asyncio
async def test_validation_cannot_change_tested_revision(setup, tmp_path):
    source, base, ledger = setup
    identity = assignment_id("implementation")
    ledger.reserve(identity, {})
    ledger.put(
        identity,
        {"result": {"status": "implemented", "checkout": str(source), "final_revision": base}},
    )
    commands = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('answer.txt').write_text('changed')",
        ],
        ["/bin/true"],
    ]
    result = await QAExecutor(ledger, tmp_path / "qa", identity, base, commands)(state("qa", "qa"))
    assert result["status"] == "qa_failed"
    assert len(result["commands"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["helper.py", "ignored/helper.py"])
async def test_validation_rejects_untracked_and_ignored_inputs(setup, tmp_path, filename):
    source, _, ledger = setup
    (source / ".gitignore").write_text("ignored/\n")
    git(source, "add", ".gitignore")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "ignore",
    )
    revision = git(source, "rev-parse", "HEAD")
    identity = assignment_id("implementation")
    ledger.reserve(identity, {})
    ledger.put(
        identity,
        {"result": {"status": "implemented", "checkout": str(source), "final_revision": revision}},
    )
    commands = [
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; p=Path({filename!r}); "
            "p.parent.mkdir(parents=True, exist_ok=True); p.write_text('pass')",
        ],
        [sys.executable, "-c", f"from pathlib import Path; assert Path({filename!r}).exists()"],
    ]
    result = await QAExecutor(ledger, tmp_path / "qa", identity, revision, commands)(
        state("qa", "qa")
    )
    assert result["status"] == "qa_failed"
    assert len(result["commands"]) == 1


@pytest.mark.asyncio
async def test_validation_allows_python_caches_but_commands_get_fresh_checkout(setup, tmp_path):
    source, revision, ledger = setup
    identity = assignment_id("implementation")
    ledger.reserve(identity, {})
    ledger.put(
        identity,
        {"result": {"status": "implemented", "checkout": str(source), "final_revision": revision}},
    )
    commands = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; p=Path('__pycache__/answer.cpython-312.pyc'); "
            "p.parent.mkdir(); p.write_bytes(b'cache')",
        ],
        [sys.executable, "-c", "from pathlib import Path; assert not Path('__pycache__').exists()"],
    ]
    result = await QAExecutor(ledger, tmp_path / "qa", identity, revision, commands)(
        state("qa", "qa")
    )
    assert result["status"] == "verified_success"


@pytest.mark.asyncio
async def test_executor_outcomes_follow_existing_graph_contract(setup, tmp_path):
    source, base, ledger = setup
    graph = build_agent_graph(
        "engineering", executor=EngineeringExecutor(ledger, tmp_path / "work", source, base, None)
    )
    result = await invoke_agent_graph(graph, state())
    assert result["result"]["status"] == "blocked_configuration"


@pytest.mark.asyncio
async def test_console_routes_and_provider_binding(tmp_path):
    class Adapter(ConsoleCodex):
        async def request(self, method, suffix, **kwargs):
            if suffix == "":
                return {
                    "agent": {
                        "providerId": "codex",
                        "name": "engineering-test",
                        "locationId": "local-location",
                        "switchAgentId": "core-codex",
                    }
                }
            if method == "POST":
                assert kwargs["json"] == {"title": "switchcrew:assignment"}
                return {"session": {"id": "s"}}
            return {"sessions": [{"title": "switchcrew:assignment", "id": "s"}]}

    import sqlite3

    database = tmp_path / "console.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE locations(id TEXT,dir TEXT,ssh_host TEXT)")
        db.execute("INSERT INTO locations VALUES(?,?,?)", ("local-location", "/isolated", ""))
    adapter = Adapter(
        "unused",
        "agent",
        "engineering-test",
        None,
        console_database=database,
        switch_agent_id="core-codex",
    )
    await adapter.validate(Path("/isolated"))
    assert await adapter.find("assignment") == "s"
    assert await adapter.create("assignment") == "s"
    with pytest.raises(ValueError):
        await adapter.validate(Path("/wrong-checkout"))


@pytest.mark.asyncio
async def test_durable_independent_qa_handoff(setup):
    from switchcrew.engineering import enqueue_independent_qa, qa_handoff_id
    from switchcrew.store import Store

    _, revision, _ = setup
    store = Store(":memory:")
    try:
        task = store.create_task("Implement", {}, ["Validate"])
        handoff = store.create_handoff(
            task["id"], "coordinator", "engineering", "Implement", ["Validate"], task["version"]
        )
        assert enqueue_independent_qa(store, handoff["id"], "engineering", "qa") is None
        store.accept_handoff(handoff["id"], "engineering")
        store.complete_handoff(
            handoff["id"], "engineering", {"status": "implemented", "final_revision": revision}
        )
        qa = enqueue_independent_qa(store, handoff["id"], "engineering", "qa")
        assert qa["id"] == qa_handoff_id(handoff["id"], revision)
        assert qa["sender"] == "engineering" and qa["recipient"] == "qa"
        assert enqueue_independent_qa(store, handoff["id"], "engineering", "qa") == qa
        assert len([o for o in store.pending_outbox() if o["recipient"] == "qa"]) == 1
        with pytest.raises(ValueError):
            enqueue_independent_qa(store, handoff["id"], "engineering", "engineering")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_invalid_receipt_cannot_be_implementation_success(setup, tmp_path):
    source, base, ledger = setup
    session = ControlledSession()
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    await executor(state())
    receipt(session.checkout, "different-assignment", base)
    assert (await executor(state()))["status"] == "implementation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [[], None, "receipt"])
async def test_non_object_receipt_is_an_implementation_failure(setup, tmp_path, record):
    source, base, ledger = setup
    session = ControlledSession()
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    await executor(state())
    (session.checkout / ".switchcrew-result.json").write_text(json.dumps(record))
    assert (await executor(state()))["status"] == "implementation_failed"


@pytest.mark.asyncio
async def test_console_outage_after_delivery_keeps_handoff_pending(setup, tmp_path):
    source, base, ledger = setup

    class IntermittentSession(ControlledSession):
        outage = False

        async def validate(self, checkout):
            if self.outage:
                raise httpx.ConnectError("unavailable")
            await super().validate(checkout)

    session = IntermittentSession()
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    assert (await executor(state()))["status"] == "pending_execution"
    identity = assignment_id("engineering-1")
    final = commit(session.checkout, "correct")
    receipt(session.checkout, identity, final)
    session.outage = True
    assert (await executor(state()))["status"] == "pending_execution"
    session.outage = False
    result = await executor(state())
    assert result["status"] == "implemented"
    assert result["final_revision"] == final
    assert session.creates == session.deliveries == 1


@pytest.mark.asyncio
async def test_assignment_inputs_are_immutable(setup, tmp_path):
    source, base, ledger = setup
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, ControlledSession())
    await executor(state())
    assert (await executor({**state(), "question": "Other change"}))[
        "status"
    ] == "blocked_configuration"


@pytest.mark.asyncio
async def test_qa_timeout_records_exact_revision(setup, tmp_path):
    source, base, ledger = setup
    identity = assignment_id("implementation")
    ledger.reserve(identity, {})
    ledger.put(
        identity,
        {"result": {"status": "implemented", "checkout": str(source), "final_revision": base}},
    )
    qa = QAExecutor(
        ledger,
        tmp_path / "qa",
        identity,
        base,
        [[sys.executable, "-c", "import time; time.sleep(30)"]],
        timeout=0.05,
    )
    result = await qa(state("qa-timeout", "qa"))
    assert result["status"] == "qa_failed"
    assert result["commands"][0]["timed_out"] is True
    assert result["commands"][0]["revision"] == base


@pytest.mark.asyncio
async def test_runtime_does_not_complete_pending_execution(tmp_path):
    from test_runtime import setup_runtime

    runtime, event, handoff, _ = setup_runtime(tmp_path)

    class PendingGraph:
        name = "switchcrew_research"
        checkpointer = None

        async def ainvoke(self, state, config):
            return {"result": {"status": "pending_execution"}}

    runtime.graph = PendingGraph()
    await runtime.receive(event)
    await runtime.process_pending()
    assert runtime.store.get_handoff(handoff["id"])["status"] == "accepted"
    assert len(runtime.store.pending_inbox(runtime.connection.agent_id)) == 1
    runtime.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_config", [False, True])
async def test_console_onboarding_config_stays_private(setup, tmp_path, commit_config):
    source, base, ledger = setup
    root = tmp_path / "work"
    prepared = await EngineeringExecutor(ledger, root, source, base, None)(state())
    checkout = Path(prepared["checkout"])
    config = checkout / ".switch/config/dedicated.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"test_only": true}')
    session = ControlledSession()
    executor = EngineeringExecutor(ledger, root, source, base, session)
    assert (await executor(state()))["status"] == "pending_execution"
    assert git(checkout, "status", "--porcelain") == ""
    final = commit(checkout, "correct")
    if commit_config:
        git(checkout, "add", "-f", ".switch/config/dedicated.json")
        git(
            checkout,
            "-c",
            "user.name=Controlled Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "unsafe fixture",
        )
        final = git(checkout, "rev-parse", "HEAD")
    receipt(checkout, assignment_id(state()["handoff_id"]), final)
    result = await executor(state())
    assert result["status"] == ("implementation_failed" if commit_config else "implemented")
