"""Deterministic incident team selection and work planning."""

from copy import deepcopy


class TeamAssembler:
    """Build a bounded incident plan from configured capabilities only."""

    ROLE_CAPABILITIES = {
        "incident": {"triage", "impact_assessment", "timeline"},
        "research": {"evidence_gathering", "root_cause"},
        "security": {"security_risk", "threat_assessment"},
        "engineering": {"implementation"},
        "documentation": {"runbook", "incident_summary"},
    }
    CATEGORY_CAPABILITIES = {
        "service_failure": {"triage", "timeline", "evidence_gathering"},
        "security": {"triage", "timeline", "evidence_gathering", "security_risk"},
        "bug": {"triage", "timeline", "evidence_gathering"},
        "support": {"triage", "timeline", "evidence_gathering"},
    }

    def __init__(self, configured_roles):
        self.configured_roles = frozenset(configured_roles)

    def assemble(self, incident):
        if not isinstance(incident, dict):
            raise ValueError("incident must be an object")
        category = incident.get("category")
        severity = incident.get("severity")
        summary = incident.get("summary")
        if category not in {"service_failure", "security", "bug", "support"}:
            raise ValueError("unsupported incident category")
        if severity not in {"SEV1", "SEV2", "SEV3"}:
            raise ValueError("unsupported incident severity")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1200:
            raise ValueError("incident summary is required and limited to 1200 characters")
        evidence = incident.get("evidence_refs", [])
        if (
            not isinstance(evidence, list)
            or len(evidence) > 20
            or any(not isinstance(x, str) or len(x) > 120 for x in evidence)
        ):
            raise ValueError("evidence_refs must contain at most 20 strings of 120 characters")

        required_caps = self.CATEGORY_CAPABILITIES[category]
        desired = []
        covered = set()
        for role, capabilities in self.ROLE_CAPABILITIES.items():
            if role in self.configured_roles and capabilities & required_caps:
                desired.append(role)
                covered |= capabilities
        mandatory = ["incident", "research"] + (["security"] if category == "security" else [])
        for role in mandatory:
            if role not in desired:
                desired.append(role)
        if incident.get("needs_engineering") is True and "engineering" not in desired:
            desired.append("engineering")
        # Incident triage and evidence review are core requirements, not optional fallbacks.
        missing_caps = required_caps - covered
        selected, missing = [], []
        reasons = {}
        for role in desired:
            if role in self.configured_roles:
                selected.append(role)
                reasons[role] = {
                    "capabilities": sorted(self.ROLE_CAPABILITIES[role]),
                    "reason": self._reason(role, category, incident),
                }
            else:
                missing.append(role)

        steps = []
        investigators = [r for r in selected if r != "engineering"]
        for role in investigators:
            # Investigation specialists can gather evidence in parallel.
            identity = f"step-{len(steps) + 1}"
            responsibilities = {
                "incident": "Establish incident facts, impact, and a timestamped event timeline.",
                "research": "Corroborate claims against supplied evidence and identify unknowns.",
                "security": "Assess security exposure and preserve relevant indicators.",
            }[role]
            deliverable = {
                "incident": "incident timeline and impact findings",
                "research": "evidence-backed findings with source references",
                "security": "security risk assessment and indicators",
            }[role]
            criterion = f"{responsibilities} Incident: {summary}. Evidence refs: {evidence}"
            steps.append(
                {
                    "id": identity,
                    "role": role,
                    "criteria": [criterion[:2000]],
                    "depends_on": [],
                    "responsibilities": responsibilities,
                    "deliverable": deliverable,
                }
            )

        if "engineering" in selected:
            steps.append(
                {
                    "id": f"step-{len(steps) + 1}",
                    "role": "engineering",
                    "criteria": [f"Investigate and propose a fix for: {summary}"],
                    "depends_on": [s["id"] for s in steps],
                    "responsibilities": (
                        "Implement a scoped fix only through the approval-gated engineering route."
                    ),
                    "deliverable": "approved engineering workflow receipt",
                }
            )
        if steps and "documentation" in self.configured_roles:
            steps.append(
                {
                    "id": f"step-{len(steps) + 1}",
                    "role": "documentation",
                    "criteria": [
                        "Prepare an incident summary and runbook updates from "
                        f"completed findings: {summary}"
                    ],
                    "depends_on": [s["id"] for s in steps],
                    "responsibilities": "Record verified findings and operational follow-up.",
                    "deliverable": "incident summary and runbook updates",
                }
            )
            selected.append("documentation")
            reasons["documentation"] = {
                "capabilities": sorted(self.ROLE_CAPABILITIES["documentation"]),
                "reason": "Produces durable incident artifacts after required findings.",
            }
        pipeline_missing = []
        if "engineering" in selected:
            pipeline_missing = [
                r
                for r in ("qa", "reviewer", "release")
                if not incident.get("engineering_route", {}).get(r)
            ]
            for role in ("qa", "reviewer", "release"):
                identity = incident.get("engineering_route", {}).get(role)
                if identity:
                    selected.append(role)
                    reasons[role] = {
                        "capabilities": [f"engineering_{role}"],
                        "reason": "Required member of the approval-gated engineering route.",
                    }
        explanation_parts = []
        if missing:
            explanation_parts.append("Required configured roles unavailable: " + ", ".join(missing))
        if missing_caps:
            explanation_parts.append(
                "No configured role covers required capabilities: "
                + ", ".join(sorted(missing_caps))
            )
        if pipeline_missing:
            explanation_parts.append(
                "Engineering route identities unavailable: " + ", ".join(pipeline_missing)
            )
        explanation = "; ".join(explanation_parts)
        if not steps:
            explanation = explanation or "No configured role has a suitable incident capability."
        return {
            "planner": "incident_capability_rules_v1",
            "incident": deepcopy(incident),
            "team": selected,
            "selection_reasons": reasons,
            "missing_roles": missing,
            "blocked_reason": explanation,
            "steps": steps,
        }

    @staticmethod
    def _reason(role, category, incident):
        return {
            "incident": f"Leads triage for {category} incidents.",
            "research": "Provides evidence gathering and timeline/root-cause investigation.",
            "security": "Security category requires an explicit risk assessment.",
            "engineering": (
                "Incident explicitly requests engineering work; route remains approval gated."
            ),
        }[role]
