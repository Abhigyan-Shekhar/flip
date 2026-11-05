"""Bounded subprocess capture for operator-approved validation commands."""

import asyncio
import hashlib
import os
import re
import signal
import time
from datetime import UTC, datetime

_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+"
    r"|(?:ghp_|github_pat_|sk-)[A-Za-z0-9_.-]{12,})"
)


async def capture_command(argv, *, cwd, env, timeout, artifact, max_bytes=1024 * 1024):
    """Kill the process group on timeout, cancellation or excess output.

    Only the bounded, redacted output reaches the log artifact and receipt.
    Container launch/cleanup and task authorization remain the caller's job.
    """
    started = datetime.now(UTC).isoformat()
    clock = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    content = bytearray()
    limited = False
    timed_out = False

    def terminate():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def reap():
        terminate()
        # A detached descendant can retain stdout after the owned process dies.
        # Close our pipe transport so it cannot delay Docker's cleanup context.
        # asyncio Process has no public pipe-close API; this transport owns only
        # the subprocess and its pipes, never a caller's file descriptor.
        process._transport.close()
        await asyncio.wait_for(process.wait(), 1)

    async def drain():
        nonlocal limited
        while chunk := await process.stdout.read(65536):
            available = max_bytes - len(content)
            content.extend(chunk[:available])
            if len(chunk) > available and not limited:
                limited = True
                await reap()
                break
        await process.wait()

    try:
        await asyncio.wait_for(drain(), timeout)
    except TimeoutError:
        timed_out = True
        await reap()
    except asyncio.CancelledError:
        await reap()
        raise
    output = _SECRET.sub("[REDACTED]", content.decode("utf-8", errors="replace"))
    # Persist a private bounded artifact; callers supply a fresh task directory.
    encoded = output.encode()[:max_bytes]
    # Avoid an incomplete UTF-8 suffix after enforcing the artifact byte cap.
    encoded = encoded.decode("utf-8", errors="ignore").encode()
    output = encoded.decode()
    descriptor = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(encoded)
    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "output_limit_exceeded": limited,
        "output_summary": output[:4096],
        "output_bytes": len(encoded),
        "output_sha256": hashlib.sha256(encoded).hexdigest(),
        "log_artifact": str(artifact),
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(time.monotonic() - clock, 4),
    }
