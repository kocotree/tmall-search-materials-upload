from pathlib import Path

import pytest

import upload_search_materials.gallery_jobs as gallery_module
from upload_search_materials.gallery_jobs import (
    create_or_reuse_gallery_job,
    process_gallery_job,
    read_gallery_job,
    reconcile_gallery_job,
    migrate_legacy_gallery_handoff,
    resolve_material_folders,
)
from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


def _local_gallery_session(tmp_path: Path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    stage_path = store._stage_path(session.session_id, "asset_matching")
    folder = tmp_path / "adopted"
    folder.mkdir()
    decisions = [
        {
            "folder_id": "F1",
            "folder_path": str(folder),
            "product_id": "P1",
            "source_system": "nas",
            "decision": "confirmed",
        }
    ]
    committed = store.save_local_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(tmp_path)],
            "source_types": ["image"],
            "folder_decisions": decisions,
            "asset_decisions": [],
            "license_decisions": [],
        },
        expected_revision=0,
        request_id="prepare-gallery-test",
    )
    status_path = (
        session.path
        / "collected"
        / "promotion"
        / "current"
        / "promotion-material-status.csv"
    )
    status_path.parent.mkdir(parents=True)
    status_path.write_text("product_id,status\n", encoding="utf-8")
    (session.path / "inputs").mkdir(exist_ok=True)
    (session.path / "inputs" / "products.csv").write_text(
        "unused\n", encoding="utf-8"
    )
    return store, session.session_id, stage_path, decisions, committed


def test_material_folder_resolution_uses_source_id_and_relative_path(
    tmp_path,
):
    source_root = tmp_path / "mounted-source"
    folder = source_root / "year" / "product"
    folder.mkdir(parents=True)
    stage_path = tmp_path / "stage"
    stage_path.mkdir()
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(
            {
                "source_id": "model-materials",
                "label": "model",
                "path": str(source_root),
            },
        ),
        runs_root=tmp_path,
    )

    resolved = resolve_material_folders(
        [
            {
                "folder_id": "F1",
                "product_id": "P1",
                "source_id": "model-materials",
                "relative_path": "year/product",
                "folder_path": r"Y:\old-machine\year\product",
                "decision": "confirmed",
            }
        ],
        stage_path=stage_path,
        runtime=runtime,
    )

    assert resolved[0]["folder_path"] == str(folder)
    assert resolved[0]["source_id"] == "model-materials"
    assert resolved[0]["relative_path"] == "year/product"


def test_material_folder_resolution_rejects_parent_traversal(tmp_path):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(
            {
                "source_id": "model-materials",
                "label": "model",
                "path": str(tmp_path),
            },
        ),
        runs_root=tmp_path,
    )

    with pytest.raises(ValueError, match="SOURCE_PATH_INVALID"):
        resolve_material_folders(
            [
                {
                    "folder_id": "F1",
                    "product_id": "P1",
                    "source_id": "model-materials",
                    "relative_path": "../outside",
                    "decision": "confirmed",
                }
            ],
            stage_path=tmp_path,
            runtime=runtime,
        )


def test_gallery_job_success_publishes_review_without_handoff(
    tmp_path, monkeypatch
):
    store, session_id, stage_path, decisions, committed = (
        _local_gallery_session(tmp_path)
    )
    monkeypatch.setattr(gallery_module, "read_product_csv", lambda path: [])
    monkeypatch.setattr(
        gallery_module,
        "build_confirmed_folder_gallery",
        lambda *args, **kwargs: {
            "requirements": [{"product_id": "P1"}],
            "asset_candidates": [{"asset_id": "A1", "product_id": "P1"}],
            "scan_summary": {
                "discovered_images": 1,
                "discovered_path_count": 1,
                "planned_inspection_count": 1,
                "inspected_count": 1,
                "inspection_failure_count": 0,
                "content_duplicate_count": 0,
                "final_candidate_count": 1,
                "pending_count": 0,
            },
        },
    )

    job, created = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )
    duplicate, duplicate_created = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate["attempt_id"] == job["attempt_id"]

    completed = process_gallery_job(
        store, session_id, job["job_id"], job["attempt_id"]
    )

    assert completed["status"] == "completed"
    assert not (stage_path / "handoff.json").exists()
    assert not (stage_path / "processing-claim.json").exists()
    review = store.read_optional_stage_document(
        session_id, "asset_matching", "review-context"
    )
    assert review["data"]["workflow_step"] == "image_selection"
    assert review["data"]["asset_candidates"][0]["asset_id"] == "A1"
    assert completed["progress"]["inspected_count"] == 1
    assert completed["progress"]["final_candidate_count"] == 1


