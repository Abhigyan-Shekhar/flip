"""The complete specification roster, with explicit specialist capabilities."""

from dataclasses import dataclass
from enum import StrEnum


class AgentRole(StrEnum):
    VOICE = "voice"
    COMMUNITY = "community"
    PM = "pm"
    RESEARCH = "research"
    ENGINEERING = "engineering"
    QA = "qa"
    RELEASE = "release"
    DOCUMENTATION = "documentation"
    KNOWLEDGE = "knowledge"
    ONBOARDING = "onboarding"
    MODERATION = "moderation"
    SECURITY = "security"
    SCHEDULING = "scheduling"
    ANALYTICS = "analytics"
    INCIDENT = "incident"


@dataclass(frozen=True)
class AgentSpec:
    role: AgentRole
    name: str
    capability: str
    work_node: str
    core: bool = True


_SPECS = (
    AgentSpec(AgentRole.VOICE, "Voice Agent", "consented_voice_io", "prepare_voice"),
    AgentSpec(AgentRole.COMMUNITY, "Community Agent", "support_triage", "triage_support"),
    AgentSpec(AgentRole.PM, "PM Agent", "research_planning", "plan_handoff"),
    AgentSpec(AgentRole.RESEARCH, "Research Agent", "source_retrieval", "retrieve_evidence"),
    AgentSpec(AgentRole.ENGINEERING, "Engineering Agent", "switch_codex_session", "request_codex"),
    AgentSpec(AgentRole.QA, "QA Agent", "isolated_qa_runner", "prepare_qa"),
    AgentSpec(AgentRole.RELEASE, "Release Agent", "authorized_publication", "prepare_release"),
    AgentSpec(
        AgentRole.DOCUMENTATION, "Documentation Agent", "documentation_writer", "draft_docs", False
    ),
    AgentSpec(
        AgentRole.KNOWLEDGE, "Knowledge Agent", "knowledge_curation", "curate_knowledge", False
    ),
    AgentSpec(
        AgentRole.ONBOARDING, "Onboarding Agent", "onboarding_guide", "prepare_onboarding", False
    ),
    AgentSpec(
        AgentRole.MODERATION, "Moderation Agent", "moderation_review", "review_moderation", False
    ),
    AgentSpec(AgentRole.SECURITY, "Security Agent", "security_analysis", "analyze_security", False),
    AgentSpec(
        AgentRole.SCHEDULING, "Scheduling Agent", "calendar_access", "prepare_schedule", False
    ),
    AgentSpec(AgentRole.ANALYTICS, "Analytics Agent", "metrics_access", "analyze_metrics", False),
    AgentSpec(AgentRole.INCIDENT, "Incident Agent", "incident_triage", "triage_incident", False),
)
AGENTS = {spec.role.value: spec for spec in _SPECS}
AGENT_ROLES = tuple(AGENTS)
