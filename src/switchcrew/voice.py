"""Consented, bounded speech-file I/O; no implicit microphone or Discord capture.

Only private operator jobs and a current consent registry authorize processing.
Provider credentials remain outside graph state, artifacts and execution receipts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import io
import json
import math
import os
import re
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .engineering import Ledger, assignment_id, outcome


class VoiceJob(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)
    task_id: str = Field(min_length=1, max_length=200)
    operation: Literal["transcribe", "speak"]
    subject_ids: list[str] = Field(min_length=1, max_length=20)
    input_file: str | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def valid_operation(self):
        if (self.operation == "transcribe" and (not self.input_file or self.text is not None)) or (
            self.operation == "speak" and (not self.text or self.input_file is not None)
        ):
            raise ValueError("Configure one WAV input or one speech text")
        if any(not s.strip() for s in self.subject_ids) or len(set(self.subject_ids)) != len(
            self.subject_ids
        ):
            raise ValueError("Distinct nonblank consenting subject identities are required")
        return self


class VoiceConsent:
    """Private operator-maintained proof of explicit, revocable per-task consent.

    Transport adapters must verify the real human before updating this file.
    Task text is never consulted for consent, actor identity or provider choice.
    """

    def __init__(self, path):
        self.path = Path(path)

    def require(self, job):
        if self.path.stat().st_mode & 0o077:
            raise PermissionError("Consent registry must be private")
        registry = json.loads(self.path.read_text())
        grants = registry.get("grants") if isinstance(registry, dict) else None
        if not isinstance(grants, list):
            raise PermissionError("Consent grants are required")
        identities = []
        for subject in job.subject_ids:
            matches = []
            for grant in grants:
                if not isinstance(grant, dict):
                    continue
                if (
                    grant.get("subject_id") != subject
                    or grant.get("task_id") != job.task_id
                    or grant.get("provider") != "elevenlabs"
                    or grant.get("operation") != job.operation
                    or grant.get("revoked") is not False
                    or not grant.get("grant_id")
                    or grant.get("actor") != subject
                    or not grant.get("evidence")
                ):
                    continue
                try:
                    expiry = datetime.fromisoformat(grant["expires_at"])
                    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                matches.append(grant["grant_id"])
            if len(matches) != 1:
                raise PermissionError("Current unambiguous subject consent required")
            identities.extend(matches)
        return sorted(identities)


class ElevenLabsVoice:
    """Actual ElevenLabs file STT/TTS APIs with bounded streamed responses."""

    def __init__(
        self,
        api_key,
        *,
        voice_id="",
        stt_model="scribe_v2",
        tts_model="eleven_multilingual_v2",
        timeout=60,
        transport=None,
        max_audio_bytes=16 * 1024 * 1024,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Configure a private ElevenLabs API key")
        if voice_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice_id):
            raise ValueError("Invalid configured voice identifier")
        if not 1 <= timeout <= 180 or not 1024 <= max_audio_bytes <= 32 * 1024 * 1024:
            raise ValueError("Voice resource limits are out of bounds")
        if any(api_key in str(value) for value in (voice_id, stt_model, tts_model)):
            raise ValueError("Provider credentials cannot be used as public identifiers")
        self._api_key, self.voice_id = api_key, voice_id
        self.stt_model, self.tts_model = stt_model, tts_model
        self.timeout, self.transport, self.max_audio_bytes = timeout, transport, max_audio_bytes

    def spec(self):
        return {
            "provider": "elevenlabs",
            "voice_id": self.voice_id,
            "stt_model": self.stt_model,
            "tts_model": self.tts_model,
        }

    async def _call(self, path, *, limit, audio=False, **kwargs):
        async with httpx.AsyncClient(
            base_url="https://api.elevenlabs.io",
            timeout=self.timeout,
            follow_redirects=False,
            transport=self.transport,
            headers={"xi-api-key": self._api_key},
        ) as client:
            async with client.stream("POST", path, **kwargs) as response:
                response.raise_for_status()
                if audio and response.headers.get("content-type", "").split(";")[0] not in {
                    "audio/mpeg",
                    "audio/mp3",
                }:
                    raise ValueError("Provider returned an unexpected audio format")
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > limit:
                        raise ValueError("Provider response exceeds configured byte limit")
                return bytes(content)

    async def transcribe(self, audio):
        content = await self._call(
            "/v1/speech-to-text",
            limit=256 * 1024,
            data={"model_id": self.stt_model, "tag_audio_events": "false", "diarize": "false"},
            files={"file": ("consented.wav", audio, "audio/wav")},
        )
        result = json.loads(content)
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError("Provider transcript is blank or exceeds the text limit")
        if self._api_key in text:
            raise ValueError("Credential-like provider output rejected")
        return text.strip()

    async def transcribe_meeting(self, audio):
        """Return real provider word timings and anonymous diarization labels.

        Provider ``speaker_id`` values are acoustic labels only. This method
        never maps them to meeting participants or employee identities.
        """
        content = await self._call(
            "/v1/speech-to-text",
            limit=2 * 1024 * 1024,
            data={
                "model_id": self.stt_model,
                "tag_audio_events": "false",
                "diarize": "true",
                "timestamps_granularity": "word",
            },
            files={"file": ("consented-meeting.wav", audio, "audio/wav")},
        )
        result = json.loads(content)
        text = result.get("text") if isinstance(result, dict) else None
        records = result.get("words") if isinstance(result, dict) else None
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 250_000
            or self._api_key in text
            or not isinstance(records, list)
            or len(records) > 100_000
        ):
            raise ValueError("Meeting transcript is missing or exceeds provider bounds")
        words = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Meeting word record is invalid")
            token = record.get("text")
            start, end = record.get("start"), record.get("end")
            kind = record.get("type", "word")
            if (
                not isinstance(token, str)
                or len(token) > 1000
                or type(start) not in (float, int)
                or type(end) not in (float, int)
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end < start
                or kind not in {"word", "spacing", "audio_event"}
            ):
                raise ValueError("Meeting word timing or content is invalid")
            speaker = record.get("speaker_id")
            if speaker is not None and (
                not isinstance(speaker, str)
                or not re.fullmatch(r"speaker_[A-Za-z0-9_-]{1,80}", speaker)
            ):
                # Do not turn unexpected provider labels into identity claims.
                speaker = None
            logprob = record.get("logprob")
            if type(logprob) not in (float, int) or not math.isfinite(logprob):
                logprob = None
            words.append(
                {
                    "text": token,
                    "start": float(start),
                    "end": float(end),
                    "type": kind,
                    "speaker_id": speaker,
                    "speaker_attribution": (
                        "provider_anonymous_id" if speaker is not None else "unattributed"
                    ),
                    # Log probability is preserved as provider evidence, never
                    # mislabeled as a calibrated confidence percentage.
                    "logprob": float(logprob) if logprob is not None else None,
                }
            )
        if not words:
            raise ValueError("Meeting provider response has no timestamped words")
        words.sort(key=lambda word: (word["start"], word["end"]))
        return {"text": text.strip(), "words": words}

    async def speak(self, text):
        if not self.voice_id:
            raise ValueError("A configured licensed voice ID is required")
        if self._api_key in text:
            raise ValueError("Credential-like speech input rejected")
        content = await self._call(
            "/v1/text-to-speech/" + quote(self.voice_id, safe=""),
            limit=self.max_audio_bytes,
            audio=True,
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": self.tts_model},
        )
        if not content:
            raise ValueError("Provider returned empty audio")
        return content


def wav_input(root, filename, max_bytes=10 * 1024 * 1024, max_seconds=60):
    """Accept only short PCM WAV files under the configured upload directory."""
    root = Path(root).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("Audio input must be a bounded file under the configured input root")
    with path.open("rb") as source:
        content = source.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("Audio input exceeds the byte limit")
    with wave.open(io.BytesIO(content), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        if (
            audio.getcomptype() != "NONE"
            or audio.getsampwidth() != 2
            or audio.getnchannels() not in {1, 2}
            or not 8000 <= rate <= 48000
            or not 0.1 <= frames / rate <= max_seconds
            or len(audio.readframes(frames)) != frames * audio.getnchannels() * 2
        ):
            raise ValueError("A complete 16-bit PCM WAV of 0.1–60 seconds is required")
    return content


class VoiceExecutor:
    def __init__(self, ledger, provider, jobs, consent, input_root, artifact_root, authorize=None):
        self.ledger, self.provider = ledger, provider
        self.jobs = {key: VoiceJob.model_validate(job) for key, job in jobs.items()}
        self.consent = consent
        self.input_root, self.artifact_root = Path(input_root), Path(artifact_root)
        self.authorize = authorize

    def permitted(self, state, job):
        if state["task_id"] != job.task_id:
            raise PermissionError("Configured voice task differs from the handoff")
        if self.authorize:
            decision = self.authorize(state)
            if inspect.isawaitable(decision):
                # This contract is synchronous. Reject accidental async callbacks
                # rather than treating an unexecuted coroutine as authorization.
                if inspect.iscoroutine(decision):
                    decision.close()
                raise PermissionError("Voice authorization callback must be synchronous")
            if decision is not None and decision is not True:
                raise PermissionError("Voice task authorization was denied")
        return self.consent.require(job)

    async def __call__(self, state):
        identity = "voice-" + assignment_id(state["handoff_id"])
        job = self.jobs.get(state["handoff_id"])
        if job is None:
            return outcome("blocked_configuration", identity, reason="operator_voice_job_required")
        try:
            grants = self.permitted(state, job)
            if job.operation == "speak" and not self.provider.spec().get("voice_id"):
                raise ValueError("Configure a licensed voice before claiming synthesis")
            content = (
                wav_input(self.input_root, job.input_file)
                if job.operation == "transcribe"
                else job.text.encode()
            )
            spec = {
                "job": job.model_dump(exclude={"text", "input_file"}),
                "input_sha256": hashlib.sha256(content).hexdigest(),
                "provider": self.provider.spec(),
                "consent_grants": grants,
            }
            record = self.ledger.reserve(identity, spec)
            if "result" in record:
                return record["result"]
            self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.artifact_root.stat().st_mode & 0o077:
                raise PermissionError("Voice artifact directory must be private")
            self.permitted(state, job)
            if not self.ledger.claim(identity, "reserved", "calling"):
                return outcome(
                    "pending_execution", identity, reason="voice_provider_outcome_uncertain"
                )
            # Claimed before sending: uncertain, billed requests are never repeated.
            if job.operation == "transcribe":
                transcript = await self.provider.transcribe(content)
                output = json.dumps({"text": transcript}, ensure_ascii=False).encode()
                extension, details = "json", {"transcript": transcript}
            else:
                output = await self.provider.speak(job.text)
                extension, details = "mp3", {"disclosure": "AI-generated speech"}
            self.permitted(state, job)  # Revocation blocks local delivery of an in-flight result.
            artifact = self.artifact_root / f"{identity}.{extension}"
            temporary = self.artifact_root / f".{identity}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(output)
            temporary.replace(artifact)
            result = outcome(
                "completed",
                identity,
                operation=job.operation,
                provider="elevenlabs",
                consent_grants=grants,
                artifact={
                    "name": artifact.name,
                    "sha256": hashlib.sha256(output).hexdigest(),
                    "bytes": len(output),
                    "format": extension,
                },
                **details,
            )
            self.ledger.put(identity, {"phase": "finished", "result": result})
            return result
        except PermissionError:
            return outcome("blocked", identity, reason="current_voice_consent_required")
        except (OSError, ValueError, KeyError, TypeError, wave.Error, httpx.HTTPError):
            record = self.ledger.get(identity)
            if record and record.get("phase") == "calling":
                return outcome(
                    "pending_execution", identity, reason="voice_provider_outcome_uncertain"
                )
            return outcome(
                "blocked_configuration", identity, reason="voice_configuration_or_input_invalid"
            )


def voice_provider_from_config(config, *, transport=None):
    """Construct the STT/TTS provider from private Voice process config."""
    provider_config = dict(config.get("provider", {}))
    key_variable = provider_config.pop("api_key_env", "ELEVENLABS_API_KEY")
    return ElevenLabsVoice(os.environ.get(key_variable, ""), transport=transport, **provider_config)


def voice_executor_from_config(config, ledger, *, authorize=None, transport=None):
    """Construct from private operator config; never pass this config into graph state."""
    provider = voice_provider_from_config(config, transport=transport)
    return VoiceExecutor(
        ledger,
        provider,
        config.get("jobs", {}),
        VoiceConsent(config["consent_registry"]),
        config["input_root"],
        config["artifact_root"],
        authorize=authorize,
    )


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--handoff", required=True)
    args = parser.parse_args(argv)
    ledger = None
    try:
        if args.config.stat().st_mode & 0o077:
            raise PermissionError("Voice configuration must be private")
        config = json.loads(args.config.read_text())
        ledger = Ledger(config["ledger"])
        executor = voice_executor_from_config(config, ledger)
        job = executor.jobs[args.handoff]
        result = await executor({"task_id": job.task_id, "handoff_id": args.handoff})
        print(json.dumps(result))
        return 0 if result["status"] == "completed" else 1
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        print(
            json.dumps(
                {"status": "blocked_configuration", "reason": "private_voice_config_invalid"}
            )
        )
        return 1
    finally:
        if ledger:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
