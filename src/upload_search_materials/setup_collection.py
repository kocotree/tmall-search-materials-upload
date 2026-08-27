"""Deterministic setup-to-high-value collection orchestration."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Any, Callable

from .browser.config import (
    SelectorConfigError,
    load_selector_profile,
)
from .browser.collection_actions import (
    perform_random_collection_action,
    random_page_interval,
)
from .browser.material_page import (
    SelectorInvalidError,
)
from .browser.session import (
    CdpUnavailable,
    HumanCheckRequired,
    LoginInteractionRequired,
    StoreIdentityError,
    ensure_cdp_browser,
    human_check_visible,
    open_cdp_page,
    recommendation_material_center_url,
    validate_collection_page,
)
from .agent_diagnostics import write_agent_diagnostic
from .interaction.session import (
    InteractionConflict,
    SessionStore,
)
from .io_tables import (
    SchemaError,
    read_product_csv,
    sha256_file,
    validate_product_records,
)
from .lark_base_sync import (
    inspect_product_metadata_snapshot,
    product_metadata_snapshot_path,
    sync_product_metadata_from_snapshot,
)
from .material_state import build_completeness_matrix
from .collection_runtime import (
    CollectionRuntimeError,
    attempt_path,
    read_json_object,
)
from .collection_readiness import merge_default_safe_popup_selectors
from .persistence import atomic_write_bytes, atomic_write_json, read_json
from .runtime_config import RuntimeConfig
from .supplement_collection import (
    CheckpointIdentityError,
    collect_supplement_material_status,
    read_backend_status,
    validate_checkpoint_identity,
)
from .time_utils import iso_timestamp


def _now_iso() -> str:
    return iso_timestamp()


def _write_json(path: Path, document: Any) -> None:
    atomic_write_json(path, document)


def _input_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise InteractionConflict(f"{path.name} must contain an object")
    return value


def _public_lark_product_sync(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return product-sync evidence safe to expose in workbench stage data."""

    return {
        key: evidence.get(key)
        for key in (
            "status",
            "reason_code",
            "message",
            "table_id",
            "fetched_count",
            "matched_count",
            "updated_owner_count",
            "snapshot_updated_at",
            "snapshot_sha256",
            "recorded_at",
        )
    }


def _product_metadata_snapshot_path(runtime: RuntimeConfig) -> Path:
    root = runtime.user_data_root or runtime.runs_root.parent
    return product_metadata_snapshot_path(root)


def refresh_completeness_product_metadata(
    *,
    runs_root: Path,
    session_id: str,
    runtime: RuntimeConfig,
) -> dict[str, Any]:
    """Apply the local owner snapshot without recollecting or calling Feishu.

    This is intentionally narrower than ``process_setup_collection``: it reads
    the already published promotion status, overlays the machine-local owner
    snapshot, and rewrites only the completeness matrix/review context.
    """

    store = SessionStore(runs_root)
    state = store.load_session(session_id)
    if str(state.get("current_stage") or "") != "completeness":
        return {
            "status": "skipped",
            "reason_code": "LARK_OWNER_REFRESH_STAGE_NOT_CURRENT",
            "message": "负责人数据会在生成巡检结果时从本机快照读取。",
        }
    stage_state = state.get("stages", {}).get("completeness", {})
    stage_status = str(stage_state.get("status") or "")
    if stage_status in {"ready_for_agent", "processing", "completed"}:
        return {
            "status": "skipped",
            "reason_code": "LARK_OWNER_REFRESH_STAGE_BUSY",
            "message": "当前步骤正在处理，负责人将保留本次已确认的数据。",
        }
    if any(
        str(item.get("status") or "") == "processing"
        for item in state.get("stages", {}).values()
        if isinstance(item, dict)
    ):
        return {
            "status": "skipped",
            "reason_code": "LARK_OWNER_REFRESH_WORKFLOW_BUSY",
            "message": "工作台正在处理当前任务，暂不刷新负责人。",
        }

    session_path = store._session_path(session_id)
    completeness_path = (
        store._stage_path(session_id, "completeness")
        / "completeness-matrix.json"
    )
    existing_matrix = _read_json(completeness_path)
    inputs_path = session_path / "inputs"
    products_path = inputs_path / "products.csv"
    collected_candidates = (
        session_path
        / "collected"
        / "promotion"
        / "current"
        / "promotion-material-status.csv",
        session_path
        / "collected"
        / "promotion"
        / "promotion-material-status.csv",
    )
    collected_path = next(
        (path for path in collected_candidates if path.is_file()),
        None,
    )
    if not products_path.is_file() or collected_path is None:
        return {
            "status": "skipped",
            "reason_code": "LARK_OWNER_REFRESH_INPUT_MISSING",
            "message": "本次巡检快照尚未准备完成，暂不刷新负责人。",
        }

    evidence_path = inputs_path / "lark-product-sync.json"
    prior_evidence = (
        _read_json(evidence_path) if evidence_path.is_file() else {}
    )
    snapshot_status = inspect_product_metadata_snapshot(
        _product_metadata_snapshot_path(runtime)
    )
    applied_sync = existing_matrix.get("lark_product_sync")
    if (
        isinstance(applied_sync, dict)
        and applied_sync.get("status") == "completed"
        and prior_evidence.get("status") == "completed"
        and int(prior_evidence.get("fetched_count") or 0) > 0
        and snapshot_status.get("status") == "available"
        and prior_evidence.get("snapshot_sha256")
        == snapshot_status.get("snapshot_sha256")
        and applied_sync.get("recorded_at") == prior_evidence.get("recorded_at")
    ):
        return {
            "status": "unchanged",
            "reason_code": "LARK_OWNER_REFRESH_ALREADY_CURRENT",
            "message": "当前巡检已使用最新的本机负责人数据。",
            "lark_product_sync": _public_lark_product_sync(prior_evidence),
        }

    products = read_product_csv(products_path)
    collected_rows = read_backend_status(collected_path)
    sync = sync_product_metadata_from_snapshot(
        products,
        _product_metadata_snapshot_path(runtime),
        enabled=runtime.lark_base.enabled,
    )
    sync_evidence = sync.evidence()
    _write_json(evidence_path, sync_evidence)
    scan_summary_path = inputs_path / "scan-summary.json"
    if scan_summary_path.is_file():
        scan_summary = _read_json(scan_summary_path)
        scan_summary["lark_product_sync"] = sync_evidence
        _write_json(scan_summary_path, scan_summary)
    public_sync = _public_lark_product_sync(sync_evidence)
    if sync.status != "completed":
        return {
            "status": "unavailable",
            "reason_code": sync.reason_code,
            "message": sync.message,
            "lark_product_sync": public_sync,
        }

    refreshed_matrix = build_completeness_matrix(
        collected_rows,
        products=[record.raw for record in sync.records],
    )
    for key in ("pagination", "product_row_anomalies"):
        if key in existing_matrix:
            refreshed_matrix[key] = existing_matrix[key]
    refreshed_matrix["lark_product_sync"] = public_sync

    expected_revision = int(stage_state.get("revision") or 0)
    with store._session_lock(session_id):
        current_state = store.load_session(session_id)
        current_stage_state = current_state.get("stages", {}).get(
            "completeness", {}
        )
        if (
            str(current_state.get("current_stage") or "") != "completeness"
            or int(current_stage_state.get("revision") or 0)
            != expected_revision
            or str(current_stage_state.get("status") or "")
            in {"ready_for_agent", "processing", "completed"}
        ):
            return {
                "status": "skipped",
                "reason_code": "LARK_OWNER_REFRESH_STAGE_CHANGED",
                "message": "任务步骤已经变化，本次未覆盖负责人数据。",
                "lark_product_sync": public_sync,
            }
        _write_json(completeness_path, refreshed_matrix)
        context = store.read_optional_stage_document(
            session_id, "completeness", "review-context"
        ) or {}
        evidence = [
            str(item)
            for item in context.get("evidence", [])
            if str(item).strip()
        ]
        if str(evidence_path) not in evidence:
            evidence.append(str(evidence_path))
        store.write_review_context(
            session_id,
            "completeness",
            {
                "schema_version": 1,
                "session_id": session_id,
                "stage_id": "completeness",
                "revision": expected_revision,
                "status": "needs_user_input",
                "summary": (
                    str(context.get("summary") or "").strip()
                    or f"已刷新 {len(refreshed_matrix.get('products', []))} 个巡检商品"
                ),
                "blocking_reasons": [],
                "evidence": evidence,
                "next_action": "按负责人筛选商品并提交给工作台",
                "created_at": _now_iso(),
                "data": refreshed_matrix,
            },
        )

    return {
        "status": "refreshed",
        "reason_code": "",
        "message": (
            f"已从本机负责人数据匹配 {sync.matched_count} 个巡检商品，"
            f"更新负责人 {sync.updated_owner_count} 个。"
        ),
        "lark_product_sync": public_sync,
    }


