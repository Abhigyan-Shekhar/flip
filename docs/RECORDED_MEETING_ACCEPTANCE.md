# Voice + Notes: recorded first, live later

This slice adds a **read-only acceptance inspector** for consented recorded
meetings. It consumes only existing SQLite artifacts; no recording, transcript,
participant identity, or consent evidence is printed to stdout. Existing
MeetingService, Voice and Notes Switch agents remain responsible for all state
transitions and handoffs.

On the verification host, with a real consented recording and independent
authenticated PM, Voice and Notes identities:

1. Register the meeting, source Discord thread, full participant roster and
   private recording manifest; ensure every participant independently grants
   current, revocable consent.
2. Upload a bounded WAV into the private recording root and create the registered
   PM → Voice recording job. The file's SHA-256 must match the manifest.
3. Have the real Voice agent transcribe via its configured provider; record the
   provider response, exact transcript SHA and anonymous speaker labels.
4. Deliver the Notes handoff through Switch, with notes, action proposals and
   provenance linked to the transcript SHA. Do not auto-approve action items.
5. Run the inspector and archive the sanitized JSON alongside the registered
   task/handoff audit trail and independent Switch/Discord receipts.

```bash
uv run python -m switchcrew.recorded_acceptance \
  --database /private/path/switchcrew.sqlite3 \
  --meeting-id YOUR_REGISTERED_MEETING_ID
```

The inspector verifies current consent, absence of revocation, completed
registered Voice job, exact audio/transcript/notes digests, Notes assignment and
meeting audit events. It **never** claims the upstream provider, recording,
Discord capture, or human identity is externally authenticated just because
SQLite contains a matching artifact. A direct transcription without a real
Voice handoff intentionally remains `incomplete`.

## Independent next-stage gates

- **Live Discord audio:** Capture is not accepted until a real consented DAVE
  stream is retrieved, all participant opt-outs/revocations stop processing,
  audio discontinuities are accounted for and the operator can replay evidence.
- **Presence/connectivity:** Existing meeting presence events must be tied to
  observed member/session data; mock events are not live evidence. No claim that
  a participant missed content without a reliable timing window.
- **Interactive clarifications:** The agent may propose source-linked questions
  for a human moderator to confirm. It must not inject claims or new tasks into
  the conversation without approval and independent speaker identification.
- **Model-assisted Notes:** Never feed private recordings/transcripts to a
  generic external harness without separate recording and inference disclosure.

Automated tests use synthesized silence and a controlled transcription provider;
they validate consent and artifact rules, not real API connectivity.
