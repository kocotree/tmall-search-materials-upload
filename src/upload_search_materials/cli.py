from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import socket
import stat
import subprocess
import sys
import time
from typing import Sequence
from urllib.parse import urlencode

from .agent_handoff import (
    cancel_agent_request,
    claim_agent_request,
    complete_agent_request,
    list_agent_requests,
    wait_for_agent_request,
)
from .approval import create_manifest, render_review_html, verify_manifest
from .asset_index import (
    bind_confirmed_folder_matches,
    IncrementalAssetIndexer,
    IndexOptions,
    NamedRoot,
    ScanOutcome,
    write_match_candidates,
    write_scan_summary,
)
from .asset_index_store import AssetIndexStore, IndexIdentity, IndexIdentityError
from .asset_matching import ProductPathMatcher
from .asset_selection import build_gallery_data
from .asset_matching_workflow import (
    CONFIRMED_FOLDER_EMPTY,
    CONFIRMED_FOLDER_UNREADABLE,
    FOLDER_REVIEW,
    GALLERY_PREPARATION_FAILED,
    IMAGE_SELECTION,
    build_gallery_identity,
)
from .assets import (
    DirectoryAssetSource,
    ManifestAssetSource,
    inspect_asset,
    load_media_policy,
    validate_asset_group,
)
from .browser.config import (
    SelectorConfigError,
    load_selector_profile,
    load_selectors,
)
from .browser.export_page import export_reports
from .browser.material_page import (
    SelectorInvalidError,
)
from .browser.session import (
    assert_store_identity,
    detect_human_check,
    open_cdp_page,
)
from .browser.upload_page import upload_approved_item
from .browser.verifier import verify_remote_item
from .copywriting import generate_and_validate_copy
from .copy_draft_workflow import (
    CopyDraftProcessingError,
    process_copy_draft_request,
)
from .confirmed_assets import (
    build_confirmed_folder_gallery,
    extract_folder_decisions,
)
from .eligibility import collect_titles_by_product, evaluate_all, load_monthly_rules
from .folder_index import (
    absolute_path_without_io,
    build_folder_review_data,
    build_folder_index,
    rematch_folder_index,
    snapshot_folder_candidates,
    write_folder_candidates,
)
from .nas_sources import (
    browse_nas_folders,
    check_nas_source,
    load_nas_sources,
    prepare_nas_source,
    statuses_json,
)
from .platform_support import AssetSourceUnavailable
from .io_tables import (
    SchemaError,
    read_basic_materials_xlsx,
    read_product_csv,
    read_search_materials_xlsx,
    sha256_file,
    validate_product_records,
)
from .interaction.session import (
    InteractionConflict,
    InteractionPathError,
    SessionStore,
)
from .interaction.service import (
    dispatch_collection_start,
    ManagedServiceError,
    restart_service,
    start_service,
    status_service,
    stop_service,
)
from .interaction.fallback import write_chat_fallback
from .interaction.stages import STAGES
from .interaction.web import create_app
from .image_review import (
    migrate_legacy_image_review_session,
    migrate_legacy_suitability_to_selected_preflight,
    prepare_image_review_session,
)
from .material_state import (
    build_completeness_matrix,
    merge_material_state,
    products_requiring_supplement,
)
from .models import MaterialStatus, make_run_id
from .reporting import (
    material_item_from_dict,
    read_json,
    write_eligibility_csv,
    write_json,
    write_summary,
    write_supplement_candidates,
)
from .runtime_config import load_runtime_config
from .team_folder_index import (
    TeamFolderIndexError,
    publish_snapshot,
    snapshot_status,
    sync_snapshots,
)
from .setup_collection import process_setup_collection
from .collection_worker import (
    collection_status,
    environment_fingerprint,
    launch_collection_worker,
    run_collection_worker,
)
from .state_store import StateStore
from .slot_planning import prepare_slot_board_session
from .supplement_collection import collect_supplement_material_status
from .runtime_identity import current_runtime_identity
from .desktop_launcher import (
    DesktopLauncherError,
    ensure_login_browser,
    launch_desktop_workbench,
)
from .dry_run_workflow import prepare_publish_run_from_authorization
from .agent_diagnostics import read_agent_diagnostic, write_exception_diagnostic
from .final_material_handoff import process_final_material_handoff
from .product_selection_handoff import (
    ProductSelectionProcessingError,
    process_product_selection_handoff,
)
from .gallery_jobs import process_gallery_job, run_material_executor
from .material_executor_launcher import launch_material_executor
from .tasks import build_material_items, build_product_tasks
from .time_utils import iso_timestamp


def _now_iso() -> str:
    return iso_timestamp()


class BrowserSessionRequired(RuntimeError):
    pass


@contextmanager
def page_context(page, cdp_url: str | None, page_factory=None):
    if page is not None:
        yield page
        return
    if not cdp_url:
        raise BrowserSessionRequired("没有注入页面，也没有提供 --cdp-url")
    factory = page_factory or open_cdp_page
    with factory(cdp_url) as connected_page:
        yield connected_page


def _read_backend_status(path: str | None) -> list[dict[str, str]]:
    if not path:
        return []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class _StaticCopyProvider:
    def __init__(self, response: dict[str, str]):
        self.response = response

    def generate(self, request):
        return self.response


def _build_resolved_material_items(args, run_id, products, basic, snapshots):
    if not (args.asset_root or args.asset_manifest) or not args.copy_responses:
        return []
    responses = read_json(Path(args.copy_responses))
    media_policy = load_media_policy(Path(args.media_policy)) if args.media_policy else None
    source = (
        ManifestAssetSource(Path(args.asset_manifest))
        if args.asset_manifest
        else DirectoryAssetSource(Path(args.asset_root))
    )
    products_by_id = {product.product_id: product for product in products if product.product_id}
    basic_by_id = {row.get("商品ID", ""): row for row in basic}
    all_items = []
    for snapshot in snapshots:
        product = products_by_id.get(snapshot.product_id)
        if product is None or snapshot.empty_slot_indexes is None:
            continue
        collected = source.collect(product.product_id, product.sku)
        if args.asset_manifest:
            assets = collected
        else:
            assets = [
                inspect_asset(
                    path,
                    product.product_id,
                    args.license_status,
                    media_policy=media_policy,
                    sku=product.sku,
                )
                for path in collected
            ]
        image_assets = [asset for asset in assets if asset.asset_type == "image"]
        video_assets = [asset for asset in assets if asset.asset_type == "video"]
        target_slots = sorted(snapshot.empty_slot_indexes)
        asset_groups = []
        video_limit = min(1 if snapshot.desired_slots == 3 else 3, len(video_assets), len(target_slots))
        for asset in video_assets[:video_limit]:
            asset_groups.append(("video", [asset]))
        image_offset = 0
        while len(asset_groups) < len(target_slots) and image_offset + 3 <= len(image_assets):
            group = image_assets[image_offset : image_offset + 3]
            image_offset += 3
            validation = validate_asset_group(group, material_type="image_text")
            if validation.status != "valid":
                for asset in group:
                    asset.validation_status = "blocked"
                    for reason in validation.reason_codes:
                        if reason not in asset.reason_codes:
                            asset.reason_codes.append(reason)
            asset_groups.append(("image_text", group))
        usable_slots = target_slots[: len(asset_groups)]
        copy_results = []
        complete_groups = []
        complete_slots = []
        basic_row = basic_by_id.get(product.product_id, {})
        source_fields = {
            "品牌": "KK树",
            "商品标题": basic_row.get("商品标题", product.title),
            "品类": product.category,
            "卖点1": basic_row.get("卖点1", ""),
            "卖点2": basic_row.get("卖点2", ""),
            "商家短标题": basic_row.get("商家短标题", ""),
            "利益点（短）": basic_row.get("利益点（短）", ""),
        }
        for slot_index, group in zip(usable_slots, asset_groups, strict=True):
            response = responses.get(f"{product.product_id}:{slot_index}")
            if not isinstance(response, dict):
                continue
            copy_results.append(
                generate_and_validate_copy(
                    source_fields,
                    _StaticCopyProvider(response),
                    args.prohibited_term,
                    generated_at=args.started_at or _now_iso(),
                )
            )
            complete_slots.append(slot_index)
            complete_groups.append(group)
        if complete_groups:
            all_items.extend(
                build_material_items(
                    run_id=run_id,
                    product_id=product.product_id,
                    target_slot_indexes=complete_slots,
                    asset_groups=complete_groups,
                    copy_results=copy_results,
                )
            )
    return all_items


def _run(args) -> int:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    started_at = args.started_at or _now_iso()
    run_id = make_run_id(started_at, args.store, args.month)
    paths = {
        "products": Path(args.products),
        "rules": Path(args.rules),
        "basic": Path(args.basic),
        "search": Path(args.search),
    }
    products = read_product_csv(paths["products"])
    basic = read_basic_materials_xlsx(paths["basic"])
    search = read_search_materials_xlsx(paths["search"])
    titles = collect_titles_by_product(basic, search)
    rules = load_monthly_rules(paths["rules"], args.month)
    decisions = evaluate_all(products, titles, rules)
    eligible_ids = {decision.product_id for decision in decisions if decision.status == "eligible"}
    snapshots = merge_material_state(
        [row for row in basic if row.get("商品ID") in eligible_ids],
        [row for row in search if row.get("商品ID") in eligible_ids],
        _read_backend_status(args.backend_status),
    )
    supplement_ids = products_requiring_supplement(snapshots)
    items = _build_resolved_material_items(
        args,
        run_id,
        products,
        basic,
        snapshots,
    )
    items_by_product = {}
    for item in items:
        items_by_product.setdefault(item.product_id, []).append(item)
    decisions_by_product = {
        decision.product_id: (decision.status, list(decision.reason_codes))
        for decision in decisions
    }
    desired_slots = {
        snapshot.product_id: snapshot.desired_slots
        for snapshot in snapshots
        if snapshot.desired_slots in (3, 9)
    }
    required_new_items = {
        snapshot.product_id: len(snapshot.empty_slot_indexes)
        for snapshot in snapshots
        if snapshot.empty_slot_indexes is not None
    }
    product_tasks = build_product_tasks(
        run_id=run_id,
        products=products,
        decisions_by_product=decisions_by_product,
        desired_slots_by_product=desired_slots,
        material_items_by_product=items_by_product,
        required_new_items_by_product=required_new_items,
    )
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    run_metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "store": args.store,
        "month": args.month,
        "mode": args.mode,
        "started_at": started_at,
        "source_files": {name: str(path.resolve()) for name, path in paths.items()},
        "source_sha256": source_hashes,
    }
    write_json(output / "run.json", run_metadata)
    write_eligibility_csv(output / "eligibility.csv", products, decisions)
    write_supplement_candidates(output / "supplement-candidates.csv", supplement_ids)
    write_json(output / "material-snapshots.json", snapshots)
    write_json(output / "product-tasks.json", product_tasks)
    write_json(output / "material-items.json", items)
    render_review_html(items, output / "review.html", store=args.store)
    state = StateStore(output / "run.sqlite3")
    try:
        state.save_run(
            run_id=run_id,
            store=args.store,
            month=args.month,
            mode=args.mode,
            status="dry_run_completed",
            config_hash=sha256_file(output / "run.json"),
        )
        for item in items:
            state.save_item(item.task_id, item.status.value)
    finally:
        state.close()
    write_summary(output)
    return 0


