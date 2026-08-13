from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from upload_search_materials.cli import build_parser, main
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.product_selection_handoff import (
    ProductSelectionProcessingError,
    _ensure_team_index_mount,
    process_product_selection_handoff,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


FIELDS = [
    "folder_id",
    "source_system",
    "absolute_path",
    "relative_path",
    "folder_name",
    "product_id",
    "sku",
    "product_title",
    "match_type",
    "match_status",
]


def _write_shared_index(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "folder-candidates.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "folder_id": "folder-1",
                    "source_system": "source-a",
                    "absolute_path": "X:/materials/product-one",
                    "relative_path": "materials/product-one",
                    "folder_name": "product-one",
                    "product_id": "1",
                    "sku": "SKU-1",
                    "product_title": "Product One",
                    "match_type": "exact_product_id",
                    "match_status": "matched",
                },
                {
                    "folder_id": "folder-unselected",
                    "source_system": "source-a",
                    "absolute_path": "X:/materials/product-two",
                    "relative_path": "materials/product-two",
                    "folder_name": "product-two",
                    "product_id": "2",
                    "sku": "SKU-2",
                    "product_title": "Product Two",
                    "match_type": "exact_product_id",
                    "match_status": "matched",
                },
            ]
        )
    (root / "folder-scan-summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "complete": True,
                "candidate_rows": 2,
            }
        ),
        encoding="utf-8",
    )


def _submitted_selection(tmp_path: Path) -> tuple[SessionStore, str, dict]:
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    setup = store.save_input(session.session_id, "setup", {"store": "shop"})
    store.write_result(
        session.session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup complete",
    )
    handoff = store.save_input(
        session.session_id,
        "completeness",
        {"selected_product_ids": ["1"]},
    )
    return store, session.session_id, handoff


def test_one_processor_prepares_folder_review_and_advances_stage(tmp_path):
    store, session_id, handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "shared-folder-index"
    _write_shared_index(index_root)

    result = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
    )

    assert result == {
        "status": "completed",
        "idempotent": False,
        "session_id": session_id,
        "completeness_revision": handoff["revision"],
        "requested_products": 1,
        "matched_products": 1,
        "candidate_rows": 1,
        "unmatched_products": 0,
        "next_stage": "asset_matching",
        "folder_review": str(
            store._stage_path(session_id, "asset_matching")
            / "folder-review.json"
        ),
    }
    state = store.load_session(session_id)
    assert state["current_stage"] == "asset_matching"
    assert state["stages"]["completeness"]["status"] == "completed"
    assert state["stages"]["asset_matching"]["status"] == "needs_user_input"
    context = store.read_optional_stage_document(
        session_id, "asset_matching", "review-context"
    )
    assert context["data"]["workflow_step"] == "folder_review"
    assert [
        item["folder_id"] for item in context["data"]["folder_candidates"]
    ] == ["folder-1"]
    assert context["data"]["product_selection_identity"] == {
        "revision": handoff["revision"],
        "input_sha256": handoff["input_sha256"],
        "selected_product_ids": ["1"],
        "shared_candidates_sha256": context["data"][
            "product_selection_identity"
        ]["shared_candidates_sha256"],
    }
    assert not (
        store._stage_path(session_id, "asset_matching")
        / "folder-index.sqlite3"
    ).exists()

    repeated = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
    )
    assert repeated["status"] == "completed"
    assert repeated["idempotent"] is True


