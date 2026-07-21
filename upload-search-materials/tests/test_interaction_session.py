import json
from datetime import datetime
from hashlib import sha256

import pytest

from upload_search_materials.interaction import STAGES
from upload_search_materials.interaction.session import (
    InteractionConflict,
    InteractionPathError,
    SessionStore,
)


def _events(session_path):
    return [
        json.loads(line)
        for line in (session_path / "events.ndjson").read_text(encoding="utf-8").splitlines()
    ]


def test_same_second_sessions_get_collision_suffix(tmp_path):
    fixed_now = datetime(2026, 7, 21, 14, 30, 25)
    store = SessionStore(tmp_path)

    first = store.create_session(fixed_now)
    second = store.create_session(fixed_now)

    assert first.session_id == "20260721_143025"
    assert second.session_id == "20260721_143025_02"


def test_create_session_uses_registry_stage_directories(tmp_path):
    session = SessionStore(tmp_path).create_session(datetime(2026, 7, 21, 14, 30, 25))

    stage_directories = sorted(path.name for path in session.path.iterdir() if path.is_dir())
    assert stage_directories == [
        f"{index:02d}-{stage.id.replace('_', '-')}"
        for index, stage in enumerate(STAGES, start=1)
    ]
    state = json.loads((session.path / "session.json").read_text(encoding="utf-8"))
    assert state["session_id"] == session.session_id
    assert state["current_stage"] == "setup"
    assert state["stages"]["setup"] == {"revision": 0, "status": "draft"}


def test_handoff_hash_matches_atomic_input_bytes(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    handoff = store.save_input(session.session_id, "setup", {"store": "测试店铺"})

    input_path = session.path / "01-setup" / "input.json"
    assert handoff["input_sha256"] == sha256(input_path.read_bytes()).hexdigest()
    assert "测试店铺" in input_path.read_text(encoding="utf-8")
    assert handoff["session_id"] == session.session_id
    assert handoff["stage_id"] == "setup"
    assert handoff["revision"] == 1


def test_result_with_wrong_revision_is_rejected(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(session.session_id, "setup", {"store": "测试店铺"})

    with pytest.raises(InteractionConflict, match="revision"):
        store.write_result(
            session.session_id,
            "setup",
            handoff["revision"] + 1,
            handoff["input_sha256"],
            status="completed",
            summary="ok",
        )


def test_result_with_wrong_input_hash_is_rejected(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(session.session_id, "setup", {"store": "测试店铺"})

    with pytest.raises(InteractionConflict, match="input_sha256"):
        store.write_result(
            session.session_id,
            "setup",
            handoff["revision"],
            "0" * 64,
            status="completed",
            summary="ok",
        )


@pytest.mark.parametrize("session_id", ["../escape", "C:/escape", "..\\escape"])
def test_session_id_cannot_escape_runs_root(tmp_path, session_id):
    with pytest.raises(InteractionPathError):
        SessionStore(tmp_path).load_session(session_id)


def test_unknown_stage_is_rejected_without_creating_a_directory(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    with pytest.raises(KeyError):
        store.save_input(session.session_id, "not-a-stage", {})

    assert not (session.path / "not-a-stage").exists()


def test_edit_increments_revision_and_invalidates_current_and_downstream_state(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    setup = store.save_input(session.session_id, "setup", {"store": "first"})
    store.write_result(
        session.session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup complete",
    )
    completeness = store.save_input(
        session.session_id, "completeness", {"confirmed_product_ids": ["p1"]}
    )
    store.write_result(
        session.session_id,
        "completeness",
        completeness["revision"],
        completeness["input_sha256"],
        status="completed",
        summary="inspection complete",
    )
    (session.path / "01-setup" / "approval.json").write_text(
        '{"approved": true}', encoding="utf-8"
    )

    revised = store.save_input(session.session_id, "setup", {"store": "second"})

    assert revised["revision"] == 2
    state = store.load_session(session.session_id)
    assert state["stages"]["setup"]["status"] == "ready_for_agent"
    assert state["stages"]["completeness"]["status"] == "draft"
    assert not (session.path / "01-setup" / "result.json").exists()
    assert not (session.path / "01-setup" / "approval.json").exists()
    assert not (session.path / "02-completeness" / "handoff.json").exists()
    assert not (session.path / "02-completeness" / "result.json").exists()
    invalidated = [event for event in _events(session.path) if event["event"] == "artifact_invalidated"]
    assert {event["artifact"] for event in invalidated} >= {
        "approval.json",
        "result.json",
        "handoff.json",
    }


def test_wait_verifies_hash_and_marks_stage_processing(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    expected = store.save_input(session.session_id, "setup", {"store": "测试店铺"})

    actual = store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.1)

    assert actual == expected
    state = store.load_session(session.session_id)
    assert state["stages"]["setup"]["status"] == "processing"
    assert state["last_agent_heartbeat"] is not None


def test_wait_rejects_input_changed_after_handoff(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    store.save_input(session.session_id, "setup", {"store": "before"})
    input_path = session.path / "01-setup" / "input.json"
    input_path.write_text('{"store":"tampered"}', encoding="utf-8")

    with pytest.raises(InteractionConflict, match="input_sha256"):
        store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.1)


def test_wait_times_out_only_when_explicit_deadline_expires(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    with pytest.raises(TimeoutError, match="setup"):
        store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0)


def test_write_result_binds_identity_and_updates_session(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(session.session_id, "setup", {"store": "测试店铺"})

    result = store.write_result(
        session.session_id,
        "setup",
        handoff["revision"],
        handoff["input_sha256"],
        status="needs_user_input",
        summary="choose an asset source",
        blocking_reasons=["asset source missing"],
        next_action="edit setup",
    )

    assert result["session_id"] == session.session_id
    assert result["stage_id"] == "setup"
    assert result["revision"] == handoff["revision"]
    assert result["input_sha256"] == handoff["input_sha256"]
    assert store.load_session(session.session_id)["stages"]["setup"]["status"] == "needs_user_input"


def test_recovery_instruction_names_absolute_session_path_and_stage(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    instruction = store.recovery_instruction(session.session_id, "setup")

    assert str(session.path.resolve()) in instruction
    assert "setup" in instruction
    assert "handoff.json" in instruction
    assert "input_sha256" in instruction