def _archive_prior_attempt_checkpoint(
    checkpoint: Path,
    output: Path,
    *,
    checkpoint_context: dict[str, Any],
    attempt_id: str | None,
) -> Path | None:
    if not attempt_id or not checkpoint.is_file():
        return None
    prior = read_json_object(checkpoint)
    if not prior:
        return None
    prior_attempt_id = str(prior.get("attempt_id", "")).strip()
    if not prior_attempt_id:
        # Legacy checkpoints without an attempt identity can only be resumed
        # when they belong to the exact current handoff.  Keep the existing
        # fail-closed behavior for those ambiguous files.
        validate_checkpoint_identity(
            checkpoint,
            {
                "scan_mode": "high-value",
                **{
                    key: value
                    for key, value in checkpoint_context.items()
                    if key != "attempt_id"
                },
            },
            compatible_missing_fields=frozenset({"attempt_id"}),
        )
        return None
    if prior_attempt_id == attempt_id:
        validate_checkpoint_identity(
            checkpoint,
            {"scan_mode": "high-value", **checkpoint_context},
        )
        return None
    # A published checkpoint from an earlier immutable attempt is historical
    # evidence, not a resumable checkpoint for the new attempt.  Validate its
    # own stable identity without comparing the old revision/input to the new
    # handoff, then preserve a copy in the old attempt directory.  The current
    # projection stays intact until a new attempt has been fully validated and
    # atomically published.
    validate_checkpoint_identity(
        checkpoint,
        {
            "scan_mode": "high-value",
            "session_id": checkpoint_context["session_id"],
            "attempt_id": prior_attempt_id,
        },
    )
    archive = (
        checkpoint.parent
        / "attempts"
        / f"a-{prior_attempt_id[:12]}"
    )
    archive.mkdir(parents=True, exist_ok=True)
    for source in (checkpoint, output):
        if not source.is_file():
            continue
        destination = archive / source.name
        if destination.is_file():
            if destination.read_bytes() != source.read_bytes():
                raise CheckpointIdentityError(
                    f"CHECKPOINT_ARCHIVE_CONFLICT:{destination.name}"
                )
        else:
            atomic_write_bytes(destination, source.read_bytes())
    return archive


