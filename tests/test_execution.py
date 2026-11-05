import asyncio
import os
import signal
import sys
import time

import pytest

from switchcrew.execution import capture_command


async def run(tmp_path, code, **kwargs):
    return await capture_command(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PATH": os.defpath},
        timeout=kwargs.pop("timeout", 5),
        artifact=tmp_path / "command.log",
        **kwargs,
    )


async def test_excess_output_terminates_and_artifact_is_bounded(tmp_path):
    receipt = await run(tmp_path, "import os\nwhile True: os.write(1, b'x'*8192)", max_bytes=1024)
    assert receipt["output_limit_exceeded"]
    assert receipt["exit_code"] != 0
    assert receipt["output_bytes"] == 1024
    assert (tmp_path / "command.log").stat().st_size == 1024
    assert (tmp_path / "command.log").stat().st_mode & 0o077 == 0


async def test_logs_redact_secrets_before_preview_and_disk(tmp_path):
    receipt = await run(tmp_path, "print('token=private-value'); print('assertion passed')")
    assert receipt["exit_code"] == 0
    assert "private-value" not in receipt["output_summary"]
    assert "private-value" not in (tmp_path / "command.log").read_text()
    assert "assertion passed" in receipt["output_summary"]
    assert receipt["duration_seconds"] >= 0


async def test_timeout_and_cancellation_reap_child(tmp_path):
    receipt = await run(tmp_path, "import time; time.sleep(30)", timeout=0.05)
    assert receipt["timed_out"]
    assert receipt["exit_code"] != 0
    (tmp_path / "command.log").unlink()
    pending = asyncio.create_task(run(tmp_path, "import time; time.sleep(30)"))
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not (tmp_path / "command.log").exists()


async def test_detached_child_cannot_hold_timeout_pipe_open(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time,pathlib; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
        "start_new_session=True); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid)); time.sleep(30)"
    )
    started = time.monotonic()
    try:
        receipt = await run(tmp_path, code, timeout=0.2)
        assert receipt["timed_out"]
        assert time.monotonic() - started < 2
    finally:
        if pidfile.exists():
            try:
                os.killpg(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


async def test_invalid_utf8_cannot_expand_artifact_limit(tmp_path):
    receipt = await run(tmp_path, "import os; os.write(1,b'\\xff'*1024)", max_bytes=1024)
    assert receipt["output_bytes"] <= 1024
    assert (tmp_path / "command.log").stat().st_size <= 1024
