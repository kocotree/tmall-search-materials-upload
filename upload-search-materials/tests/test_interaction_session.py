import json
import threading
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
    result_invalidation = next(
        event
        for event in invalidated
        if event["stage_id"] == "setup" and event["artifact"] == "result.json"
    )
    assert {
        "session_id",
        "stage_id",
        "artifact",
        "revision",
        "status",
        "input_sha256",
    } <= result_invalidation.keys()
    assert "summary" not in result_invalidation
    assert "values" not in result_invalidation
    assert "user_notes" not in result_invalidation


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


def test_wait_timeout_applies_while_another_process_holds_the_session_lock(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    store.save_input(session.session_id, "setup", {"store": "locked"})
    outcome = []

    def claim_with_deadline():
        try:
            store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.1)
        except TimeoutError as error:
            outcome.append(str(error))

    with store._session_lock(session.session_id):
        claimant = threading.Thread(target=claim_with_deadline)
        claimant.start()
        claimant.join(1)

    assert outcome and "setup" in outcome[0]


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


def test_edit_cannot_be_overwritten_by_stale_result_claim(tmp_path, monkeypatch):
    agent = SessionStore(tmp_path)
    editor = SessionStore(tmp_path)
    session = agent.create_session()
    first = agent.save_input(session.session_id, "setup", {"store": "first"})
    result_write_started = threading.Event()
    allow_result_write = threading.Event()
    edit_session_write_started = threading.Event()
    allow_edit_session_write = threading.Event()
    original_write = agent._write_json_atomic
    original_editor_write = editor._write_json_atomic

    def pause_stale_result(path, document):
        if path.name == "result.json":
            result_write_started.set()
            assert allow_result_write.wait(2)
        original_write(path, document)

    def pause_edit_session_write(path, document):
        if path.name == "session.json" and document["stages"]["setup"]["revision"] == 2:
            edit_session_write_started.set()
            assert allow_edit_session_write.wait(2)
        original_editor_write(path, document)

    monkeypatch.setattr(agent, "_write_json_atomic", pause_stale_result)
    monkeypatch.setattr(editor, "_write_json_atomic", pause_edit_session_write)
    agent_thread = threading.Thread(
        target=agent.write_result,
        args=(session.session_id, "setup", first["revision"], first["input_sha256"]),
        kwargs={"status": "completed", "summary": "stale"},
    )
    edit_thread = threading.Thread(
        target=lambda: editor.save_input(session.session_id, "setup", {"store": "second"}),
    )
    agent_thread.start()
    try:
        assert result_write_started.wait(2)
        edit_thread.start()
        assert not edit_session_write_started.wait(0.25)
    finally:
        allow_result_write.set()
        agent_thread.join(2)
        allow_edit_session_write.set()
        if edit_thread.ident is not None:
            edit_thread.join(2)

    state = editor.load_session(session.session_id)
    handoff = json.loads((session.path / "01-setup" / "handoff.json").read_text(encoding="utf-8"))
    assert state["stages"]["setup"] == {"revision": 2, "status": "ready_for_agent"}
    assert handoff["revision"] == 2
    assert not (session.path / "01-setup" / "result.json").exists()


def test_edit_cannot_be_overwritten_by_stale_handoff_claim(tmp_path, monkeypatch):
    agent = SessionStore(tmp_path)
    editor = SessionStore(tmp_path)
    session = agent.create_session()
    agent.save_input(session.session_id, "setup", {"store": "first"})
    claim_write_started = threading.Event()
    allow_claim_write = threading.Event()
    edit_session_write_started = threading.Event()
    allow_edit_session_write = threading.Event()
    original_write = agent._write_json_atomic
    original_editor_write = editor._write_json_atomic

    def pause_stale_claim(path, document):
        if path.name == "session.json" and document["stages"]["setup"]["status"] == "processing":
            claim_write_started.set()
            assert allow_claim_write.wait(2)
        original_write(path, document)

    def pause_edit_session_write(path, document):
        if path.name == "session.json" and document["stages"]["setup"]["revision"] == 2:
            edit_session_write_started.set()
            assert allow_edit_session_write.wait(2)
        original_editor_write(path, document)

    monkeypatch.setattr(agent, "_write_json_atomic", pause_stale_claim)
    monkeypatch.setattr(editor, "_write_json_atomic", pause_edit_session_write)
    agent_thread = threading.Thread(
        target=agent.wait_for_handoff,
        args=(session.session_id, "setup"),
        kwargs={"timeout_seconds": 1},
    )
    edit_thread = threading.Thread(
        target=lambda: editor.save_input(session.session_id, "setup", {"store": "second"}),
    )
    agent_thread.start()
    try:
        assert claim_write_started.wait(2)
        edit_thread.start()
        assert not edit_session_write_started.wait(0.25)
    finally:
        allow_claim_write.set()
        agent_thread.join(2)
        allow_edit_session_write.set()
        if edit_thread.ident is not None:
            edit_thread.join(2)

    state = editor.load_session(session.session_id)
    handoff = json.loads((session.path / "01-setup" / "handoff.json").read_text(encoding="utf-8"))
    assert state["stages"]["setup"] == {"revision": 2, "status": "ready_for_agent"}
    assert handoff["revision"] == 2


def test_expected_revision_allows_only_one_concurrent_submit(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    barrier = threading.Barrier(2)
    handoffs = []
    errors = []

    def submit(label):
        barrier.wait()
        try:
            handoffs.append(
                store.save_input(
                    session.session_id,
                    "setup",
                    {"store": label},
                    expected_revision=1,
                )
            )
        except Exception as error:
            errors.append(error)

    first = threading.Thread(target=submit, args=("first",))
    second = threading.Thread(target=submit, args=("second",))
    first.start()
    second.start()
    first.join(2)
    second.join(2)

    assert len(handoffs) == 1
    assert handoffs[0]["revision"] == 1
    assert len(errors) == 1
    assert isinstance(errors[0], InteractionConflict)
    assert store.load_session(session.session_id)["stages"]["setup"] == {
        "revision": 1,
        "status": "ready_for_agent",
    }


def test_save_draft_invalidates_current_handoff_and_downstream_state(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    setup_handoff = store.save_input(session.session_id, "setup", {"store": "submitted"})
    store.save_input(session.session_id, "completeness", {"confirmed_product_ids": ["1"]})
    store.write_result(
        session.session_id,
        "setup",
        setup_handoff["revision"],
        setup_handoff["input_sha256"],
        status="completed",
        summary="finished",
    )
    approval_path = session.path / "01-setup" / "approval.json"
    approval_path.write_text("{}", encoding="utf-8")

    draft = store.save_draft(session.session_id, "setup", {"store": "draft"})

    assert draft["revision"] == 2
    assert draft["values"] == {"store": "draft"}
    assert not (session.path / "01-setup" / "handoff.json").exists()
    assert not (session.path / "01-setup" / "result.json").exists()
    assert not approval_path.exists()
    assert not (session.path / "02-completeness" / "handoff.json").exists()
    state = store.load_session(session.session_id)
    assert state["stages"]["setup"] == {"revision": 2, "status": "draft"}
    assert state["stages"]["completeness"]["status"] == "draft"