def _reuse_published_collection(
    session_path: Path,
    setup_path: Path,
    setup_input: dict[str, Any],
    *,
    attempt_id: str | None,
    artifact_root: Path,
    output: Path,
    checkpoint: Path,
    page_evidence: Path,
    pagination_evidence: Path,
    checkpoint_context: dict[str, Any],
) -> list[dict[str, str]] | None:
    """Clone a verified completed collection for an identical setup input.

    The previous immutable attempt remains untouched.  The cloned checkpoint
    is rebound to the current handoff and records its provenance, so later
    stage/result identity checks still refer to the current revision.
    """

    if not attempt_id:
        return None
    promotion_root = session_path / "collected" / "promotion"
    current_root = promotion_root / "current"
    try:
        publication = read_json_object(current_root / "publication.json")
        if not publication:
            return None
        prior_attempt_id = str(publication.get("attempt_id", "")).strip()
        if not prior_attempt_id or prior_attempt_id == attempt_id:
            return None
        prior_revision = int(publication.get("revision", -1))
        prior_input_sha256 = str(
            publication.get("input_sha256", "")
        ).strip()
        prior_input = read_json_object(
            setup_path
            / "revisions"
            / f"{prior_revision:04d}"
            / "input.json"
        )
        if (
            not prior_input
            or prior_input.get("values") != setup_input.get("values")
            or publication.get("session_id")
            != checkpoint_context["session_id"]
            or publication.get("selector_profile_sha256")
            != checkpoint_context["selector_profile_sha256"]
            or publication.get("target_store")
            != checkpoint_context["target_store"]
        ):
            return None

        source_output = current_root / output.name
        source_checkpoint = current_root / checkpoint.name
        source_page_evidence = current_root / page_evidence.name
        source_pagination_evidence = (
            current_root / pagination_evidence.name
        )
        required = (
            source_output,
            source_checkpoint,
            source_page_evidence,
            source_pagination_evidence,
        )
        if any(not path.is_file() for path in required):
            return None
        prior_checkpoint = validate_checkpoint_identity(
            source_checkpoint,
            {
                "scan_mode": "high-value",
                "session_id": checkpoint_context["session_id"],
                "revision": prior_revision,
                "input_sha256": prior_input_sha256,
                "selector_profile_sha256": checkpoint_context[
                    "selector_profile_sha256"
                ],
                "attempt_id": prior_attempt_id,
                "target_store": checkpoint_context["target_store"],
            },
        )
        if not prior_checkpoint or prior_checkpoint.get("status") != "complete":
            return None
        if (
            sha256_file(source_output)
            != prior_checkpoint.get("output_sha256")
            or sha256_file(source_checkpoint)
            != publication.get("checkpoint_sha256")
            or sha256_file(source_pagination_evidence)
            != prior_checkpoint.get("pagination_evidence_sha256")
            or sha256_file(source_pagination_evidence)
            != publication.get("pagination_evidence_sha256")
        ):
            return None
        rows = read_backend_status(source_output)
        product_ids = [
            str(row.get("商品ID", "")).strip() for row in rows
        ]
        if (
            publication.get("row_count") != len(rows)
            or prior_checkpoint.get("row_count") != len(rows)
            or any(not product_id for product_id in product_ids)
            or len(set(product_ids)) != len(product_ids)
        ):
            return None
        pagination_document = read_json(source_pagination_evidence)
        pagination_events = (
            pagination_document.get("events", [])
            if isinstance(pagination_document, dict)
            else []
        )
        if not any(
            isinstance(event, dict)
            and event.get("event_type") == "origin"
            and event.get("current_page") == 1
            for event in pagination_events
        ) or not any(
            isinstance(event, dict)
            and event.get("event_type") == "terminal"
            and event.get("current_page") == event.get("terminal_page")
            and event.get("next_enabled") is False
            for event in pagination_events
        ):
            return None
    except (
        CheckpointIdentityError,
        CollectionRuntimeError,
        OSError,
        TypeError,
        ValueError,
    ):
        return None

    atomic_write_bytes(output, source_output.read_bytes())
    atomic_write_bytes(
        page_evidence,
        source_page_evidence.read_bytes(),
    )
    atomic_write_bytes(
        pagination_evidence,
        source_pagination_evidence.read_bytes(),
    )
    rebound_checkpoint = dict(prior_checkpoint)
    rebound_checkpoint.update(checkpoint_context)
    rebound_checkpoint["reused_from_attempt_id"] = prior_attempt_id
    rebound_checkpoint["reused_from_revision"] = prior_revision
    rebound_checkpoint["reused_from_input_sha256"] = prior_input_sha256
    rebound_checkpoint["reused_at"] = _now_iso()
    atomic_write_json(checkpoint, rebound_checkpoint)
    atomic_write_json(
        artifact_root / "collection-reuse.json",
        {
            "schema_version": 1,
            **checkpoint_context,
            "reused_from_attempt_id": prior_attempt_id,
            "reused_from_revision": prior_revision,
            "reused_from_input_sha256": prior_input_sha256,
            "output_sha256": rebound_checkpoint.get("output_sha256"),
            "pagination_evidence_sha256": rebound_checkpoint.get(
                "pagination_evidence_sha256"
            ),
            "reused_at": rebound_checkpoint["reused_at"],
        },
    )
    return rows


def _claim_setup(
    store: SessionStore,
    session_id: str,
    claimant_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = store.load_session(session_id)
    existing = store.read_optional_stage_document(
        session_id, "setup", "result"
    )
    handoff = store.read_optional_stage_document(
        session_id, "setup", "handoff"
    )
    if handoff is None:
        raise InteractionConflict("SETUP_HANDOFF_REQUIRED")
    if (
        existing
        and existing.get("status") == "completed"
        and existing.get("revision") == handoff.get("revision")
        and existing.get("input_sha256") == handoff.get("input_sha256")
    ):
        return handoff, {"completed_result": existing}
    if existing and (
        existing.get("revision") != handoff.get("revision")
        or existing.get("input_sha256") != handoff.get("input_sha256")
    ):
        raise InteractionConflict("BOUND_RESULT_IDENTITY_MISMATCH")

    current_status = state["stages"]["setup"]["status"]
    claim = store.processing_claim(session_id, "setup")
    if current_status == "processing" and claim and not claim["expired"]:
        if claim.get("claimant_id") != claimant_id:
            raise InteractionConflict("PROCESSING_CLAIM_ACTIVE")
        return handoff, claim
    if current_status == "processing" and (
        claim is None or claim.get("expired")
    ):
        _validate_expired_recovery(
            store._session_path(session_id),
            handoff,
        )
    store.wait_for_handoff(
        session_id,
        "setup",
        timeout_seconds=0.5,
        claimant_id=claimant_id,
        reclaim_expired=True,
        resume_needs_user_input=True,
        resume_blocked=True,
    )
    claim = store.processing_claim(session_id, "setup")
    if claim is None:
        raise InteractionConflict("PROCESSING_CLAIM_REQUIRED")
    return handoff, claim


def _validate_expired_recovery(
    session_path: Path,
    handoff: dict[str, Any],
) -> None:
    """Validate durable partial output before replacing a stale owner."""

    checkpoint = (
        session_path
        / "collected"
        / "promotion"
        / "promotion-material-status.checkpoint.json"
    )
    if not checkpoint.is_file():
        return
    document = validate_checkpoint_identity(
        checkpoint,
        {
            "scan_mode": "high-value",
            "session_id": handoff["session_id"],
            "revision": handoff["revision"],
            "input_sha256": handoff["input_sha256"],
        },
    )
    if not document or document.get("status") not in {
        "in_progress",
        "complete",
    }:
        return
    output = checkpoint.with_name("promotion-material-status.csv")
    rows = read_backend_status(output)
    row_count = document.get("row_count")
    if isinstance(row_count, int) and row_count != len(rows):
        raise CheckpointIdentityError(
            "CHECKPOINT_OUTPUT_ROW_COUNT_MISMATCH"
        )


def _snapshot_inputs(
    session_path: Path,
    setup_input: dict[str, Any],
    *,
    session_id: str,
    revision: int,
    input_sha256: str,
) -> tuple[Path, Path, list[Any], dict[str, Any]]:
    values = setup_input.get("values")
    if not isinstance(values, dict):
        raise InteractionConflict("SETUP_VALUES_INVALID")
    products_source = Path(str(values.get("products_csv", ""))).expanduser()
    rules_source = Path(str(values.get("rules_csv", ""))).expanduser()
    if not products_source.is_file():
        raise InteractionConflict("PRODUCTS_FILE_REQUIRED")
    if not rules_source.is_file():
        raise InteractionConflict("RULES_FILE_REQUIRED")
    inputs = session_path / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    products_target = inputs / "products.csv"
    rules_target = inputs / "rules.csv"
    for source, target in (
        (products_source, products_target),
        (rules_source, rules_target),
    ):
        source_sha = sha256_file(source)
        if target.is_file() and sha256_file(target) != source_sha:
            raise InteractionConflict("INPUT_SNAPSHOT_CONFLICT")
        if not target.is_file():
            shutil.copy2(source, target)
    products = read_product_csv(products_target)
    validation = validate_product_records(products)
    manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "setup",
        "revision": revision,
        "input_sha256": input_sha256,
        "created_at": _now_iso(),
        "files": [
            {
                "kind": "products",
                "source_path": str(products_source.resolve()),
                "snapshot_path": str(products_target.resolve()),
                "sha256": sha256_file(products_target),
            },
            {
                "kind": "rules",
                "source_path": str(rules_source.resolve()),
                "snapshot_path": str(rules_target.resolve()),
                "sha256": sha256_file(rules_target),
            },
        ],
    }
    _write_json(inputs / "input-manifest.json", manifest)
    scan_summary = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "setup",
        "revision": revision,
        "products_sha256": sha256_file(products_target),
        "status": (
            "blocked"
            if not products or validation.batch_blocking
            else "ready"
        ),
        "row_count": validation.row_count,
        "valid_row_count": validation.row_count
        - validation.blocked_row_count,
        "blocked_row_count": validation.blocked_row_count,
        "duplicate_id_count": sum(
            "DUPLICATE_PRODUCT_ID" in reasons
            for reasons in validation.reason_codes_by_row.values()
        ),
        "missing_id_count": sum(
            "MISSING_PRODUCT_ID" in reasons
            for reasons in validation.reason_codes_by_row.values()
        ),
        "invalid_id_count": sum(
            "INVALID_PRODUCT_ID" in reasons
            for reasons in validation.reason_codes_by_row.values()
        ),
        "excluded_row_count": validation.blocked_row_count,
        "processable_row_count": (
            validation.row_count - validation.blocked_row_count
        ),
        "reason_codes_by_row": {
            str(row): list(reasons)
            for row, reasons in validation.reason_codes_by_row.items()
        },
        "checked_at": _now_iso(),
    }
    _write_json(inputs / "scan-summary.json", scan_summary)
    if not products or validation.batch_blocking:
        raise InteractionConflict("PRODUCT_TABLE_BATCH_BLOCKED")
    return products_target, rules_target, products, scan_summary