def test_gallery_job_publishes_progressive_batch_before_final_result(
    tmp_path, monkeypatch
):
    store, session_id, stage_path, decisions, committed = (
        _local_gallery_session(tmp_path)
    )
    monkeypatch.setattr(gallery_module, "read_product_csv", lambda path: [])

    def progressive_builder(*args, **kwargs):
        partial = {
            "requirements": [{"product_id": "P1"}],
            "asset_candidates": [{"asset_id": "A1", "product_id": "P1"}],
            "scan_summary": {
                "discovered_images": 2,
                "discovered_path_count": 2,
                "planned_inspection_count": 2,
                "inspected_count": 1,
                "inspection_failure_count": 0,
                "content_duplicate_count": 0,
                "final_candidate_count": 1,
                "pending_count": 1,
                "per_product": [],
            },
            "gallery_complete": False,
        }
        kwargs["batch_callback"](partial)
        return {
            **partial,
            "asset_candidates": [
                {"asset_id": "A1", "product_id": "P1"},
                {"asset_id": "A2", "product_id": "P1"},
            ],
            "scan_summary": {
                **partial["scan_summary"],
                "inspected_count": 2,
                "final_candidate_count": 2,
                "pending_count": 0,
            },
            "gallery_complete": True,
        }

    monkeypatch.setattr(
        gallery_module,
        "build_confirmed_folder_gallery",
        progressive_builder,
    )
    job, _ = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )

    completed = process_gallery_job(
        store, session_id, job["job_id"], job["attempt_id"]
    )

    partial = store._read_json(
        stage_path / "partial-gallery.json", "partial-gallery"
    )
    assert partial["workflow_step"] == "gallery_preparing"
    assert partial["asset_candidates"][0]["asset_id"] == "A1"
    assert completed["progress"]["available_candidate_count"] == 2
    assert completed["progress"]["published_batch_count"] == 1


def test_stale_gallery_attempt_cannot_publish(tmp_path, monkeypatch):
    store, session_id, stage_path, decisions, committed = (
        _local_gallery_session(tmp_path)
    )
    job, _ = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )
    current = read_gallery_job(store, session_id)
    current["attempt_id"] = "replacement-attempt"
    store._write_json_atomic(stage_path / "gallery-job.json", current)

    with pytest.raises(InteractionConflict, match="GALLERY_IDENTITY_STALE"):
        process_gallery_job(
            store, session_id, job["job_id"], job["attempt_id"]
        )

    assert not (stage_path / "confirmed-gallery.json").exists()


def test_expired_gallery_job_becomes_retryable_and_keeps_old_attempt(
    tmp_path,
):
    store, session_id, stage_path, decisions, committed = (
        _local_gallery_session(tmp_path)
    )
    first, _ = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )
    first = gallery_module.claim_queued_gallery_job(store, session_id)
    first["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    store._write_json_atomic(stage_path / "gallery-job.json", first)

    expired = reconcile_gallery_job(store, session_id)
    retried, created = create_or_reuse_gallery_job(
        store, session_id, committed, decisions, retry=True
    )

    assert expired["status"] == "failed"
    assert expired["reason_code"] == "GALLERY_WORKER_HEARTBEAT_EXPIRED"
    assert created is True
    assert retried["job_id"] == first["job_id"]
    assert retried["attempt_id"] != first["attempt_id"]
    assert (
        stage_path
        / "gallery-attempts"
        / first["attempt_id"]
        / "error.json"
    ).is_file()


def test_resource_identity_mismatch_is_a_stable_gallery_failure(
    tmp_path, monkeypatch
):
    store, session_id, stage_path, decisions, committed = (
        _local_gallery_session(tmp_path)
    )
    job, _ = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )

    def mismatch(expected):
        raise gallery_module.LocalResourceIdentityMismatch(
            "sid_or_session_changed"
        )

    monkeypatch.setattr(
        gallery_module, "require_local_resource_identity", mismatch
    )
    with pytest.raises(
        gallery_module.LocalResourceIdentityMismatch
    ):
        process_gallery_job(
            store, session_id, job["job_id"], job["attempt_id"]
        )

    failed = read_gallery_job(store, session_id)
    assert failed["status"] == "failed"
    assert failed["reason_code"] == "LOCAL_RESOURCE_IDENTITY_MISMATCH"
    assert not (stage_path / "confirmed-gallery.json").exists()


