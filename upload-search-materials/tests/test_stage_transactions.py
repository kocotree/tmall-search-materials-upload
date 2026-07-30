import json
from pathlib import Path

import pytest

from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.persistence import PersistenceAccessDenied


def _event_count(session_path: Path, request_id: str) -> int:
    return sum(
        1
        for line in (session_path / "events.ndjson").read_text(
            encoding="utf-8"
        ).splitlines()
        if json.loads(line).get("persistence_request_id") == request_id
    )


def test_retry_repairs_snapshot_ahead_of_session_state(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request_id = "draft-half-commit-0001"
    real_write_state = store._write_session_state
    failed = False

    def fail_state_once(session_id, state):
        nonlocal failed
        if not failed:
            failed = True
            raise PersistenceAccessDenied(
                store._session_path(session_id) / "session.json"
            )
        return real_write_state(session_id, state)

    monkeypatch.setattr(store, "_write_session_state", fail_state_once)
    with pytest.raises(PersistenceAccessDenied):
        store.save_draft(
            session.session_id,
            "setup",
            {"store": "测试店铺"},
            expected_revision=0,
            request_id=request_id,
        )

    raw = store._load_session_file(session.session_id)
    stage = store._stage_path(session.session_id, "setup")
    assert raw["stages"]["setup"] == {"revision": 0, "status": "draft"}
    assert (stage / "revisions" / "0001" / "input.json").is_file()
    assert store.stage_transaction_status(
        session.session_id, "setup"
    )["recoverable"]

    monkeypatch.setattr(store, "_write_session_state", real_write_state)
    repaired = store.save_draft(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
        expected_revision=0,
        request_id=request_id,
    )

    assert repaired["revision"] == 1
    assert store.load_session(session.session_id)["stages"]["setup"] == {
        "revision": 1,
        "status": "draft",
    }
    assert store.stage_transaction_status(session.session_id, "setup") is None
    assert _event_count(session.path, request_id) == 1


def test_duplicate_completed_request_returns_original_result(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request_id = "submit-idempotent-0001"

    first = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
        expected_revision=1,
        request_id=request_id,
    )
    second = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
        expected_revision=1,
        request_id=request_id,
    )

    assert second == first
    assert store.load_session(session.session_id)["stages"]["setup"] == {
        "revision": 1,
        "status": "ready_for_agent",
    }
    assert _event_count(session.path, request_id) == 1


def test_same_request_id_with_different_content_fails_closed(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request_id = "draft-content-conflict-0001"
    store.save_draft(
        session.session_id,
        "setup",
        {"store": "first"},
        expected_revision=0,
        request_id=request_id,
    )
    snapshot = (
        store._stage_path(session.session_id, "setup")
        / "revisions"
        / "0001"
        / "input.json"
    ).read_bytes()

    with pytest.raises(
        InteractionConflict, match="REVISION_CONTENT_CONFLICT"
    ):
        store.save_draft(
            session.session_id,
            "setup",
            {"store": "different"},
            expected_revision=1,
            request_id=request_id,
        )

    assert snapshot == (
        store._stage_path(session.session_id, "setup")
        / "revisions"
        / "0001"
        / "input.json"
    ).read_bytes()


def test_legacy_half_commit_without_envelope_is_reconciled(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    stage = store._stage_path(session.session_id, "setup")
    document = {
        "schema_version": 1,
        "session_id": session.session_id,
        "stage_id": "setup",
        "revision": 1,
        "created_at": "2026-07-30T00:46:09+00:00",
        "submitted_at": "2026-07-30T00:46:09+00:00",
        "values": {"store": "测试店铺"},
        "user_notes": "",
        "interaction_history": [],
    }
    store._write_json_atomic(stage / "input.json", document)
    store._write_json_atomic(
        stage / "revisions" / "0001" / "input.json", document
    )

    repaired = store.save_draft(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
        expected_revision=0,
        request_id="legacy-half-commit-0001",
    )

    assert repaired == document
    assert store.load_session(session.session_id)["stages"]["setup"] == {
        "revision": 1,
        "status": "draft",
    }


def test_sanitized_live_half_commit_fixture_replays_to_one_draft(tmp_path):
    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "session_20260730_004051_half_commit_sanitized.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    stage = store._stage_path(session.session_id, "setup")
    document = {
        **fixture["current_input"],
        "session_id": session.session_id,
    }
    store._write_json_atomic(stage / "input.json", document)
    store._write_json_atomic(
        stage / "revisions" / "0001" / "input.json", document
    )

    repaired = store.save_draft(
        session.session_id,
        "setup",
        document["values"],
        expected_revision=0,
        request_id="fixture-live-half-commit-0001",
    )

    assert repaired == document
    assert store.load_session(session.session_id)["stages"]["setup"] == {
        "revision": 1,
        "status": "draft",
    }
    assert not (stage / "handoff.json").exists()
    assert _event_count(
        session.path, "fixture-live-half-commit-0001"
    ) == 1


@pytest.mark.parametrize(
    "failure_point",
    [
        "prepared",
        "content_written",
        "snapshot_written",
        "state_committed",
        "completed",
    ],
)
def test_failure_after_each_transaction_milestone_recovers_once(
    tmp_path, monkeypatch, failure_point
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request_id = f"milestone-{failure_point}-0001"
    values = {"store": "milestone"}
    failed = False

    if failure_point == "prepared":
        real_prepare = store._prepare_stage_transaction

        def prepare_then_fail(**kwargs):
            nonlocal failed
            result = real_prepare(**kwargs)
            if not failed:
                failed = True
                raise OSError("simulated crash after prepared")
            return result

        monkeypatch.setattr(
            store, "_prepare_stage_transaction", prepare_then_fail
        )
    else:
        real_advance = store._advance_stage_transaction

        def advance_then_fail(stage_path, transaction, state):
            nonlocal failed
            real_advance(stage_path, transaction, state)
            if state == failure_point and not failed:
                failed = True
                raise OSError(f"simulated crash after {state}")

        monkeypatch.setattr(
            store, "_advance_stage_transaction", advance_then_fail
        )

    with pytest.raises(OSError, match="simulated crash"):
        store.save_draft(
            session.session_id,
            "setup",
            values,
            expected_revision=0,
            request_id=request_id,
        )

    monkeypatch.undo()
    recovered = store.save_draft(
        session.session_id,
        "setup",
        values,
        expected_revision=0,
        request_id=request_id,
    )

    assert recovered["revision"] == 1
    assert store.stage_transaction_status(session.session_id, "setup") is None
    assert _event_count(session.path, request_id) == 1
