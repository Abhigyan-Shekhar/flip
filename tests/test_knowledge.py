import json

import pytest

from switchcrew.knowledge import KnowledgeService
from switchcrew.store import Store


def actors():
    return {
        "alice": {"projects": ["project-a"], "teams": ["red"], "orgs": ["org-a"]},
        "bob": {"projects": ["project-a"], "teams": ["red"], "orgs": ["org-a"]},
        "reviewer": {
            "projects": ["project-a"],
            "teams": ["red"],
            "orgs": ["org-a"],
            "approve_personal": ["alice"],
            "approve_teams": ["red"],
            "approve_orgs": ["org-a"],
            "approve_projects": ["project-a"],
        },
        "outsider": {"projects": ["project-b"], "teams": ["blue"], "orgs": ["org-b"]},
    }


def setup(tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    store = Store(tmp_path / "state.sqlite3")
    service = KnowledgeService(store, trusted_actors=actors(), import_roots=[root])
    return store, root, service


def test_personal_note_requires_owner_approval_and_keeps_provenance(tmp_path):
    store, root, service = setup(tmp_path)
    path = root / "decision.md"
    path.write_text("The rollback requires restoring the previous revision.", encoding="utf-8")
    note = service.ingest_file(
        path,
        actor_id="alice",
        project_id="project-a",
        scope="personal",
        source_owner="Alice",
        source_timestamp="2026-11-02T10:15:00+05:30",
    )
    assert note["approval_status"] == "pending"
    assert note["owner_id"] == "alice"
    assert note["source_owner"] == "Alice"
    assert note["source_timestamp"] == "2026-11-02T04:45:00+00:00"
    assert (
        service.retrieve("rollback revision", actor_id="alice", project_id="project-a")["documents"]
        == []
    )
    with pytest.raises(PermissionError):
        service.approve_document(note["id"], actor_id="bob")
    approved = service.approve_document(note["id"], actor_id="alice")
    assert approved["verification_status"] == "snapshot_hash_verified"
    assert (
        service.retrieve("rollback revision", actor_id="bob", project_id="project-a")["documents"]
        == []
    )
    result = service.retrieve("rollback revision", actor_id="alice", project_id="project-a")
    assert result["trust"] == "untrusted_external_source_material"
    assert result["documents"][0]["content_sha256"] == note["content_sha256"]
    store.close()


def test_team_scope_requires_configured_approver_and_membership(tmp_path):
    store, root, service = setup(tmp_path)
    (root / "runbook.txt").write_text("Restart the worker after repairing its lease.")
    note = service.ingest_file(
        "runbook.txt",
        actor_id="alice",
        project_id="project-a",
        scope="team",
        scope_id="red",
        source_owner="operations team",
    )
    assert (
        service.retrieve("restart worker", actor_id="bob", project_id="project-a")["documents"]
        == []
    )
    with pytest.raises(PermissionError):
        service.approve_document(note["id"], actor_id="bob")
    service.approve_document(note["id"], actor_id="reviewer")
    assert (
        len(service.retrieve("restart worker", actor_id="bob", project_id="project-a")["documents"])
        == 1
    )
    assert (
        service.retrieve("restart worker", actor_id="outsider", project_id="project-b")["documents"]
        == []
    )
    store.close()


def test_org_and_project_scopes_are_visible_only_within_their_acl(tmp_path):
    store, root, service = setup(tmp_path)
    org_file = root / "org.txt"
    org_file.write_text("Organization wide rollback checklist.")
    project_file = root / "project.md"
    project_file.write_text("Project release checklist.")
    org_note = service.ingest_file(
        org_file,
        actor_id="alice",
        project_id="project-a",
        scope="org",
        scope_id="org-a",
        source_owner="Reliability team",
    )
    project_note = service.ingest_file(
        project_file,
        actor_id="alice",
        project_id="project-a",
        scope="project",
        source_owner="Project lead",
    )
    service.approve_document(org_note["id"], actor_id="reviewer")
    service.approve_document(project_note["id"], actor_id="reviewer")
    assert (
        len(
            service.retrieve("rollback checklist", actor_id="bob", project_id="project-a")[
                "documents"
            ]
        )
        == 1
    )
    assert (
        service.retrieve("rollback checklist", actor_id="outsider", project_id="project-b")[
            "documents"
        ]
        == []
    )
    with pytest.raises(PermissionError):
        service.retrieve("rollback checklist", actor_id="unknown", project_id="project-a")
    store.close()


def test_document_content_cannot_assign_scope_or_owner_and_duplicate_import_survives_restart(
    tmp_path,
):
    store, root, service = setup(tmp_path)
    path = root / "note.json"
    path.write_text(
        json.dumps(
            {"scope": "org", "owner_id": "reviewer", "note": "Use revision pinned evidence."}
        )
    )
    first = service.ingest_file(
        path,
        actor_id="alice",
        project_id="project-a",
        scope="personal",
        source_owner="External author",
    )
    service.approve_document(first["id"], actor_id="alice")
    restarted = KnowledgeService(store, trusted_actors=actors(), import_roots=[root])
    duplicate = restarted.ingest_file(
        path,
        actor_id="alice",
        project_id="project-a",
        scope="personal",
        source_owner="External author",
    )
    assert duplicate["id"] == first["id"]
    assert duplicate["approval_status"] == "approved"
    assert duplicate["scope"] == "personal"
    assert duplicate["owner_id"] == "alice"
    store.close()


def test_rejects_traversal_symlinks_secrets_and_untrusted_actor(tmp_path):
    store, root, service = setup(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private note")
    with pytest.raises(ValueError, match="traversal"):
        service.ingest_file(
            "../outside.txt", actor_id="alice", project_id="project-a", source_owner="owner"
        )
    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink"):
        service.ingest_file(
            "link.txt", actor_id="alice", project_id="project-a", source_owner="owner"
        )
    secret_file = root / "private.txt"
    secret_file.write_text("password=hunter2")
    with pytest.raises(ValueError, match="sensitive"):
        service.ingest_file(
            secret_file, actor_id="alice", project_id="project-a", source_owner="owner"
        )
    with pytest.raises(PermissionError):
        service.ingest_file(
            secret_file, actor_id="outsider", project_id="project-a", source_owner="owner"
        )
    with pytest.raises(ValueError):
        service.ingest_file(
            secret_file, actor_id="alice", project_id="project-a", source_owner="Bearer secret"
        )
    store.close()


def test_related_task_must_exist_and_be_same_project_when_tagged(tmp_path):
    store, root, service = setup(tmp_path)
    (root / "note.md").write_text("Current project handoff process.")
    task = store.create_task("Project work", {"project_id": "project-a"}, [], task_id="task-a")
    assert task["id"] == "task-a"
    with pytest.raises(ValueError, match="existing task"):
        service.ingest_file(
            "note.md",
            actor_id="alice",
            project_id="project-a",
            source_owner="owner",
            related_task_id="missing",
        )
    store.create_task("Other project work", {"project_id": "project-b"}, [], task_id="task-b")
    with pytest.raises(PermissionError, match="another project"):
        service.ingest_file(
            "note.md",
            actor_id="alice",
            project_id="project-a",
            source_owner="owner",
            related_task_id="task-b",
        )
    store.close()