def _approve(args) -> int:
    run_dir = Path(args.run_dir)
    run = read_json(run_dir / "run.json")
    items = [
        material_item_from_dict(value)
        for value in read_json(run_dir / "material-items.json")
    ]
    product_tasks_path = run_dir / "product-tasks.json"
    if not product_tasks_path.is_file():
        print("批准失败：product-tasks.json 不存在", file=sys.stderr)
        return 1
    product_statuses = {
        str(task.get("product_id", "")): task.get("status", "")
        for task in read_json(product_tasks_path)
    }
    requested = set(args.task_id)
    selected = [
        item
        for item in items
        if item.task_id in requested
        and item.status == MaterialStatus.READY_FOR_REVIEW
        and product_statuses.get(item.product_id) == "ready_for_review"
    ]
    if {item.task_id for item in selected} != requested:
        print("批准失败：存在未知或未达到 ready_for_review 的 task ID", file=sys.stderr)
        return 1
    for item in selected:
        item.status = MaterialStatus.APPROVED
    manifest = create_manifest(
        run["store"],
        selected,
        args.confirmed_by,
        args.confirmed_at,
        valid_until=args.valid_until,
        run_id=run["run_id"],
        source_sha256=run.get("source_sha256", {}),
    )
    write_json(run_dir / "material-items.json", items)
    state = StateStore(run_dir / "run.sqlite3")
    try:
        for item in selected:
            if state.item_status(item.task_id) is None:
                state.save_item(item.task_id, "ready_for_review")
            _persist_item_transition(
                state,
                item.task_id,
                "approved",
                reason="APPROVED_BY_USER",
                evidence=f"manifest={manifest['manifest_sha256']}",
            )
    finally:
        state.close()
    write_json(run_dir / "approval-manifest.json", manifest)
    return 0


def partition_persisted_items(state: StateStore, items, *, resume: bool):
    recoverable_ids = set(state.recoverable_items(item.task_id for item in items))
    upload_items = [item for item in items if item.task_id in recoverable_ids]
    if not resume:
        return upload_items, []
    verification_ids = set(state.items_requiring_verification())
    verification_items = [item for item in items if item.task_id in verification_ids]
    return upload_items, verification_items


def _persist_item_transition(
    state: StateStore,
    task_id: str,
    new_status: str,
    *,
    reason: str,
    evidence: str,
    remote_material_id: str | None = None,
    attempt_count: int = 0,
) -> None:
    current = state.item_status(task_id)
    if current is None:
        state.save_item(
            task_id,
            new_status,
            remote_material_id=remote_material_id,
            evidence=evidence,
            attempt_count=attempt_count,
        )
        return
    if current != new_status:
        state.record_transition(
            task_id,
            current,
            new_status,
            reason=reason,
            evidence=evidence,
            remote_material_id=remote_material_id,
            attempt_count=attempt_count,
        )
        return
    state.update_item_evidence(
        task_id,
        evidence=evidence,
        remote_material_id=remote_material_id,
        attempt_count=attempt_count,
    )


def _run_sources_unchanged(run: dict) -> bool:
    files = run.get("source_files", {})
    expected = run.get("source_sha256", {})
    if set(files) != set(expected):
        return False
    try:
        return all(sha256_file(Path(files[name])) == expected[name] for name in files)
    except OSError:
        return False


def _publish(args, page, page_factory=None, *, resume: bool = False) -> int:
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "approval-manifest.json"
    if not manifest_path.is_file():
        print("发布被阻断：approval-manifest.json 不存在", file=sys.stderr)
        return 2
    if not Path(args.selectors).is_file():
        print("发布被阻断：选择器配置不存在", file=sys.stderr)
        return 2
    try:
        selector_profile = load_selector_profile(
            Path(args.selectors),
            # The PlaywrightAuto-derived uploader operates on the same
            # verified search-recommendation page as high-value collection.
            purpose="high_value_collection",
            production=page is None and page_factory is None,
        )
        selectors = selector_profile.selectors
    except SelectorConfigError as error:
        print(f"发布被阻断：{error}", file=sys.stderr)
        return 2
    material_center_url = (
        selector_profile.material_center_url
        or (
            "https://myseller.taobao.com/home.htm/"
            "material-center/material-management"
        )
    )
    run = read_json(run_dir / "run.json")
    manifest = read_json(manifest_path)
    manifest_entries = manifest.get("entries")
    if not isinstance(manifest_entries, list):
        print("发布被阻断：manifest entries 无效", file=sys.stderr)
        return 2
    entries = {entry.get("task_id") for entry in manifest_entries if isinstance(entry, dict)}
    items = [
        material_item_from_dict(value)
        for value in read_json(run_dir / "material-items.json")
        if value.get("task_id") in entries
    ]
    if not entries or {item.task_id for item in items} != entries:
        print("发布被阻断：manifest task 与批次任务不一致", file=sys.stderr)
        return 2
    for item in items:
        item.status = MaterialStatus.APPROVED
    if not _run_sources_unchanged(run):
        print("发布被阻断：批次输入文件缺失或哈希变化", file=sys.stderr)
        return 2
    approval = verify_manifest(
        manifest,
        items,
        expected_store=run.get("store", ""),
        now=_now_iso(),
        expected_run_id=run.get("run_id", ""),
        expected_source_sha256=run.get("source_sha256", {}),
        rehash_assets=True,
    )
    if not approval.valid or args.store.strip() != run.get("store", "").strip():
        reason = approval.reason or "STORE_IDENTITY_MISMATCH"
        print(f"发布被阻断：{reason}", file=sys.stderr)
        return 2
    state_path = run_dir / "run.sqlite3"
    if not state_path.is_file():
        print("发布被阻断：STATE_MISSING / run.sqlite3 不存在，必须先远端核验", file=sys.stderr)
        return 2
    try:
        preflight_state = StateStore(state_path)
        try:
            persisted = {
                item.task_id: preflight_state.item_record(item.task_id)
                for item in items
            }
        finally:
            preflight_state.close()
    except sqlite3.DatabaseError:
        print("发布被阻断：STATE_MISSING / run.sqlite3 损坏，必须先远端核验", file=sys.stderr)
        return 2
    if any(record is None for record in persisted.values()):
        print("发布被阻断：STATE_MISSING / 任务状态记录缺失，必须先远端核验", file=sys.stderr)
        return 2
    if not resume:
        unresolved = [
            task_id
            for task_id, record in persisted.items()
            if record["status"] not in {"approved", "ready_for_review"}
        ]
        if unresolved:
            print("发布被阻断：存在已尝试任务，请使用 resume", file=sys.stderr)
            return 2
    try:
        with page_context(page, args.cdp_url, page_factory) as resolved_page:
            outcomes = []
            state = StateStore(run_dir / "run.sqlite3")
            try:
                upload_items, verification_items = partition_persisted_items(
                    state,
                    items,
                    resume=resume,
                )
                batch_paused = False
                for item in verification_items:
                    existing = state.item_record(item.task_id)
                    outcome = verify_remote_item(
                        resolved_page,
                        item,
                        selectors,
                        expected_store=args.store,
                        submitted_at=existing["updated_at"],
                        workflow="qianniu_recommend",
                        material_center_url=material_center_url,
                    )
                    persisted_status = "failed" if outcome.status == "not_found" else outcome.status
                    if existing["status"] == "under_review" and persisted_status == "submitted":
                        persisted_status = "under_review"
                    _persist_item_transition(
                        state,
                        item.task_id,
                        persisted_status,
                        reason=outcome.reason or "REMOTE_VERIFIED",
                        remote_material_id=outcome.remote_material_id,
                        evidence=outcome.evidence or outcome.reason,
                        attempt_count=int(existing["attempt_count"]),
                    )
                    outcomes.append(
                        {
                            "task_id": item.task_id,
                            "status": outcome.status,
                            "reason": outcome.reason,
                            "remote_material_id": outcome.remote_material_id,
                            "evidence": outcome.evidence,
                        }
                    )
                    if outcome.status == "publish_uncertain":
                        batch_paused = True
                        break
                for item in ([] if batch_paused else upload_items):
                    existing = state.item_record(item.task_id)
                    if existing is None or existing["status"] != "approved":
                        if existing is None:
                            state.save_item(item.task_id, "ready_for_review")
                        _persist_item_transition(
                            state,
                            item.task_id,
                            "approved",
                            reason="MANIFEST_APPROVED",
                            evidence=f"manifest={manifest['manifest_sha256']}",
                        )
                    attempt_count = int(existing["attempt_count"]) if existing else 0

                    def persist_pre_publish_checkpoint(
                        task_id=item.task_id,
                        next_attempt=attempt_count + 1,
                    ):
                        _persist_item_transition(
                            state,
                            task_id,
                            "uploading",
                            reason="PRE_PUBLISH_CHECKPOINT",
                            evidence="PRE_PUBLISH_CHECKPOINT",
                            attempt_count=next_attempt,
                        )

                    outcome = upload_approved_item(
                        resolved_page,
                        item,
                        manifest,
                        selectors,
                        expected_store=args.store,
                        now=_now_iso(),
                        before_publish=persist_pre_publish_checkpoint,
                        workflow="qianniu_recommend",
                        material_center_url=material_center_url,
                    )
                    checkpoint = state.item_record(item.task_id)
                    _persist_item_transition(
                        state,
                        item.task_id,
                        outcome.status,
                        reason=outcome.reason or "PUBLISH_RESULT",
                        remote_material_id=outcome.remote_material_id,
                        evidence=outcome.evidence or outcome.reason,
                        attempt_count=int(checkpoint["attempt_count"]),
                    )
                    outcomes.append(
                        {
                            "task_id": item.task_id,
                            "status": outcome.status,
                            "reason": outcome.reason,
                            "remote_material_id": outcome.remote_material_id,
                            "evidence": outcome.evidence,
                        }
                    )
                    if outcome.status == "publish_uncertain":
                        break
            finally:
                state.close()
    except BrowserSessionRequired as error:
        print(f"发布被阻断：{error}", file=sys.stderr)
        return 2
    results_path = run_dir / "upload-results.json"
    previous_outcomes = read_json(results_path) if results_path.is_file() else []
    outcomes_by_task = {
        value.get("task_id"): value
        for value in previous_outcomes
        if value.get("task_id")
    }
    outcomes_by_task.update(
        {value["task_id"]: value for value in outcomes if value.get("task_id")}
    )
    durable_outcomes = [outcomes_by_task[task_id] for task_id in sorted(outcomes_by_task)]
    write_json(results_path, durable_outcomes)
    successful_states = {"submitted", "under_review", "success"}
    return 0 if entries and all(
        outcomes_by_task.get(task_id, {}).get("status") in successful_states
        for task_id in entries
    ) else 1


def _report(args) -> int:
    write_summary(Path(args.run_dir))
    return 0


def _export(args, page, page_factory=None) -> int:
    output = Path(args.output)
    if output.exists():
        try:
            if any(output.iterdir()):
                print("导出被阻断：输出目录必须不存在或为空", file=sys.stderr)
                return 2
        except OSError as error:
            print(f"导出被阻断：无法检查输出目录：{error}", file=sys.stderr)
            return 2
    try:
        selectors = load_selectors(
            Path(args.selectors),
            purpose="export",
            production=page is None and page_factory is None,
        )
    except SelectorConfigError as error:
        print(f"导出被阻断：{error}", file=sys.stderr)
        return 2
    report_types = (
        ("basic", "promotion")
        if args.report == "both"
        else (args.report,)
    )
    try:
        with page_context(page, args.cdp_url, page_factory) as resolved_page:
            assert_store_identity(resolved_page, selectors["store_name"], args.store)
            detect_human_check(resolved_page, selectors["human_check"])
            records = export_reports(
                resolved_page,
                selectors,
                Path(args.output),
                run_id=args.run_id,
                downloaded_at=args.downloaded_at,
                report_types=report_types,
            )
    except BrowserSessionRequired as error:
        print(f"导出被阻断：{error}", file=sys.stderr)
        return 2
    invalid_reason_codes = sorted(
        {
            reason
            for record in records
            for reason in record.reason_codes
        }
    )
    write_json(output / "source-files.json", records)
    write_json(
        output / "export-manifest.json",
        {
            "schema_version": 1,
            "run_id": args.run_id,
            "store": args.store,
            "downloaded_at": args.downloaded_at,
            "filters": {"product_status": args.product_status},
            "status": "needs_manual_review" if invalid_reason_codes else "complete",
            "reason_codes": invalid_reason_codes,
            "promotion_contract_status": (
                "draft" if "promotion" in report_types else "not_requested"
            ),
            "reports": records,
        },
    )
    return 1 if invalid_reason_codes else 0


