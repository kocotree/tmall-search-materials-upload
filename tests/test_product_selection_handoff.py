from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from upload_search_materials.cli import build_parser, main
from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.product_selection_handoff import (
    ProductSelectionProcessingError,
    _ensure_team_index_mount,
    _recovery_action,
    process_product_selection_handoff,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig
from upload_search_materials.team_folder_index import TeamFolderIndexError


def _write_shared_index(root: Path) -> None:
    snapshot_id = "snapshot-1"
    snapshot = (
        root
        / "team-cache"
        / "sources"
        / "source-a"
        / "snapshots"
        / snapshot_id
    )
    snapshot.mkdir(parents=True, exist_ok=True)
    folders_path = snapshot / "folders.csv"
    with folders_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "folder_id",
                "source_id",
                "relative_path",
                "folder_name",
                "parent_relative_path",
            ],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "folder_id": "folder-1",
                    "source_id": "source-a",
                    "relative_path": "materials/1",
                    "folder_name": "1",
                    "parent_relative_path": "materials",
                },
                {
                    "folder_id": "folder-unselected",
                    "source_id": "source-a",
                    "relative_path": "materials/2",
                    "folder_name": "2",
                    "parent_relative_path": "materials",
                },
            ]
        )
    folders_sha256 = hashlib.sha256(folders_path.read_bytes()).hexdigest()
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "complete": True,
                "snapshot_id": snapshot_id,
                "source_id": "source-a",
                "canonical_source": r"\\nas\source-a",
                "folder_count": 2,
                "files": {"folders.csv": {"sha256": folders_sha256}},
            }
        ),
        encoding="utf-8",
    )
    current = snapshot.parent.parent / "current.json"
    current.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_id": "source-a",
                "snapshot_id": snapshot_id,
            }
        ),
        encoding="utf-8",
    )


def _write_products(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "商品ID",
        "商品名称（查找引用）",
        "货号（查找引用）",
        "产品等级",
        "链接",
        "运营",
        "组别",
        "品类-公司维度划分",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "商品ID": "1",
                    "商品名称（查找引用）": "Product One",
                    "货号（查找引用）": "SKU-1",
                    "产品等级": "A",
                    "链接": "https://example.test/1",
                    "运营": "test",
                    "组别": "test",
                    "品类-公司维度划分": "test",
                },
                {
                    "商品ID": "2",
                    "商品名称（查找引用）": "Product Two",
                    "货号（查找引用）": "SKU-2",
                    "产品等级": "A",
                    "链接": "https://example.test/2",
                    "运营": "test",
                    "组别": "test",
                    "品类-公司维度划分": "test",
                },
            ]
        )


def _write_split_title_products(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "商品ID",
        "商品名称（查找引用）",
        "货号（查找引用）",
        "产品等级",
        "链接",
        "运营",
        "组别",
        "品类-公司维度划分",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for product_id, title in (
            ("886506466908", "分龄成长软软镜/稳稳镜/酷酷镜"),
            ("887243232283", "分龄成长软软镜"),
        ):
            writer.writerow(
                {
                    "商品ID": product_id,
                    "商品名称（查找引用）": title,
                    "货号（查找引用）": "KQ25029",
                    "产品等级": "A",
                    "链接": f"https://example.test/{product_id}",
                    "运营": "test",
                    "组别": "test",
                    "品类-公司维度划分": "test",
                }
            )


def _write_split_title_cache(root: Path) -> None:
    snapshot_id = "snapshot-split-title"
    snapshot = (
        root
        / "team-cache"
        / "sources"
        / "source-a"
        / "snapshots"
        / snapshot_id
    )
    snapshot.mkdir(parents=True, exist_ok=True)
    folders_path = snapshot / "folders.csv"
    folder_names = (
        "分龄成长软软镜",
        "分龄成长稳稳镜",
        "分龄成长酷酷镜",
    )
    with folders_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "folder_id",
                "source_id",
                "relative_path",
                "folder_name",
                "parent_relative_path",
            ],
        )
        writer.writeheader()
        for index, folder_name in enumerate(folder_names, start=1):
            writer.writerow(
                {
                    "folder_id": f"split-folder-{index}",
                    "source_id": "source-a",
                    "relative_path": f"materials/{folder_name}",
                    "folder_name": folder_name,
                    "parent_relative_path": "materials",
                }
            )
    folders_sha256 = hashlib.sha256(folders_path.read_bytes()).hexdigest()
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "complete": True,
                "snapshot_id": snapshot_id,
                "source_id": "source-a",
                "canonical_source": r"\\nas\source-a",
                "folder_count": len(folder_names),
                "files": {"folders.csv": {"sha256": folders_sha256}},
            }
        ),
        encoding="utf-8",
    )
    (snapshot.parent.parent / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_id": "source-a",
                "snapshot_id": snapshot_id,
            }
        ),
        encoding="utf-8",
    )