def _persist_selector_error(
    session_path: Path,
    handoff: dict[str, Any],
    error: Exception,
    *,
    artifact_root: Path | None = None,
) -> Path:
    path = (artifact_root or session_path / "collected") / "selector-error.json"
    promotion_root = (
        artifact_root
        if artifact_root is not None
        else session_path / "collected" / "promotion"
    )
    profile_evidence = {}
    page_evidence = {}
    for source, target in (
        (promotion_root / "selector-profile.json", profile_evidence),
        (promotion_root / "store-page-evidence.json", page_evidence),
    ):
        if source.is_file():
            try:
                target.update(_read_json(source))
            except (OSError, ValueError, InteractionConflict):
                pass
    detail = str(error)
    _write_json(
        path,
        {
            "schema_version": 1,
            "session_id": handoff["session_id"],
            "stage_id": "setup",
            "revision": handoff["revision"],
            "input_sha256": handoff["input_sha256"],
            "status": "needs_manual_review",
            "reason_code": (
                "SELECTOR_INVALID"
                if isinstance(error, SelectorInvalidError)
                else "SELECTOR_PROFILE_INVALID"
            ),
            "detail": detail,
            "action": "verify_browser_outcome",
            "target_field": detail.split(":", 1)[0],
            "retry_count": 2,
            "elapsed_ms": 0,
            "page_url": str(page_evidence.get("page_url", "")),
            "selector_profile": {
                "name": str(profile_evidence.get("profile_name", "")),
                "version": str(
                    profile_evidence.get("profile_version", "")
                ),
                "sha256": str(
                    profile_evidence.get("profile_sha256", "")
                ),
            },
            "overlay_summary": (
                "recognized_guide_or_unknown_overlay"
                if "popup_blocked" in detail
                else ""
            ),
            "sensitive_content_persisted": False,
            "safe_evidence_reference": "attempt worker.log and selector-error.json",
            "reproducible": True,
            "repair_boundary": (
                "Use Playwright to reproduce against the real DOM, then repair "
                "the maintained selector profile or collector and rerun "
                "supplement --scan-mode high-value."
            ),
            "recorded_at": _now_iso(),
        },
    )
    return path


def _write_selector_failure_result(
    store: SessionStore,
    session_id: str,
    handoff: dict[str, Any],
    claim: dict[str, Any],
    error: SelectorConfigError,
    *,
    attempt_id: str | None = None,
    additional_evidence: tuple[Any, ...] | list[Any] = (),
) -> dict[str, Any]:
    """Persist a retryable selector failure and release the exact claim."""

    evidence = _persist_selector_error(
        store._session_path(session_id), handoff, error
    )
    missing = str(error).split(":", 1)[0] == "SELECTOR_PROFILE_NOT_FOUND"
    result = store.write_result(
        session_id,
        "setup",
        int(handoff["revision"]),
        str(handoff["input_sha256"]),
        status="needs_user_input",
        summary=(
            "需要在配置页设置并验证生产选择器"
            if missing
            else "生产选择器配置无效"
        ),
        blocking_reasons=[str(error)],
        evidence=[str(evidence), *(str(item) for item in additional_evidence)],
        next_action="在前端修复生产选择器后恢复同一会话",
        claim_id=str(claim["claim_id"]),
        attempt_id=(
            attempt_id
            or str(claim.get("attempt_id", "")).strip()
            or None
        ),
    )
    return {"status": "needs_user_input", "result": result}


