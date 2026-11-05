"""Discord scan intent resolved only against operator-owned repository routes."""

import re
import subprocess
from uuid import NAMESPACE_URL, uuid5

_SCAN = re.compile(
    r"(?:hey\s+flip[,!]?\s+)?(?:please\s+)?(?:can you\s+)?scan\s+"
    r"(?P<repo>(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)|(?:this|the)\s+repository)"
    r"(?:\s+for\s+(?:vulnerabilities|security\s+(?:issues|findings)))?"
    r"(?:\s+and\s+report\s+back)?[.!?]*",
    re.I,
)


async def handle_scan_request(worker, source):
    match = _SCAN.fullmatch(source.body.strip())
    if not match:
        return False
    from .triggers import schedule_security_scan

    routes = worker.security_scans
    requested = match["repo"]
    matches = [
        (name, route)
        for name, route in routes.items()
        if name.casefold() == requested.casefold() or ("/" not in requested and len(routes) == 1)
    ]
    task_id = str(uuid5(NAMESPACE_URL, f"switchcrew:{source.room_id}:{source.message_id}"))
    context = {
        "room_id": source.room_id,
        "thread_id": source.thread_id,
        "message_id": source.message_id,
        "user_id": source.user_id,
        "channel_id": source.channel_id,
        "guild_id": source.guild_id,
        "project_id": worker.project_id,
    }
    if len(matches) != 1:
        text = "Name one configured repository as owner/repo so I can select its scanner."
    else:
        repository, route = matches[0]
        try:
            identity = route["security_id"]
            if identity not in worker.names or identity == worker.pm_id:
                raise ValueError("Independent Security route not registered")
            result = schedule_security_scan(
                worker.store,
                task_id=task_id,
                source=context,
                repository=repository,
                repo_root=route["repo_root"],
                pm_id=worker.pm_id,
                security_id=identity,
            )
            worker._reply(
                task_id,
                "security_scan_requested",
                f"Security scan queued for {repository}.\n"
                f"Revision: {result['revision']}\nTask: {task_id}\n"
                "Use ‘show me the findings’ to inspect the scanner result.",
                freeze=True,
            )
            return True
        except (
            ValueError,
            OSError,
            KeyError,
            TypeError,
            PermissionError,
            subprocess.SubprocessError,
        ):
            text = "The configured scanner route or pinned checkout is unavailable. No scan ran."
    worker.store.create_task(
        "Security scan needs configuration or repository selection",
        {**context, "command_receipt": True},
        [],
        task_id=task_id,
    )
    worker._reply(task_id, "scan_clarification", text, freeze=True)
    return True
