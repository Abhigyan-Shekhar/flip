"""Private operator wiring for optional organization services; never handoff configuration."""

import json
import os
from pathlib import Path


def load_organization_config(path):
    if path is None:
        return {}
    path = Path(path)
    if path.stat().st_mode & 0o077 or path.stat().st_size > 262144:
        raise PermissionError("Organization configuration must be a bounded private file")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Organization configuration must be an object")
    return data


def meeting_service(store, settings, config, *, provider=None, enable_voice_provider=True):
    from .meetings import MeetingService

    meeting = config.get("meetings")
    if not meeting:
        return None
    if enable_voice_provider and provider is None and meeting.get("provider"):
        from .voice import ElevenLabsVoice

        provider_config = dict(meeting["provider"])
        key_env = provider_config.pop("api_key_env", "ELEVENLABS_API_KEY")
        provider = ElevenLabsVoice(os.environ.get(key_env, ""), **provider_config)
    targets = {"research": settings.research_agent_id, **settings.specialist_targets}
    if settings.engineering_agent_id:
        targets["engineering"] = settings.engineering_agent_id
    return MeetingService(
        store,
        provider,
        meeting["input_root"],
        pm_id=settings.pm_agent_id,
        notes_agent_id=meeting.get("notes_agent_id")
        or settings.specialist_targets.get("notes", {}).get("agent_id", ""),
        specialist_targets={
            role: target
            for role, target in targets.items()
            if target and role not in {"voice", "notes"}
        },
        human_approvers=settings.approval_credentials or settings.approval_user_ids,
        engineering_route={
            "qa": settings.qa_agent_id,
            "reviewer": settings.reviewer_agent_id,
            "release": settings.release_agent_id,
            "job_key": settings.orchestration_job_key,
            "repository": settings.release_repository,
            "base": settings.release_base,
        },
        max_seconds=meeting.get("max_seconds", 3600),
        max_audio_bytes=meeting.get("max_audio_bytes", 100 * 1024 * 1024),
        recording_manifest=meeting.get("recording_manifest", {}),
        require_recording_authorization=True,
    )


def knowledge_services(store, settings, config):
    from .knowledge import KnowledgeService
    from .lessons import LessonService
    from .memory import memory_from_settings

    knowledge = config.get("knowledge")
    if not knowledge:
        return None, None
    actors = knowledge.get("trusted_actors", {})
    return (
        KnowledgeService(
            store, trusted_actors=actors, import_roots=knowledge.get("import_roots", [])
        ),
        LessonService(store, trusted_actors=actors, memory=memory_from_settings(settings)),
    )


def employee_directory(store, config):
    from .employees import EmployeeDirectory

    employee = config.get("employees")
    if not employee:
        return None
    return EmployeeDirectory.from_file(
        store, employee["registrations_file"], allowed_assigners=employee["allowed_assigners"]
    )


def oss_service(store, settings, config, company_brain=None):
    from .oss import OSSMaintainer

    oss = config.get("oss")
    if not oss:
        return None
    targets = {"research": settings.research_agent_id, "engineering": settings.engineering_agent_id}
    targets.update({role: spec["agent_id"] for role, spec in settings.specialist_targets.items()})
    return OSSMaintainer(
        store,
        settings.pm_agent_id,
        targets,
        {
            repo: {"project_id": f"github.com/{repo}", **entry}
            for repo, entry in oss["repositories"].items()
        },
        {
            "qa": settings.qa_agent_id,
            "reviewer": settings.reviewer_agent_id,
            "release": settings.release_agent_id,
            "job_key": settings.orchestration_job_key,
            "repository": settings.release_repository,
            "base": settings.release_base,
        },
        company_brain=company_brain,
        source_context=oss.get("source", {}),
        self_bot_logins=oss.get("self_bot_logins", []),
    )


def configure_security(store, settings, config):
    gate = config.get("security_gate")
    if gate:
        from .security_gate import configure_security_policy

        configure_security_policy(
            store,
            settings.memory_project,
            settings.pm_agent_id,
            gate["security_id"],
            gate["policy"],
        )
        for repo, entry in config.get("oss", {}).get("repositories", {}).items():
            configure_security_policy(
                store,
                entry.get("project_id", f"github.com/{repo}"),
                settings.pm_agent_id,
                gate["security_id"],
                gate["policy"],
            )
    return gate
