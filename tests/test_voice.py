"""Provider contract doubles and real WAV boundaries; no microphone/network capture."""

import json
import wave
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from switchcrew.engineering import Ledger
from switchcrew.graphs import build_agent_graph, invoke_agent_graph
from switchcrew.voice import ElevenLabsVoice, VoiceConsent, VoiceExecutor, VoiceJob, main, wav_input


@pytest.fixture
def voice_setup(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    recording = uploads / "input.wav"
    with wave.open(str(recording), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 8000)
    consent = tmp_path / "consent.json"
    consent.write_text(
        json.dumps(
            {
                "grants": [
                    {
                        "grant_id": "explicit-human-consent",
                        "task_id": "task",
                        "subject_id": "speaker",
                        "actor": "speaker",
                        "provider": "elevenlabs",
                        "operation": "transcribe",
                        "revoked": False,
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                        "evidence": "discord-message-id",
                    }
                ]
            }
        )
    )
    consent.chmod(0o600)
    ledger = Ledger(tmp_path / "voice.sqlite3")
    yield uploads, consent, ledger
    ledger.close()


def state():
    return {
        "task_id": "task",
        "handoff_id": "voice-assignment",
        "agent_role": "voice",
        "question": "Transcribe consented speech",
        "acceptance_criteria": ["Return transcript"],
    }


def job(operation="transcribe", **changes):
    record = {
        "task_id": "task",
        "subject_ids": ["speaker"],
        "operation": operation,
        **({"input_file": "input.wav"} if operation == "transcribe" else {"text": "Hello"}),
    }
    return {**record, **changes}


def set_consent(path, **changes):
    record = json.loads(path.read_text())
    record["grants"][0].update(changes)
    path.write_text(json.dumps(record))
    path.chmod(0o600)


def executor(voice_setup, tmp_path, handler, *, record=None):
    uploads, consent, ledger = voice_setup
    provider = ElevenLabsVoice(
        "private-key-never-persist",
        voice_id="configured-voice",
        transport=httpx.MockTransport(handler),
    )
    return VoiceExecutor(
        ledger,
        provider,
        {state()["handoff_id"]: record or job()},
        VoiceConsent(consent),
        uploads,
        tmp_path / "artifacts",
    )


@pytest.mark.asyncio
async def test_transcribe_real_wav_and_durable_replay(voice_setup, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url == "https://api.elevenlabs.io/v1/speech-to-text"
        assert request.headers["xi-api-key"] == "private-key-never-persist"
        assert b"scribe_v2" in request.content
        assert b"RIFF" in request.content
        assert b"consented.wav" in request.content
        return httpx.Response(200, json={"text": "Please research Gateway intents."})

    execute = executor(voice_setup, tmp_path, handler)
    graph = build_agent_graph("voice", executor=execute)
    result = await invoke_agent_graph(graph, state())
    assert result["result"]["status"] == "completed"
    assert result["result"]["transcript"] == "Please research Gateway intents."
    artifact = tmp_path / "artifacts" / result["result"]["artifact"]["name"]
    assert json.loads(artifact.read_text())["text"] == "Please research Gateway intents."
    assert artifact.stat().st_mode & 0o077 == 0
    assert len(calls) == 1
    # Recreate the executor and reopen its journal to exercise restart replay.
    reopened = Ledger(tmp_path / "voice.sqlite3")
    execute.ledger = reopened
    try:
        assert await execute(state()) == result["result"]
        assert len(calls) == 1
        assert "private-key-never-persist" not in json.dumps(
            execute.ledger.get(result["result"]["assignment_id"])
        )
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_meeting_transcription_requests_timestamps_and_keeps_speaker_ids_anonymous():
    calls = []

    def handler(request):
        calls.append(request)
        assert b'diarize"\r\n\r\ntrue' in request.content
        assert b'timestamps_granularity"\r\n\r\nword' in request.content
        assert b"speaker_library" not in request.content
        return httpx.Response(
            200,
            json={
                "text": "We should ship Monday.",
                "words": [
                    {
                        "text": "We",
                        "start": 0.1,
                        "end": 0.2,
                        "type": "word",
                        "speaker_id": "speaker_0",
                        "logprob": -0.2,
                    },
                    {
                        "text": " should ship Monday.",
                        "start": 0.21,
                        "end": 0.9,
                        "type": "word",
                        "speaker_id": None,
                    },
                ],
            },
        )

    provider = ElevenLabsVoice("private-api-key", transport=httpx.MockTransport(handler))
    result = await provider.transcribe_meeting(b"RIFF test audio")
    assert result["text"] == "We should ship Monday."
    assert result["words"][0]["speaker_id"] == "speaker_0"
    assert result["words"][0]["speaker_attribution"] == "provider_anonymous_id"
    assert result["words"][0]["logprob"] == -0.2
    assert result["words"][1]["speaker_id"] is None
    assert result["words"][1]["speaker_attribution"] == "unattributed"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"revoked": True},
        {"expires_at": "2000-01-01T00:00:00+00:00"},
        {"expires_at": "2099-01-01T00:00:00"},
        {"task_id": "other"},
        {"actor": "pm-agent"},
        {"provider": "other"},
        {"evidence": ""},
    ],
)
async def test_missing_expired_or_forged_consent_never_sends_audio(voice_setup, tmp_path, changes):
    set_consent(voice_setup[1], **changes)

    def forbidden(request):
        pytest.fail("Consent failure must precede provider upload")

    execute = executor(voice_setup, tmp_path, forbidden)
    assert (await execute(state()))["status"] == "blocked"
    assert not (tmp_path / "artifacts").exists()
    assert not voice_setup[2].db.execute("SELECT * FROM executions").fetchall()


