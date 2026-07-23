from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Sequence
from urllib.parse import urlencode

from .approval import create_manifest, render_review_html, verify_manifest
from .asset_index import (
    IncrementalAssetIndexer,
    IndexOptions,
    NamedRoot,
    ScanOutcome,
    write_match_candidates,
    write_scan_summary,
)
from .asset_index_store import AssetIndexStore, IndexIdentity, IndexIdentityError
from .asset_matching import ProductPathMatcher
from .assets import (
    DirectoryAssetSource,
    ManifestAssetSource,
    inspect_asset,
    load_media_policy,
    validate_asset_group,
)
from .browser.config import load_selectors
from .browser.export_page import export_reports
from .browser.material_page import supplement_material_status
from .browser.session import (
    assert_store_identity,
    detect_human_check,
    open_cdp_page,
)
from .browser.upload_page import upload_approved_item
from .browser.verifier import verify_remote_item
from .copywriting import generate_and_validate_copy
from .eligibility import collect_titles_by_product, evaluate_all, load_monthly_rules
from .io_tables import (
    SchemaError,
    read_basic_materials_xlsx,
    read_product_csv,
    read_search_materials_xlsx,
    sha256_file,
    validate_product_records,
)
from .interaction.session import SessionStore
from .interaction.stages import STAGES
from .interaction.web import create_app
from .material_state import merge_material_state, products_requiring_supplement
from .models import MaterialStatus, make_run_id
from .reporting import (
    material_item_from_dict,
    read_json,
    write_eligibility_csv,
    write_json,
    write_summary,
    write_supplement_candidates,
)
from .state_store import StateStore
from .tasks import build_material_items, build_product_tasks


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    selectors = load_selectors(Path(args.selectors))
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
    selectors = load_selectors(Path(args.selectors))
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
            )
    except BrowserSessionRequired as error:
        print(f"导出被阻断：{error}", file=sys.stderr)
        return 2
    write_json(Path(args.output) / "source-files.json", records)
    return 0


def _supplement(args, page, page_factory=None) -> int:
    selectors = load_selectors(Path(args.selectors))
    with Path(args.candidates).open("r", encoding="utf-8-sig", newline="") as stream:
        product_ids = [
            str(row.get("商品ID", "")).strip()
            for row in csv.DictReader(stream)
            if str(row.get("商品ID", "")).strip()
        ]
    try:
        with page_context(page, args.cdp_url, page_factory) as resolved_page:
            assert_store_identity(resolved_page, selectors["store_name"], args.store)
            detect_human_check(resolved_page, selectors["human_check"])
            rows = supplement_material_status(
                resolved_page,
                selectors,
                product_ids,
                collected_at=args.collected_at,
            )
    except BrowserSessionRequired as error:
        print(f"补采被阻断：{error}", file=sys.stderr)
        return 2
    fieldnames = [
        "商品ID",
        "目标坑位",
        "现有素材数",
        "空坑位",
        "审核状态",
        "审核状态完整",
        "状态",
        "原因码",
        "采集时间",
        "证据",
    ]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
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


def _interact(args) -> int:
    runs_root = args.runs_root
    if runs_root is None:
        runs_root = os.environ.get("TMALL_RUNS_ROOT")
    if not runs_root:
        print(
            "interact requires --runs-root or TMALL_RUNS_ROOT",
            file=sys.stderr,
        )
        return 2

    store = SessionStore(Path(runs_root))
    if args.session:
        store.load_session(args.session)
        session_id = args.session
    else:
        session_id = store.create_session().session_id

    app = create_app(Path(runs_root))
    query = urlencode({"session_id": session_id})
    print(f"http://127.0.0.1:{args.port}/?{query}", flush=True)
    app.run(
        host="127.0.0.1",
        port=args.port,
        debug=False,
        use_reloader=False,
    )
    return 0


def _wait_handoff(args) -> int:
    store = SessionStore(Path(args.runs_root))
    try:
        handoff = store.wait_for_handoff(
            args.session,
            args.stage,
            timeout_seconds=args.timeout,
        )
    except TimeoutError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(handoff, ensure_ascii=False))
    return 0