def _publish_attempt(
    session_path: Path,
    *,
    attempt_id: str,
    checkpoint: Path,
    output: Path,
    selector_evidence: Path,
    page_evidence: Path,
    pagination_evidence: Path,
    checkpoint_context: dict[str, Any],
    expected_rows: int,
) -> dict[str, str]:
    """Validate and atomically project one successful attempt as current."""

    pagination_document = (
        read_json(pagination_evidence)
        if pagination_evidence.is_file()
        else {}
    )
    pagination_events = (
        pagination_document.get("events", [])
        if isinstance(pagination_document, dict)
        else []
    )
    origin_verified = any(
        isinstance(event, dict)
        and event.get("event_type") == "origin"
        and event.get("current_page") == 1
        for event in pagination_events
    )
    terminal_verified = any(
        isinstance(event, dict)
        and event.get("event_type") == "terminal"
        and event.get("current_page") == event.get("terminal_page")
        and event.get("next_enabled") is False
        for event in pagination_events
    )
    checkpoint_document = validate_checkpoint_identity(
        checkpoint,
        {"scan_mode": "high-value", **checkpoint_context},
    )
    if (
        not checkpoint_document
        or checkpoint_document.get("status") != "complete"
        or checkpoint_document.get("row_count") != expected_rows
        or sha256_file(output)
        != checkpoint_document.get("output_sha256")
        or not pagination_evidence.is_file()
        or sha256_file(pagination_evidence)
        != checkpoint_document.get("pagination_evidence_sha256")
        or not origin_verified
        or not terminal_verified
    ):
        raise CheckpointIdentityError("ATTEMPT_PUBLICATION_INVALID")
    rows = read_backend_status(output)
    product_ids = [str(row.get("商品ID", "")).strip() for row in rows]
    if (
        len(rows) != expected_rows
        or any(not value for value in product_ids)
        or len(set(product_ids)) != len(product_ids)
    ):
        raise CheckpointIdentityError(
            "ATTEMPT_PUBLICATION_PRODUCT_ID_INVALID"
        )
    current_attempt = read_json_object(
        session_path
        / "collected"
        / "promotion"
        / "current-attempt.json"
    )
    if (
        current_attempt is not None
        and current_attempt.get("attempt_id") != attempt_id
    ):
        raise InteractionConflict("COLLECTION_ATTEMPT_STALE")
    promotion_root = session_path / "collected" / "promotion"
    current_root = promotion_root / "current"
    projections = {
        "promotion_status": current_root / output.name,
        "checkpoint": current_root / checkpoint.name,
        "selector_evidence": current_root / selector_evidence.name,
        "page_evidence": current_root / page_evidence.name,
        "pagination_evidence": current_root / pagination_evidence.name,
    }
    for source, destination in (
        (output, projections["promotion_status"]),
        (checkpoint, projections["checkpoint"]),
        (selector_evidence, projections["selector_evidence"]),
        (page_evidence, projections["page_evidence"]),
        (pagination_evidence, projections["pagination_evidence"]),
        (output, promotion_root / output.name),
        (checkpoint, promotion_root / checkpoint.name),
        (selector_evidence, promotion_root / selector_evidence.name),
        (page_evidence, promotion_root / page_evidence.name),
        (pagination_evidence, promotion_root / pagination_evidence.name),
    ):
        atomic_write_bytes(destination, source.read_bytes())
    atomic_write_json(
        current_root / "publication.json",
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            **checkpoint_context,
            "row_count": expected_rows,
            "csv_sha256": sha256_file(output),
            "checkpoint_sha256": sha256_file(checkpoint),
            "pagination_evidence_sha256": sha256_file(
                pagination_evidence
            ),
            "published_at": _now_iso(),
        },
    )
    return {key: str(value) for key, value in projections.items()}


