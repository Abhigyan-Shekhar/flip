# Voice Agent: consented speech files and generated replies

`switchcrew.voice` implements useful speech-to-text and text-to-speech executors
through real ElevenLabs HTTP APIs. It accepts a short operator-selected WAV file
or bounded text, verifies current consent for every declared subject, and produces
a transcript or a private MP3 artifact. It requires only the existing `httpx`
dependency. It does not turn a transcript into an authorized task or approval.

This is an **uploaded-recording / generated-reply workflow**, not a live Discord
voice receiver. The provisional DAVE receive investigation remains in
[research/voice/README.md](../research/voice/README.md). Its boundary probes do
not establish real MLS/DAVE handshakes, speaker mapping or intelligible live
capture. No microphone is opened and no Discord audio is captured by this module.

## Credentials and actual consent

Set `ELEVENLABS_API_KEY` only in the Voice process environment. The application
never serializes it into graph state, the journal, output artifacts or receipts.
No paid provider request or real audio generation was performed during the
implementation tests. They use HTTP contract doubles and real WAV boundaries.

Choose a voice ID you are permitted to use; this implementation does not create
or clone voices. Speak output includes `AI-generated speech` in its receipt.
Use a visible disclosure when presenting generated audio to participants.

Before recording/uploading any real person, obtain explicit consent for this
specific task and for processing by ElevenLabs. Record the real human's identity,
the consent message/reference and an expiry in a private registry. This is an
operator-maintained trust boundary: the module does not authenticate a Discord
user itself. A future Discord capture adapter must verify consent **before**
starting capture and must correctly identify every speaker. Do not use a mixed
recording unless every speaker is known and has a matching current grant.

Example registry, saved locally with mode 0600:

```json
{
  "grants": [
    {
      "grant_id": "consent-message-unique-id",
      "task_id": "voice-test-task",
      "subject_id": "VERIFIED_HUMAN_ID",
      "actor": "VERIFIED_HUMAN_ID",
      "operation": "transcribe",
      "provider": "elevenlabs",
      "evidence": "ACTUAL_CONSENT_MESSAGE_REFERENCE",
      "expires_at": "REPLACE_WITH_FUTURE_ISO_TIMESTAMP_WITH_TIMEZONE",
      "revoked": false
    }
  ]
}
```

Use `operation: "speak"` for generated speech. Each subject needs exactly one
matching current grant for the task/operation/provider. Missing, expired,
revoked, duplicate or mismatched consent blocks processing before any upload.
An `actor` or `consent` property in incoming task text provides no authorization.
Consent is reread before the provider request, before cached output is returned,
and before delivery of an in-flight response. Revocation cannot recall audio
already transmitted to the provider, but prevents subsequent local delivery.

The provider's account/data-retention terms still apply. This code does not
claim zero retention: ElevenLabs' `enable_logging=false` option is limited to
eligible enterprise accounts. Apply your account's retention settings and
explain the external processing to consenting participants.

## Private jobs and file boundaries

Create a mode-0600 Voice config outside Git:

```json
{
  "ledger": "/private/crew/voice-executions.sqlite3",
  "consent_registry": "/private/crew/voice-consent.json",
  "input_root": "/private/crew/voice-uploads",
  "artifact_root": "/private/crew/voice-artifacts",
  "provider": {
    "api_key_env": "ELEVENLABS_API_KEY",
    "voice_id": "CONFIGURED_LICENSED_VOICE_ID",
    "stt_model": "scribe_v2",
    "tts_model": "eleven_multilingual_v2"
  },
  "jobs": {
    "voice-transcribe-handoff": {
      "task_id": "voice-test-task",
      "operation": "transcribe",
      "subject_ids": ["VERIFIED_HUMAN_ID"],
      "input_file": "consented-recording.wav"
    }
  }
}
```

The job is keyed by the exact durable handoff ID and also binds the parent task.
Input paths and speech text come only from this trusted configuration, never
from a message's asserted filename, consent flag or arbitrary URL. There is no
network URL downloader or shell command runner in the Voice executor.

Inputs must resolve underneath `input_root`, including through symlinks. The
file must be complete uncompressed 16-bit PCM WAV, mono/stereo, 8–48 kHz,
0.1–60 seconds and at most 10 MiB. These conservative limits support a short
push-to-talk/upload demonstration without adding decoding dependencies. Convert
other formats separately before authorizing the job.

For a spoken reply, use a separate handoff/grant:

```json
{
  "voice-reply-handoff": {
    "task_id": "voice-test-task",
    "operation": "speak",
    "subject_ids": ["VERIFIED_HUMAN_ID"],
    "text": "Your research request has completed. The source evidence is in the thread."
  }
}
```

Speech text is limited to 2,000 characters. Transcripts are limited to 8,000
characters, provider JSON to 256 KiB and generated MP3 to 16 MiB by default
(configurable provider audio limit is capped at 32 MiB). Response bodies are
streamed with bounded chunks and redirects are disabled.

