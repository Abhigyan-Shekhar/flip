import asyncio
import subprocess
import sys

from switchcrew.engineering import Ledger
from switchcrew.reviewer import ReviewerExecutor


def run(*args, cwd):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    run("git", "init", "-q", cwd=source)
    run("git", "config", "user.email", "test@example.com", cwd=source)
    run("git", "config", "user.name", "Test", cwd=source)
    (source / "app.py").write_text("print('base')\n")
    run("git", "add", ".", cwd=source)
    run("git", "commit", "-qm", "base", cwd=source)
    base = run("git", "rev-parse", "HEAD", cwd=source)
    checkout = tmp_path / "engineering"
    run("git", "clone", "-q", str(source), str(checkout), cwd=tmp_path)
    run("git", "config", "user.email", "test@example.com", cwd=checkout)
    run("git", "config", "user.name", "Test", cwd=checkout)
    (checkout / "app.py").write_text("print('implemented')\n")
    run("git", "commit", "-qam", "implementation", cwd=checkout)
    revision = run("git", "rev-parse", "HEAD", cwd=checkout)
    return base, checkout, revision


def executor(tmp_path, payload, command, *, auth=None):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.reserve("impl", {})
    diff = run("git", "diff", "--binary", payload[0], payload[2], cwd=payload[1])
    ledger.put(
        "impl",
        {
            "phase": "finished",
            "result": {
                "status": "implemented",
                "checkout": str(payload[1]),
                "base_revision": payload[0],
                "final_revision": payload[2],
                "diff": diff,
            },
        },
    )
    return ledger, ReviewerExecutor(
        ledger,
        tmp_path / "reviews",
        "impl",
        payload[2],
        [[sys.executable, "-c", command]],
        authorize=auth,
    )


def test_accepts_json_review_on_exact_diff(tmp_path):
    p = repo(tmp_path)
    ledger, review = executor(
        tmp_path, p, "import json; print(json.dumps({'accepted': True, 'findings': []}))"
    )
    result = asyncio.run(review({"handoff_id": "review-1"}))
    assert result["status"] == "review_accepted"
    assert result["reviewed_revision"] == p[2] and result["files_changed"] == ["app.py"]
    assert result["commands"][0]["revision"] == p[2]
    ledger.close()


def test_rejects_reported_finding_and_command_failure(tmp_path):
    p = repo(tmp_path)
    ledger, review = executor(
        tmp_path,
        p,
        "import json; print(json.dumps({'accepted': False, 'findings':"
        "[{'path':'app.py','line':1,'message':'bug'}]}))",
    )
    result = asyncio.run(review({"handoff_id": "review-2"}))
    assert result["status"] == "review_rejected" and result["findings"][0]["message"] == "bug"
    ledger.close()


def test_malformed_or_out_of_scope_report_is_blocked(tmp_path):
    p = repo(tmp_path)
    ledger, review = executor(tmp_path, p, "print('{bad json')")
    assert asyncio.run(review({"handoff_id": "review-3"}))["status"] == "blocked_configuration"
    ledger.close()


def test_stale_head_and_mutating_review_are_not_accepted(tmp_path):
    p = repo(tmp_path)
    ledger, review = executor(
        tmp_path,
        p,
        "import json; print(json.dumps({'accepted': True, 'findings': []})); "
        "open('app.py','w').write('tampered')",
    )
    result = asyncio.run(review({"handoff_id": "review-4"}))
    assert result["status"] == "review_rejected"
    assert result["command_findings"]
    ledger.close()
    run("git", "commit", "--allow-empty", "-m", "advance", cwd=p[1])
    ledger, review = executor(tmp_path, p, "print('{}')")
    assert asyncio.run(review({"handoff_id": "review-5"}))["status"] == "stale_review"
    ledger.close()


def test_cancellation_reserves_uncertain_execution_and_denial_blocks(tmp_path):
    p = repo(tmp_path)
    ledger, review = executor(tmp_path, p, "import time; time.sleep(2)")

    async def cancel():
        task = asyncio.create_task(review({"handoff_id": "review-6"}))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(cancel())
    assert (
        ledger.get("review-" + __import__("hashlib").sha256(b"review-6").hexdigest()[:32])["phase"]
        == "uncertain"
    )
    resumed = ReviewerExecutor(
        ledger,
        tmp_path / "reviews",
        "impl",
        p[2],
        [[sys.executable, "-c", "import time; time.sleep(2)"]],
    )
    assert asyncio.run(resumed({"handoff_id": "review-6"}))["status"] == "pending_execution"
    ledger.close()
    ledger, review = executor(tmp_path, p, "print('{}')", auth=lambda state: False)
    assert asyncio.run(review({"handoff_id": "review-7"}))["status"] == "blocked"
    ledger.close()


def test_authorization_revocation_during_command_blocks_result(tmp_path):
    p = repo(tmp_path)
    calls = 0

    def auth(_state):
        nonlocal calls
        calls += 1
        return calls < 4

    ledger, review = executor(
        tmp_path, p, "import json; print(json.dumps({'accepted': True, 'findings': []}))", auth=auth
    )
    assert asyncio.run(review({"handoff_id": "review-8"}))["status"] == "blocked"
    ledger.close()


def test_async_authorizer_cannot_accidentally_grant_permission(tmp_path):
    async def denied(_state):
        return False

    payload = repo(tmp_path)
    ledger, review = executor(tmp_path, payload, "print('{}')", auth=denied)
    assert asyncio.run(review({"handoff_id": "review-async"}))["status"] == "blocked"
    assert (
        ledger.get("review-" + __import__("hashlib").sha256(b"review-async").hexdigest()[:32])
        is None
    )
    ledger.close()
