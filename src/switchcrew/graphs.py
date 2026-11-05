"""Independent, checkpointable specialist graphs; Switch owns cross-agent delivery.

Executors are explicit application dependencies, never inferred shell/model tools.
Credentials, network clients, and providers stay outside checkpointed state.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agents import AGENTS, AgentRole
from .memory import NullMemory
from .research import ResearchProvider


class AgentState(TypedDict, total=False):
    agent_role: str
    task_id: str
    handoff_id: str
    session_id: str
    question: str
    requested_role: str
    acceptance_criteria: list[str]
    valid: bool
    memory_context: str
    memory_warnings: list[str]
    memory_persisted: bool
    result: dict[str, Any]


class AgentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    question: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)


Executor = Callable[[AgentState], Awaitable[dict[str, Any]]]
RESULT_STATUSES = {
    "awaiting_approval",
    "pending_execution",
    "blocked_configuration",
    "implemented",
    "implementation_failed",
    "qa_failed",
    "verified_success",
    "findings",
    "scanner_failed",
    "stale_qa",
    "review_accepted",
    "review_rejected",
    "stale_review",
    "completed",
    "evidence_found",
    "blocked",
    "needs_clarification",
    "delegation_ready",
    "ready",
}


def graph_config(role: str, task_id: str, handoff_id: str = "") -> dict:
    """Namespaced, opaque checkpoint key: never mix specialist/task assignments."""
    role = AgentRole(role).value
    if not task_id:
        raise ValueError("A stable task ID is required for checkpointed execution")
    identity = str(uuid5(NAMESPACE_URL, f"switchcrew:{role}:{task_id}:{handoff_id}"))
    return {"configurable": {"thread_id": identity}, "recursion_limit": 20}


def build_agent_graph(
    role: str,
    *,
    provider=None,
    executor: Executor | None = None,
    memory=None,
    checkpointer=None,
    company_brain=None,
):
    """Compile one specialist. Missing integrations produce explicit blocked results."""
    spec = AGENTS[AgentRole(role).value]
    memory = memory if memory is not None else NullMemory()
    if spec.role == AgentRole.RESEARCH and provider is None:
        provider = ResearchProvider([])

    def validate(state: AgentState):
        try:
            data = AgentRequest.model_validate(state)
            valid = all(item.strip() and len(item) <= 2000 for item in data.acceptance_criteria)
        except ValidationError:
            data, valid = None, False
        output = {
            "agent_role": spec.role.value,
            "valid": valid
            and state.get("requested_role", "research")
            in {"research", "engineering", "orchestration"},
            "requested_role": state.get("requested_role", "research"),
            "memory_context": "",
            "memory_warnings": [],
            "memory_persisted": False,
        }
        if valid:
            output.update(
                question=data.question,
                acceptance_criteria=[item.strip() for item in data.acceptance_criteria],
            )
        return output

    def clarify(state: AgentState):
        return {
            "result": {
                "status": "needs_clarification",
                "role": spec.role.value,
                "reason": "A question and nonblank acceptance criteria are required",
            }
        }

    async def recall(state: AgentState):
        session = state.get("session_id") or state.get("task_id", "")
        primed = await memory.prime(spec.role.value, session)
        recalled = await memory.recall(state["question"], spec.role.value, session)
        warnings = [r.warning for r in (primed, recalled) if r.warning]
        context = "\n".join(r.context for r in (primed, recalled) if r.context)
        return {"memory_context": context[:12000], "memory_warnings": warnings}

    async def work(state: AgentState):
        if executor is not None:
            result = await executor(dict(state))
            if not isinstance(result, dict) or result.get("status") not in RESULT_STATUSES:
                raise ValueError("Executor must return a structured specialist result")
        elif spec.role == AgentRole.PM:
            result = {
                "status": "awaiting_approval"
                if state.get("requested_role") == "engineering"
                else "delegation_ready",
                "role": spec.role.value,
                "selected_role": state.get("requested_role", AgentRole.RESEARCH.value),
                "question": state["question"],
                "acceptance_criteria": state["acceptance_criteria"],
            }
        elif spec.role == AgentRole.RESEARCH:
            snapshot = None
            if company_brain is not None:
                try:
                    brain = await company_brain.get_context(
                        state["question"],
                        max_results=getattr(company_brain, "max_results", 5),
                    )
                    snapshot = {
                        "context": brain.context,
                        "warning": brain.warning,
                    }
                except Exception as exc:
                    # Brain enriches evidence but cannot block the independent
                    # Switch handoff or turn an outage into a research claim.
                    snapshot = {
                        "context": None,
                        "warning": f"Company Brain unavailable ({type(exc).__name__})",
                    }
            contextual = getattr(provider, "investigate_with_context", None)
            if snapshot and contextual is not None:
                result = await contextual(state["question"], state["acceptance_criteria"], snapshot)
            else:
                result = await provider.investigate(state["question"], state["acceptance_criteria"])
            if snapshot is not None:
                result["company_brain"] = snapshot
        else:
            result = {
                "status": "blocked",
                "role": spec.role.value,
                "missing_capability": spec.capability,
                "reason": "Specialist scaffold requires a configured capability executor",
            }
        return {"result": result}

    async def observe(state: AgentState):
        # Curated durable workflow outcome only: no raw source bodies or recalled
        # memories are re-ingested. Memory is opt-in through the configured adapter.
        summary = {
            "role": spec.role.value,
            "task_id": state.get("task_id", ""),
            "handoff_id": state.get("handoff_id", ""),
            "status": state["result"]["status"],
        }
        for key in ("selected_role", "missing_capability"):
            if key in state["result"]:
                summary[key] = state["result"][key]
        # Only a trusted capability executor can contribute a curated note.
        note = state["result"].get("memory_note")
        if isinstance(note, str) and note.strip() and len(note) <= 2000:
            summary["decision_note"] = note.strip()
        result = await memory.observe(
            f"Specialist workflow for task {state.get('task_id', '')}",
            json.dumps(summary),
            spec.role.value,
            state.get("session_id") or state.get("task_id", ""),
        )
        warnings = [*state.get("memory_warnings", [])]
        if result.warning:
            warnings.append(result.warning)
        return {"memory_persisted": result.persisted, "memory_warnings": warnings}

    builder = StateGraph(AgentState)
    builder.add_node("validate_request", validate)
    builder.add_node("clarify", clarify)
    builder.add_node("recall_memory", recall)
    builder.add_node(spec.work_node, work)
    builder.add_node("observe_outcome", observe)
    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges(
        "validate_request",
        lambda state: state["valid"],
        {
            True: "recall_memory",
            False: "clarify",
        },
    )
    builder.add_edge("clarify", END)
    builder.add_edge("recall_memory", spec.work_node)
    builder.add_edge(spec.work_node, "observe_outcome")
    builder.add_edge("observe_outcome", END)
    return builder.compile(checkpointer=checkpointer, name=f"switchcrew_{spec.role.value}")


async def invoke_agent_graph(graph, state: AgentState) -> AgentState:
    """Resume pending nodes or reuse a committed result for the same assignment.

    Stable assignment identity makes worker replay safe. A changed request must
    use a new handoff ID instead of silently replacing checkpointed inputs.
    """
    state = dict(state)
    if isinstance(state.get("question"), str):
        state["question"] = state["question"].strip()
    if isinstance(state.get("acceptance_criteria"), list):
        state["acceptance_criteria"] = [
            value.strip() if isinstance(value, str) else value
            for value in state["acceptance_criteria"]
        ]
    role = state.get("agent_role", "")
    expected_name = f"switchcrew_{role}"
    if getattr(graph, "name", expected_name) != expected_name:
        raise ValueError("Graph specialist differs from assignment role")
    config = graph_config(role, state.get("task_id", ""), state.get("handoff_id", ""))
    if graph.checkpointer is not None:
        saved = await graph.aget_state(config)
        if saved.values:
            for key in (
                "question",
                "requested_role",
                "acceptance_criteria",
                "task_id",
                "handoff_id",
                "agent_role",
                "session_id",
            ):
                previous = (
                    saved.values.get(key, "research")
                    if key == "requested_role"
                    else saved.values.get(key)
                )
                if key in state and previous != state[key]:
                    raise ValueError("Checkpoint assignment differs from incoming request")
            if saved.next:
                return await graph.ainvoke(None, config=config)
            return saved.values
    return await graph.ainvoke(state, config=config)


def graph_result(state: AgentState) -> dict:
    """Safe operator metadata without leaking recalled context or server errors."""
    result = dict(state["result"])
    result["memory"] = {
        "persisted": bool(state.get("memory_persisted", False)),
        "status": "degraded"
        if state.get("memory_warnings")
        else ("stored" if state.get("memory_persisted") else "disabled_or_no_write"),
    }
    return result
