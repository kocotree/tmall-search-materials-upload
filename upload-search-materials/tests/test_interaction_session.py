import json
import threading
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

from upload_search_materials.interaction import STAGES
from upload_search_materials.interaction.session import (
    SCHEMA_VERSION,
    InteractionConflict,
    InteractionPathError,
    SessionStore,
)


VALID_STAGE_STATUSES = {
    "draft",
    "ready_for_agent",
    "processing",
    "needs_user_input",
    "completed",
    "blocked",
}


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


def test_stage_path_falls_back_to_unique_legacy_numbered_directory(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    current = session.path / "03-asset-matching"
    legacy = session.path / "04-asset-matching"
    current.rename(legacy)

    assert store._stage_path(session.session_id, "asset_matching") == legacy


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
        session.session_id, "completeness", {"selected_product_ids": ["p1"]}
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


def test_handoff_claim_records_authoritative_processing_lease(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )

    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="thread-123",
        lease_seconds=60,
    )

    claim = store.processing_claim(session.session_id, "setup")
    assert claim is not None
    assert claim["claimant_id"] == "thread-123"
    assert claim["revision"] == handoff["revision"]
    assert claim["input_sha256"] == handoff["input_sha256"]
    assert claim["expired"] is False
    assert datetime.fromisoformat(claim["claimed_at"]).tzinfo is not None


def test_expired_processing_claim_can_be_reclaimed_and_rejects_late_write(
    tmp_path,
):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )
    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="old-agent",
    )
    old_claim = store.processing_claim(session.session_id, "setup")
    state = store.load_session(session.session_id)
    state["processing_claim"]["lease_expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    store._write_json_atomic(session.path / "session.json", state)

    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="new-agent",
        reclaim_expired=True,
    )
    new_claim = store.processing_claim(session.session_id, "setup")
    assert new_claim["claim_id"] != old_claim["claim_id"]
    with pytest.raises(InteractionConflict, match="PROCESSING_CLAIM_STALE"):
        store.write_result(
            session.session_id,
            "setup",
            handoff["revision"],
            handoff["input_sha256"],
            status="completed",
            summary="late",
            claim_id=old_claim["claim_id"],
        )


def test_legacy_processing_without_lease_can_be_reclaimed(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )
    state = store.load_session(session.session_id)
    state["stages"]["setup"]["status"] = "processing"
    state["processing_claim"] = None
    store._write_json_atomic(session.path / "session.json", state)

    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="migration-agent",
        reclaim_expired=True,
    )

    claim = store.processing_claim(session.session_id, "setup")
    assert claim["claimant_id"] == "migration-agent"
    assert claim["revision"] == handoff["revision"]
    assert claim["input_sha256"] == handoff["input_sha256"]


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