def test_team_index_mount_is_prepared_and_fixed_subdirectory_is_created(
    tmp_path, monkeypatch
):
    mount_root = tmp_path / "mounted-share"
    mount_root.mkdir()
    shared_root = mount_root / "天猫部" / "搜推素材索引-虾米"
    source = object()
    monkeypatch.setattr(
        "upload_search_materials.product_selection_handoff.load_nas_sources",
        lambda _path: {"team-index": source},
    )
    prepared = []

    def fake_prepare(value, *, allow_mount):
        prepared.append((value, allow_mount))
        return SimpleNamespace(
            state="ready",
            reason_code="PATH_AVAILABLE",
            mount_path=str(mount_root),
        )

    monkeypatch.setattr(
        "upload_search_materials.product_selection_handoff.prepare_nas_source",
        fake_prepare,
    )
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
        nas_sources_file=tmp_path / "nas-sources.yaml",
        team_folder_index_root=shared_root,
        team_folder_index_nas_source_id="team-index",
    )

    _ensure_team_index_mount(runtime)

    assert prepared == [(source, True)]
    assert shared_root.is_dir()


def test_product_selection_syncs_configured_team_index_before_snapshot(
    tmp_path, monkeypatch
):
    store, session_id, _handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "local-folder-index"
    team_root = tmp_path / "team-folder-index"
    team_root.mkdir()
    products_snapshot = store._session_path(session_id) / "inputs" / "products.csv"
    products_snapshot.parent.mkdir(parents=True)
    products_snapshot.write_text("products", encoding="utf-8")
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        _write_shared_index(kwargs["local_root"])
        return {"complete": True}

    monkeypatch.setattr(
        "upload_search_materials.product_selection_handoff.sync_snapshots",
        fake_sync,
    )
    ensured = []
    monkeypatch.setattr(
        "upload_search_materials.product_selection_handoff.ensure_missing_snapshots",
        lambda **kwargs: ensured.append(kwargs),
    )
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(
            {
                "source_id": "source-a",
                "label": "Source A",
                "path": str(tmp_path / "media"),
            },
        ),
        runs_root=store.runs_root,
        folder_index_root=index_root,
        team_folder_index_root=team_root,
    )

    result = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
        runtime=runtime,
    )

    assert result["status"] == "completed"
    assert len(calls) == 1
    assert len(ensured) == 1
    assert calls[0]["shared_root"] == team_root
    assert calls[0]["local_root"] == index_root
    assert calls[0]["products_path"] == products_snapshot


def test_product_selection_bootstraps_missing_snapshots_before_sync(
    tmp_path, monkeypatch
):
    store, session_id, _handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "local-folder-index"
    team_root = tmp_path / "team-folder-index"
    team_root.mkdir()
    products_snapshot = store._session_path(session_id) / "inputs" / "products.csv"
    products_snapshot.parent.mkdir(parents=True)
    products_snapshot.write_text("products", encoding="utf-8")
    events = []

    def fake_ensure(**kwargs):
        events.append(("ensure", kwargs))
        return {"created": [{"source_id": "source-a"}]}

    def fake_sync(**kwargs):
        events.append(("sync", kwargs))
        _write_shared_index(kwargs["local_root"])
        return {"complete": True}

    monkeypatch.setattr(
        "upload_search_materials.product_selection_handoff.ensure_missing_snapshots",
        fake_ensure,
    )
    monkeypatch.setattr(
        "upload_search_materials.product_selection_handoff.sync_snapshots",
        fake_sync,
    )
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=({"source_id": "source-a", "path": str(tmp_path)},),
        runs_root=store.runs_root,
        folder_index_root=index_root,
        team_folder_index_root=team_root,
    )

    result = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
        runtime=runtime,
    )

    assert result["status"] == "completed"
    assert [event[0] for event in events] == ["ensure", "sync"]