The artifact directory must be private (0700); new artifacts are written
atomically with mode 0600. Only generated speech or a transcript JSON is saved;
the module does not copy the original recording. Results include artifact name,
byte count, SHA-256, provider, operation and consent grant IDs. STT intentionally
returns the transcript as the useful result, so the graph/task database and
journal can retain that consented transcript. Apply a retention policy to input
files, artifacts, execution journals and task/checkpoint data; delete completed
demo recordings when no longer needed. Raw audio/transcripts are not automatically
ingested into Waggle by this executor.

## Run directly or in a specialist graph

After configuring credentials locally and placing an explicitly consented WAV
under the input root:

```sh
uv run python -m switchcrew.voice --config /private/crew/voice.json \
  --handoff voice-transcribe-handoff
```

The CLI prints structured completion/block/pending metadata and exits nonzero
unless the operation completed. Config/provider failures omit raw exception
text so credentials and provider responses are not printed. On hosts affected
by the documented editable-install issue, install with
`uv sync --frozen --no-editable` and use `uv run --no-sync`.

Application wiring for a real Switch Voice specialist:

```python
from switchcrew.engineering import Ledger
from switchcrew.graphs import build_agent_graph
from switchcrew.voice import voice_executor_from_config

ledger = Ledger(private_config["ledger"])
executor = voice_executor_from_config(
    private_config,
    ledger,
    authorize=check_parent_task_is_still_active,
)
graph = build_agent_graph("voice", executor=executor, memory=memory)
```

The trusted runtime supplies ordinary `task_id`, `handoff_id`, `question` and
`acceptance_criteria`. Keep the config, credential and filesystem paths outside
graph/checkpoint state. The optional synchronous authorization callback should
raise `PermissionError` or return `False` when parent-task cancellation or runtime
policy denies processing. Only a successful `None` or `True` return is accepted;
an accidentally supplied async callback is refused rather than bypassed. Use the existing authenticated `AgentRuntime` to receive addressed
handoffs and deliver acknowledgments/results; do not bypass its sender checks.
The standalone CLI is useful for provider acceptance but does not prove a
Discord/Switch end-to-end route.

The generic Switch runtime also supports a private capability config. Wrap the
Voice config's provider, jobs, consent and directory fields inside `capability`,
with `role: "voice"` and `ledger` at the top level:

```json
{
  "role": "voice",
  "ledger": "/private/crew/voice-executions.sqlite3",
  "capability": {
    "consent_registry": "/private/crew/voice-consent.json",
    "input_root": "/private/crew/voice-uploads",
    "artifact_root": "/private/crew/voice-artifacts",
    "provider": {"api_key_env": "ELEVENLABS_API_KEY", "voice_id": "LICENSED_VOICE_ID"},
    "jobs": {}
  }
}
```

Populate `jobs` using the exact task/handoff entries above before delivery, and
use the Voice agent's own private registry:

```sh
uv run python -m switchcrew.runtime voice --registry /private/crew/voice-registry.json \
  --database /private/crew/application.sqlite3 \
  --url http://127.0.0.1:8080 --capability-config /private/crew/voice-runtime.json
```

This entry point rechecks active task/parent state before processing and returning
cached results, and disables graph checkpoint-result caching for Voice so consent
and liveness are reevaluated on each invocation. Keep application state and the
Voice execution ledger in separate files.

Provider calls are claimed in the durable execution journal **before** upload
or synthesis. A restart can reuse a committed result without a second paid
request. A lost response or crash after claiming returns `pending_execution`
with `voice_provider_outcome_uncertain`; it does not blindly repeat a billable
request. The operator must reconcile such a case before issuing a new assignment.
Changing content, provider selection or grant identity under an old handoff is
refused. Ledger records retain only input hashes and public job/provider scope,
not raw input audio or configured TTS text; useful transcripts remain in results.

## Live acceptance still required

1. Record actual participant consent and expire/revoke one grant: verify zero
   provider calls and no delivered artifact for the revoked task.
2. Transcribe an intelligible short WAV with the real provider. Compare actual
   speech with returned text; capture task/handoff and consent evidence IDs.
3. Generate a short licensed-voice reply and listen to the actual MP3. Confirm
   the AI disclosure and artifact hash/permissions.
4. Restart after completed processing: verify the same result and no second
   provider operation. Test a controlled lost response only if reversible.
5. If demonstrating through Switch, run an independent Voice identity, verify
   real handoff/ack/result delivery and confirm a transcript cannot approve an
   Engineering or Release task.
6. For live Discord channel audio, separately pass the existing DAVE receive
   gate with consenting speakers, key/member transitions, reconnect and stop.
   This module alone does not close that gate.

Official provider contracts checked during implementation:
[create transcript](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)
and [create speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).
These use multipart `/v1/speech-to-text` and JSON `/v1/text-to-speech/{voice_id}`;
credentials are sent only in the `xi-api-key` header to the fixed HTTPS origin.
