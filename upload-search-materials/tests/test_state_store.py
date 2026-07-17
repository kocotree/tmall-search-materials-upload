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


def test_invalid_transition_does_not_modify_state(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "approved")

    with pytest.raises(InvalidTransition, match="approved -> success"):
        store.record_transition("MAT-1", "approved", "success", reason="bad", evidence="")

    assert store.item_status("MAT-1") == "approved"
    assert store.transitions_for("MAT-1") == []
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