def test_unreadable_adopted_folder_is_a_stable_gallery_failure(
    tmp_path, monkeypatch
):
    store, session_id, stage_path, decisions, committed = (
        _local_gallery_session(tmp_path)
    )
    job, _ = create_or_reuse_gallery_job(
        store, session_id, committed, decisions
    )
    monkeypatch.setattr(gallery_module, "read_product_csv", lambda path: [])

    def unreadable(*args, **kwargs):
        raise ValueError(
            f"confirmed folder is not readable: {decisions[0]['folder_path']}"
        )

    monkeypatch.setattr(
        gallery_module, "build_confirmed_folder_gallery", unreadable
    )
    with pytest.raises(ValueError, match="not readable"):
        process_gallery_job(
            store, session_id, job["job_id"], job["attempt_id"]
        )

    failed = read_gallery_job(store, session_id)
    assert failed["status"] == "failed"
    assert failed["reason_code"] == "CONFIRMED_FOLDER_UNREADABLE"
    assert not (stage_path / "confirmed-gallery.json").exists()


def test_unclaimed_legacy_folder_handoff_migrates_to_local_job(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    folder = tmp_path / "legacy-folder"
    folder.mkdir()
    handoff = store.save_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(tmp_path)],
            "source_types": ["image"],
            "folder_decisions": [
                {
                    "folder_id": "F1",
                    "folder_path": str(folder),
                    "product_id": "P1",
                    "source_system": "nas",
                    "decision": "confirmed",
                }
            ],
            "asset_decisions": [],
            "license_decisions": [],
        },
    )

    job, migrated = migrate_legacy_gallery_handoff(
        store, session.session_id
    )

    stage_path = store._stage_path(
        session.session_id, "asset_matching"
    )
    assert migrated is True
    assert job["status"] == "queued"
    assert not (stage_path / "handoff.json").exists()
    assert (
        stage_path
        / "legacy-handoffs"
        / f"folder-r{handoff['revision']:04d}.json"
    ).is_file()
    state = store.load_session(session.session_id)
    assert state["stages"]["asset_matching"]["status"] == "draft"


def test_live_legacy_folder_claim_is_not_migrated(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    folder = tmp_path / "live-folder"
    folder.mkdir()
    store.save_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(tmp_path)],
            "folder_decisions": [
                {
                    "folder_id": "F1",
                    "folder_path": str(folder),
                    "product_id": "P1",
                    "decision": "confirmed",
                }
            ],
        },
    )
    store.wait_for_handoff(
        session.session_id,
        "asset_matching",
        timeout_seconds=0.1,
    )

    job, migrated = migrate_legacy_gallery_handoff(
        store, session.session_id
    )

    assert migrated is False
    assert job is None
    assert store.processing_claim(
        session.session_id, "asset_matching"
    )["expired"] is False


def test_expired_legacy_folder_claim_migrates_to_local_retry(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    folder = tmp_path / "expired-folder"
    folder.mkdir()
    store.save_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(tmp_path)],
            "folder_decisions": [
                {
                    "folder_id": "F1",
                    "folder_path": str(folder),
                    "product_id": "P1",
                    "decision": "confirmed",
                }
            ],
        },
    )
    store.wait_for_handoff(
        session.session_id,
        "asset_matching",
        timeout_seconds=0.1,
    )
    state = store.load_session(session.session_id)
    state["processing_claim"]["lease_expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    store._write_session_state(session.session_id, state)

    job, migrated = migrate_legacy_gallery_handoff(
        store, session.session_id
    )

    assert migrated is True
    assert job["status"] == "queued"
    assert store.processing_claim(
        session.session_id, "asset_matching"
    ) is None