def test_failure_writes_codex_diagnostic_and_same_entry_can_resume(tmp_path):
    store, session_id, handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "missing-folder-index"

    with pytest.raises(ProductSelectionProcessingError) as raised:
        process_product_selection_handoff(
            store,
            session_id,
            folder_index_root=index_root,
            claimant_id="test-codex",
        )

    assert raised.value.reason_code == "FOLDER_INDEX_NOT_READY"
    diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert diagnostic["phase"] == "snapshot_candidates"
    assert diagnostic["revision"] == handoff["revision"]
    assert diagnostic["input_sha256"] == handoff["input_sha256"]
    assert diagnostic["codex_recovery"]["retry_same_handoff"] is True
    assert diagnostic["codex_recovery"]["command"][1] == (
        "process-product-selection"
    )
    failed = store.read_optional_stage_document(
        session_id, "completeness", "result"
    )
    assert failed["status"] == "blocked"
    assert failed["blocking_reasons"] == ["FOLDER_INDEX_NOT_READY"]
    assert store.load_session(session_id)["processing_claim"] is None

    _write_shared_index(index_root)
    resumed = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
    )
    assert resumed["status"] == "completed"
    assert store.load_session(session_id)["current_stage"] == "asset_matching"
    resolved_diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert resolved_diagnostic["status"] == "resolved"
    assert resolved_diagnostic["resolved_at"]


def test_product_selection_reports_an_index_that_is_still_building(tmp_path):
    store, session_id, _handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "building-folder-index"
    index_root.mkdir()
    (index_root / "folder-index-progress.json").write_text(
        json.dumps({"status": "running", "folders_discovered": 4321}),
        encoding="utf-8",
    )

    with pytest.raises(ProductSelectionProcessingError) as raised:
        process_product_selection_handoff(
            store,
            session_id,
            folder_index_root=index_root,
            claimant_id="test-codex",
        )

    assert raised.value.reason_code == "FOLDER_INDEX_BUILDING"
    diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert diagnostic["artifacts"]["shared_progress"]["exists"] is True
    assert "等待共享文件夹索引完成" in diagnostic["codex_recovery"]["action"]


def test_cli_exposes_single_product_selection_entry():
    args = build_parser().parse_args(
        [
            "process-product-selection",
            "--runs-root",
            "runs",
            "--session",
            "session-1",
        ]
    )
    assert args.command == "process-product-selection"
    assert args.claimant_id == "codex-agent"


def test_resume_session_does_not_claim_completeness_before_processor(
    tmp_path, capsys
):
    store, session_id, handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "shared-folder-index"
    _write_shared_index(index_root)

    code = main(
        [
            "resume-session",
            "--runs-root",
            str(store.runs_root),
            "--session",
            session_id,
            "--ack",
            "已提交",
        ]
    )
    resumed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert resumed["status"] == "ready"
    assert resumed["claim_deferred"] is True
    assert resumed["processor"] == "process-product-selection"
    assert resumed["next_command"][1] == "process-product-selection"
    state = store.load_session(session_id)
    assert state["stages"]["completeness"]["status"] == "ready_for_agent"
    assert state["processing_claim"] is None

    result = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="codex-agent",
    )

    assert result["status"] == "completed"
    assert result["completeness_revision"] == handoff["revision"]
    assert store.load_session(session_id)["current_stage"] == "asset_matching"


def test_completeness_recovery_instruction_names_only_specialized_entry(
    tmp_path,
):
    store, session_id, _handoff = _submitted_selection(tmp_path)

    instruction = store.recovery_instruction(session_id, "completeness")

    assert "process-product-selection" in instruction
    assert "Do not run wait-handoff or resume-session first" in instruction


def test_wait_handoff_refuses_to_claim_completeness(tmp_path, capsys):
    store, session_id, _handoff = _submitted_selection(tmp_path)

    code = main(
        [
            "wait-handoff",
            "--runs-root",
            str(store.runs_root),
            "--session",
            session_id,
            "--stage",
            "completeness",
            "--timeout",
            "0.1",
        ]
    )
    payload = json.loads(capsys.readouterr().err)

    assert code == 2
    assert payload["reason_code"] == "SPECIALIZED_PROCESSOR_REQUIRED"
    assert payload["next_command"][1] == "process-product-selection"
    state = store.load_session(session_id)
    assert state["stages"]["completeness"]["status"] == "ready_for_agent"
    assert state["processing_claim"] is None
