"""Extractive, evidence-linked meeting notes and the Switch Notes executor."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_ACTION = re.compile(
    r"\b(?:i will|i'll|we will|we'll|action item|i can|we can|needs? to|must)\b", re.I
)
_DECISION = re.compile(r"\b(?:decided|decision|agreed|approved|we chose)\b", re.I)
_BLOCKER = re.compile(r"\b(?:blocked|blocker|waiting for|depends on|dependency)\b", re.I)
_DEADLINE = re.compile(
    r"\b(?:by|before|on)\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"\d{4}-\d{2}-\d{2}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2})\b",
    re.I,
)


def _segments(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    words = transcript.get("words")
    if not isinstance(words, list):
        raise ValueError("Timestamped provider word records are required")
    segments = []
    current = None
    for index, word in enumerate(words):
        if not isinstance(word, dict):
            raise ValueError("Transcript word records must be objects")
        text, start, end = word.get("text"), word.get("start"), word.get("end")
        if (
            not isinstance(text, str)
            or not text
            or type(start) not in (int, float)
            or type(end) not in (int, float)
            or start < 0
            or end < start
        ):
            raise ValueError("Transcript word timing is invalid")
        speaker = word.get("speaker_id")
        if speaker is not None and not (
            isinstance(speaker, str) and re.fullmatch(r"speaker_[A-Za-z0-9_-]{1,80}", speaker)
        ):
            speaker = None
        if (
            current is None
            or current["speaker_id"] != speaker
            or start - current["end"] > 1.5
            or word.get("type") == "audio_event"
        ):
            if current:
                segments.append(current)
            current = {
                "segment_id": f"seg-{len(segments) + 1:05d}",
                "speaker_id": speaker,
                "speaker_attribution": ("provider_anonymous_id" if speaker else "unattributed"),
                "start": float(start),
                "end": float(end),
                "text": "",
                "word_refs": [],
                "provider_logprobs": [],
            }
        current["text"] += text
        current["end"] = float(end)
        current["word_refs"].append(f"word-{index + 1:06d}")
        if isinstance(word.get("logprob"), (int, float)):
            current["provider_logprobs"].append(float(word["logprob"]))
    if current:
        segments.append(current)
    for segment in segments:
        segment["text"] = segment["text"].strip()
    return [segment for segment in segments if segment["text"]]


def _canonical(statement: str) -> str:
    text = re.sub(r"\b(?:not|n't)\b", " ", statement.lower())
    return " ".join(re.findall(r"[a-z0-9]+", text))


def find_discrepancies(segments, verified_facts=()):
    """Use narrow, explicit comparisons; uncertain findings stay potential."""
    findings = []
    for index, left in enumerate(segments):
        deadline = _DEADLINE.search(left["text"])
        if deadline:
            for right in segments[index + 1 :]:
                other = _DEADLINE.search(right["text"])
                if not other or other.group(1).casefold() == deadline.group(1).casefold():
                    continue
                prefix_left = _canonical(left["text"][: deadline.start()])
                prefix_right = _canonical(right["text"][: other.start()])
                left_tokens, right_tokens = set(prefix_left.split()), set(prefix_right.split())
                if (
                    left_tokens
                    and right_tokens
                    and len(left_tokens & right_tokens)
                    >= min(2, len(left_tokens), len(right_tokens))
                ):
                    findings.append(
                        {
                            "classification": "potential_discrepancy",
                            "summary": "Different dates were stated for a similar commitment.",
                            "evidence_refs": [left["segment_id"], right["segment_id"]],
                            "values": [deadline.group(1), other.group(1)],
                        }
                    )
                    break
    for fact in verified_facts:
        if not isinstance(fact, dict) or fact.get("verification") != "verified":
            continue
        baseline = fact.get("statement")
        source = fact.get("source_ref")
        if not isinstance(baseline, str) or not isinstance(source, str):
            continue
        negated_baseline = re.search(r"\b(?:not|n't)\b", baseline, re.I) is not None
        base_key = _canonical(baseline)
        if not base_key:
            continue
        for segment in segments:
            candidate = segment["text"]
            negated_candidate = re.search(r"\b(?:not|n't)\b", candidate, re.I) is not None
            if base_key == _canonical(candidate) and negated_baseline != negated_candidate:
                findings.append(
                    {
                        "classification": "verified_contradiction",
                        "summary": "A meeting statement conflicts with a verified fact.",
                        "evidence_refs": [source, segment["segment_id"]],
                    }
                )
    return findings


def build_meeting_notes(meeting_id, transcript, *, presence=(), verified_facts=()):
    """Create reviewable minutes without attributing anonymous voices to people."""
    segments = _segments(transcript)
    if not segments:
        raise ValueError("Transcript contains no attributable speech segments")
    decisions, actions, questions, blockers = [], [], [], []
    speakers: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        speaker = segment["speaker_id"]
        speaker_label = f"Speaker {speaker}" if speaker else "Unattributed speaker"
        speakers.setdefault(speaker_label, []).append(segment)
        ref = segment["segment_id"]
        if _DECISION.search(segment["text"]):
            decisions.append(
                {
                    "statement": segment["text"],
                    "speaker_id": speaker,
                    "evidence_refs": [ref],
                }
            )
        if "?" in segment["text"]:
            questions.append({"question": segment["text"], "evidence_refs": [ref]})
        if _BLOCKER.search(segment["text"]):
            blockers.append({"blocker": segment["text"], "evidence_refs": [ref]})
        if _ACTION.search(segment["text"]):
            deadline = _DEADLINE.search(segment["text"])
            deps = [
                segment["text"][match.start() :].strip()
                for match in [
                    re.search(r"\b(?:blocked by|depends on|waiting for)\b.*", segment["text"], re.I)
                ]
                if match
            ]
            actions.append(
                {
                    "proposal_id": hashlib.sha256(f"{meeting_id}:{ref}".encode()).hexdigest()[:24],
                    "title": segment["text"][:300],
                    "acceptance_criteria": [
                        "Complete the confirmed action and report evidence in its task thread."
                    ],
                    "assignee_speaker_id": speaker,
                    "assignee_identity": None,
                    "deadline_text": deadline.group(1) if deadline else None,
                    "dependencies": deps,
                    "evidence_refs": [ref],
                    "confirmation_required": True,
                    "clarification_required": speaker is None or deadline is None,
                    "target_role": None,
                }
            )
    minutes = {
        "title": "Meeting minutes",
        "summary": [
            {
                "text": segment["text"],
                "evidence_refs": [segment["segment_id"]],
            }
            for segment in segments[:5]
        ],
        "important_discussions": [
            {
                "text": segment["text"],
                "speaker_id": segment["speaker_id"],
                "evidence_refs": [segment["segment_id"]],
            }
            for segment in segments
        ],
        "decisions": decisions,
        "action_items": actions,
        "open_questions": questions,
        "blockers": blockers,
        "presence": list(presence),
        "discrepancies": find_discrepancies(segments, verified_facts),
        "review_note": "Extractive draft; confirm names, ownership, dates and commitments.",
    }
    conversational = {
        "speakers": [
            {
                "speaker": speaker,
                "speaker_id": values[0]["speaker_id"],
                "attribution": values[0]["speaker_attribution"],
                "highlights": [
                    {"text": value["text"], "evidence_refs": [value["segment_id"]]}
                    for value in values
                ],
            }
            for speaker, values in speakers.items()
        ],
        "review_note": "Provider speaker IDs are anonymous; no employee identity was inferred.",
    }
    return {
        "meeting_id": meeting_id,
        "minutes": minutes,
        "speaker_wise_summary": conversational,
        "segments": segments,
        "action_items": actions,
        "discrepancies": minutes["discrepancies"],
    }


class NotesExecutor:
    """Consume only a meeting artifact already registered by MeetingService."""

    def __init__(self, meeting_service, *, agent_id, context_provider=None):
        self.meeting_service, self.agent_id = meeting_service, agent_id
        self.context_provider = context_provider

    async def __call__(self, state):
        try:
            meeting = self.meeting_service.notes_context_for_handoff(state, self.agent_id)
            handoff = self.meeting_service.store.get_handoff(state.get("handoff_id"))
            if handoff["status"] == "completed" and handoff["result"]:
                return handoff["result"]
            context = None
            if self.context_provider is None:
                context = {
                    "verified_facts": [],
                    "untrusted_evidence": [],
                    "warnings": ["Scoped meeting context integration is unavailable."],
                }
            else:
                try:
                    context = await self.context_provider(state, meeting)
                except Exception:
                    context = {
                        "verified_facts": [],
                        "untrusted_evidence": [],
                        "warnings": ["Scoped meeting context retrieval failed."],
                    }
            if not isinstance(context, dict):
                context = {
                    "verified_facts": [],
                    "untrusted_evidence": [],
                    "warnings": ["Scoped meeting context returned an invalid result."],
                }
            facts = context.get("verified_facts", [])
            untrusted = context.get("untrusted_evidence", [])
            warnings = context.get("warnings", [])
            if not isinstance(warnings, list):
                warnings = ["Meeting context returned invalid warning metadata."]
            if not isinstance(facts, list):
                facts = []
                warnings = [*warnings, "Verified meeting facts were invalid and omitted."]
            if not isinstance(untrusted, list):
                untrusted = []
                warnings = [*warnings, "Untrusted meeting context was invalid and omitted."]
            warnings = [str(item)[:500] for item in warnings[:30] if isinstance(item, str)]
            context_evidence = {
                "verified_facts": facts,
                "untrusted_evidence": untrusted,
                "warnings": warnings,
            }
            return self.meeting_service.notes_for_handoff(
                state,
                self.agent_id,
                verified_facts=facts,
                context_evidence=context_evidence,
            )
        except PermissionError:
            return {"status": "blocked", "reason": "authorized_meeting_artifact_required"}
        except (ValueError, KeyError):
            return {"status": "blocked", "reason": "registered_transcript_required"}