def _runtime(store: SessionStore, index_root: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace_root=index_root.parent,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(
            {
                "source_id": "source-a",
                "label": "Source A",
                "path": str(index_root.parent / "media"),
                "canonical_unc": r"\\nas\source-a",
            },
        ),
        runs_root=store.runs_root,
        folder_index_root=index_root,
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
    _write_products(store._session_path(session.session_id) / "inputs" / "products.csv")
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
        runtime=_runtime(store, index_root),
    )

    assert result["status"] == "completed"
    assert result["idempotent"] is False
    assert result["session_id"] == session_id
    assert result["completeness_revision"] == handoff["revision"]
    assert result["requested_products"] == 1
    assert result["matched_products"] == 1
    assert result["candidate_rows"] == 1
    assert result["unmatched_products"] == 0
    assert result["next_stage"] == "asset_matching"
    assert result["folder_review"] == str(
        store._stage_path(session_id, "asset_matching")
        / "folder-review.json"
    )
    assert result["matcher_version"] == 2
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
        "task_candidates_sha256": context["data"][
            "product_selection_identity"
        ]["task_candidates_sha256"],
        "product_snapshot_sha256": context["data"][
            "folder_snapshot"
        ]["product_snapshot_sha256"],
        "matcher_version": context["data"]["folder_snapshot"][
            "matcher_version"
        ],
        "source_snapshots": context["data"]["folder_snapshot"]["sources"],
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
        runtime=_runtime(store, index_root),
    )
    assert repeated["status"] == "completed"
    assert repeated["idempotent"] is True


def test_reselected_product_clears_late_removed_product_draft(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    setup = store.save_input(
        session.session_id, "setup", {"store": "shop"}
    )
    store.write_result(
        session.session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup complete",
    )
    store.save_input(
        session.session_id,
        "completeness",
        {"selected_product_ids": ["1", "2"]},
    )
    _write_products(
        store._session_path(session.session_id)
        / "inputs"
        / "products.csv"
    )
    index_root = tmp_path / "shared-folder-index"
    _write_shared_index(index_root)
    runtime = _runtime(store, index_root)

    process_product_selection_handoff(
        store,
        session.session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
        runtime=runtime,
    )
    first_asset_revision = int(
        store.load_session(session.session_id)["stages"]["asset_matching"][
            "revision"
        ]
    )
    removed_values = {
        "image_roots": [str(index_root.parent / "media")],
        "source_types": ["image"],
        "removed_product_ids": ["1"],
        "folder_decisions": [
            {
                "folder_id": "folder-1",
                "product_id": "1",
                "decision": "rejected",
                "note": "本次任务已去掉该商品",
            }
        ],
        "asset_decisions": [],
        "license_decisions": [],
    }
    removed = store.save_local_input(
        session.session_id,
        "asset_matching",
        removed_values,
        expected_revision=first_asset_revision,
    )

    store.reopen_previous_stage(
        session.session_id,
        "asset_matching",
        expected_revision=removed["revision"],
    )
    store.save_input(
        session.session_id,
        "completeness",
        {"selected_product_ids": ["1", "2"]},
    )

    # Model a delayed autosave from the old asset-matching page. Before the
    # fresh review context is published this request still has the old stage
    # revision and can recreate the removed-product input.
    late_draft = store.save_local_input(
        session.session_id,
        "asset_matching",
        removed_values,
        expected_revision=removed["revision"],
    )
    state = store.load_session(session.session_id)
    state["current_stage"] = "completeness"
    store._write_session_state(session.session_id, state)
    assert store.read_optional_stage_document(
        session.session_id, "asset_matching", "input"
    )["values"]["removed_product_ids"] == ["1"]

    process_product_selection_handoff(
        store,
        session.session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
        runtime=runtime,
    )

    state = store.load_session(session.session_id)
    context = store.read_optional_stage_document(
        session.session_id, "asset_matching", "review-context"
    )
    assert state["current_stage"] == "asset_matching"
    assert state["stages"]["asset_matching"]["revision"] > int(
        late_draft["revision"]
    )
    assert store.read_optional_stage_document(
        session.session_id, "asset_matching", "input"
    ) is None
    assert context["revision"] == state["stages"]["asset_matching"][
        "revision"
    ]
    assert context["data"]["product_selection_identity"][
        "selected_product_ids"
    ] == ["1", "2"]
    assert {
        item["product_id"]
        for item in context["data"]["folder_candidates"]
    } == {"1", "2"}
    with pytest.raises(InteractionConflict, match="expected revision is stale"):
        store.save_local_input(
            session.session_id,
            "asset_matching",
            removed_values,
            expected_revision=int(late_draft["revision"]),
        )


def test_product_selection_ignores_stale_global_candidates_and_rematches_split_title(
    tmp_path,
):
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
    store.save_input(
        session.session_id,
        "completeness",
        {"selected_product_ids": ["886506466908"]},
    )
    products_path = (
        store._session_path(session.session_id) / "inputs" / "products.csv"
    )
    _write_split_title_products(products_path)
    index_root = tmp_path / "folder-index"
    _write_split_title_cache(index_root)

    stale_global = index_root / "folder-candidates.csv"
    with stale_global.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "folder_id": "old-soft",
                "source_system": "source-a",
                "absolute_path": "X:/old/soft",
                "relative_path": "old/soft",
                "folder_name": "分龄成长软软镜",
                "product_id": "887243232283",
                "sku": "KQ25029",
                "product_title": "分龄成长软软镜",
                "match_type": "name_candidate",
                "match_status": "needs_manual_confirmation",
            }
        )

    result = process_product_selection_handoff(
        store,
        session.session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
        runtime=_runtime(store, index_root),
    )

    assert result["candidate_rows"] == 3
    task_candidates = (
        store._stage_path(session.session_id, "asset_matching")
        / "folder-candidates.csv"
    )
    with task_candidates.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["product_id"] for row in rows} == {"886506466908"}
    assert {row["folder_name"] for row in rows} == {
        "分龄成长软软镜",
        "分龄成长稳稳镜",
        "分龄成长酷酷镜",
    }
    assert {row["match_type"] for row in rows} == {
        "split_name_candidate",
        "short_split_name_candidate",
    }


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
                "canonical_unc": r"\\nas\source-a",
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
    assert ensured == []
    assert calls[0]["shared_root"] == team_root
    assert calls[0]["local_root"] == index_root
    assert calls[0]["source_ids"] == ("source-a",)
    assert "products_path" not in calls[0]
    assert products_snapshot.is_file()


