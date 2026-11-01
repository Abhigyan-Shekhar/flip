"""Approval-gated publication of an independently tested commit as a draft PR."""

import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx

from .engineering import assignment_id, git, outcome


class GitHubPublisher:
    """Fixed GitHub origin and repository. Never run hooks in agent-authored checkouts."""

    def __init__(self, repository, base, token, *, transport=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("An explicit owner/repository is required")
        if not base or base.startswith("-") or any(c.isspace() for c in base):
            raise ValueError("An explicit base branch is required")
        self.repository, self.base, self.token = repository, base, token
        self.transport = transport

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=30,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            transport=self.transport,
        ) as client:
            response = await client.request(method, f"/repos/{self.repository}/{path}", **kwargs)
            if response.status_code == 404 and method == "GET":
                return None
            response.raise_for_status()
            return response.json()

    async def branch_revision(self, branch):
        data = await self.request("GET", "git/ref/heads/" + quote(branch, safe=""))
        return data["object"]["sha"] if data else None

    async def push(self, checkout, revision, branch):
        existing = await self.branch_revision(branch)
        if existing:
            if existing != revision:
                raise ValueError("Release branch already points at another revision")
            return
        await asyncio.to_thread(self._push_git, checkout, revision, branch)
        if await self.branch_revision(branch) != revision:
            raise ValueError("Remote release revision differs")

    def _push_git(self, checkout, revision, branch):
        # A fresh bare repository has no agent-authored hooks or remote settings.
        # Credentials exist only in the push process, never in the checkout/config/argv.
        env = {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        with tempfile.TemporaryDirectory(prefix="switchcrew-release-") as directory:
            bare = Path(directory) / "publish.git"
            for args in (
                ["init", "--bare", str(bare)],
                [
                    "-C",
                    str(bare),
                    "-c",
                    "protocol.file.allow=always",
                    "fetch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    str(Path(checkout).resolve()),
                    revision,
                ],
            ):
                subprocess.run(
                    ["git", *args], env=env, check=True, capture_output=True, timeout=120
                )
            auth = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            push_env = {
                **env,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": "Authorization: Basic " + auth,
            }
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(bare),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "push",
                    "--no-verify",
                    f"https://github.com/{self.repository}.git",
                    f"{revision}:refs/heads/{branch}",
                ],
                env=push_env,
                check=True,
                capture_output=True,
                timeout=120,
            )

    def validate_pr(self, pr, branch, revision):
        if not (
            pr.get("draft") is True
            and pr.get("state") == "open"
            and pr.get("head", {}).get("sha") == revision
            and pr.get("head", {}).get("ref") == branch
            and pr.get("head", {}).get("repo", {}).get("full_name", "").lower()
            == self.repository.lower()
            and pr.get("base", {}).get("ref") == self.base
            and pr.get("base", {}).get("repo", {}).get("full_name", "").lower()
            == self.repository.lower()
            and re.fullmatch(
                r"https://github\.com/" + re.escape(self.repository) + r"/pull/[0-9]+",
                pr.get("html_url", ""),
                re.IGNORECASE,
            )
        ):
            raise ValueError("GitHub PR is not the approved draft revision")
        return pr

    async def find(self, branch, revision):
        pulls = await self.request(
            "GET",
            "pulls",
            params={
                "head": self.repository.split("/")[0] + ":" + branch,
                "base": self.base,
                "state": "all",
                "per_page": 100,
            },
        )
        if not pulls:
            return None
        if len(pulls) != 1:
            raise ValueError("Ambiguous release PRs require operator reconciliation")
        return self.validate_pr(pulls[0], branch, revision)

    async def create(self, branch, revision, title, body):
        pr = await self.request(
            "POST",
            "pulls",
            json={
                "title": title[:200],
                "head": branch,
                "base": self.base,
                "body": body,
                "draft": True,
                "maintainer_can_modify": False,
            },
        )
        return self.validate_pr(pr, branch, revision)


class ReleaseExecutor:
    def __init__(self, workflow, ledger, agent, publisher):
        self.workflow, self.ledger = workflow, ledger
        self.agent, self.publisher = agent, publisher

    async def __call__(self, state):
        identity = "release-" + assignment_id(state["handoff_id"])
        data = self.workflow.get(state["task_id"])
        if data and data.get("receipt") and data.get("release_handoff") == state["handoff_id"]:
            return outcome("completed", identity, receipt=data["receipt"])
        try:
            data = self.workflow.require_release(state, self.agent)
            if (
                data["spec"]["repository"] != self.publisher.repository
                or data["spec"]["base"] != self.publisher.base
            ):
                raise PermissionError("Release destination differs from approved request")
            implementation = self.ledger.get(assignment_id(data["engineering_handoff"]))
            implementation = (implementation or {}).get("result", {})
            revision = data["revision"]
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Full commit SHA required")
            if (
                implementation.get("status") != "implemented"
                or implementation.get("final_revision") != revision
                or git(implementation["checkout"], "rev-parse", "HEAD") != revision
            ):
                return outcome(
                    "stale_qa",
                    identity,
                    tested_revision=revision,
                    reason="implementation_changed_since_qa",
                )
            branch = "codex/switchcrew-" + assignment_id(state["handoff_id"])
            record = self.ledger.reserve(
                identity,
                {
                    "revision": revision,
                    "repository": self.publisher.repository,
                    "base": self.publisher.base,
                    "approval": data["release_approval"],
                    "branch": branch,
                },
            )
            # Claim publication transactionally. Cancellation is refused once this
            # begins; an in-flight network request cannot be safely undone.
            self.workflow.start_release(state, self.agent)
            if record["phase"] == "reserved":
                self.ledger.claim(identity, "reserved", "pushing")
            if self.ledger.get(identity)["phase"] == "pushing":
                self.workflow.require_release(state, self.agent)
                await self.publisher.push(implementation["checkout"], revision, branch)
                self.ledger.claim(identity, "pushing", "branch_ready")
            pr = await self.publisher.find(branch, revision)
            if pr is None:
                if not self.ledger.claim(identity, "branch_ready", "creating_pr"):
                    return outcome(
                        "pending_execution", identity, reason="reconcile_uncertain_pr_creation"
                    )
                self.workflow.require_release(state, self.agent)
                evidence = self.workflow.evidence(state["task_id"])
                body = json.dumps(
                    {
                        "task_id": state["task_id"],
                        "commit_sha": revision,
                        "test_count": len(evidence["commands"]),
                        "tests": [
                            {
                                "command": json.dumps(test["command"])[:500],
                                "exit_code": test["exit_code"],
                                "timed_out": test["timed_out"],
                                "revision": test["revision"],
                                "output_sha256": test.get("output_sha256"),
                            }
                            for test in evidence["commands"][:30]
                        ],
                        "approval": data["release_approval"],
                    },
                    indent=2,
                )
                pr = await self.publisher.create(branch, revision, state["question"], body)
            receipt = {
                "task_id": state["task_id"],
                "commit_sha": revision,
                "tests": self.workflow.evidence(state["task_id"])["commands"],
                "engineering_approval": data["engineering_approval"],
                "approval": data["release_approval"],
                "pr_url": pr["html_url"],
                "draft": True,
            }
            self.workflow.finish_release(state["task_id"], receipt)
            self.ledger.put(identity, {"phase": "finished", "receipt": receipt})
            return outcome("completed", identity, receipt=receipt)
        except PermissionError:
            return outcome("blocked", identity, reason="approval_or_qa_required")
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, httpx.HTTPError):
            # No raw subprocess/HTTP errors: they can contain credential headers.
            return outcome("pending_execution", identity, reason="release_requires_reconciliation")
