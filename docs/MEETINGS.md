# Meetings and Notes

November 5 acceptance status: no real recording, participant consent evidence
or speech-provider configuration exists on this deployment. The user deferred
this test to a human. Recorded-file and live VC acceptance remain unverified.

SwitchCrew's meeting service supports a recorded-file workflow. It does not
capture live Discord voice. The service takes a WAV file selected by an
operator, checks a current consent grant for every registered participant,
then sends the recording to the configured speech provider. The provider must
return timestamped words and anonymous speaker labels. A label such as
`speaker_0` is only the provider's diarization label; SwitchCrew never maps it
to a Discord user or employee.

The existing `ElevenLabsVoice.transcribe_meeting` adapter requests diarization
and word-level timestamps. See the [provider API](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)
for the response contract. No provider calls or real recordings are required
by the automated tests.

## Service flow

1. The trusted runtime creates the meeting with the authenticated organizer,
   participant IDs, Discord source identifiers, and the actual Switch `room_id`.
2. Every participant grants consent as themselves, with an evidence reference,
   a future expiry, and an acknowledged recording/transcription disclosure.
3. The authenticated organizer selects an in-root WAV file. The coordination
   service bounds duration and bytes, stores its digest, and creates a durable
   PM-to-Voice Switch handoff. It does not call the provider itself. The Voice
   runtime validates that addressed job, rechecks consent, verifies the audio
   digest, and sends audio to STT. The default bounds are 100 MiB and 60
   minutes.
4. The Voice result returned over Switch includes the transcript digest,
   provider metadata, and recording provenance, never the transcript words.
   The timestamped transcript is persisted in the meeting database for the
   separately addressed Notes task. If a provider call has an uncertain
   outcome, the job is left uncertain and is not automatically repeated.
5. `request_notes` creates a normal durable PM task and Switch handoff to the
   configured Notes specialist. Its trusted task source contains `meeting_id`;
   its prompt does not contain the transcript.
6. The Notes executor validates the addressed Notes handoff and retrieves only
   the registered meeting artifact. Its result is persisted through the normal
   Switch completion path. Notes contain evidence references to transcript
   segments, timestamp ranges, anonymous speaker-wise summaries, decisions,
   questions, blockers, and proposed actions.
7. An authenticated human approver explicitly confirms selected proposals.
   Confirmed items become durable tasks with source evidence and Switch
   outbox handoffs. A selected Engineering item enters the configured
   `ApprovalWorkflow`; this service never directly dispatches Engineering work.

Notes are conservative extractive drafts. Similar statements with different
dates are marked `potential_discrepancy`; only comparison against an explicitly
verified fact can be labeled `verified_contradiction`. Names, task ownership,
dates, and commitments need human review. Clarification is expected after the
meeting when speaker identity or a deadline is unclear.

## Consent and retention

The service persists meeting records, consent evidence references, presence
events, transcript words, Notes, action proposals, recording jobs, and
provenance in the existing SwitchCrew SQLite database. Raw audio is read from
the configured input root and is not copied into the database or artifact
directory. The provider necessarily receives the audio during transcription; apply the
provider account's data-retention settings and disclose that processing to
participants.

Revoking consent blocks subsequent processing and clears the locally stored
transcript, Notes, and unconfirmed proposals. It cannot recall audio already
sent to the provider or retract tasks and messages already created after human
confirmation. Confirmed task evidence references are retained for audit and
must follow the organization's retention policy.

Presence is recorded only from authenticated participant events supplied by
the runtime. The service tracks join/leave/reconnect durations; it does not
infer why a participant disconnected or claim that the person heard a given
part of the recording. `live_capture_status()` remains blocked until an
authenticated Discord voice transport and verified DAVE receive path exist.

## Integration boundary

`MeetingService` owns the persisted meeting state and service-level
authorization. The API/PM process can use `provider=None` and call
`request_transcription(..., voice_agent_id=...)`; only the private Voice
runtime constructs the provider with `voice_provider_from_config` and installs
`MeetingVoiceExecutor` for its configured Switch identity. The runtime must
also install `NotesExecutor` for the distinct Notes identity. Supply identity
from verified API or Discord credentials, bind allowed source rooms, and
provide the real Switch room ID and configured agent IDs. Do not accept actor,
room, role, consent, or approver identity from untrusted request JSON. Provider
absence blocks the Voice handoff; it does not create placeholder speech or
Notes. Direct `transcribe_recording` remains a service-level operator/test
method; production API routes should queue through `request_transcription`.


## Production recording allocations

Production factories require a private `recording_manifest` mapping relative WAV
paths to the permitted meeting ID, owner ID and exact participant roster. This
allocation is checked before reading/hashing the file; current consent is still
required separately. Missing or mismatched allocations block. API/task bodies
cannot grant file ownership or disable this boundary. The direct service's
non-strict mode is reserved for operator/unit-test construction and is not used
by deployed organization factories.

Confirmed proposals can include a human-selected participant `owner_id` and an
ISO date or timezone-aware `deadline`. Anonymous speaker IDs remain anonymous;
a confirmed owner is attributed to human confirmation. Missing values remain
explicitly unspecified. Dependencies defer handoffs/Engineering approval until
prerequisite deliverables complete; cancelled/failed prerequisites prevent
execution. The PM reconciles readiness durably after restart.

Notes retrieval is checked against the completed Switch handoff and notes hash.
The PM posts clipped proposal IDs/titles in the original thread for confirmation;
revoked consent suppresses queued previews before delivery. Full personal notes
remain in the consent-gated view. Scoped task facts and configured approved
lesson/Company Brain context are attached before hashing. Untrusted historical
sources do not become verified meeting commitments.