def test_product_selection_bootstraps_missing_snapshots_before_sync(
    tmp_path, monkeypatch
):
    store, session_id, _handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "local-folder-index"
    team_root = tmp_path / "team-folder-index"
    team_root.mkdir()
    events = []

    def fake_ensure(**kwargs):
        events.append(("ensure", kwargs))
        return {"created": [{"source_id": "source-a"}]}

    sync_attempts = 0

    def fake_sync(**kwargs):
        nonlocal sync_attempts
        sync_attempts += 1
        events.append(("sync", kwargs))
        if sync_attempts == 1:
            raise TeamFolderIndexError(
                "TEAM_INDEX_NO_VALID_SNAPSHOT: source-a"
            )
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
        image_sources=(
            {
                "source_id": "source-a",
                "path": str(tmp_path),
                "canonical_unc": r"\\nas\source-a",
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
    assert [event[0] for event in events] == ["sync", "ensure", "sync"]
    assert events[2][1]["source_ids"] == ("source-a",)


def test_product_selection_uses_local_cache_when_shared_smb_login_fails(
    tmp_path, monkeypatch
):
    store, session_id, _handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "local-folder-index"
    _write_shared_index(index_root)
    team_root = tmp_path / "unavailable-team-folder-index"
    original_is_dir = Path.is_dir

    def smb_aware_is_dir(path):
        if path == team_root:
            error = OSError("logon failure")
            error.winerror = 1326
            raise error
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", smb_aware_is_dir)
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(
            {
                "source_id": "source-a",
                "label": "Source A",
                "path": str(tmp_path / "media"),
                "canonical_unc": r"\\nas\source-a",
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
    sync_summary = json.loads(
        (index_root / "team-sync.json").read_text(encoding="utf-8")
    )
    assert sync_summary["shared_available"] is False
    assert sync_summary["sources"][0]["origin"] == "local_cache"


def test_failure_writes_codex_diagnostic_and_same_entry_can_resume(tmp_path):
    store, session_id, handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "missing-folder-index"

    with pytest.raises(ProductSelectionProcessingError) as raised:
        process_product_selection_handoff(
            store,
            session_id,
            folder_index_root=index_root,
            claimant_id="test-codex",
            runtime=_runtime(store, index_root),
        )

    assert raised.value.reason_code == "TEAM_INDEX_NO_VALID_LOCAL_CACHE"
    diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert diagnostic["phase"] == "match_task_candidates"
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
    assert failed["blocking_reasons"] == ["TEAM_INDEX_NO_VALID_LOCAL_CACHE"]
    assert store.load_session(session_id)["processing_claim"] is None

    _write_shared_index(index_root)
    resumed = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
        runtime=_runtime(store, index_root),
    )
    assert resumed["status"] == "completed"
    assert store.load_session(session_id)["current_stage"] == "asset_matching"
    resolved_diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert resolved_diagnostic["status"] == "resolved"
    assert resolved_diagnostic["resolved_at"]


def test_team_index_recovery_does_not_request_first_snapshot_authorization():
    action = _recovery_action("TEAM_INDEX_NO_VALID_SNAPSHOT")

    assert "无需再次口头授权" in action
    assert "只有已有快照的增量更新" in action


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
            runtime=_runtime(store, index_root),
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
        runtime=_runtime(store, index_root),
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
    assert "workflow_dispatch is online" in instruction
    assert "Codex must not call legacy" in instruction


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