@pytest.mark.asyncio
async def test_all_speakers_must_consent_and_task_text_cannot_grant_it(voice_setup, tmp_path):
    execute = executor(
        voice_setup,
        tmp_path,
        lambda _: pytest.fail("must not upload"),
        record=job(subject_ids=["speaker", "unconsenting-person"]),
    )
    supplied = {**state(), "consent": True, "actor": "speaker", "subject_ids": ["speaker"]}
    assert (await execute(supplied))["status"] == "blocked"


@pytest.mark.asyncio
async def test_tts_real_contract_private_mp3_and_disclosure(voice_setup, tmp_path):
    set_consent(voice_setup[1], operation="speak")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/text-to-speech/configured-voice"
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert json.loads(request.content) == {
            "text": "Hello",
            "model_id": "eleven_multilingual_v2",
        }
        return httpx.Response(
            200, content=b"ID3controlled-provider-audio", headers={"content-type": "audio/mpeg"}
        )

    execute = executor(voice_setup, tmp_path, handler, record=job("speak"))
    result = await execute(state())
    assert result["status"] == "completed"
    assert result["disclosure"] == "AI-generated speech"
    assert result["artifact"]["format"] == "mp3"
    assert (tmp_path / "artifacts" / result["artifact"]["name"]).read_bytes().startswith(b"ID3")
    assert "private-key-never-persist" not in json.dumps(result)
    assert (await execute(state())) == result
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_uncertain_billed_request_is_not_repeated(voice_setup, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-key-never-persist")

    execute = executor(voice_setup, tmp_path, handler)
    result = await execute(state())
    assert result["status"] == "pending_execution"
    assert "private-key-never-persist" not in json.dumps(result)
    assert (await execute(state())) == result
    assert len(calls) == 1
    assert not list((tmp_path / "artifacts").iterdir())


@pytest.mark.asyncio
async def test_revocation_during_upload_prevents_local_delivery(voice_setup, tmp_path):
    def handler(request):
        set_consent(voice_setup[1], revoked=True)
        return httpx.Response(200, json={"text": "Not to be delivered"})

    execute = executor(voice_setup, tmp_path, handler)
    assert (await execute(state()))["status"] == "blocked"
    assert not list((tmp_path / "artifacts").iterdir())


@pytest.mark.asyncio
async def test_provider_size_limit_and_error_response_are_sanitized(voice_setup, tmp_path):
    set_consent(voice_setup[1], operation="speak")
    execute = executor(
        voice_setup,
        tmp_path,
        lambda _: httpx.Response(200, content=b"x" * 1025, headers={"content-type": "audio/mpeg"}),
        record=job("speak"),
    )
    execute.provider.max_audio_bytes = 1024
    assert (await execute(state()))["status"] == "pending_execution"
    assert not list((tmp_path / "artifacts").iterdir())


@pytest.mark.asyncio
async def test_operator_jobs_bind_inputs_and_require_private_consent(voice_setup, tmp_path):
    execute = executor(voice_setup, tmp_path, lambda _: pytest.fail("must not upload"))
    assert (await execute({**state(), "handoff_id": "unknown"}))[
        "status"
    ] == "blocked_configuration"
    assert (await execute({**state(), "task_id": "other"}))["status"] == "blocked"
    voice_setup[1].chmod(0o644)
    assert (await execute(state()))["status"] == "blocked"


def test_wav_boundaries_and_path_escape(voice_setup, tmp_path):
    uploads, _, _ = voice_setup
    assert wav_input(uploads, "input.wav").startswith(b"RIFF")
    outside = tmp_path / "private.wav"
    outside.write_bytes((uploads / "input.wav").read_bytes())
    (uploads / "escape.wav").symlink_to(outside)
    for path in ["../private.wav", "escape.wav"]:
        with pytest.raises(ValueError):
            wav_input(uploads, path)
    with pytest.raises(ValueError):
        wav_input(uploads, "input.wav", max_seconds=0.1)
    with pytest.raises(ValueError):
        wav_input(uploads, "input.wav", max_bytes=100)
    (uploads / "input.wav").write_bytes((uploads / "input.wav").read_bytes()[:-100])
    with pytest.raises(ValueError):
        wav_input(uploads, "input.wav")


def test_job_rejects_arbitrary_operation_and_ambiguous_inputs():
    for change in [
        {"operation": "capture_microphone"},
        {"text": "unexpected"},
        {"subject_ids": []},
    ]:
        with pytest.raises(ValueError):
            VoiceJob.model_validate(job(**change))


@pytest.mark.asyncio
async def test_cli_refuses_public_config_without_exposing_credentials(tmp_path, capsys):
    path = tmp_path / "voice.json"
    path.write_text(json.dumps({"api_key": "private-key-never-persist"}))
    path.chmod(0o644)
    assert await main(["--config", str(path), "--handoff", "one"]) == 1
    printed = capsys.readouterr().out
    assert json.loads(printed)["status"] == "blocked_configuration"
    assert "private-key-never-persist" not in printed


@pytest.mark.asyncio
async def test_missing_tts_voice_blocks_before_provider_claim(voice_setup, tmp_path):
    set_consent(voice_setup[1], operation="speak")
    execute = executor(
        voice_setup, tmp_path, lambda _: pytest.fail("must not synthesize"), record=job("speak")
    )
    execute.provider.voice_id = ""
    assert (await execute(state()))["status"] == "blocked_configuration"
    assert not voice_setup[2].db.execute("SELECT * FROM executions").fetchall()


@pytest.mark.asyncio
async def test_false_authorizer_blocks_graph_execution_despite_valid_consent(voice_setup, tmp_path):
    execute = executor(voice_setup, tmp_path, lambda _: pytest.fail("must not upload"))
    execute.authorize = lambda _: False
    result = await invoke_agent_graph(build_agent_graph("voice", executor=execute), state())
    assert result["result"]["status"] == "blocked"
    assert not voice_setup[2].db.execute("SELECT * FROM executions").fetchall()
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.asyncio
async def test_false_authorizer_prevents_cached_result_delivery(voice_setup, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "Consented transcript"})

    execute = executor(voice_setup, tmp_path, handler)
    execute.authorize = lambda _: True
    assert (await execute(state()))["status"] == "completed"
    execute.authorize = lambda _: False
    assert (await execute(state()))["status"] == "blocked"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unawaited_async_authorizer_is_not_authorization(voice_setup, tmp_path):
    execute = executor(voice_setup, tmp_path, lambda _: pytest.fail("must not upload"))

    async def denied(_):
        return False

    execute.authorize = denied
    assert (await execute(state()))["status"] == "blocked"
    assert not voice_setup[2].db.execute("SELECT * FROM executions").fetchall()