def _index_assets(args) -> int:
    store = None
    started = datetime.now(timezone.utc)
    try:
        products_path = Path(args.products)
        if not products_path.is_file():
            raise ValueError(f"商品表不存在: {products_path}")
        products_path = products_path.resolve()
        products = read_product_csv(products_path)
        validation = validate_product_records(products)
        if not products or validation.blocking:
            raise ValueError("商品表校验失败")

        roots = tuple(NamedRoot.parse(value) for value in args.root)
        source_names = [root.source_system for root in roots]
        if len(source_names) != len(set(source_names)):
            raise ValueError("--root 来源名称不得重复")
        normalized_roots = []
        for root in roots:
            resolved = root.path.resolve()
            if not resolved.is_dir():
                raise ValueError(f"素材根目录不存在: {root.path}")
            normalized_roots.append(NamedRoot(root.source_system, resolved))
        normalized_roots.sort(key=lambda root: root.source_system)

        output = Path(args.output).resolve()
        if output.exists() and not output.is_dir():
            raise ValueError(f"输出路径不是目录: {output}")
        output.mkdir(parents=True, exist_ok=True)
        database_path = output / "asset-index.sqlite3"
        requested_mode = "resume" if args.resume else "refresh" if args.refresh else None
        if database_path.exists() and requested_mode is None:
            raise ValueError("索引数据库已存在；请明确使用 --resume 或 --refresh")
        if not database_path.exists() and requested_mode is not None:
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
            partition_depth=args.partition_depth,
            checkpoint_size=args.checkpoint_size,
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
            IndexOptions(args.partition_depth, args.checkpoint_size),
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
            )
            print(
                f"索引被中断；checkpoint 已保留。恢复命令: {summary['resume_command']}",
                file=sys.stderr,
            )
            return 1

        write_match_candidates(store, output / "match-candidates.csv")
        write_scan_summary(
            store,
            outcome,
            output / "scan-summary.json",
            mode,
            products_sha256,
        )
        return 1 if outcome.partial_failure else 0
    except (IndexIdentityError, SchemaError, sqlite3.DatabaseError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


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
    approve.add_argument("--valid-until", required=True)

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

    export = subparsers.add_parser("export", help="Export both Tmall XLSX reports via Playwright")
    export.add_argument("--store", required=True)
    export.add_argument("--selectors", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--run-id", required=True)
    export.add_argument("--downloaded-at", required=True)
    export.add_argument("--cdp-url")

    supplement = subparsers.add_parser(
        "supplement",
        help="Collect slot and moderation state for candidate products",
    )
    supplement.add_argument("--store", required=True)
    supplement.add_argument("--selectors", required=True)
    supplement.add_argument("--candidates", required=True)
    supplement.add_argument("--output", required=True)
    supplement.add_argument("--collected-at", required=True)
    supplement.add_argument("--cdp-url")

    interact = subparsers.add_parser("interact", help="Serve the local interaction UI")
    interact.add_argument("--runs-root")
    interact.add_argument("--session")
    interact.add_argument("--port", type=int, default=8765)

    wait_handoff = subparsers.add_parser(
        "wait-handoff",
        help="Wait for one validated interaction handoff",
    )
    wait_handoff.add_argument("--runs-root", required=True)
    wait_handoff.add_argument("--session", required=True)
    wait_handoff.add_argument("--stage", choices=[stage.id for stage in STAGES], required=True)
    wait_handoff.add_argument("--timeout", type=float)

    index_assets = subparsers.add_parser(
        "index-assets", help="Build or continue the local incremental asset index"
    )
    index_assets.add_argument("--products", required=True, metavar="PATH")
    index_assets.add_argument("--root", action="append", required=True, metavar="SOURCE=PATH")
    index_assets.add_argument("--output", required=True, metavar="DIR")
    index_assets.add_argument("--partition-depth", type=int, default=2)
    index_assets.add_argument("--checkpoint-size", type=int, default=1000)
    index_mode = index_assets.add_mutually_exclusive_group()
    index_mode.add_argument("--resume", action="store_true")
    index_mode.add_argument("--refresh", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, page=None, page_factory=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "approve":
        return _approve(args)
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
    if args.command == "export":
        return _export(args, page, page_factory)
    if args.command == "supplement":
        return _supplement(args, page, page_factory)
    if args.command == "interact":
        return _interact(args)
    if args.command == "wait-handoff":
        return _wait_handoff(args)
    if args.command == "index-assets":
        return _index_assets(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