BACKEND_STATUS_FIELDS = [
    "商品ID",
    "目标容量",
    "目标坑位",
    "现有素材数",
    "缺失数量",
    "空坑位",
    "精确坑位状态",
    "远端素材ID",
    "素材状态",
    "审核状态",
    "状态完整",
    "审核状态完整",
    "状态",
    "原因码",
    "采集时间",
    "证据",
]


def _write_backend_status(path: Path, rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=BACKEND_STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def _supplement(args, page, page_factory=None) -> int:
    scan_mode = args.scan_mode or ("exact" if args.candidates else "high-value")
    purpose = (
        "high_value_collection"
        if scan_mode in {"high-value", "recommended"}
        else "exact_material_status"
    )
    try:
        selectors = load_selectors(
            Path(args.selectors),
            purpose=purpose,
            production=page is None and page_factory is None,
        )
    except SelectorConfigError as error:
        print(f"补采被阻断：{error}", file=sys.stderr)
        return 2
    product_ids: list[str] = []
    if scan_mode == "exact":
        if not args.candidates:
            print("精确补采需要 --candidates", file=sys.stderr)
            return 2
        with Path(args.candidates).open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            product_ids = [
                str(row.get("商品ID", "")).strip()
                for row in csv.DictReader(stream)
                if str(row.get("商品ID", "")).strip()
            ]
    output = Path(args.output)
    checkpoint = (
        Path(args.checkpoint)
        if args.checkpoint
        else output.with_suffix(".checkpoint.json")
    )
    try:
        with page_context(page, args.cdp_url, page_factory) as resolved_page:
            assert_store_identity(resolved_page, selectors["store_name"], args.store)
            detect_human_check(resolved_page, selectors["human_check"])
            collect_supplement_material_status(
                resolved_page,
                selectors,
                scan_mode=scan_mode,
                product_ids=product_ids,
                output=output,
                checkpoint=checkpoint,
                collected_at=args.collected_at,
                max_pages=args.max_pages,
                settle_delay_ms=args.settle_delay_ms,
                action_wait_ms=args.action_wait_ms,
            )
    except BrowserSessionRequired as error:
        print(f"补采被阻断：{error}", file=sys.stderr)
        return 2
    except SelectorInvalidError as error:
        print(f"补采被阻断：SELECTOR_INVALID：{error}", file=sys.stderr)
        return 1

    return 0


def _inspect_xlsx(args) -> int:
    basic = read_basic_materials_xlsx(Path(args.basic))
    search = read_search_materials_xlsx(Path(args.search))
    print(json.dumps(
        {
            "basic_rows": len(basic),
            "search_rows": len(search),
            "basic_unique_ids": len({row["商品ID"] for row in basic if row["商品ID"]}),
            "search_unique_ids": len({row["商品ID"] for row in search if row["商品ID"]}),
            "basic_sha256": sha256_file(Path(args.basic)),
            "search_sha256": sha256_file(Path(args.search)),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def _inspect_completeness(args) -> int:
    products = read_product_csv(Path(args.products))
    promotion = _read_backend_status(args.promotion_status)
    data = build_completeness_matrix(
        promotion,
        products=[record.raw for record in products],
    )
    write_json(Path(args.output), data)
    return 0


def _prepare_gallery(args) -> int:
    with Path(args.status).open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        status_rows = list(csv.DictReader(stream))
    license_decisions = (
        read_json(Path(args.license_decisions))
        if args.license_decisions
        else []
    )
    remote_fingerprints = (
        read_json(Path(args.remote_fingerprints))
        if args.remote_fingerprints
        else None
    )
    with AssetIndexStore.open_readonly(Path(args.index)) as store:
        data = build_gallery_data(
            store.match_candidate_records(),
            status_rows,
            images_per_material=args.images_per_material,
            license_decisions=license_decisions,
            remote_sha256_by_product=remote_fingerprints,
        )
    write_json(Path(args.output), data)
    return 0


def _prepare_confirmed_gallery(args) -> int:
    try:
        output_path = Path(args.output)
        products = read_product_csv(Path(args.products))
        with Path(args.status).open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            status_rows = list(csv.DictReader(stream))
        decisions = extract_folder_decisions(read_json(Path(args.input)))
        data = build_confirmed_folder_gallery(
            products,
            status_rows,
            decisions,
            candidate_limit=args.candidate_limit,
            page_size=args.page_size,
            sampling_seed=output_path.parent.parent.name,
            preview_dir=output_path.parent / "preview-cache",
        )
        input_document = read_json(Path(args.input))
        session_id = str(input_document.get("session_id", ""))
        revision = int(input_document.get("revision", 0))
        input_sha256 = (
            hashlib.sha256(Path(args.input).read_bytes()).hexdigest()
            if session_id
            else ""
        )
        data["workflow_step"] = IMAGE_SELECTION
        data["gallery_identity"] = build_gallery_identity(
            session_id=session_id,
            revision=revision,
            input_sha256=input_sha256,
            folder_decisions=decisions,
        )
        write_json(output_path, data)
        return 0
    except (OSError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _prepare_image_review(args) -> int:
    try:
        context = prepare_image_review_session(
            SessionStore(Path(args.runs_root)),
            args.session,
            policy_path=Path(args.policy),
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.output:
        write_json(Path(args.output), context)
    print(
        json.dumps(
            {
                "session_id": args.session,
                "selected_count": context["data"]["selected_count"],
                "reviewable_count": context["data"]["reviewable_count"],
                "policy_sha256": context["data"]["policy_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _migrate_slot_first(args) -> int:
    try:
        report = migrate_legacy_image_review_session(
            SessionStore(Path(args.runs_root)),
            args.session,
            policy_path=Path(args.policy),
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _migrate_deterministic_selection(args) -> int:
    try:
        report = migrate_legacy_suitability_to_selected_preflight(
            SessionStore(Path(args.runs_root)),
            args.session,
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _prepare_slot_board(args) -> int:
    try:
        context = prepare_slot_board_session(
            SessionStore(Path(args.runs_root)),
            args.session,
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.output:
        write_json(Path(args.output), context)
    print(
        json.dumps(
            {
                "session_id": args.session,
                "product_count": len(context["data"]["products"]),
                "blocked_output_count": len(context["data"]["blocked_outputs"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _interact(args) -> int:
    runtime = load_runtime_config(args.config)
    runs_root = args.runs_root or runtime.runs_root

    if not _port_is_available(args.port):
        owner = _port_owner_pid(args.port)
        owner_text = f" by PID {owner}" if owner is not None else ""
        print(
            f"interact port {args.port} is already in use{owner_text}; "
            "stop the old service or choose --port <other-port>",
            file=sys.stderr,
        )
        return 2

    store = SessionStore(Path(runs_root))
    if args.session:
        store.load_session(args.session)
        session_id = args.session
    else:
        session_id = store.create_session().session_id

    app_kwargs = {
        "runtime_config": runtime,
        "material_executor_launcher": launch_material_executor,
    }
    if args.ownership_token:
        app_kwargs["service_identity"] = {
            "ownership_token": args.ownership_token,
            "runtime_identity": current_runtime_identity(),
        }
    app = create_app(Path(runs_root), **app_kwargs)
    query = urlencode({"session_id": session_id})
    print(f"http://127.0.0.1:{args.port}/?{query}", flush=True)
    app.run(
        host="127.0.0.1",
        port=args.port,
        debug=False,
        use_reloader=False,
    )
    return 0


def _port_is_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _port_owner_pid(port: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(
            ["netstat", "-ano"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    suffix = f":{port}"
    for line in completed.stdout.splitlines():
        columns = line.split()
        if len(columns) >= 5 and columns[1].endswith(suffix) and columns[3] == "LISTENING":
            try:
                return int(columns[4])
            except ValueError:
                return None
    return None


def _specialized_processor_payload(
    stage_id: str,
    *,
    runs_root: str | Path,
    session_id: str,
    claimant_id: str,
) -> dict[str, Any] | None:
    processor = ""
    if stage_id == "completeness":
        processor = "process-product-selection"
    elif stage_id == "asset_matching":
        store = SessionStore(Path(runs_root))
        handoff = store.read_optional_stage_document(
            session_id, stage_id, "handoff"
        )
        if (
            isinstance(handoff, dict)
            and handoff.get("handoff_kind") == "final_material_selection"
        ):
            processor = "process-final-material-handoff"
    if not processor:
        return None
    return {
        "claim_deferred": True,
        "processor": processor,
        "next_command": [
            "tmall-materials",
            processor,
            "--runs-root",
            str(Path(runs_root)),
            "--session",
            session_id,
            "--claimant-id",
            claimant_id,
        ],
        "next_action": (
            f"Run the specialized {processor} processor directly; "
            "generic recovery must not claim this handoff."
        ),
    }


def _wait_handoff(args) -> int:
    store = SessionStore(Path(args.runs_root))
    specialized = _specialized_processor_payload(
        args.stage,
        runs_root=args.runs_root,
        session_id=args.session,
        claimant_id=args.claimant,
    )
    if specialized is not None:
        print(
            json.dumps(
                {
                    "status": "specialized_processor_required",
                    "reason_code": "SPECIALIZED_PROCESSOR_REQUIRED",
                    **specialized,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    state = store.load_session(args.session)
    stage_state = state["stages"][args.stage]
    expected_revision = int(stage_state["revision"]) + (
        0
        if stage_state["status"] in {"ready_for_agent", "processing", "completed"}
        else 1
    )
    timeout = (
        args.timeout
        if args.timeout is not None
        else store.watch_budget_seconds(args.stage)
    )
    wait = store.create_agent_wait(
        args.session,
        args.stage,
        expected_revision=expected_revision,
        claimant_id=args.claimant,
        lease_seconds=30,
        budget_seconds=max(0.0, float(timeout)),
    )
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out waiting for handoff for stage {args.stage}"
                )
            try:
                handoff = store.wait_for_handoff(
                    args.session,
                    args.stage,
                    timeout_seconds=min(
                        remaining, max(0.1, float(args.segment_seconds))
                    ),
                    claimant_id=args.claimant,
                )
                break
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise
                wait = store.renew_agent_wait(
                    args.session, wait["wait_id"], lease_seconds=30
                )
    except TimeoutError as error:
        current = store.resolve_recovery_state(args.session, args.stage)
        print(
            json.dumps(
                {
                    "status": "timeout",
                    "reason_code": "HANDOFF_BUDGET_TIMEOUT",
                    "message": str(error),
                    "agent_wait": store.agent_wait(args.session, args.stage),
                    "recovery": current,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except InteractionConflict as error:
        print(
            json.dumps(
                {"status": "changed", "reason_code": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3
    else:
        print(json.dumps(handoff, ensure_ascii=False))
        return 0
    finally:
        store.clear_agent_wait(args.session, wait_id=wait["wait_id"])


def _process_confirmed_gallery(args) -> int:
    """Claim a folder-review handoff and publish its task-local image gallery."""

    store = SessionStore(Path(args.runs_root))
    handoff = None
    try:
        handoff = store.wait_for_handoff(
            args.session,
            "asset_matching",
            timeout_seconds=0.5,
            claimant_id=args.claimant,
        )
        stage_path = store._stage_path(args.session, "asset_matching")
        session_path = store._session_path(args.session)
        input_path = stage_path / "input.json"
        input_document = read_json(input_path)
        decisions = extract_folder_decisions(input_document)
        progress_path = stage_path / "gallery-progress.json"
        store._write_json_atomic(
            progress_path,
            {
                "schema_version": 1,
                "session_id": args.session,
                "stage_id": "asset_matching",
                "workflow_step": "gallery_preparing",
                "revision": int(handoff["revision"]),
                "input_sha256": str(handoff["input_sha256"]),
                "current_product": None,
                "current_folder": None,
                "discovered_count": 0,
                "prepared_count": 0,
                "heartbeat_at": _now_iso(),
                "recovery_action": (
                    "在当前 Codex 会话输入“已提交”，按同一 session 恢复"
                ),
            },
        )
        products = read_product_csv(session_path / "inputs" / "products.csv")
        status_path = (
            session_path
            / "collected"
            / "promotion"
            / "current"
            / "promotion-material-status.csv"
        )
        if not status_path.is_file():
            status_path = (
                session_path
                / "collected"
                / "promotion"
                / "promotion-material-status.csv"
            )
        with status_path.open("r", encoding="utf-8-sig", newline="") as stream:
            status_rows = list(csv.DictReader(stream))
        data = build_confirmed_folder_gallery(
            products,
            status_rows,
            decisions,
            candidate_limit=args.candidate_limit,
            page_size=args.page_size,
            sampling_seed=args.session,
            preview_dir=stage_path / "preview-cache",
        )
        prior = store.read_optional_stage_document(
            args.session, "asset_matching", "review-context"
        )
        prior_data = (
            prior.get("data")
            if isinstance(prior, dict) and isinstance(prior.get("data"), dict)
            else {}
        )
        data["folder_candidates"] = list(
            prior_data.get("folder_candidates", [])
        )
        data["workflow_step"] = IMAGE_SELECTION
        data["gallery_identity"] = build_gallery_identity(
            session_id=args.session,
            revision=int(handoff["revision"]),
            input_sha256=str(handoff["input_sha256"]),
            folder_decisions=decisions,
        )
        output_path = stage_path / "confirmed-gallery.json"
        write_json(output_path, data)
        store._write_json_atomic(
            progress_path,
            {
                "schema_version": 1,
                "session_id": args.session,
                "stage_id": "asset_matching",
                "workflow_step": IMAGE_SELECTION,
                "revision": int(handoff["revision"]),
                "input_sha256": str(handoff["input_sha256"]),
                "current_product": None,
                "current_folder": None,
                "discovered_count": int(
                    data.get("scan_summary", {}).get("discovered_images", 0)
                ),
                "prepared_count": len(data.get("asset_candidates", [])),
                "heartbeat_at": _now_iso(),
                "recovery_action": None,
            },
        )
        empty_products = [
            str(item.get("product_id", ""))
            for item in data.get("requirements", [])
            if isinstance(item, dict)
            and not any(
                isinstance(candidate, dict)
                and str(candidate.get("product_id", ""))
                == str(item.get("product_id", ""))
                for candidate in data.get("asset_candidates", [])
            )
        ]
        result = store.write_result(
            args.session,
            "asset_matching",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="needs_user_input",
            summary=(
                f"已为 {len(data.get('requirements', []))} 个商品准备 "
                f"{len(data.get('asset_candidates', []))} 张候选图片"
            ),
            blocking_reasons=(
                [f"{CONFIRMED_FOLDER_EMPTY}:{','.join(empty_products)}"]
                if empty_products
                else []
            ),
            evidence=[str(output_path)],
            next_action="逐个商品选择图片，然后确认选图并进入坑位编排",
            data=data,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, SchemaError, ValueError, InteractionConflict) as error:
        reason = (
            CONFIRMED_FOLDER_UNREADABLE
            if "not readable" in str(error).casefold()
            else GALLERY_PREPARATION_FAILED
        )
        if isinstance(handoff, dict):
            prior = store.read_optional_stage_document(
                args.session, "asset_matching", "review-context"
            )
            prior_data = (
                dict(prior.get("data"))
                if isinstance(prior, dict)
                and isinstance(prior.get("data"), dict)
                else {}
            )
            prior_data["workflow_step"] = FOLDER_REVIEW
            try:
                store.write_result(
                    args.session,
                    "asset_matching",
                    int(handoff["revision"]),
                    str(handoff["input_sha256"]),
                    status="blocked",
                    summary="采用文件夹中的图片读取失败",
                    blocking_reasons=[reason],
                    evidence=[],
                    next_action="调整文件夹决定或恢复路径访问后重新提交",
                    data=prior_data,
                )
            except (OSError, InteractionConflict):
                pass
        print(
            json.dumps(
                {"status": "blocked", "reason_code": reason, "message": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


def _process_gallery_job(args) -> int:
    store = SessionStore(Path(args.runs_root))
    try:
        result = process_gallery_job(
            store,
            args.session,
            args.job,
            args.attempt,
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        diagnostic = write_exception_diagnostic(
            store,
            args.session,
            "asset_matching",
            processor="process-gallery-job",
            phase="prepare_gallery",
            error=error,
        )
        print(
            json.dumps(
                {
                    "reason_code": diagnostic["reason_code"],
                    "message": str(error),
                    "diagnostic_path": str(
                        store._session_path(args.session)
                        / "agent-diagnostics"
                        / "current.json"
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _process_publish_authorization(args, page, page_factory=None) -> int:
    store = SessionStore(Path(args.runs_root))
    try:
        state = store.load_session(args.session)
        claim = state.get("processing_claim")
        if (
            state.get("current_stage") != "approval"
            or state["stages"]["approval"]["status"] != "processing"
            or not isinstance(claim, dict)
            or claim.get("stage_id") != "approval"
        ):
            raise InteractionConflict("PUBLISH_AUTHORIZATION_NOT_CLAIMED")
        prepared = prepare_publish_run_from_authorization(store, args.session)
        runtime = load_runtime_config(args.config)
        selectors = Path(args.selectors) if args.selectors else runtime.selectors_file
        if selectors is None:
            raise InteractionConflict("PRODUCTION_SELECTORS_MISSING")
        cdp_url = args.cdp_url or runtime.cdp_url
    except (InteractionConflict, OSError, ValueError) as error:
        diagnostic = write_exception_diagnostic(
            store,
            args.session,
            "approval",
            processor="process-publish-authorization",
            phase="prepare_publish",
            error=error,
        )
        print(
            json.dumps(
                {
                    "reason_code": diagnostic["reason_code"],
                    "message": str(error),
                    "diagnostic_path": str(
                        store._session_path(args.session)
                        / "agent-diagnostics"
                        / "current.json"
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    manifest_path = Path(prepared["run_dir"]) / "approval-manifest.json"
    if not manifest_path.is_file():
        approve_args = argparse.Namespace(
            run_dir=prepared["run_dir"],
            task_id=prepared["task_ids"],
            confirmed_by=prepared["confirmed_by"],
            confirmed_at=_now_iso(),
            valid_until=None,
        )
        approval_code = _approve(approve_args)
        if approval_code:
            return approval_code
    publish_args = argparse.Namespace(
        run_dir=prepared["run_dir"],
        store=prepared["store"],
        selectors=str(selectors),
        cdp_url=cdp_url,
    )
    publish_code = _publish(
        publish_args,
        page,
        page_factory,
        resume=bool(prepared.get("existing")),
    )

    state_store = StateStore(Path(prepared["run_dir"]) / "run.sqlite3")
    try:
        records = [
            state_store.item_record(task_id)
            for task_id in prepared["task_ids"]
        ]
    finally:
        state_store.close()
    completed = publish_code == 0 and all(
        record is not None
        and record["status"] in {"submitted", "under_review", "success"}
        and record.get("remote_material_id")
        for record in records
    )
    results_path = Path(prepared["run_dir"]) / "upload-results.json"
    store.write_result(
        args.session,
        "approval",
        int(claim["revision"]),
        str(claim["input_sha256"]),
        status="completed" if completed else "blocked",
        summary=(
            f"已提交 {len(records)} 个搜推素材任务。"
            if completed
            else "上传未全部完成，已保留逐任务状态并停止自动重试。"
        ),
        blocking_reasons=[] if completed else ["PUBLISH_BATCH_INCOMPLETE"],
        evidence=[str(manifest_path), str(results_path)],
        next_action=(
            "在千牛查看审核状态。"
            if completed
            else "查看上传结果中的稳定原因码；不确定任务先远端回查。"
        ),
        data={
            "store": prepared["store"],
            "task_count": len(records),
            "submitted_count": sum(
                1
                for record in records
                if record is not None
                and record["status"] in {"submitted", "under_review", "success"}
            ),
            "tasks": records,
        },
        claim_id=str(claim.get("claim_id", "")) or None,
        attempt_id=str(claim.get("attempt_id", "")) or None,
    )
    return 0 if completed else (publish_code or 1)


def _run_material_executor(args) -> int:
    try:
        runtime = load_runtime_config(args.config)
        handled = run_material_executor(
            runtime=runtime,
            session_id=args.session,
            watch=args.watch,
            idle_timeout_seconds=args.idle_timeout,
            poll_seconds=args.poll_interval,
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "completed", "handled_jobs": handled},
            ensure_ascii=False,
        )
    )
    return 0


def _process_final_material_handoff(args) -> int:
    store = SessionStore(Path(args.runs_root))
    try:
        result = process_final_material_handoff(
            store,
            args.session,
            claimant_id=args.claimant,
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        diagnostic = write_exception_diagnostic(
            store,
            args.session,
            "asset_matching",
            processor="process-final-material-handoff",
            phase="validate_and_plan",
            error=error,
            handoff_kind="final_material_selection",
        )
        print(
            json.dumps(
                {
                    "reason_code": diagnostic["reason_code"],
                    "message": str(error),
                    "diagnostic_path": str(
                        store._session_path(args.session)
                        / "agent-diagnostics"
                        / "current.json"
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _resume_session(args) -> int:
    """Treat “已提交” as an acknowledgement, never as business authority."""

    if args.ack.strip() != "已提交":
        print("RECOVERY_ACK_INVALID", file=sys.stderr)
        return 2
    store = SessionStore(Path(args.runs_root))
    resolved = store.resolve_recovery_state(args.session)
    if (
        resolved["stage_id"] == "setup"
        and resolved["status"] in {"processing", "recoverable"}
    ):
        authoritative = collection_status(
            Path(args.runs_root), args.session
        )
        if authoritative["status"] == "recoverable":
            attempt_id = str(
                authoritative.get("attempt_id", "")
            ).strip()
            if attempt_id:
                store.mark_processing_claim_recoverable(
                    args.session, "setup", attempt_id=attempt_id
                )
            resolved = {
                **resolved,
                "status": "recoverable",
                "collection_status": authoritative,
            }
        elif authoritative["status"] in {
            "processing",
            "processing_indeterminate",
        }:
            resolved = {
                **resolved,
                "status": authoritative["status"],
                "collection_status": authoritative,
            }
    if (
        resolved["stage_id"] in {"completeness", "asset_matching"}
        and resolved["status"]
        in {"ready", "recoverable", "needs_user_input", "blocked"}
    ):
        specialized = _specialized_processor_payload(
            resolved["stage_id"],
            runs_root=args.runs_root,
            session_id=args.session,
            claimant_id=args.claimant,
        )
        if specialized is not None:
            resolved = {**resolved, **specialized}
            print(json.dumps(resolved, ensure_ascii=False))
            return 0
    if resolved["status"] in {
        "ready",
        "recoverable",
        "needs_user_input",
        "blocked",
    }:
        handoff = store.wait_for_handoff(
            args.session,
            resolved["stage_id"],
            timeout_seconds=0.5,
            claimant_id=args.claimant,
            reclaim_expired=resolved["status"] == "recoverable",
            resume_needs_user_input=(
                resolved["status"] == "needs_user_input"
            ),
            resume_blocked=resolved["status"] == "blocked",
        )
        resolved = {
            "status": "processing",
            "session_id": args.session,
            "stage_id": resolved["stage_id"],
            "revision": handoff["revision"],
            "handoff": handoff,
            "processing_claim": store.processing_claim(
                args.session, resolved["stage_id"]
            ),
        }
    print(json.dumps(resolved, ensure_ascii=False))
    return 0 if resolved["status"] != "draft" else 2


def _claim_agent_request(args) -> int:
    try:
        request_document = claim_agent_request(
            SessionStore(Path(args.runs_root)),
            args.session,
            args.request,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(request_document, ensure_ascii=False))
    return 0


def _list_agent_requests(args) -> int:
    try:
        values = list_agent_requests(
            SessionStore(Path(args.runs_root)),
            args.session,
            statuses=set(args.status) if args.status else None,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({"requests": values}, ensure_ascii=False))
    return 0


def _wait_agent_request(args) -> int:
    try:
        value = wait_for_agent_request(
            SessionStore(Path(args.runs_root)),
            args.session,
            timeout_seconds=args.timeout,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False))
    return 0


def _cancel_agent_request(args) -> int:
    try:
        value = cancel_agent_request(
            SessionStore(Path(args.runs_root)),
            args.session,
            args.request,
            actor="codex-agent",
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False))
    return 0


def _complete_agent_request(args) -> int:
    try:
        response = read_json(Path(args.response))
        written = complete_agent_request(
            SessionStore(Path(args.runs_root)),
            args.session,
            args.request,
            response,
        )
    except (OSError, RuntimeError, SchemaError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(written, ensure_ascii=False))
    return 0


def _index_assets(args) -> int:
    store = None
    started = datetime.now(timezone.utc)
    try:
        options = IndexOptions(args.partition_depth, args.checkpoint_size)
        products_path = Path(args.products)
        if not products_path.is_file():
            raise ValueError(f"商品表不存在: {products_path}")
        products_path = products_path.resolve()
        products = read_product_csv(products_path)
        validation = validate_product_records(products)
        if not products or validation.batch_blocking:
            raise ValueError("商品表校验失败")
        reason_code_counts: dict[str, int] = {}
        blocked_rows = []
        for source_row, reason_codes in sorted(
            validation.reason_codes_by_row.items()
        ):
            stable_codes = sorted(set(reason_codes))
            blocked_rows.append(
                {"source_row": source_row, "reason_codes": stable_codes}
            )
            for reason_code in stable_codes:
                reason_code_counts[reason_code] = (
                    reason_code_counts.get(reason_code, 0) + 1
                )
        product_validation = {
            "row_count": validation.row_count,
            "eligible_count": validation.row_count - len(blocked_rows),
            "blocked_count": len(blocked_rows),
            "reason_code_counts": dict(sorted(reason_code_counts.items())),
            "blocked_rows": blocked_rows,
        }

        roots = tuple(NamedRoot.parse(value) for value in args.root)
        source_names = [root.source_system for root in roots]
        if len(source_names) != len(set(source_names)):
            raise ValueError("--root 来源名称不得重复")
        normalized_roots = []
        for root in roots:
            declared_metadata = root.path.lstat()
            if stat.S_ISLNK(declared_metadata.st_mode) or (
                getattr(declared_metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ValueError(f"素材根目录不得是符号链接或重解析点: {root.path}")
            resolved = root.path.resolve()
            if not resolved.is_dir():
                raise ValueError(f"素材根目录不存在: {root.path}")
            normalized_roots.append(NamedRoot(root.source_system, resolved))
        normalized_roots.sort(key=lambda root: root.source_system)

        output = Path(args.output).resolve()
        requested_mode = "resume" if args.resume else "refresh" if args.refresh else None
        if requested_mode is None:
            if output.exists():
                if not output.is_dir():
                    raise ValueError(f"输出路径不是目录: {output}")
                if next(output.iterdir(), None) is not None:
                    raise ValueError("new 模式要求不存在或完全为空的输出目录")
            else:
                output.mkdir(parents=True, exist_ok=False)
        elif not output.is_dir():
            raise ValueError(f"--{requested_mode} 需要已有输出目录")
        database_path = output / "asset-index.sqlite3"
        if requested_mode is not None and not database_path.is_file():
            raise ValueError(f"--{requested_mode} 需要已有索引数据库")
        mode = requested_mode or "new"

        roots_json = json.dumps(
            [
                {"path": str(root.path), "source_system": root.source_system}
                for root in normalized_roots
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        products_sha256 = sha256_file(products_path)
        identity = IndexIdentity(
            products_path=str(products_path),
            products_sha256=products_sha256,
            roots_json=roots_json,
            partition_depth=options.partition_depth,
            checkpoint_size=options.checkpoint_size,
        )
        store = (
            AssetIndexStore.open(database_path, identity)
            if database_path.exists()
            else AssetIndexStore.create(database_path, identity)
        )
        matcher = ProductPathMatcher.from_products(products, validation)
        indexer = IncrementalAssetIndexer(
            store,
            matcher,
            tuple(normalized_roots),
            options,
        )
        try:
            outcome = indexer.run(mode)
        except KeyboardInterrupt:
            outcome = ScanOutcome(
                complete=False,
                partial_failure=True,
                discovered=0,
                indexed=0,
                matched=0,
                failed=0,
                elapsed_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
            )
            write_match_candidates(store, output / "match-candidates.csv")
            summary = write_scan_summary(
                store,
                outcome,
                output / "scan-summary.json",
                mode,
                products_sha256,
                product_validation,
            )
            print(
                f"索引被中断；checkpoint 已保留。恢复命令: {summary['resume_command']}",
                file=sys.stderr,
            )
            return 1

        binding_summary = None
        if args.folder_decisions:
            decisions_payload = read_json(Path(args.folder_decisions))
            if isinstance(decisions_payload, dict):
                values = decisions_payload.get("values")
                decisions_payload = (
                    values.get("folder_decisions")
                    if isinstance(values, dict)
                    else decisions_payload.get("folder_decisions")
                )
            if not isinstance(decisions_payload, list):
                raise ValueError(
                    "folder decisions must be a JSON list or an input object "
                    "containing values.folder_decisions"
                )
            binding_summary = bind_confirmed_folder_matches(
                store,
                products,
                decisions_payload,
            )
            outcome = ScanOutcome(
                complete=outcome.complete,
                partial_failure=(
                    outcome.partial_failure
                    or binding_summary["inspection_failures"] > 0
                ),
                discovered=outcome.discovered,
                indexed=outcome.indexed,
                matched=binding_summary["bound_files"],
                failed=outcome.failed + binding_summary["inspection_failures"],
                elapsed_seconds=outcome.elapsed_seconds,
                scan_id=outcome.scan_id,
            )

        write_match_candidates(store, output / "match-candidates.csv")
        summary = write_scan_summary(
            store,
            outcome,
            output / "scan-summary.json",
            mode,
            products_sha256,
            product_validation,
        )
        if binding_summary is not None:
            summary["confirmed_folder_binding"] = binding_summary
            summary["folder_decisions_path"] = str(
                Path(args.folder_decisions).resolve()
            )
            write_json(output / "scan-summary.json", summary)
        return 1 if outcome.partial_failure else 0
    except (IndexIdentityError, SchemaError, sqlite3.DatabaseError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


def _index_folders(args) -> int:
    try:
        refresh_sources = tuple(args.refresh_source or ())
        refresh_prefixes = []
        for raw_scope in args.refresh_prefix or ():
            source, separator, prefix = raw_scope.partition("=")
            if not separator or not source.strip() or not prefix.strip():
                raise ValueError(
                    "--refresh-prefix 必须使用 SOURCE=RELATIVE_PATH 格式"
                )
            refresh_prefixes.append((source.strip(), prefix.strip()))
        if args.rematch_only and (refresh_sources or refresh_prefixes):
            raise ValueError("--rematch-only 不能与定向刷新范围同时使用")
        resume_requested = bool(args.resume)
        refresh_requested = bool(
            args.refresh or refresh_sources or refresh_prefixes
        )
        products_path = Path(args.products)
        if not products_path.is_file():
            raise ValueError(f"商品表不存在: {products_path}")
        products_path = products_path.resolve()
        products = read_product_csv(products_path)
        validation = validate_product_records(products)
        if not products or validation.batch_blocking:
            raise ValueError("商品表校验失败")

        roots = tuple(NamedRoot.parse(value) for value in args.root)
        source_names = [root.source_system for root in roots]
        if len(source_names) != len(set(source_names)):
            raise ValueError("--root 来源名称不得重复")
        normalized_roots = []
        for root in roots:
            if args.rematch_only:
                normalized_roots.append(
                    NamedRoot(
                        root.source_system,
                        absolute_path_without_io(root.path),
                    )
                )
                continue
            declared_metadata = root.path.lstat()
            if stat.S_ISLNK(declared_metadata.st_mode) or (
                getattr(declared_metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ValueError(f"素材根目录不得是符号链接或重解析点: {root.path}")
            resolved = root.path.resolve()
            if not resolved.is_dir():
                raise ValueError(f"素材根目录不存在: {root.path}")
            normalized_roots.append(NamedRoot(root.source_system, resolved))

        output = Path(args.output).resolve()
        database_path = output / "folder-index.sqlite3"
        if refresh_requested or resume_requested or args.rematch_only:
            if not database_path.is_file():
                mode_name = (
                    "--resume"
                    if resume_requested
                    else ("--refresh" if refresh_requested else "--rematch-only")
                )
                raise ValueError(f"{mode_name} 需要已有 folder-index.sqlite3")
        elif output.exists():
            if not output.is_dir():
                raise ValueError(f"输出路径不是目录: {output}")
            if next(output.iterdir(), None) is not None:
                raise ValueError("new 文件夹索引要求不存在或完全为空的输出目录")
        else:
            output.mkdir(parents=True)

        matcher = ProductPathMatcher.from_products(products, validation)
        products_sha256 = sha256_file(products_path)
        if args.rematch_only:
            summary = rematch_folder_index(
                database_path=database_path,
                products_sha256=products_sha256,
                roots=tuple(normalized_roots),
                matcher=matcher,
                candidates_path=output / "folder-candidates.csv",
            )
        else:
            summary = build_folder_index(
                database_path=database_path,
                products_sha256=products_sha256,
                roots=tuple(normalized_roots),
                matcher=matcher,
                refresh=refresh_requested,
                checkpoint_size=args.checkpoint_size,
                target_sources=refresh_sources,
                target_prefixes=tuple(refresh_prefixes),
                resume=resume_requested,
                candidates_path=output / "folder-candidates.csv",
            )
        if "candidate_rows" not in summary:
            summary["candidate_rows"] = write_folder_candidates(
                database_path, output / "folder-candidates.csv"
            )
        write_json(output / "folder-scan-summary.json", summary)
        return 0 if summary["complete"] else 1
    except (SchemaError, sqlite3.DatabaseError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _team_folder_index(args) -> int:
    try:
        runtime = load_runtime_config(args.config)
        shared_root = (
            Path(args.shared_root).expanduser()
            if args.shared_root
            else runtime.team_folder_index_root
        )
        if shared_root is None:
            raise TeamFolderIndexError(
                "TEAM_INDEX_SHARED_ROOT_REQUIRED: 请配置 team_folder_index_root"
            )
        local_root = (
            Path(args.local_root).expanduser()
            if args.local_root
            else runtime.folder_index_root
        )
        source_ids = tuple(args.source_id or ())
        if args.action == "status":
            result = snapshot_status(shared_root=shared_root, local_root=local_root)
        elif args.action == "sync":
            products_path = Path(args.products) if args.products else runtime.products.path
            if products_path is None or not products_path.is_file():
                raise TeamFolderIndexError("TEAM_INDEX_PRODUCTS_MISSING")
            result = sync_snapshots(
                shared_root=shared_root,
                local_root=local_root,
                products_path=products_path,
                image_sources=runtime.image_sources,
                source_ids=source_ids,
            )
        else:
            if len(source_ids) != 1:
                raise TeamFolderIndexError("TEAM_INDEX_PUBLISH_REQUIRES_ONE_SOURCE")
            source_id = source_ids[0]
            binding = next(
                (
                    item for item in runtime.image_sources
                    if item.get("source_id") == source_id
                ),
                None,
            )
            if binding is None:
                raise TeamFolderIndexError(
                    f"TEAM_INDEX_LOCAL_BINDING_MISSING: {source_id}"
                )
            result = publish_snapshot(
                database_path=local_root / "folder-index.sqlite3",
                shared_root=shared_root,
                source_id=source_id,
                canonical_source=str(binding.get("canonical_unc", "")),
                publisher=args.publisher,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TeamFolderIndexError, SchemaError, sqlite3.DatabaseError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _prepare_folder_review(args) -> int:
    try:
        decisions = (
            read_json(Path(args.decisions)) if args.decisions else []
        )
        if not isinstance(decisions, list):
            raise ValueError("文件夹决定 JSON 必须是数组")
        exact_folder_queries = []
        for raw_query in args.exact_folder or ():
            product_id, separator, folder_name = raw_query.partition("=")
            if not separator or not product_id.strip() or not folder_name.strip():
                raise ValueError(
                    "精确文件夹查询必须使用 PRODUCT_ID=FOLDER_NAME 格式"
                )
            exact_folder_queries.append(
                {
                    "product_id": product_id.strip(),
                    "folder_name": folder_name.strip(),
                }
            )
        data = build_folder_review_data(
            Path(args.candidates),
            decisions=decisions,
            exact_folder_queries=exact_folder_queries,
        )
        data["workflow_step"] = FOLDER_REVIEW
        write_json(Path(args.output), data)
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _snapshot_folder_candidates(args) -> int:
    try:
        summary = snapshot_folder_candidates(
            Path(args.candidates),
            Path(args.output),
            args.product_id,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _selected_nas_sources(args):
    sources = load_nas_sources(Path(args.config))
    requested = args.source_id
    selected_ids = (
        [requested]
        if isinstance(requested, str)
        else list(requested or sources)
    )
    unknown = [source_id for source_id in selected_ids if source_id not in sources]
    if unknown:
        raise AssetSourceUnavailable(
            "NAS_SOURCE_SELECTION_INVALID", f"未知 NAS 来源：{', '.join(unknown)}"
        )
    return sources, selected_ids


def _nas_check(args) -> int:
    try:
        sources, selected_ids = _selected_nas_sources(args)
        statuses = [check_nas_source(sources[source_id]) for source_id in selected_ids]
    except (OSError, AssetSourceUnavailable) as error:
        reason = getattr(error, "reason_code", "NAS_CONFIG_INVALID")
        print(f"NAS 检查失败 [{reason}]：{error}", file=sys.stderr)
        return 2
    print(statuses_json(statuses))
    return 0 if all(status.state == "ready" for status in statuses) else 2


def _nas_prepare(args) -> int:
    try:
        sources, selected_ids = _selected_nas_sources(args)
        statuses = [
            prepare_nas_source(
                sources[source_id],
                allow_mount=args.allow_mount,
                wait_seconds=args.wait_seconds,
            )
            for source_id in selected_ids
        ]
    except (OSError, AssetSourceUnavailable) as error:
        reason = getattr(error, "reason_code", "NAS_MOUNT_LAUNCH_FAILED")
        print(f"NAS 连接失败 [{reason}]：{error}", file=sys.stderr)
        return 2
    print(statuses_json(statuses))
    return 0 if all(status.state == "ready" for status in statuses) else 2


def _nas_browse(args) -> int:
    try:
        sources, _ = _selected_nas_sources(args)
        if args.source_id not in sources:
            raise AssetSourceUnavailable(
                "NAS_SOURCE_SELECTION_INVALID", f"未知 NAS 来源：{args.source_id}"
            )
        folders = browse_nas_folders(
            sources[args.source_id], relative_path=args.relative_path
        )
    except (OSError, AssetSourceUnavailable) as error:
        reason = getattr(error, "reason_code", "ASSET_ROOT_IO_ERROR")
        print(f"NAS 浏览失败 [{reason}]：{error}", file=sys.stderr)
        return 2
    print(json.dumps(folders, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tmall-materials")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Create a dry-run batch")
    run.add_argument("--mode", choices=["dry-run"], default="dry-run")
    run.add_argument("--month", type=int, choices=range(1, 13), required=True)
    run.add_argument("--store", required=True)
    run.add_argument("--products", required=True)
    run.add_argument("--rules", required=True)
    run.add_argument("--basic", required=True)
    run.add_argument("--search", required=True)
    run.add_argument("--backend-status")
    asset_source = run.add_mutually_exclusive_group()
    asset_source.add_argument("--asset-root")
    asset_source.add_argument("--asset-manifest")
    run.add_argument("--license-status", choices=["confirmed", "unknown"], default="unknown")
    run.add_argument("--media-policy")
    run.add_argument("--copy-responses")
    run.add_argument("--prohibited-term", action="append", default=[])
    run.add_argument("--output", required=True)
    run.add_argument("--started-at")

    approve = subparsers.add_parser("approve", help="Approve exact review-ready task IDs")
    approve.add_argument("--run-dir", required=True)
    approve.add_argument("--task-id", action="append", required=True)
    approve.add_argument("--confirmed-by", required=True)
    approve.add_argument("--confirmed-at", required=True)
    approve.add_argument("--valid-until")

    process_authorization = subparsers.add_parser(
        "process-publish-authorization",
        help="Approve, publish, and persist one claimed page authorization",
    )
    process_authorization.add_argument("--runs-root", required=True)
    process_authorization.add_argument("--session", required=True)
    process_authorization.add_argument("--config", metavar="JSON")
    process_authorization.add_argument("--selectors", metavar="YAML")
    process_authorization.add_argument("--cdp-url")

    publish = subparsers.add_parser("publish", help="Publish an immutable approved manifest")
    publish.add_argument("--run-dir", required=True)
    publish.add_argument("--store", required=True)
    publish.add_argument("--selectors", required=True)
    publish.add_argument("--cdp-url")

    resume = subparsers.add_parser("resume", help="Resume using persisted state")
    resume.add_argument("--run-dir", required=True)
    resume.add_argument("--store", required=True)
    resume.add_argument("--selectors", required=True)
    resume.add_argument("--cdp-url")

    report = subparsers.add_parser("report", help="Regenerate the Chinese batch summary")
    report.add_argument("--run-dir", required=True)

    inspect_xlsx = subparsers.add_parser("inspect-xlsx", help="Read-only XLSX inspection")
    inspect_xlsx.add_argument("--basic", required=True)
    inspect_xlsx.add_argument("--search", required=True)

    nas_check = subparsers.add_parser(
        "nas-check", help="只读检查已配置 NAS 的挂载身份和可访问性"
    )
    nas_check.add_argument("--config", default="config/nas-sources.yaml")
    nas_check.add_argument("--source-id", action="append")

    nas_prepare = subparsers.add_parser(
        "nas-prepare", help="检查 NAS，并在明确授权时打开系统连接窗口"
    )
    nas_prepare.add_argument("--config", default="config/nas-sources.yaml")
    nas_prepare.add_argument("--source-id", action="append")
    nas_prepare.add_argument("--allow-mount", action="store_true")
    nas_prepare.add_argument("--wait-seconds", type=float, default=30)

    nas_browse = subparsers.add_parser(
        "nas-browse", help="只读列出一个 NAS 路径的直接子目录"
    )
    nas_browse.add_argument("--config", default="config/nas-sources.yaml")
    nas_browse.add_argument("--source-id", required=True)
    nas_browse.add_argument("--relative-path", default="")

    inspect_completeness = subparsers.add_parser(
        "inspect-completeness",
        help="Build the read-only stage-02 completeness matrix",
    )
    inspect_completeness.add_argument("--products", required=True)
    inspect_completeness.add_argument("--promotion-status", required=True)
    inspect_completeness.add_argument("--output", required=True)

    export = subparsers.add_parser(
        "export",
        help="Export basic and/or promotion material XLSX reports via Playwright",
    )
    export.add_argument("--store", required=True)
    export.add_argument("--selectors", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--run-id", required=True)
    export.add_argument("--downloaded-at", required=True)
    export.add_argument("--product-status", required=True)
    export.add_argument(
        "--report",
        choices=("basic", "promotion", "both"),
        default="both",
    )
    export.add_argument("--cdp-url")

    supplement = subparsers.add_parser(
        "supplement",
        help="Collect slot and moderation state for candidate products",
    )
    supplement.add_argument("--store", required=True)
    supplement.add_argument("--selectors", required=True)
    supplement.add_argument("--candidates")
    supplement.add_argument("--output", required=True)
    supplement.add_argument("--collected-at", required=True)
    supplement.add_argument(
        "--scan-mode",
        choices=("high-value", "recommended", "exact"),
        help=(
            "high-value (default) scans 商品分类 → 搜推高价值; "
            "recommended is retained for historical compatibility"
        ),
    )
    supplement.add_argument("--checkpoint")
    supplement.add_argument("--max-pages", type=int)
    supplement.add_argument("--settle-delay-ms", type=int, default=1000)
    supplement.add_argument("--action-wait-ms", type=int, default=3000)
    supplement.add_argument("--cdp-url")

    process_setup = subparsers.add_parser(
        "process-setup",
        help=(
            "Process one submitted setup stage through the maintained "
            "high-value collector and completeness hydration"
        ),
    )
    process_setup.add_argument("--runs-root", required=True, metavar="DIR")
    process_setup.add_argument("--session", required=True)
    process_setup.add_argument("--config")
    process_setup.add_argument("--selectors")
    process_setup.add_argument("--cdp-url")
    process_setup.add_argument("--claimant-id", default="codex-agent")
    process_setup.add_argument(
        "--foreground",
        action="store_true",
        help="Debug only: keep collection in the invoking process",
    )

    process_product_selection = subparsers.add_parser(
        "process-product-selection",
        help=(
            "Claim one submitted product selection and prepare the complete "
            "folder-review stage in one invocation"
        ),
    )
    process_product_selection.add_argument(
        "--runs-root", required=True, metavar="DIR"
    )
    process_product_selection.add_argument("--session", required=True)
    process_product_selection.add_argument("--config", metavar="JSON")
    process_product_selection.add_argument(
        "--claimant-id", default="codex-agent"
    )

    process_copy_request = subparsers.add_parser(
        "process-copy-request",
        help=(
            "Claim one durable copy request and run the maintained Qianniu "
            "Playwright copy workflow"
        ),
    )
    process_copy_request.add_argument(
        "--runs-root", required=True, metavar="DIR"
    )
    process_copy_request.add_argument("--session", required=True)
    process_copy_request.add_argument("--request", required=True)
    process_copy_request.add_argument("--config", metavar="JSON")
    process_copy_request.add_argument("--actor", default="codex-agent")

    diagnose_session = subparsers.add_parser(
        "diagnose-session",
        help="Read the single current Agent-only workflow diagnostic",
    )
    diagnose_session.add_argument(
        "--runs-root", required=True, metavar="DIR"
    )
    diagnose_session.add_argument("--session", required=True)

    collection_worker = subparsers.add_parser(
        "collection-worker",
        help=argparse.SUPPRESS,
    )
    collection_worker.add_argument("--runs-root", required=True)
    collection_worker.add_argument("--session", required=True)
    collection_worker.add_argument("--attempt-id", required=True)
    collection_worker.add_argument("--ownership-token", required=True)
    collection_worker.add_argument("--claimant-id", required=True)
    collection_worker.add_argument("--selectors", required=True)
    collection_worker.add_argument("--cdp-url", required=True)
    collection_worker.add_argument("--config")
    collection_worker.add_argument(
        "--expected-windows-sid", help=argparse.SUPPRESS
    )
    collection_worker.add_argument(
        "--expected-login-session-id",
        type=int,
        help=argparse.SUPPRESS,
    )
    collection_worker.add_argument(
        "--require-interactive-desktop",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    collection_status_parser = subparsers.add_parser(
        "collection-status",
        help="Show authoritative managed high-value collection status",
    )
    collection_status_parser.add_argument("--runs-root", required=True)
    collection_status_parser.add_argument("--session", required=True)

    environment_status_parser = subparsers.add_parser(
        "environment-status",
        help="Check the prepared project-local runtime without syncing",
    )
    environment_status_parser.add_argument("--project-root")

    interact = subparsers.add_parser("interact", help="Serve the local interaction UI")
    interact.add_argument("--runs-root")
    interact.add_argument("--config", help="Machine-local runtime path configuration JSON")
    interact.add_argument("--session")
    interact.add_argument("--port", type=int, default=8765)
    interact.add_argument("--ownership-token", help=argparse.SUPPRESS)

    desktop = subparsers.add_parser(
        "desktop-workbench",
        help=argparse.SUPPRESS,
    )
    desktop.add_argument("--runs-root", required=True)
    desktop.add_argument("--config")
    desktop.add_argument("--session")
    desktop.add_argument("--port-start", type=int, default=8765)
    desktop.add_argument("--port-end", type=int, default=8795)

    for command, help_text in (
        ("ui-start", "Start or reuse a managed interaction UI"),
        ("ui-restart", "Restart a managed interaction UI"),
    ):
        managed = subparsers.add_parser(command, help=help_text)
        managed.add_argument("--runs-root")
        managed.add_argument("--config", help="Machine-local runtime path configuration JSON")
        managed.add_argument("--session")
        managed.add_argument("--port-start", type=int, default=8765)
        managed.add_argument("--port-end", type=int, default=8795)
        managed.add_argument("--startup-timeout", type=float, default=15.0)
        managed.add_argument("--open-system-browser", action="store_true")

    for command, help_text in (
        ("ui-status", "Show managed interaction UI status"),
        ("ui-stop", "Stop an owned managed interaction UI"),
    ):
        managed = subparsers.add_parser(command, help=help_text)
        managed.add_argument("--runs-root")
        managed.add_argument("--config", help="Machine-local runtime path configuration JSON")
        managed.add_argument("--session", required=True)

    chat_fallback = subparsers.add_parser(
        "chat-fallback",
        help="Write an allowed reason-coded fallback into the current stage",
    )
    chat_fallback.add_argument("--runs-root", required=True)
    chat_fallback.add_argument("--session", required=True)
    chat_fallback.add_argument("--stage", choices=[stage.id for stage in STAGES], required=True)
    chat_fallback.add_argument("--revision", type=int, required=True)
    chat_fallback.add_argument("--reason-code", required=True)
    chat_fallback.add_argument("--reason-detail", required=True)
    chat_fallback.add_argument("--values-json", required=True)
    chat_fallback.add_argument("--actor", default="codex-agent")
    chat_fallback.add_argument("--mode", choices=("draft", "submit"), default="draft")
    chat_fallback.add_argument("--user-notes", default="")

    wait_handoff = subparsers.add_parser(
        "wait-handoff",
        help="Wait for one validated interaction handoff",
    )
    wait_handoff.add_argument("--runs-root", required=True)
    wait_handoff.add_argument("--session", required=True)
    wait_handoff.add_argument("--stage", choices=[stage.id for stage in STAGES], required=True)
    wait_handoff.add_argument("--timeout", type=float)
    wait_handoff.add_argument("--segment-seconds", type=float, default=15)
    wait_handoff.add_argument("--claimant", default="codex-agent")

    resume_session = subparsers.add_parser(
        "resume-session",
        help="Resolve one explicitly bound session after an 已提交 acknowledgement",
    )
    resume_session.add_argument("--runs-root", required=True)
    resume_session.add_argument("--session", required=True)
    resume_session.add_argument("--ack", required=True)
    resume_session.add_argument("--claimant", default="codex-agent")

    claim_agent = subparsers.add_parser(
        "claim-agent-request",
        help="Claim one explicit Codex Agent request from the task directory",
    )
    claim_agent.add_argument("--runs-root", required=True)
    claim_agent.add_argument("--session", required=True)
    claim_agent.add_argument("--request", required=True)

    list_agent = subparsers.add_parser(
        "list-agent-requests",
        help="List durable Codex Agent requests for one session",
    )
    list_agent.add_argument("--runs-root", required=True)
    list_agent.add_argument("--session", required=True)
    list_agent.add_argument(
        "--status",
        action="append",
        choices=[
            "pending_agent",
            "processing",
            "completed",
            "failed",
            "superseded",
            "cancelled",
        ],
    )

    wait_agent = subparsers.add_parser(
        "wait-agent-request",
        help="Wait for a pending Codex Agent request without claiming it",
    )
    wait_agent.add_argument("--runs-root", required=True)
    wait_agent.add_argument("--session", required=True)
    wait_agent.add_argument("--timeout", type=float)

    cancel_agent = subparsers.add_parser(
        "cancel-agent-request",
        help="Cancel a pending or processing Codex Agent request",
    )
    cancel_agent.add_argument("--runs-root", required=True)
    cancel_agent.add_argument("--session", required=True)
    cancel_agent.add_argument("--request", required=True)

    complete_agent = subparsers.add_parser(
        "complete-agent-request",
        help="Validate and atomically complete one Codex Agent request",
    )
    complete_agent.add_argument("--runs-root", required=True)
    complete_agent.add_argument("--session", required=True)
    complete_agent.add_argument("--request", required=True)
    complete_agent.add_argument("--response", required=True)

    migrate_slot_first = subparsers.add_parser(
        "migrate-slot-first",
        help="Rebuild stage-four suitability for a historical session",
    )
    migrate_slot_first.add_argument("--runs-root", required=True)
    migrate_slot_first.add_argument("--session", required=True)
    migrate_slot_first.add_argument("--policy", required=True)

    migrate_deterministic = subparsers.add_parser(
        "migrate-deterministic-selection",
        help=(
            "Copy a compatible historical suitability snapshot into the "
            "selected-asset preflight stage without deleting legacy files"
        ),
    )
    migrate_deterministic.add_argument("--runs-root", required=True)
    migrate_deterministic.add_argument("--session", required=True)

    index_assets = subparsers.add_parser(
        "index-assets", help="Build or continue the local incremental asset index"
    )
    index_assets.add_argument("--products", required=True, metavar="PATH")
    index_assets.add_argument("--root", action="append", required=True, metavar="SOURCE=PATH")
    index_assets.add_argument("--output", required=True, metavar="DIR")
    index_assets.add_argument("--partition-depth", type=int, default=2)
    index_assets.add_argument("--checkpoint-size", type=int, default=1000)
    index_assets.add_argument(
        "--folder-decisions",
        metavar="JSON",
        help=(
            "Bind indexed roots to products using reviewed folder decisions "
            "instead of heuristic path matching"
        ),
    )
    index_mode = index_assets.add_mutually_exclusive_group()
    index_mode.add_argument("--resume", action="store_true")
    index_mode.add_argument("--refresh", action="store_true")
    index_folders = subparsers.add_parser(
        "index-folders",
        help="Build or refresh a lightweight directory-only media index",
    )
    index_folders.add_argument("--products", required=True, metavar="PATH")
    index_folders.add_argument(
        "--root", action="append", required=True, metavar="SOURCE=PATH"
    )
    index_folders.add_argument("--output", required=True, metavar="DIR")
    index_folders.add_argument("--checkpoint-size", type=int, default=1000)
    folder_index_mode = index_folders.add_mutually_exclusive_group()
    folder_index_mode.add_argument("--resume", action="store_true")
    folder_index_mode.add_argument("--refresh", action="store_true")
    folder_index_mode.add_argument("--rematch-only", action="store_true")
    index_folders.add_argument(
        "--refresh-source",
        action="append",
        metavar="SOURCE",
        help="Refresh one complete configured source; may be repeated",
    )
    index_folders.add_argument(
        "--refresh-prefix",
        action="append",
        metavar="SOURCE=RELATIVE_PATH",
        help="Refresh one source-relative subtree; may be repeated",
    )
    team_folder_index = subparsers.add_parser(
        "team-folder-index",
        help="Inspect, sync, or explicitly publish decentralized folder-index snapshots",
    )
    team_folder_index.add_argument("action", choices=("status", "sync", "publish"))
    team_folder_index.add_argument("--config")
    team_folder_index.add_argument("--shared-root", metavar="DIR")
    team_folder_index.add_argument("--local-root", metavar="DIR")
    team_folder_index.add_argument("--products", metavar="CSV")
    team_folder_index.add_argument("--source-id", action="append", metavar="SOURCE")
    team_folder_index.add_argument("--publisher", default="")
    folder_review = subparsers.add_parser(
        "prepare-folder-review",
        help="Build visual folder-ownership review data",
    )
    folder_review.add_argument("--candidates", required=True, metavar="CSV")
    folder_review.add_argument("--output", required=True, metavar="JSON")
    folder_review.add_argument("--decisions", metavar="JSON")
    folder_review.add_argument(
        "--exact-folder",
        action="append",
        metavar="PRODUCT_ID=FOLDER_NAME",
        help=(
            "Add a run-scoped exact folder-name query without recording "
            "a reusable alias"
        ),
    )
    folder_snapshot = subparsers.add_parser(
        "snapshot-folder-candidates",
        help="Copy selected products from the machine-shared folder candidate cache",
    )
    folder_snapshot.add_argument("--candidates", required=True, metavar="CSV")
    folder_snapshot.add_argument(
        "--product-id", action="append", required=True, metavar="ID"
    )
    folder_snapshot.add_argument("--output", required=True, metavar="CSV")
    prepare_gallery = subparsers.add_parser(
        "prepare-gallery",
        help="Build deterministic visual-review candidates from an asset index",
    )
    prepare_gallery.add_argument("--index", required=True, metavar="PATH")
    prepare_gallery.add_argument("--status", required=True, metavar="CSV")
    prepare_gallery.add_argument("--output", required=True, metavar="JSON")
    prepare_gallery.add_argument(
        "--images-per-material",
        type=int,
        choices=range(3, 10),
        default=3,
    )
    prepare_gallery.add_argument("--license-decisions", metavar="JSON")
    prepare_gallery.add_argument("--remote-fingerprints", metavar="JSON")
    confirmed_gallery = subparsers.add_parser(
        "prepare-confirmed-gallery",
        help="Build a bounded task-local gallery from reviewed folders",
    )
    confirmed_gallery.add_argument("--products", required=True, metavar="CSV")
    confirmed_gallery.add_argument("--status", required=True, metavar="CSV")
    confirmed_gallery.add_argument(
        "--input",
        required=True,
        metavar="JSON",
        help="Submitted asset-matching input containing folder_decisions",
    )
    confirmed_gallery.add_argument("--output", required=True, metavar="JSON")
    confirmed_gallery.add_argument(
        "--candidate-limit",
        type=int,
        default=100,
        choices=range(1, 101),
    )
    confirmed_gallery.add_argument(
        "--page-size",
        type=int,
        default=30,
    )
    process_gallery = subparsers.add_parser(
        "process-confirmed-gallery",
        help="Claim the current folder review and publish the image-selection gallery",
    )
    process_gallery.add_argument("--runs-root", required=True, metavar="PATH")
    process_gallery.add_argument("--session", required=True)
    process_gallery.add_argument("--claimant", default="codex-agent")
    process_gallery.add_argument(
        "--candidate-limit",
        type=int,
        default=100,
        choices=range(1, 101),
    )
    process_gallery.add_argument("--page-size", type=int, default=30)
    local_gallery_job = subparsers.add_parser(
        "process-gallery-job",
        help="Process one durable local gallery job without claiming a Codex handoff",
    )
    local_gallery_job.add_argument("--runs-root", required=True, metavar="PATH")
    local_gallery_job.add_argument("--session", required=True)
    local_gallery_job.add_argument("--job", required=True)
    local_gallery_job.add_argument("--attempt", required=True)
    material_executor = subparsers.add_parser(
        "material-executor",
        help="Process queued image work using this machine's local source bindings",
    )
    material_executor.add_argument("--config", metavar="JSON")
    material_executor.add_argument("--session", default="")
    material_executor.add_argument("--watch", action="store_true")
    material_executor.add_argument("--idle-timeout", type=float, default=600)
    material_executor.add_argument("--poll-interval", type=float, default=1)
    final_material = subparsers.add_parser(
        "process-final-material-handoff",
        help="Claim the final material selection and prepare the slot draft",
    )
    final_material.add_argument("--runs-root", required=True, metavar="PATH")
    final_material.add_argument("--session", required=True)
    final_material.add_argument("--claimant", default="codex-agent")
    image_review = subparsers.add_parser(
        "prepare-image-review",
        help="Prepare task-local stage-four image compliance and crop context",
    )
    image_review.add_argument("--runs-root", required=True, metavar="DIR")
    image_review.add_argument("--session", required=True)
    image_review.add_argument("--policy", required=True, metavar="YAML")
    image_review.add_argument("--output", metavar="JSON")
    slot_board = subparsers.add_parser(
        "prepare-slot-board",
        help="Prepare stage-five slots from confirmed stage-four outputs",
    )
    slot_board.add_argument("--runs-root", required=True, metavar="DIR")
    slot_board.add_argument("--session", required=True)
    slot_board.add_argument("--output", metavar="JSON")
    return parser


def main(argv: Sequence[str] | None = None, *, page=None, page_factory=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "approve":
        return _approve(args)
    if args.command == "process-publish-authorization":
        return _process_publish_authorization(args, page, page_factory)
    if args.command in {"publish", "resume"}:
        return _publish(
            args,
            page,
            page_factory,
            resume=args.command == "resume",
        )
    if args.command == "report":
        return _report(args)
    if args.command == "inspect-xlsx":
        return _inspect_xlsx(args)
    if args.command == "nas-check":
        return _nas_check(args)
    if args.command == "nas-prepare":
        return _nas_prepare(args)
    if args.command == "nas-browse":
        return _nas_browse(args)
    if args.command == "inspect-completeness":
        return _inspect_completeness(args)
    if args.command == "export":
        return _export(args, page, page_factory)
    if args.command == "supplement":
        return _supplement(args, page, page_factory)
    if args.command == "process-setup":
        try:
            runtime = load_runtime_config(args.config)
            arguments = {
                "runs_root": Path(args.runs_root),
                "session_id": args.session,
                "runtime": runtime,
                "selectors_path": (
                    Path(args.selectors) if args.selectors else None
                ),
                "cdp_url": args.cdp_url,
                "claimant_id": args.claimant_id,
            }
            if args.foreground or page is not None or page_factory is not None:
                result = process_setup_collection(
                    **arguments,
                    page=page,
                    page_factory=page_factory,
                )
            else:
                result = dispatch_collection_start(
                    Path(args.runs_root),
                    args.session,
                    claimant_id=args.claimant_id,
                )
                if result is None:
                    result = launch_collection_worker(**arguments)
        except (
            InteractionConflict,
            InteractionPathError,
            ManagedServiceError,
            OSError,
            SchemaError,
        ) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(error).split(":", 1)[0],
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return (
            0
            if result.get("status") in {"completed", "processing"}
            else 1
        )
    if args.command == "process-product-selection":
        try:
            runtime = load_runtime_config(args.config)
            result = process_product_selection_handoff(
                SessionStore(Path(args.runs_root)),
                args.session,
                folder_index_root=runtime.folder_index_root,
                claimant_id=args.claimant_id,
                config_path=args.config,
                runtime=runtime,
            )
        except ProductSelectionProcessingError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": error.reason_code,
                        "message": str(error),
                        "diagnostic_path": str(error.diagnostic_path),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        except (
            InteractionConflict,
            InteractionPathError,
            OSError,
            SchemaError,
            ValueError,
        ) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(error).split(":", 1)[0],
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.command == "process-copy-request":
        try:
            runtime = load_runtime_config(args.config)
            result = process_copy_draft_request(
                SessionStore(Path(args.runs_root)),
                args.session,
                args.request,
                runtime=runtime,
                page=page,
                page_factory=page_factory,
                actor=args.actor,
            )
        except CopyDraftProcessingError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": error.reason_code,
                        "message": str(error),
                        "diagnostic_path": str(error.diagnostic_path),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        except (InteractionConflict, OSError, TypeError, ValueError) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(error).split(":", 1)[0],
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, "response": result}, ensure_ascii=False))
        return 0
    if args.command == "diagnose-session":
        try:
            result = read_agent_diagnostic(
                Path(args.runs_root), args.session
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": "AGENT_DIAGNOSTIC_INVALID",
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.command == "collection-worker":
        try:
            result = run_collection_worker(
                runs_root=Path(args.runs_root),
                session_id=args.session,
                attempt_id=args.attempt_id,
                ownership_token=args.ownership_token,
                claimant_id=args.claimant_id,
                selectors_path=Path(args.selectors),
                cdp_url=args.cdp_url,
                config_path=args.config,
                local_resource_identity=(
                    {
                        "sid": args.expected_windows_sid,
                        "login_session_id": (
                            args.expected_login_session_id
                        ),
                        "interactive_desktop": bool(
                            args.require_interactive_desktop
                        ),
                    }
                    if args.expected_windows_sid
                    and args.expected_login_session_id is not None
                    else None
                ),
            )
        except Exception as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(error).split(":", 1)[0],
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0 if result.get("status") == "completed" else 1
    if args.command == "collection-status":
        try:
            result = collection_status(
                Path(args.runs_root), args.session
            )
        except (InteractionConflict, InteractionPathError, OSError) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(error).split(":", 1)[0],
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.command == "environment-status":
        project_root = (
            Path(args.project_root)
            if args.project_root
            else Path(__file__).resolve().parents[2]
        )
        result = environment_fingerprint(project_root)
        print(json.dumps({"ok": result["prepared"], **result}, ensure_ascii=False))
        return 0 if result["prepared"] else 2
    if args.command == "interact":
        return _interact(args)
    if args.command == "desktop-workbench":
        try:
            result = launch_desktop_workbench(
                runs_root=Path(args.runs_root),
                session_id=args.session,
                port_start=args.port_start,
                port_end=args.port_end,
                config=Path(args.config) if args.config else None,
            )
        except (DesktopLauncherError, ManagedServiceError) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": getattr(
                            error,
                            "reason_code",
                            "DESKTOP_LAUNCH_FAILED",
                        ),
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.command in {"ui-start", "ui-restart", "ui-status", "ui-stop"}:
        runtime = load_runtime_config(args.config)
        runs_root = Path(args.runs_root or runtime.runs_root)
        try:
            if args.command == "ui-start":
                login_browser = ensure_login_browser(runtime)
                result = start_service(
                    runs_root,
                    session_id=args.session,
                    port_start=args.port_start,
                    port_end=args.port_end,
                    startup_timeout=args.startup_timeout,
                    open_system_browser=args.open_system_browser,
                    config=args.config,
                )
                result["login_browser"] = login_browser
            elif args.command == "ui-restart":
                if not args.session:
                    print("ui-restart requires --session", file=sys.stderr)
                    return 2
                login_browser = ensure_login_browser(runtime)
                result = restart_service(
                    runs_root,
                    args.session,
                    port_start=args.port_start,
                    port_end=args.port_end,
                    startup_timeout=args.startup_timeout,
                    open_system_browser=args.open_system_browser,
                    config=args.config,
                )
                result["login_browser"] = login_browser
            elif args.command == "ui-status":
                result = status_service(runs_root, args.session)
            else:
                result = stop_service(runs_root, args.session)
        except (ManagedServiceError, InteractionPathError) as error:
            payload = {
                "ok": False,
                "reason_code": getattr(error, "reason_code", "SESSION_NOT_FOUND"),
                "message": str(error),
                "recovery_command": getattr(error, "recovery_command", ""),
            }
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.command == "chat-fallback":
        try:
            values = json.loads(Path(args.values_json).read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError("values JSON must contain an object")
            result = write_chat_fallback(
                SessionStore(Path(args.runs_root)),
                args.session,
                args.stage,
                values=values,
                expected_revision=args.revision,
                reason_code=args.reason_code,
                reason_detail=args.reason_detail,
                actor=args.actor,
                mode=args.mode,
                user_notes=args.user_notes,
            )
        except (OSError, json.JSONDecodeError, ValueError, InteractionConflict) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(error).split(":", 1)[0],
                        "message": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.command == "wait-handoff":
        return _wait_handoff(args)
    if args.command == "resume-session":
        return _resume_session(args)
    if args.command == "claim-agent-request":
        return _claim_agent_request(args)
    if args.command == "list-agent-requests":
        return _list_agent_requests(args)
    if args.command == "wait-agent-request":
        return _wait_agent_request(args)
    if args.command == "cancel-agent-request":
        return _cancel_agent_request(args)
    if args.command == "complete-agent-request":
        return _complete_agent_request(args)
    if args.command == "migrate-slot-first":
        return _migrate_slot_first(args)
    if args.command == "migrate-deterministic-selection":
        return _migrate_deterministic_selection(args)
    if args.command == "index-assets":
        return _index_assets(args)
    if args.command == "index-folders":
        return _index_folders(args)
    if args.command == "team-folder-index":
        return _team_folder_index(args)
    if args.command == "prepare-folder-review":
        return _prepare_folder_review(args)
    if args.command == "snapshot-folder-candidates":
        return _snapshot_folder_candidates(args)
    if args.command == "prepare-gallery":
        return _prepare_gallery(args)
    if args.command == "prepare-confirmed-gallery":
        return _prepare_confirmed_gallery(args)
    if args.command == "process-confirmed-gallery":
        return _process_confirmed_gallery(args)
    if args.command == "process-gallery-job":
        return _process_gallery_job(args)
    if args.command == "material-executor":
        return _run_material_executor(args)
    if args.command == "process-final-material-handoff":
        return _process_final_material_handoff(args)
    if args.command == "prepare-image-review":
        return _prepare_image_review(args)
    if args.command == "prepare-slot-board":
        return _prepare_slot_board(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
