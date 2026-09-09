import pytest

from upload_search_materials.state_store import InvalidTransition, StateStore


def test_resume_skips_item_with_remote_evidence(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "submitted", remote_material_id="RM-1", evidence="list-row.png")
    store.close()

    reopened = StateStore(tmp_path / "run.sqlite3")

    assert "MAT-1" not in reopened.recoverable_items(["MAT-1", "MAT-2"])
    assert reopened.recoverable_items(["MAT-1", "MAT-2"]) == ["MAT-2"]
    reopened.close()


def test_publish_uncertain_is_sent_to_verification_not_upload(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "publish_uncertain", evidence="timeout")

    assert store.recoverable_items(["MAT-1"]) == []
    assert store.items_requiring_verification() == ["MAT-1"]
    store.close()


def test_valid_transition_is_persisted_with_evidence(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "approved")

    store.record_transition(
        "MAT-1",
        "approved",
        "uploading",
        reason="batch publish",
        evidence="manifest.json",
    )
    transitions = store.transitions_for("MAT-1")

    assert transitions[0]["old_status"] == "approved"
    assert transitions[0]["new_status"] == "uploading"
    assert transitions[0]["evidence"] == "manifest.json"
    assert store.item_status("MAT-1") == "uploading"
    store.close()


def test_no_empty_slot_can_finish_as_non_retryable_skip(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "approved")

    store.record_transition(
        "MAT-1",
        "approved",
        "skipped",
        reason="QIANNIU_NO_EMPTY_SLOT",
        evidence="product=123;pre_publish_attempts=1",
    )

    assert store.item_status("MAT-1") == "skipped"
    assert store.recoverable_items(["MAT-1"]) == []
    store.close()


def test_reconciliation_can_resolve_uploading_directly_to_success(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "uploading", evidence="PRE_PUBLISH_CHECKPOINT", attempt_count=1)

    store.record_transition(
        "MAT-1",
        "uploading",
        "success",
        reason="REMOTE_VERIFIED",
        evidence="remote row",
        remote_material_id="RM-1",
        attempt_count=1,
    )

    record = store.item_record("MAT-1")
    assert record["status"] == "success"
    assert record["remote_material_id"] == "RM-1"
    assert record["attempt_count"] == 1
    assert record["evidence"] == "remote row"
    store.close()


@pytest.mark.parametrize("old_status", ["submitted", "under_review"])
def test_inconclusive_reverification_returns_durable_state_to_uncertain(tmp_path, old_status):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", old_status, remote_material_id="RM-1", evidence="old remote row")

    store.record_transition(
        "MAT-1",
        old_status,
        "publish_uncertain",
        reason="REMOTE_TABLE_NOT_VISIBLE",
        evidence="verification unavailable",
        remote_material_id="RM-1",
        attempt_count=1,
    )

    assert store.item_status("MAT-1") == "publish_uncertain"
    assert store.transitions_for("MAT-1")[-1]["reason"] == "REMOTE_TABLE_NOT_VISIBLE"
    store.close()


def test_invalid_transition_does_not_modify_state(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "approved")

    with pytest.raises(InvalidTransition, match="approved -> success"):
        store.record_transition("MAT-1", "approved", "success", reason="bad", evidence="")

    assert store.item_status("MAT-1") == "approved"
    assert store.transitions_for("MAT-1") == []
    store.close()


def test_user_retry_can_reopen_a_pre_publish_blocked_item(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item(
        "MAT-1",
        "blocked",
        evidence=(
            "QIANNIU_FORM_NOT_READY;pre_publish_attempts=4;"
            "skipped_after_pre_publish_retries=true"
        ),
    )

    store.record_transition(
        "MAT-1",
        "blocked",
        "ready_for_review",
        reason="USER_RETRY_SAFE_PRE_PUBLISH_FAILURE",
        evidence=(
            "pre_publish_attempts=4;"
            "skipped_after_pre_publish_retries=true;"
            "user_retry_authorized=true"
        ),
    )

    assert store.item_status("MAT-1") == "ready_for_review"
    store.close()


def test_run_metadata_survives_restart(tmp_path):
    database = tmp_path / "run.sqlite3"
    store = StateStore(database)
    store.save_run(
        run_id="RUN-1",
        store="KK Tree",
        month=7,
        mode="dry-run",
        status="created",
        config_hash="abc",
    )
    store.close()

    reopened = StateStore(database)

    assert reopened.get_run("RUN-1") == {
        "run_id": "RUN-1",
        "store": "KK Tree",
        "month": 7,
        "mode": "dry-run",
        "status": "created",
        "config_hash": "abc",
    }
    reopened.close()


def test_item_record_includes_persisted_remote_evidence_and_timestamp(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item(
        "MAT-1",
        "publish_uncertain",
        remote_material_id=None,
        evidence="timeout",
        attempt_count=1,
    )

    record = store.item_record("MAT-1")

    assert record["status"] == "publish_uncertain"
    assert record["evidence"] == "timeout"
    assert record["attempt_count"] == 1
    assert "T" in record["updated_at"]
    store.close()
