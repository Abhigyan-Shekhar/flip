"""Authenticated GitHub event intake and exact-SHA triggered QA execution."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import tempfile
from contextlib import nullcontext
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .change_intelligence import _bounded_communicate, _revision
from .engineering import git

MAX_WEBHOOK_BYTES = 1_000_000
MAX_SUBPROCESS_OUTPUT = 256_000
_DELIVERY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)[\"']?\s*[:=]\s*[\"']?\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)
_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


def _expand_args(
    configured: list[str], checkout: Path, revision: str, *, isolated: bool = False
) -> list[str]:
    replacements = {"repo_root": "/workspace" if isolated else str(checkout), "revision": revision}
    result = []
    for arg in configured:
        keys = _PLACEHOLDER.findall(arg)
        if any(key not in replacements for key in keys):
            raise ValueError("Unsupported operator command placeholder")
        result.append(_PLACEHOLDER.sub(lambda match: replacements[match.group(1)], arg))
    return result


class WebhookRejected(PermissionError):
    """Signature, identity, event, or scope validation failed."""


def _header(headers, name: str) -> str:
    return next((str(value) for key, value in headers.items() if key.lower() == name), "")


def _payload_scope(event: str, payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    repository = payload.get("repository")
    repo_name = repository.get("full_name") if isinstance(repository, dict) else None
    if event == "push":
        revision = payload.get("after")
        event_identity = {"ref": payload.get("ref")}
    elif event == "pull_request":
        action = payload.get("action")
        if action not in {"opened", "reopened", "synchronize", "ready_for_review"}:
            raise WebhookRejected("pull_request_action_not_authorized")
        pr = payload.get("pull_request")
        head = pr.get("head") if isinstance(pr, dict) else None
        revision = head.get("sha") if isinstance(head, dict) else None
        event_identity = {"pull_request": pr.get("number")} if isinstance(pr, dict) else {}
    else:
        raise WebhookRejected("event_type_not_authorized")
    if not isinstance(repo_name, str) or not isinstance(revision, str) or not _revision(revision):
        raise WebhookRejected("repository_or_exact_revision_missing")
    if set(revision) == {"0"}:
        raise WebhookRejected("deletion_event_has_no_testable_revision")
    if not event_identity or any(
        value is None or not str(value) for value in event_identity.values()
    ):
        raise WebhookRejected("event_identity_missing")
    return repo_name, revision.lower(), event_identity


def _create_trigger_handoff(
    store,
    task_id: str,
    delivery_hash: str,
    recipient: str,
    sender: str,
    kind: str,
    repo: str,
    revision: str,
):
    handoff_id = f"gh-{delivery_hash}-{kind}"
    existing = store.get_handoff(handoff_id)
    if existing:
        return existing
    task = store.get_task(task_id)
    return store.create_handoff(
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        question=f"Run configured {kind.upper()} checks for {repo} at exact revision {revision}.",
        acceptance_criteria=[
            "Use only operator-configured commands or scanners.",
            "Test exactly the pinned revision in an isolated checkout.",
            "Return structured findings; missing tools or failed commands are blocked, "
            "never clean.",
        ],
        expected_version=task["version"],
        handoff_id=handoff_id,
    )


def accept_github_webhook(
    store,
    headers,
    body: bytes,
    *,
    secret: str | bytes,
    repository: str,
    qa_recipient: str,
    security_recipient: str,
    sender: str = "pm",
    source_context: dict[str, str] | None = None,
    project_id: str | None = None,
    source_mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a signed GitHub delivery and durably enqueue QA/Security via Store outbox."""
    if not isinstance(body, bytes) or len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookRejected("webhook_body_invalid_or_too_large")
    signing_secret = secret.encode() if isinstance(secret, str) else secret
    if not signing_secret or len(signing_secret) < 32:
        raise WebhookRejected("webhook_secret_invalid")
    supplied = _header(headers, "x-hub-signature-256")
    expected = "sha256=" + hmac.new(signing_secret, body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise WebhookRejected("signature_invalid")
    event = _header(headers, "x-github-event")
    delivery = _header(headers, "x-github-delivery")
    if not _DELIVERY.fullmatch(delivery):
        raise WebhookRejected("delivery_id_invalid")
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        raise WebhookRejected("payload_invalid") from None
    if not isinstance(payload, dict):
        raise WebhookRejected("payload_invalid")
    repo_name, revision, event_identity = _payload_scope(event, payload)
    if repo_name.casefold() != repository.casefold():
        raise WebhookRejected("repository_scope_mismatch")
    if (
        not qa_recipient.strip()
        or not security_recipient.strip()
        or qa_recipient == security_recipient
        or not sender.strip()
    ):
        raise WebhookRejected("operator_recipients_invalid")
    allowed_source_keys = {
        "switch_room_id",
        "discord_channel_id",
        "discord_thread_id",
        "original_message_id",
    }
    if source_context is not None and (
        not isinstance(source_context, dict)
        or any(key not in allowed_source_keys for key in source_context)
        or any(not isinstance(value, str) or len(value) > 200 for value in source_context.values())
    ):
        raise WebhookRejected("operator_source_context_invalid")
    if project_id is not None and (
        not isinstance(project_id, str)
        or not project_id.strip()
        or len(project_id) > 200
        or _SECRET.search(project_id)
    ):
        raise WebhookRejected("operator_project_id_invalid")
    reserved = {
        "trigger_type",
        "event",
        "repository",
        "revision",
        "event_identity",
        "request_context",
        "room_id",
        "channel_id",
        "thread_id",
        "message_id",
        "parent_task_id",
        "plan_step",
    }
    if source_mapping is not None and (
        not isinstance(source_mapping, dict)
        or set(source_mapping).intersection(reserved)
        or len(json.dumps(source_mapping, default=str)) > 4000
        or _SECRET.search(json.dumps(source_mapping, default=str))
    ):
        raise WebhookRejected("operator_source_mapping_invalid")

    delivery_hash = hashlib.sha256(delivery.encode()).hexdigest()[:32]
    store.receive_inbox(
        f"github-delivery-{delivery_hash}",
        "github-webhook",
        {"body_sha256": hashlib.sha256(body).hexdigest(), "event": event},
    )
    event_key = json.dumps(
        [repo_name.casefold(), event, revision, event_identity],
        sort_keys=True,
        separators=(",", ":"),
    )
    event_hash = hashlib.sha256(event_key.encode()).hexdigest()[:32]
    task_id = "github-trigger-" + event_hash
    context = source_context or {}
    source = {
        **(source_mapping or {}),
        **({"project_id": project_id} if project_id else {}),
        **({"room_id": context["switch_room_id"]} if context.get("switch_room_id") else {}),
        **(
            {"channel_id": context["discord_channel_id"]}
            if context.get("discord_channel_id")
            else {}
        ),
        **({"thread_id": context["discord_thread_id"]} if context.get("discord_thread_id") else {}),
        **(
            {"message_id": context["original_message_id"]}
            if context.get("original_message_id")
            else {}
        ),
        "trigger_type": "github_webhook",
        "event": event,
        "repository": repo_name,
        "revision": revision,
        "event_identity": event_identity,
        "request_context": source_context or {},
    }
    task = store.create_task(
        objective=f"Verify GitHub {event} for {repo_name} at {revision}",
        source=source,
        acceptance_criteria=[
            "QA runs the operator-configured test suite at the exact revision.",
            "Security runs configured scanners at the exact revision.",
            "Both reports include explicit blocked status when checks are unavailable.",
        ],
        task_id=task_id,
    )
    if task["status"] == "DRAFT":
        task = store.transition_task(task_id, "PLANNED", task["version"], {"trigger": "github"})
    if task["status"] == "PLANNED":
        task = store.transition_task(task_id, "IN_PROGRESS", task["version"], {"trigger": "github"})
    qa_id = f"gh-{event_hash}-qa"
    security_id = f"gh-{event_hash}-security"
    duplicate = bool(store.get_handoff(qa_id) and store.get_handoff(security_id))
    qa = _create_trigger_handoff(
        store, task_id, event_hash, qa_recipient, sender, "qa", repo_name, revision
    )
    security = _create_trigger_handoff(
        store, task_id, event_hash, security_recipient, sender, "security", repo_name, revision
    )
    store.complete_inbox(f"github-delivery-{delivery_hash}", "github-webhook")
    return {
        "status": "duplicate" if duplicate else "accepted",
        "task_id": task_id,
        "revision": revision,
        "repository": repo_name,
        "qa_handoff_id": qa["id"],
        "security_handoff_id": security["id"],
        "outbox_pending": True,
    }


def _redact(value: bytes, limit: int = 8000) -> str:
    text = value.decode("utf-8", errors="replace")
    return _SECRET.sub("[REDACTED]", text)[:limit]


async def _process(command: list[str], cwd: Path, timeout: float, home: Path | None = None):
    home = home or (cwd.parent / "home")
    home.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env={"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _, exceeded = await _bounded_communicate(
            process, timeout=timeout, limit=MAX_SUBPROCESS_OUTPUT
        )
    except (TimeoutError, asyncio.CancelledError):
        raise
    if exceeded:
        return -1, "subprocess output exceeded the configured limit"
    return process.returncode, _redact(output)


class TriggeredQAExecutor:
    """Run only operator commands for a persisted GitHub trigger assignment."""

    def __init__(
        self,
        store,
        repo_root: str | Path,
        recipient: str,
        repository: str,
        commands: list[list[str]],
        timeout: float = 120,
        authorize: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
        isolation=None,
    ):
        self.store = store
        self.root = Path(repo_root).resolve()
        self.recipient = recipient
        self.repository = repository
        self.commands = commands
        self.timeout = timeout
        self.authorize = authorize
        self.isolation = isolation

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        handoff_id = state.get("handoff_id") if isinstance(state, dict) else None
        handoff = self.store.get_handoff(handoff_id) if handoff_id else None
        if not handoff or handoff["recipient"] != self.recipient:
            return {"status": "blocked", "reason": "trigger_handoff_scope_denied"}
        task = self.store.get_task(handoff["task_id"])
        source = task.get("source", {}) if task else {}
        if (
            not task
            or task.get("status") not in {"IN_PROGRESS", "QA_REVIEW"}
            or handoff.get("status") not in {"pending", "accepted"}
            or source.get("trigger_type") != "github_webhook"
            or source.get("repository") != self.repository
            or source.get("revision") is None
            or Path(source.get("repo_root", self.root)).resolve() != self.root
        ):
            return {"status": "blocked", "reason": "persisted_trigger_record_required"}
        if self.authorize and self.authorize(task, handoff) is not True:
            return {"status": "blocked", "reason": "trigger_authorization_denied"}
        revision = source["revision"]
        if (
            not _revision(revision)
            or not self.root.is_dir()
            or not self.commands
            or not isinstance(self.timeout, (int, float))
            or isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
            or any(
                not command or any(not isinstance(arg, str) for arg in command)
                for command in self.commands
            )
        ):
            return {
                "status": "blocked_configuration",
                "reason": "operator_qa_configuration_required",
                "tested_revision": revision,
            }
        records = []
        try:
            with tempfile.TemporaryDirectory(prefix="switchcrew-trigger-qa-") as temp:
                for index, configured in enumerate(self.commands):
                    checkout = Path(temp) / f"checkout-{index}"
                    home = Path(temp) / f"home-{index}"
                    home.mkdir()
                    clone_command = [
                        "git",
                        "clone",
                        "--no-hardlinks",
                        "--no-checkout",
                        str(self.root),
                        str(checkout),
                    ]
                    code, _ = await _process(clone_command, Path(temp), self.timeout, home)
                    if code != 0:
                        return {
                            "status": "blocked",
                            "reason": "isolated_clone_failed",
                            "tested_revision": revision,
                        }
                    code, _ = await _process(
                        ["git", "-C", str(checkout), "checkout", "--detach", revision],
                        Path(temp),
                        self.timeout,
                        home,
                    )
                    if code != 0 or git(checkout, "rev-parse", "HEAD") != revision:
                        return {
                            "status": "blocked",
                            "reason": "exact_revision_checkout_failed",
                            "tested_revision": revision,
                        }
                    command = _expand_args(
                        configured, checkout, revision, isolated=self.isolation is not None
                    )
                    context = (
                        self.isolation.launch(command, checkout, writable=True)
                        if self.isolation is not None
                        else nullcontext(command)
                    )
                    # Commands are operator-owned argv. Webhook payloads never reach this list.
                    with context as execution:
                        code, output = await _process(execution, checkout, self.timeout, home)
                    from .qa import generated_inputs

                    unchanged = (
                        git(checkout, "rev-parse", "HEAD") == revision
                        and not git(checkout, "diff", "HEAD")
                        and not generated_inputs(checkout)
                    )
                    records.append(
                        {
                            "index": index,
                            "exit_code": code,
                            "output": output,
                            "revision_unchanged": bool(unchanged),
                        }
                    )
                    current = self.store.get_task(task["id"])
                    if not current or current["status"] not in {"IN_PROGRESS", "QA_REVIEW"}:
                        return {
                            "status": "blocked",
                            "reason": "trigger_task_cancelled",
                            "tested_revision": revision,
                            "commands": records,
                        }
                    if not unchanged:
                        break
        except TimeoutError:
            return {
                "status": "qa_failed",
                "reason": "operator_command_timed_out",
                "tested_revision": revision,
                "commands": records,
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            return {
                "status": "qa_failed",
                "reason": "isolated_test_execution_failed",
                "tested_revision": revision,
                "commands": records,
            }
        passed = len(records) == len(self.commands) and all(
            record["exit_code"] == 0 and record["revision_unchanged"] for record in records
        )
        return {
            "status": "verified_success" if passed else "qa_failed",
            "tested_revision": revision,
            "handoff_id": handoff_id,
            "commands": records,
            "isolated_checkout": True,
        }


class TriggeredSecurityExecutor:
    """Gate SecurityRunner on a persisted, correctly scoped trigger handoff."""

    def __init__(
        self,
        store,
        repo_root: str | Path,
        recipient: str,
        repository: str,
        runner,
        authorize: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    ):
        self.store = store
        self.root = Path(repo_root).resolve()
        self.recipient = recipient
        self.repository = repository
        self.runner = runner
        self.authorize = authorize

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        handoff_id = state.get("handoff_id") if isinstance(state, dict) else None
        handoff = self.store.get_handoff(handoff_id) if handoff_id else None
        if not handoff or handoff["recipient"] != self.recipient:
            return {"status": "blocked", "reason": "trigger_handoff_scope_denied"}
        task = self.store.get_task(handoff["task_id"])
        source = task.get("source", {}) if task else {}
        if (
            not task
            or task.get("status") not in {"IN_PROGRESS", "QA_REVIEW"}
            or handoff.get("status") not in {"pending", "accepted"}
            or source.get("trigger_type") != "github_webhook"
            or source.get("repository") != self.repository
            or not _revision(source.get("revision"))
        ):
            return {"status": "blocked", "reason": "persisted_trigger_record_required"}
        if self.authorize and self.authorize(task, handoff) is not True:
            return {"status": "blocked", "reason": "trigger_authorization_denied"}
        report = await self.runner.run(self.root, source["revision"])
        return {**report.to_dict(), "handoff_id": handoff_id}
