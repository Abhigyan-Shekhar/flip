from switchcrew.notes import build_meeting_notes, find_discrepancies


def transcript(*entries):
    return {
        "words": [
            {
                "text": text,
                "start": start,
                "end": start + 1,
                "type": "word",
                "speaker_id": speaker,
            }
            for text, start, speaker in entries
        ]
    }


def test_notes_preserve_evidence_and_never_map_provider_speaker_ids():
    notes = build_meeting_notes(
        "meeting-a",
        transcript(
            ("We decided to ship the update.", 0, "speaker_4"),
            ("I will check the test report by Friday.", 3, "speaker_4"),
            ("Who owns deployment?", 6, None),
        ),
    )

    assert notes["minutes"]["decisions"][0]["evidence_refs"] == ["seg-00001"]
    proposal = notes["action_items"][0]
    assert proposal["assignee_speaker_id"] == "speaker_4"
    assert proposal["assignee_identity"] is None
    assert proposal["evidence_refs"] == ["seg-00002"]
    assert proposal["confirmation_required"] is True
    assert notes["speaker_wise_summary"]["speakers"][0]["attribution"] == ("provider_anonymous_id")
    assert "no employee identity was inferred" in notes["speaker_wise_summary"]["review_note"]


def test_discrepancy_engine_labels_similar_deadlines_as_potential():
    notes = build_meeting_notes(
        "meeting-b",
        transcript(
            ("We will finish the migration by Friday.", 0, "speaker_0"),
            ("We will finish the migration by Monday.", 3, "speaker_1"),
        ),
    )

    assert notes["discrepancies"] == [
        {
            "classification": "potential_discrepancy",
            "summary": "Different dates were stated for a similar commitment.",
            "evidence_refs": ["seg-00001", "seg-00002"],
            "values": ["Friday", "Monday"],
        }
    ]


def test_verified_contradiction_requires_explicit_verified_source():
    segments = [
        {
            "segment_id": "seg-00001",
            "text": "The release is ready.",
        }
    ]
    assert (
        find_discrepancies(
            segments,
            [
                {
                    "verification": "unverified",
                    "statement": "The release is not ready.",
                    "source_ref": "x",
                }
            ],
        )
        == []
    )
    assert find_discrepancies(
        segments,
        [
            {
                "verification": "verified",
                "statement": "The release is not ready.",
                "source_ref": "ticket-1",
            }
        ],
    )[0]["evidence_refs"] == ["ticket-1", "seg-00001"]


def test_notes_reject_missing_or_invalid_word_timestamps():
    for words in (None, [{"text": "hello", "start": -1, "end": 0}]):
        try:
            build_meeting_notes("meeting-c", {"words": words})
        except ValueError:
            pass
        else:
            raise AssertionError("invalid transcript should be rejected")