def process_setup_collection(
    *,
    runs_root: Path,
    session_id: str,
    runtime: RuntimeConfig,
    selectors_path: Path | None = None,
    cdp_url: str | None = None,
    claimant_id: str = "codex-agent",
    attempt_id: str | None = None,
    progress_callback: Callable[..., None] | None = None,
    page: Any | None = None,
    page_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Process one exact setup handoff through stage-two hydration."""

    store = SessionStore(runs_root)
    handoff, claim = _claim_setup(store, session_id, claimant_id)
    if "completed_result" in claim:
        return {
            "status": "completed",
            "reused": True,
            "result": claim["completed_result"],
        }
    claim_id = str(claim["claim_id"])
    active_attempt_id = str(claim.get("attempt_id", "")).strip()
    if attempt_id and active_attempt_id and attempt_id != active_attempt_id:
        raise InteractionConflict("COLLECTION_ATTEMPT_STALE")
    attempt_id = attempt_id or active_attempt_id or None
    session_path = store._session_path(session_id)
    setup_path = store._stage_path(session_id, "setup")
    setup_input = _read_json(setup_path / "input.json")
    if _input_hash(setup_path / "input.json") != handoff["input_sha256"]:
        raise InteractionConflict("SETUP_INPUT_HASH_MISMATCH")

    try:
        products_path, _, products, scan_summary = _snapshot_inputs(
            session_path,
            setup_input,
            session_id=session_id,
            revision=int(handoff["revision"]),
            input_sha256=str(handoff["input_sha256"]),
        )
    except (OSError, SchemaError, InteractionConflict) as error:
        result = store.write_result(
            session_id,
            "setup",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="blocked",
            summary="商品表或输入快照无法安全处理",
            blocking_reasons=[str(error)],
            claim_id=claim_id,
            attempt_id=attempt_id,
        )
        return {"status": "blocked", "result": result}

    selected_profile_path = selectors_path or runtime.selectors_file
    if selected_profile_path is None:
        error = SelectorConfigError("SELECTOR_PROFILE_NOT_FOUND")
        return _write_selector_failure_result(
            store,
            session_id,
            handoff,
            claim,
            error,
            attempt_id=attempt_id,
        )
    try:
        if progress_callback is not None:
            progress_callback("validating_profile")
        profile = load_selector_profile(
            selected_profile_path,
            purpose="high_value_collection",
            production=True,
        )
    except SelectorConfigError as error:
        return _write_selector_failure_result(
            store,
            session_id,
            handoff,
            claim,
            error,
            attempt_id=attempt_id,
        )

    collected_root = session_path / "collected" / "promotion"
    artifact_root = (
        attempt_path(session_path, attempt_id)
        if attempt_id
        else collected_root
    )
    selector_evidence = artifact_root / "selector-profile.json"
    _write_json(
        selector_evidence,
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "setup",
            "revision": handoff["revision"],
            "input_sha256": handoff["input_sha256"],
            "profile_name": profile.name,
            "profile_version": profile.version,
            "profile_sha256": profile.sha256,
            "purpose": profile.purpose,
            "profile_path": str(profile.path),
            "validated_at": _now_iso(),
        },
    )
    output = artifact_root / "promotion-material-status.csv"
    checkpoint = artifact_root / "promotion-material-status.checkpoint.json"
    human_checkpoint = artifact_root / "human-checkpoint.json"
    page_evidence_path = artifact_root / "store-page-evidence.json"
    pagination_evidence = artifact_root / "pagination-evidence.json"
    collected_at = _now_iso()
    collected_rows: list[dict[str, str]] = []
    checkpoint_context = {
        "session_id": session_id,
        "revision": handoff["revision"],
        "input_sha256": handoff["input_sha256"],
        "selector_profile_sha256": profile.sha256,
        **({"attempt_id": attempt_id} if attempt_id else {}),
        "target_store": str(
            setup_input.get("values", {}).get("store", "")
        ).strip(),
    }

    def renew_claim(
        _page_number: int, _rows: list[dict[str, str]]
    ) -> None:
        store.renew_processing_claim(session_id, "setup", claim_id)

    def report_pagination(event: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        event_type = str(event.get("event_type", ""))
        current_page = event.get("current_page")
        terminal_page = event.get("terminal_page")
        progress_callback(
            (
                "verifying_terminal"
                if event_type in {"terminal", "failure"}
                else "normalizing_pagination"
            ),
            action=f"pagination_{event_type or 'observation'}",
            target=(
                f"page:{current_page}"
                if current_page is not None
                else "pagination"
            ),
            retry_count=0,
            **(
                {"current_page": int(current_page)}
                if isinstance(current_page, int)
                else {}
            ),
            **(
                {"observed_page": int(current_page)}
                if isinstance(current_page, int)
                else {}
            ),
            **(
                {"pagination_origin_page": 1}
                if event_type == "origin"
                else {}
            ),
            **(
                {"terminal_page": int(terminal_page)}
                if isinstance(terminal_page, int)
                else {}
            ),
            terminal_proof=event_type == "terminal",
            pagination_reason_code=str(
                event.get("reason_code", "")
            ),
            next_recovery=(
                str(event.get("recovery_guidance", ""))
                or "根据分页证据恢复同一采集任务"
            ),
        )

    def collect(live_page: Any) -> list[dict[str, str]]:
        collection_selectors = merge_default_safe_popup_selectors(
            profile.selectors
        )

        def wait_for_human_check(
            target_page: Any,
            page_number: int,
            location: str,
        ) -> None:
            selector = str(
                collection_selectors.get("human_check", "")
            ).strip()
            if not selector or not human_check_visible(target_page, selector):
                return
            observed_page = max(1, int(page_number))
            current = read_json_object(human_checkpoint) or {
                "schema_version": 1,
                **checkpoint_context,
                "events": [],
            }
            events = list(current.get("events", []))
            data_checkpoint = read_json_object(checkpoint) or {}
            detected_at = _now_iso()
            events.append(
                {
                    "event_type": "detected",
                    "reason_code": "HUMAN_CHECK",
                    "location": location,
                    "current_page": observed_page,
                    "last_completed_page": int(
                        data_checkpoint.get("last_completed_page") or 0
                    ),
                    "recorded_at": detected_at,
                }
            )
            current.update(
                {
                    "status": "waiting_user",
                    "reason_code": "HUMAN_CHECK",
                    "location": location,
                    "current_page": observed_page,
                    "last_completed_page": int(
                        data_checkpoint.get("last_completed_page") or 0
                    ),
                    "row_count": int(data_checkpoint.get("row_count") or 0),
                    "events": events,
                    "updated_at": detected_at,
                }
            )
            _write_json(human_checkpoint, current)
            elapsed_ms = 0
            while human_check_visible(target_page, selector):
                store.renew_processing_claim(session_id, "setup", claim_id)
                if progress_callback is not None:
                    progress_callback(
                        "waiting_human_check",
                        action="complete_human_check",
                        target=f"page:{observed_page}",
                        retry_count=0,
                        elapsed_ms=elapsed_ms,
                        current_page=observed_page,
                        last_completed_page=current[
                            "last_completed_page"
                        ],
                        row_count=current["row_count"],
                        last_checkpoint_at=detected_at,
                        pagination_reason_code="HUMAN_CHECK",
                        next_recovery=(
                            "请在 CDP Chrome 完成滑动验证；验证通过后会自动继续采集"
                        ),
                    )
                target_page.wait_for_timeout(1_000)
                elapsed_ms += 1_000
            resolved_at = _now_iso()
            current["events"] = [
                *events,
                {
                    "event_type": "resolved",
                    "reason_code": "HUMAN_CHECK_RESOLVED",
                    "location": location,
                    "current_page": observed_page,
                    "recorded_at": resolved_at,
                },
            ]
            current.update(
                {
                    "status": "resolved",
                    "reason_code": "HUMAN_CHECK_RESOLVED",
                    "resolved_at": resolved_at,
                    "updated_at": resolved_at,
                }
            )
            _write_json(human_checkpoint, current)
            if progress_callback is not None:
                progress_callback(
                    "collecting_page",
                    action="resume_after_human_check",
                    target=f"page:{observed_page}",
                    retry_count=0,
                    current_page=observed_page,
                    pagination_reason_code="HUMAN_CHECK_RESOLVED",
                    next_recovery="继续当前 attempt 的分页采集",
                )

        def wait_on_main_page(page_number: int, location: str) -> None:
            wait_for_human_check(live_page, page_number, location)

        def run_random_action(page_number: int) -> dict[str, Any]:
            store.renew_processing_claim(session_id, "setup", claim_id)
            if progress_callback is not None:
                progress_callback(
                    "random_action",
                    action="inspect_random_slot",
                    target=f"page:{page_number}",
                    retry_count=0,
                    current_page=page_number,
                    next_recovery="随机查看动作结束后继续当前分页采集",
                )
            return perform_random_collection_action(
                live_page,
                rows_selector=str(collection_selectors["promotion_rows"]),
                popup_selectors=collection_selectors,
                wait_for_human_check=(
                    lambda target_page, location: wait_for_human_check(
                        target_page, page_number, location
                    )
                ),
            )

        if attempt_id:
            _archive_prior_attempt_checkpoint(
                collected_root
                / "promotion-material-status.checkpoint.json",
                collected_root / "promotion-material-status.csv",
                checkpoint_context=checkpoint_context,
                attempt_id=attempt_id,
            )
        if progress_callback is not None:
            progress_callback(
                "validating_profile",
                action="validate_selector_profile",
                target=profile.name,
                retry_count=0,
                next_recovery="修复选择器后恢复同一 session",
            )
        wait_on_main_page(0, "before_collection_validation")
        validated_page = validate_collection_page(
            live_page,
            collection_selectors,
            expected_store=str(
                setup_input.get("values", {}).get("store", "")
            ),
            profile_name=profile.name,
            profile_version=profile.version,
            profile_sha256=profile.sha256,
        )
        evidence_document = {
            **validated_page,
            "session_id": session_id,
            "stage_id": "setup",
            "revision": handoff["revision"],
            "input_sha256": handoff["input_sha256"],
        }
        _write_json(page_evidence_path, evidence_document)
        if progress_callback is not None:
            progress_callback(
                "settling_popups",
                action="close_safe_popup",
                target="recognized_safe_guides",
                retry_count=0,
                next_recovery="查看弹窗证据并恢复同一 attempt",
            )
        rows = collect_supplement_material_status(
            live_page,
            collection_selectors,
            scan_mode="high-value",
            output=output,
            checkpoint=checkpoint,
            collected_at=collected_at,
            max_pages=None,
            checkpoint_context=checkpoint_context,
            pagination_evidence=pagination_evidence,
            on_pagination_event=report_pagination,
            human_check_waiter=wait_on_main_page,
            random_action=run_random_action,
            random_interval_picker=random_page_interval,
            before_checkpoint=renew_claim,
            on_checkpoint=(
                (
                    lambda page_number, values: progress_callback(
                        "writing_checkpoint",
                        action="write_checkpoint",
                        target=f"page:{page_number}",
                        retry_count=0,
                        current_page=page_number + 1,
                        last_completed_page=page_number,
                        row_count=len(values),
                        last_checkpoint_at=_now_iso(),
                        next_recovery="从最后完整页之后恢复",
                    )
                )
                if progress_callback is not None
                else None
            ),
            on_phase=(
                (
                    lambda phase, page_number: progress_callback(
                        phase,
                        action={
                            "opening_promotion": "open_promotion_tab",
                            "selecting_high_value": "select_high_value_filter",
                            "collecting_page": "collect_page_rows",
                        }.get(phase, phase),
                        target=(
                            f"page:{page_number}"
                            if page_number is not None
                            else "promotion_materials"
                        ),
                        retry_count=0,
                        next_recovery="查看当前动作证据并恢复同一 attempt",
                        **(
                            {"current_page": page_number}
                            if page_number is not None
                            else {}
                        ),
                    )
                )
                if progress_callback is not None
                else None
            ),
        )
        evidence_document["field_results"] = {
            field: "observed_during_collection"
            for field in (
                "promotion_tab",
                "high_value_filter",
                "promotion_rows",
                "promotion_next_page",
            )
        }
        evidence_document["collection_validated_at"] = _now_iso()
        evidence_document["safe_actions"] = list(
            getattr(live_page, "_tmall_collection_events", [])
        )
        _write_json(page_evidence_path, evidence_document)
        return rows

    reused_collection = False
    try:
        reused_rows = _reuse_published_collection(
            session_path,
            setup_path,
            setup_input,
            attempt_id=attempt_id,
            artifact_root=artifact_root,
            output=output,
            checkpoint=checkpoint,
            page_evidence=page_evidence_path,
            pagination_evidence=pagination_evidence,
            checkpoint_context=checkpoint_context,
        )
        if reused_rows is not None:
            collected_rows = reused_rows
            reused_collection = True
            if progress_callback is not None:
                progress_callback(
                    "building_completeness",
                    action="reuse_completed_collection",
                    target="stage:completeness",
                    retry_count=0,
                    next_recovery="继续生成当前完整度巡检结果",
                )
        elif page is not None:
            collected_rows = collect(page)
        elif page_factory is not None:
            collected_rows = collect(
                page_factory(cdp_url or runtime.cdp_url)
            )
        else:
            material_center_url = recommendation_material_center_url(
                profile.material_center_url or runtime.material_center_url
            )
            ensure_cdp_browser(
                executable=runtime.browser_executable,
                profile_dir=runtime.browser_profile_dir,
                cdp_url=cdp_url or runtime.cdp_url,
                material_center_url=material_center_url,
            )
            if progress_callback is not None:
                progress_callback(
                    "connecting_cdp",
                    action="connect_cdp",
                    target=cdp_url or runtime.cdp_url,
                    retry_count=0,
                    next_recovery="在 CDP Chrome 完成登录后恢复",
                )
            with open_cdp_page(
                cdp_url or runtime.cdp_url,
                material_center_url,
            ) as live_page:
                collected_rows = collect(live_page)
    except CdpUnavailable as error:
        result = store.write_result(
            session_id,
            "setup",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="needs_user_input",
            summary="需要准备用户控制的 CDP Chrome",
            blocking_reasons=[str(error).split(":", 1)[0]],
            next_action=(
                "在配置页查看 CDP 状态，配置浏览器后恢复同一 session"
            ),
            claim_id=claim_id,
            attempt_id=attempt_id,
        )
        return {"status": "needs_user_input", "result": result}
    except CheckpointIdentityError as error:
        result = store.write_result(
            session_id,
            "setup",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="blocked",
            summary="已有采集 checkpoint 与当前任务身份不一致",
            blocking_reasons=[
                str(error).split(":", 1)[0],
                str(error),
            ],
            evidence=[str(checkpoint)],
            next_action=(
                "保留旧 checkpoint 作为证据，为当前输入创建新的隔离采集输出"
            ),
            claim_id=claim_id,
            attempt_id=attempt_id,
        )
        return {"status": "blocked", "result": result}
    except (LoginInteractionRequired, HumanCheckRequired) as error:
        evidence = artifact_root / "login-required.json"
        reason_code = (
            "HUMAN_CHECK"
            if isinstance(error, HumanCheckRequired)
            else "LOGIN_INTERACTION_REQUIRED"
        )
        _write_json(
            evidence,
            {
                "schema_version": 1,
                "session_id": session_id,
                "stage_id": "setup",
                "revision": handoff["revision"],
                "input_sha256": handoff["input_sha256"],
                "status": "processing",
                "reason_code": reason_code,
                "detail": str(error),
                "cdp_url": cdp_url or runtime.cdp_url,
                "material_center_url": (
                    profile.material_center_url
                    or runtime.material_center_url
                ),
                "recorded_at": _now_iso(),
            },
        )
        diagnostic = write_agent_diagnostic(
            session_path,
            session_id=session_id,
            stage_id="setup",
            revision=int(handoff["revision"]),
            input_sha256=str(handoff["input_sha256"]),
            reason_codes=[reason_code],
            phase="collection_login_check",
            message=str(error),
            evidence=[evidence],
        )
        return {
            "status": "processing",
            "reason_code": reason_code,
            "evidence": str(evidence),
            "agent_diagnostic": diagnostic,
            "processing_claim": store.processing_claim(
                session_id, "setup"
            ),
        }
    except StoreIdentityError as error:
        result = store.write_result(
            session_id,
            "setup",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="blocked",
            summary="当前登录店铺与目标店铺不一致",
            blocking_reasons=["STORE_IDENTITY_MISMATCH", str(error)],
            claim_id=claim_id,
            attempt_id=attempt_id,
        )
        return {"status": "blocked", "result": result}
    except SelectorInvalidError as error:
        evidence = _persist_selector_error(
            session_path,
            handoff,
            error,
            artifact_root=artifact_root,
        )
        result = store.write_result(
            session_id,
            "setup",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="needs_user_input",
            summary="现有 supplement 采集器需要 Playwright 诊断修复",
            blocking_reasons=["SELECTOR_INVALID", str(error)],
            evidence=[str(evidence), str(checkpoint)],
            next_action=(
                "使用 Playwright 复现并修复现有选择器或采集器，"
                "通过回归测试后重新运行 supplement --scan-mode high-value"
            ),
            claim_id=claim_id,
            attempt_id=attempt_id,
        )
        return {"status": "needs_user_input", "result": result}

    if progress_callback is not None:
        progress_callback(
            "building_completeness",
            action="build_completeness_matrix",
            target="stage:completeness",
            retry_count=0,
            next_recovery="重新校验当前 attempt 输出",
        )
    published = {
        "promotion_status": str(output),
        "checkpoint": str(checkpoint),
        "selector_evidence": str(selector_evidence),
        "page_evidence": str(page_evidence_path),
        "pagination_evidence": str(pagination_evidence),
    }
    if attempt_id:
        store.renew_processing_claim(session_id, "setup", claim_id)
        published = _publish_attempt(
            session_path,
            attempt_id=attempt_id,
            checkpoint=checkpoint,
            output=output,
            selector_evidence=selector_evidence,
            page_evidence=page_evidence_path,
            pagination_evidence=pagination_evidence,
            checkpoint_context=checkpoint_context,
            expected_rows=len(collected_rows),
        )
    lark_sync_evidence = session_path / "inputs" / "lark-product-sync.json"
    lark_sync = sync_product_metadata_from_snapshot(
        products,
        _product_metadata_snapshot_path(runtime),
        enabled=runtime.lark_base.enabled,
        evidence_path=(
            lark_sync_evidence if runtime.lark_base.enabled else None
        ),
    )
    products = list(lark_sync.records)
    lark_sync_document = lark_sync.evidence()
    scan_summary["lark_product_sync"] = lark_sync_document
    _write_json(session_path / "inputs" / "scan-summary.json", scan_summary)

    matrix = build_completeness_matrix(
        collected_rows,
        products=[record.raw for record in products],
    )
    matrix["lark_product_sync"] = _public_lark_product_sync(
        lark_sync_document
    )
    pagination_document = read_json(pagination_evidence)
    pagination_events = pagination_document.get("events", [])
    origin_event = next(
        (
            event
            for event in pagination_events
            if event.get("event_type") == "origin"
        ),
        {},
    )
    terminal_event = next(
        (
            event
            for event in reversed(pagination_events)
            if event.get("event_type") == "terminal"
        ),
        {},
    )
    failure_event = next(
        (
            event
            for event in reversed(pagination_events)
            if event.get("event_type") == "failure"
        ),
        {},
    )
    matrix["pagination"] = {
        "origin_page": origin_event.get("current_page"),
        "current_page": terminal_event.get("current_page"),
        "terminal_page": terminal_event.get("terminal_page"),
        "terminal_proof": bool(terminal_event),
        "reason_code": (
            failure_event.get("reason_code")
            or terminal_event.get("reason_code")
            or origin_event.get("reason_code")
        ),
        "evidence": published["pagination_evidence"],
    }
    matrix["product_row_anomalies"] = {
        "row_count": scan_summary["row_count"],
        "valid_row_count": scan_summary["valid_row_count"],
        "blocked_row_count": scan_summary["blocked_row_count"],
        "reason_codes_by_row": scan_summary["reason_codes_by_row"],
    }
    completeness_path = (
        store._stage_path(session_id, "completeness")
        / "completeness-matrix.json"
    )
    _write_json(completeness_path, matrix)
    result = store.write_result(
        session_id,
        "setup",
        int(handoff["revision"]),
        str(handoff["input_sha256"]),
        status="completed",
        summary=f"已采集 {len(collected_rows)} 个搜推高价值商品",
        evidence=[
            published["selector_evidence"],
            published["page_evidence"],
            published["promotion_status"],
            published["checkpoint"],
            published["pagination_evidence"],
            *([str(lark_sync_evidence)] if lark_sync_evidence.is_file() else []),
            *([str(human_checkpoint)] if human_checkpoint.is_file() else []),
            str(completeness_path),
        ],
        next_action="在第二阶段批量选择待补充商品",
        data={
            "promotion_status": published["promotion_status"],
            "checkpoint": published["checkpoint"],
            "human_checkpoint": (
                str(human_checkpoint) if human_checkpoint.is_file() else ""
            ),
            "pagination_evidence": published["pagination_evidence"],
            "pagination": matrix["pagination"],
            "completeness_matrix": str(completeness_path),
            "product_row_anomalies": matrix[
                "product_row_anomalies"
            ],
            "lark_product_sync": lark_sync_document,
        },
        claim_id=claim_id,
        attempt_id=attempt_id,
    )
    if attempt_id:
        atomic_write_json(
            artifact_root / "result.json",
            result,
        )
    completeness_state = store.load_session(session_id)
    completeness_revision = int(
        completeness_state["stages"]["completeness"]["revision"]
    )
    store.write_review_context(
        session_id,
        "completeness",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "completeness",
            "revision": completeness_revision,
            "status": "needs_user_input",
            "summary": result["summary"],
            "blocking_reasons": [],
            "evidence": result["evidence"],
            "next_action": "选择商品并提交给工作台",
            "created_at": _now_iso(),
            "data": matrix,
        },
    )
    state = store.load_session(session_id)
    state["current_stage"] = "completeness"
    store._write_session_state(session_id, state)
    return {
        "status": "completed",
        "reused": reused_collection,
        "result": result,
        "completeness": matrix,
    }