def test_session_lock_reuses_outer_os_lock_for_same_thread(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    with store._session_lock(session.session_id):
        with store._session_lock(session.session_id):
            state = store.load_session(session.session_id)

    assert state["session_id"] == session.session_id


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


def test_write_result_persists_structured_asset_gallery_data(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(tmp_path)],
            "source_types": ["image"],
            "license_decisions": [{"asset_id": "A", "status": "confirmed"}],
            "asset_decisions": [{"asset_id": "A", "decision": "selected"}],
        },
    )
    data = {
        "requirements": [
            {
                "product_id": "123",
                "product_title": "测试商品",
                "missing_materials": 1,
                "images_per_material": 3,
            }
        ],
        "asset_candidates": [
            {
                "asset_id": "A",
                "product_id": "123",
                "source_path": str(tmp_path / "a.jpg"),
                "sha256": "a" * 64,
            }
        ],
        "remote_dedupe_status": "not_available",
    }

    result = store.write_result(
        session.session_id,
        "asset_matching",
        handoff["revision"],
        handoff["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data=data,
    )

    assert result["data"] == data
    persisted = json.loads(
        (session.path / "03-asset-matching" / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["data"]["asset_candidates"][0]["asset_id"] == "A"


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
    store.save_input(session.session_id, "completeness", {"selected_product_ids": ["1"]})
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

    draft = store.save_draft(
        session.session_id,
        "setup",
        {"store": "draft"},
        expected_revision=1,
    )

    assert draft["revision"] == 2
    assert draft["values"] == {"store": "draft"}
    assert not (session.path / "01-setup" / "handoff.json").exists()
    assert not (session.path / "01-setup" / "result.json").exists()
    assert not approval_path.exists()
    assert not (session.path / "02-completeness" / "handoff.json").exists()
    state = store.load_session(session.session_id)
    assert state["stages"]["setup"] == {"revision": 2, "status": "draft"}
    assert state["stages"]["completeness"]["status"] == "draft"


def test_submitted_and_draft_revisions_keep_immutable_snapshots(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    handoff = store.save_input(session.session_id, "setup", {"store": "submitted"})
    draft = store.save_draft(
        session.session_id,
        "setup",
        {"store": "revised draft"},
        expected_revision=handoff["revision"],
    )

    first = session.path / "01-setup" / "revisions" / "0001"
    second = session.path / "01-setup" / "revisions" / "0002"
    assert json.loads((first / "input.json").read_text(encoding="utf-8"))["values"] == {
        "store": "submitted"
    }
    assert (first / "handoff.json").is_file()
    assert json.loads((second / "input.json").read_text(encoding="utf-8"))["values"] == {
        "store": "revised draft"
    }
    assert not (second / "handoff.json").exists()
    assert draft["revision"] == 2


def test_only_unclaimed_handoff_can_be_withdrawn(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(session.session_id, "setup", {"store": "one"})

    withdrawn = store.withdraw_handoff(
        session.session_id, "setup", expected_revision=handoff["revision"]
    )

    assert withdrawn == {"status": "draft", "revision": 1}
    assert not (session.path / "01-setup" / "handoff.json").exists()
    assert store.load_session(session.session_id)["stages"]["setup"]["status"] == "draft"
    assert (session.path / "01-setup" / "revisions" / "0001" / "handoff.json").is_file()

    submitted_again = store.save_input(session.session_id, "setup", {"store": "two"})
    store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.1)
    with pytest.raises(InteractionConflict, match="unclaimed"):
        store.withdraw_handoff(
            session.session_id,
            "setup",
            expected_revision=submitted_again["revision"],
        )


def test_wait_claims_each_handoff_revision_only_once_sequentially(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    expected = store.save_input(session.session_id, "setup", {"store": "one"})

    assert store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.1) == expected
    with pytest.raises(TimeoutError, match="setup"):
        store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.05)


def test_simultaneous_waiters_cannot_claim_the_same_handoff(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    expected = store.save_input(session.session_id, "setup", {"store": "one"})
    barrier = threading.Barrier(2)
    claims = []
    errors = []

    def wait():
        barrier.wait()
        try:
            claims.append(
                store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.25)
            )
        except Exception as error:
            errors.append(error)

    waiters = [threading.Thread(target=wait) for _ in range(2)]
    for waiter in waiters:
        waiter.start()
    for waiter in waiters:
        waiter.join(1)

    assert claims == [expected]
    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)


def test_stale_draft_compare_and_swap_preserves_all_durable_bytes(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(session.session_id, "setup", {"store": "submitted"})
    store.write_result(
        session.session_id,
        "setup",
        handoff["revision"],
        handoff["input_sha256"],
        status="completed",
        summary="done",
    )
    tracked = [
        session.path / "session.json",
        session.path / "01-setup" / "input.json",
        session.path / "01-setup" / "handoff.json",
        session.path / "01-setup" / "result.json",
    ]
    before = {path: path.read_bytes() for path in tracked}

    with pytest.raises(InteractionConflict, match="stale"):
        store.save_draft(
            session.session_id,
            "setup",
            {"store": "stale"},
            expected_revision=0,
        )

    assert {path: path.read_bytes() for path in tracked} == before


def test_protocol_documents_are_versioned_and_session_metadata_is_durable(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session(datetime(2026, 7, 22, 10, 0, 0))
    state = store.load_session(session.session_id)

    assert SCHEMA_VERSION == 1
    assert state["schema_version"] == SCHEMA_VERSION
    assert state["updated_at"] == state["created_at"]
    assert state["runs_root"] == str(Path(tmp_path).resolve())
    assert state["video_test_deferred"] is True
    assert state["last_agent_heartbeat"] is None
    assert {entry["status"] for entry in state["stages"].values()} <= VALID_STAGE_STATUSES

    draft = store.save_draft(
        session.session_id,
        "setup",
        {"store": "draft"},
        expected_revision=0,
    )
    assert draft["schema_version"] == SCHEMA_VERSION
    assert isinstance(draft["submitted_at"], str)
    handoff = store.save_input(
        session.session_id,
        "setup",
        {"store": "submitted"},
        expected_revision=2,
    )
    assert handoff["schema_version"] == SCHEMA_VERSION
    result = store.write_result(
        session.session_id,
        "setup",
        handoff["revision"],
        handoff["input_sha256"],
        status="completed",
        summary="done",
    )
    assert result["schema_version"] == SCHEMA_VERSION
    assert isinstance(result["completed_at"], str)


def test_every_session_mutation_refreshes_updated_at(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    state_path = session.path / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    store.save_draft(
        session.session_id,
        "setup",
        {"store": "draft"},
        expected_revision=0,
    )

    assert store.load_session(session.session_id)["updated_at"] != "2000-01-01T00:00:00+00:00"


def test_unsupported_schema_versions_are_rejected_on_session_and_handoff_reads(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    state_path = session.path / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 999
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(InteractionConflict, match="schema_version"):
        store.load_session(session.session_id)

    state["schema_version"] = SCHEMA_VERSION
    state_path.write_text(json.dumps(state), encoding="utf-8")
    store.save_input(session.session_id, "setup", {"store": "submitted"})
    handoff_path = session.path / "01-setup" / "handoff.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    handoff["schema_version"] = 999
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    with pytest.raises(InteractionConflict, match="schema_version"):
        store.wait_for_handoff(session.session_id, "setup", timeout_seconds=0.1)


def test_result_status_must_belong_to_closed_protocol_set(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    handoff = store.save_input(session.session_id, "setup", {"store": "submitted"})

    with pytest.raises(InteractionConflict, match="status"):
        store.write_result(
            session.session_id,
            "setup",
            handoff["revision"],
            handoff["input_sha256"],
            status="approved",
            summary="invalid",
        )

    assert not (session.path / "01-setup" / "result.json").exists()
