"""Correction runtime contract checks using controlled Console adapters and real Git."""

import pytest
from test_engineering_qa import ControlledSession, commit, receipt, state

from switchcrew.engineering import EngineeringExecutor, Ledger, assignment_id, git


@pytest.mark.asyncio
async def test_correction_starts_from_exact_prior_revision_and_preserves_cumulative_diff(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    initial = commit(source, "broken")
    ledger = Ledger(tmp_path / "execution.sqlite3")

    original_session = ControlledSession()
    original = EngineeringExecutor(ledger, tmp_path / "work", source, initial, original_session)
    await original(state("eng-original"))
    original_checkout = original_session.checkout
    first_revision = commit(original_checkout, "partly fixed")
    receipt(original_checkout, assignment_id("eng-original"), first_revision)
    implementation = await original(state("eng-original"))
    assert implementation["status"] == "implemented"

    correction_session = ControlledSession()
    correction = EngineeringExecutor(
        ledger,
        tmp_path / "work",
        implementation["checkout"],
        implementation["final_revision"],
        correction_session,
        review_base_revision=implementation["base_revision"],
        expected_source_revision=implementation["final_revision"],
    )
    correction_state = state("eng-correction")
    await correction(correction_state)
    corrected_checkout = correction_session.checkout
    correction_base = git(corrected_checkout, "rev-parse", "HEAD")
    assert correction_base == first_revision
    final = commit(corrected_checkout, "fully fixed")
    receipt(corrected_checkout, assignment_id("eng-correction"), final)
    result = await correction(correction_state)

    assert result["status"] == "implemented"
    assert result["checkout_base_revision"] == first_revision
    assert result["base_revision"] == initial
    assert "+fully fixed" in result["diff"]
    assert result["assignment_id"] != implementation["assignment_id"]
    assert ledger.get(assignment_id("eng-original"))["result"] == implementation
    ledger.close()


@pytest.mark.asyncio
async def test_correction_fails_closed_if_previous_checkout_head_changed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    initial = commit(source, "broken")
    ledger = Ledger(tmp_path / "execution.sqlite3")
    session = ControlledSession()
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, initial, session)
    await executor(state("eng-first"))
    revision = commit(session.checkout, "prior")
    receipt(session.checkout, assignment_id("eng-first"), revision)
    result = await executor(state("eng-first"))
    commit(session.checkout, "mutated")

    correction = EngineeringExecutor(
        ledger,
        tmp_path / "work",
        result["checkout"],
        result["final_revision"],
        ControlledSession(),
        expected_source_revision=result["final_revision"],
    )
    outcome = await correction(state("eng-next"))
    assert outcome["status"] == "blocked_configuration"
    assert outcome["reason"] == "correction_source_revision_changed"
    ledger.close()


@pytest.mark.asyncio
async def test_existing_initial_assignment_ledger_remains_resumable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    base = commit(source, "broken")
    ledger = Ledger(tmp_path / "legacy.db")
    incoming = state("legacy-assignment")
    ledger.reserve(
        assignment_id(incoming["handoff_id"]),
        {
            "source": str(source.resolve()),
            "base_revision": base,
            "question": incoming["question"],
            "acceptance_criteria": incoming["acceptance_criteria"],
        },
    )
    session = ControlledSession()
    executor = EngineeringExecutor(ledger, tmp_path / "work", source, base, session)
    assert (await executor(incoming))["status"] == "pending_execution"
    assert session.creates == 1
    ledger.close()
