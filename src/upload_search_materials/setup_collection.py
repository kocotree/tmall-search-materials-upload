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
from .material_state import build_completeness_matrix
from .collection_runtime import attempt_path, read_json_object
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


def _archive_prior_attempt_checkpoint(
    checkpoint: Path,
    output: Path,
    *,
    checkpoint_context: dict[str, Any],
    attempt_id: str | None,
) -> Path | None:
    if not attempt_id or not checkpoint.is_file():
        return None
    prior = validate_checkpoint_identity(
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
    if not prior:
        return None
    prior_attempt_id = str(prior.get("attempt_id", "")).strip()
    if not prior_attempt_id or prior_attempt_id == attempt_id:
        return None
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
            source.unlink()
        else:
            source.replace(destination)
    return archive


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
        evidence=[str(evidence)],
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
        def wait_for_human_check(
            target_page: Any,
            page_number: int,
            location: str,
        ) -> None:
            selector = str(profile.selectors.get("human_check", "")).strip()
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
                rows_selector=str(profile.selectors["promotion_rows"]),
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
        page_evidence = validate_collection_page(
            live_page,
            profile.selectors,
            expected_store=str(
                setup_input.get("values", {}).get("store", "")
            ),
            profile_name=profile.name,
            profile_version=profile.version,
            profile_sha256=profile.sha256,
        )
        evidence_document = {
            **page_evidence,
            "session_id": session_id,
            "stage_id": "setup",
            "revision": handoff["revision"],
            "input_sha256": handoff["input_sha256"],
        }
        page_evidence_path = (
            artifact_root / "store-page-evidence.json"
        )
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
            profile.selectors,
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

    try:
        if page is not None:
            collected_rows = collect(page)
        elif page_factory is not None:
            collected_rows = collect(
                page_factory(cdp_url or runtime.cdp_url)
            )
        else:
            material_center_url = (
                profile.material_center_url
                or runtime.material_center_url
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
        "page_evidence": str(artifact_root / "store-page-evidence.json"),
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
            page_evidence=artifact_root / "store-page-evidence.json",
            pagination_evidence=pagination_evidence,
            checkpoint_context=checkpoint_context,
            expected_rows=len(collected_rows),
        )
    matrix = build_completeness_matrix(
        collected_rows,
        products=[record.raw for record in products],
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
        "reused": False,
        "result": result,
        "completeness": matrix,
    }
