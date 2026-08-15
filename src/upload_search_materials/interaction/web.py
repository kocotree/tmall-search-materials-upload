"""HTTP routes for the durable local interaction session store."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import secrets
import time
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request, send_file
import yaml

from ..browser.config import SelectorConfigError, load_selector_profile
from ..browser.material_page import prepare_high_value_validation_page
from ..agent_diagnostics import write_exception_diagnostic
from ..browser.session import (
    CdpUnavailable,
    ensure_cdp_browser,
    inspect_cdp_endpoint,
    open_cdp_page,
)
from ..copy_draft_workflow import create_copy_draft_request
from ..collection_readiness import (
    build_collection_readiness,
    create_selector_candidate,
    promote_selector_candidate,
    validate_selector_candidate,
)
from ..collection_worker import (
    collection_status as get_collection_status,
    launch_collection_worker,
)
from ..collection_runtime import CollectionRuntimeError
from ..agent_handoff import (
    AgentRequestError,
    ai_default_slot_planning_enabled,
    cancel_agent_request,
    create_agent_request,
    find_equivalent_agent_request,
    read_agent_request,
    supersede_agent_request,
    three_step_slot_ui_enabled,
)
from ..assets import build_image_preview
from ..asset_matching_workflow import (
    ALL_FOLDERS_REJECTED,
    FOLDER_REVIEW,
    GALLERY_IDENTITY_STALE,
    IMAGE_SELECTION,
    PRODUCT_IMAGE_SHORTAGE,
    gallery_covers_folder_decisions,
    infer_workflow_step,
)
from ..deterministic_slot_planning import build_deterministic_slot_plan
from ..dry_run_workflow import advance_slots_copy_after_submit
from ..desktop_launcher import inspect_login_browser
from ..final_material_handoff import process_final_material_handoff
from ..image_compliance import default_image_policy
from ..gallery_jobs import (
    create_or_reuse_gallery_job,
    migrate_legacy_gallery_handoff,
    invalidate_gallery_if_scope_expands,
    read_gallery_job,
    reconcile_gallery_job,
)
from ..material_executor_launcher import MaterialExecutorLaunchError
from ..io_tables import SchemaError, read_product_csv, validate_product_records
from ..image_review import (
    build_image_review_data,
    normalize_review_decisions,
    review_context_is_stale,
)
from ..slot_planning import (
    build_rule_slot_plan,
    materialize_confirmed_slot_plan,
    slot_plan_sha256,
    slot_context_is_stale,
    validate_final_output,
    validate_slot_assignments,
)
from ..slot_workflow import (
    ai_assignments,
    mark_manual_override,
    read_current_slot_plan,
    response_sha256,
    save_current_slot_plan,
    two_step_workflow_page,
    workflow_page,
)
from ..copywriting import validate_copy
from werkzeug.exceptions import BadRequest, NotFound, UnsupportedMediaType

from ..runtime_config import (
    RuntimeConfig,
    inspect_image_sources,
    load_runtime_config,
    save_image_sources,
    save_selector_profile_path,
)
from ..nas_sources import (
    browse_nas_folders,
    check_nas_source,
    launch_nas_mount,
    load_nas_sources,
)
from ..platform_support import AssetSourceUnavailable
from ..persistence import PersistenceAccessDenied, read_json
from ..product_selection_handoff import (
    ProductSelectionProcessingError,
    process_product_selection_handoff,
)
from ..runtime_identity import (
    LocalResourceIdentityMismatch,
    require_local_resource_identity,
)
from ..decision_modes import get_decision_boundary
from .folder_picker import FolderPickerError, choose_directory
from .fallback import safety_context
from .session import (
    AGENT_WAIT_SEGMENT_SECONDS,
    InteractionConflict,
    InteractionPathError,
    SessionStore,
)
from .workflow_dispatcher import WorkflowDispatcher, WorkflowProcessor
from .stages import (
    FALLBACK_REASON_CODES,
    STAGES,
    FieldDefinition,
    StageDefinition,
    get_stage,
)


RESULTS_USER_ACTION_STATUSES = frozenset({"needs_user_input", "blocked"})
SELECTION_PREFLIGHT_ALGORITHM_VERSION = 2


def _input_quality_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "status": "blocked",
            "reason_code": "PRODUCTS_FILE_REQUIRED",
            "total": 0,
            "valid": 0,
            "duplicate_id": 0,
            "missing_id": 0,
            "invalid_id": 0,
            "excluded": 0,
            "processable": 0,
        }
    try:
        report = validate_product_records(read_product_csv(path))
    except (OSError, SchemaError, UnicodeError) as error:
        return {
            "status": "blocked",
            "reason_code": "PRODUCT_TABLE_BATCH_BLOCKED",
            "message": str(error),
            "total": 0,
            "valid": 0,
            "duplicate_id": 0,
            "missing_id": 0,
            "invalid_id": 0,
            "excluded": 0,
            "processable": 0,
        }
    reason_counts = {
        code: sum(
            code in reasons
            for reasons in report.reason_codes_by_row.values()
        )
        for code in (
            "DUPLICATE_PRODUCT_ID",
            "MISSING_PRODUCT_ID",
            "INVALID_PRODUCT_ID",
        )
    }
    valid = report.row_count - report.blocked_row_count
    return {
        "status": "ready" if valid else "blocked",
        "reason_code": (
            "INPUT_QUALITY_READY"
            if valid
            else "NO_PROCESSABLE_PRODUCTS"
        ),
        "total": report.row_count,
        "valid": valid,
        "duplicate_id": reason_counts["DUPLICATE_PRODUCT_ID"],
        "missing_id": reason_counts["MISSING_PRODUCT_ID"],
        "invalid_id": reason_counts["INVALID_PRODUCT_ID"],
        "excluded": report.blocked_row_count,
        "processable": valid,
        "reason_codes_by_row": {
            str(row): list(reasons)
            for row, reasons in report.reason_codes_by_row.items()
        },
    }


def create_app(
    runs_root: Path,
    runtime_config: RuntimeConfig | None = None,
    *,
    enforce_stage_order: bool = True,
    service_identity: dict[str, Any] | None = None,
    material_executor_launcher: (
        Callable[[RuntimeConfig, str], dict[str, Any]] | None
    ) = None,
    managed_session_id: str | None = None,
) -> Flask:
    """Create the local interaction UI and JSON API backed by ``runs_root``."""

    app = Flask(__name__)
    store = SessionStore(runs_root)
    runtime = runtime_config or load_runtime_config()
    expected_runtime_identity = (
        (service_identity or {}).get("runtime_identity")
    )
    workflow_dispatcher: WorkflowDispatcher | None = None

    def notify_workflow_dispatcher(session_id: str) -> None:
        if (
            workflow_dispatcher is not None
            and workflow_dispatcher.session_id == session_id
        ):
            try:
                workflow_dispatcher.notify()
            except (InteractionConflict, OSError, TypeError, ValueError):
                # The handoff/request is already durable. The dispatcher's
                # periodic recovery scan will pick it up if this wake-up races
                # a transient status or filesystem update.
                pass

    def workflow_dispatch_status(session_id: str) -> dict[str, Any]:
        if (
            workflow_dispatcher is not None
            and workflow_dispatcher.session_id == session_id
        ):
            return workflow_dispatcher.public_status()
        return {
            "online": False,
            "status": "unmanaged",
            "stage_id": "",
            "action": "",
            "reason_code": "",
            "updated_at": None,
        }

    def configured_nas_sources():
        if runtime.nas_sources_file is None:
            return {}
        return load_nas_sources(runtime.nas_sources_file)

    def selected_nas_source(source_id: str):
        sources = configured_nas_sources()
        source = sources.get(source_id)
        if source is None:
            raise AssetSourceUnavailable(
                "NAS_SOURCE_SELECTION_INVALID", f"未知 NAS 来源：{source_id}"
            )
        return source

    def request_material_executor(
        session_id: str,
        job: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if material_executor_launcher is None:
            return job, None
        try:
            launch = material_executor_launcher(runtime, session_id)
            return job, launch
        except MaterialExecutorLaunchError as error:
            with store._session_lock(session_id):
                current = read_gallery_job(store, session_id)
                if (
                    current is not None
                    and current.get("job_id") == job.get("job_id")
                    and current.get("attempt_id") == job.get("attempt_id")
                    and current.get("status") == "queued"
                ):
                    current.update(
                        {
                            "status": "failed",
                            "reason_code": error.reason_code,
                            "message": str(error),
                            "updated_at": datetime.now().astimezone().isoformat(),
                            "lease_expires_at": None,
                            "recovery_action": "重试加载图片",
                        }
                    )
                    store._write_json_atomic(
                        store._stage_path(
                            session_id, "asset_matching"
                        )
                        / "gallery-job.json",
                        current,
                    )
                    job = current
            return job, {
                "status": "failed",
                "reason_code": error.reason_code,
                "message": str(error),
            }

    def require_desktop_identity() -> None:
        if isinstance(expected_runtime_identity, dict):
            require_local_resource_identity(expected_runtime_identity)

    def selection_preflight_policy() -> tuple[dict[str, Any], str]:
        policy = default_image_policy()
        policy_sha256 = hashlib.sha256(
            json.dumps(
                policy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return policy, policy_sha256

    def selection_preflight_cache_path(session_id: str) -> Path:
        return (
            store._stage_path(session_id, "asset_matching")
            / "selection-preflight-cache.json"
        )

    def read_selection_preflight_cache(session_id: str) -> dict[str, Any]:
        path = selection_preflight_cache_path(session_id)
        if not path.is_file():
            return {
                "schema_version": 1,
                "algorithm_version": SELECTION_PREFLIGHT_ALGORITHM_VERSION,
                "entries": {},
            }
        document = store._read_json(path, "selection-preflight-cache")
        if (
            document.get("algorithm_version")
            != SELECTION_PREFLIGHT_ALGORITHM_VERSION
            or not isinstance(document.get("entries"), dict)
        ):
            return {
                "schema_version": 1,
                "algorithm_version": SELECTION_PREFLIGHT_ALGORITHM_VERSION,
                "entries": {},
            }
        return document

    def selection_preflight_identity(
        candidate: dict[str, Any], policy_sha256: str
    ) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "algorithm_version": SELECTION_PREFLIGHT_ALGORITHM_VERSION,
                    "asset_id": str(candidate.get("asset_id", "")),
                    "product_id": str(candidate.get("product_id", "")),
                    "source_sha256": str(candidate.get("sha256", "")),
                    "policy_sha256": policy_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def current_asset_gallery(
        session_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        context = _current_result(store, session_id, "asset_matching", state)
        data = context.get("data") if isinstance(context, dict) else None
        if not isinstance(data, dict) or not isinstance(
            data.get("asset_candidates"), list
        ):
            raise InteractionConflict("current asset gallery is unavailable")
        return data

    def build_single_selection_preflight(
        session_id: str,
        state: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        policy, policy_sha256 = selection_preflight_policy()
        identity = selection_preflight_identity(candidate, policy_sha256)
        cache = read_selection_preflight_cache(session_id)
        cached = cache["entries"].get(str(candidate.get("asset_id", "")))
        if (
            isinstance(cached, dict)
            and cached.get("identity_sha256") == identity
            and isinstance(cached.get("preflight"), dict)
        ):
            return cached
        require_desktop_identity()
        decision = {
            "product_id": str(candidate.get("product_id", "")),
            "asset_id": str(candidate.get("asset_id", "")),
            "sha256": str(candidate.get("sha256", "")),
            "decision": "selected",
            "selection_order": 1,
        }
        preflight = build_image_review_data(
            [candidate],
            [decision],
            policy=policy,
            policy_sha256=policy_sha256,
            asset_matching_revision=int(
                state["stages"]["asset_matching"]["revision"]
            ),
            inspection_cache_path=(
                store._stage_path(session_id, "asset_matching")
                / "source-inspection-cache.json"
            ),
        )
        feedback = _selected_asset_validation_feedback(preflight)
        item = feedback.get("items", [{}])[0]
        entry = {
            "schema_version": 1,
            "algorithm_version": SELECTION_PREFLIGHT_ALGORITHM_VERSION,
            "asset_id": str(candidate.get("asset_id", "")),
            "product_id": str(candidate.get("product_id", "")),
            "source_sha256": str(candidate.get("sha256", "")),
            "policy_sha256": policy_sha256,
            "identity_sha256": identity,
            "status": (
                "blocked"
                if item.get("severity") == "blocked"
                else "warning"
                if item.get("severity") == "warning"
                else "passed"
            ),
            "feasible_ratios": list(item.get("feasible_ratios", [])),
            "message": str(item.get("message", "检查通过")),
            "preflight": preflight,
            "checked_at": datetime.now().astimezone().isoformat(),
        }
        with store._session_lock(session_id):
            cache = read_selection_preflight_cache(session_id)
            cache["algorithm_version"] = SELECTION_PREFLIGHT_ALGORITHM_VERSION
            cache["entries"][entry["asset_id"]] = entry
            cache["updated_at"] = entry["checked_at"]
            store._write_json_atomic(
                selection_preflight_cache_path(session_id), cache
            )
        return entry

    def combine_cached_selection_preflights(
        session_id: str,
        candidates: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        *,
        policy: dict[str, Any],
        policy_sha256: str,
        asset_matching_revision: int,
    ) -> dict[str, Any] | None:
        by_id = {
            str(item.get("asset_id", "")): item
            for item in candidates
            if isinstance(item, dict) and item.get("asset_id")
        }
        cache = read_selection_preflight_cache(session_id)
        records: list[dict[str, Any]] = []
        seen_sha256: set[str] = set()
        duplicate_count = 0
        for decision in sorted(
            decisions,
            key=lambda item: (
                str(item.get("product_id", "")),
                int(item.get("selection_order") or 0),
                str(item.get("asset_id", "")),
            ),
        ):
            candidate = by_id.get(str(decision.get("asset_id", "")))
            if candidate is None:
                return None
            entry = cache["entries"].get(str(candidate.get("asset_id", "")))
            identity = selection_preflight_identity(candidate, policy_sha256)
            if (
                not isinstance(entry, dict)
                or entry.get("identity_sha256") != identity
                or not isinstance(entry.get("preflight"), dict)
                or not entry["preflight"].get("assets")
            ):
                return None
            asset = json.loads(json.dumps(entry["preflight"]["assets"][0]))
            asset["selection_order"] = int(decision.get("selection_order") or 0)
            source_sha256 = str(asset.get("source_sha256", ""))
            duplicate = bool(source_sha256 and source_sha256 in seen_sha256)
            if duplicate:
                duplicate_count += 1
                asset["duplicate"] = True
                asset["status"] = "blocked"
                asset["reason_codes"] = list(
                    dict.fromkeys([*asset.get("reason_codes", []), "DUPLICATE_ASSET"])
                )
            if source_sha256:
                seen_sha256.add(source_sha256)
            records.append(asset)
        return {
            "schema_version": 3,
            "record_type": "suitability_record",
            "asset_matching_revision": asset_matching_revision,
            "policy": policy,
            "policy_sha256": policy_sha256,
            "selected_count": len(records),
            "reviewable_count": sum(item.get("status") != "blocked" for item in records),
            "blocked_count": sum(item.get("status") == "blocked" for item in records),
            "duplicate_count": duplicate_count,
            "assets": records,
            "suitability_records": records,
            "capabilities": {
                "manual_crop": True,
                "ai_crop": False,
                "image_compression": True,
                "compression_provider": "pillow",
                "compression_provider_version": "1",
            },
        }

    def public_selection_preflight(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": str(entry.get("asset_id", "")),
            "product_id": str(entry.get("product_id", "")),
            "identity_sha256": str(entry.get("identity_sha256", "")),
            "status": str(entry.get("status", "pending")),
            "feasible_ratios": list(entry.get("feasible_ratios", [])),
            "message": str(entry.get("message", "")),
            "checked_at": entry.get("checked_at"),
        }
    static_root = Path(app.static_folder or "")
    static_asset_version = hashlib.sha256(
        b"".join(
            (static_root / name).read_bytes()
            for name in ("app.css", "ui-state.js", "app.js")
        )
    ).hexdigest()[:12]

    @app.errorhandler(BadRequest)
    def malformed_json(_: BadRequest):
        return _error("malformed JSON", 400)

    @app.errorhandler(UnsupportedMediaType)
    def unsupported_json(_: UnsupportedMediaType):
        return _error("JSON content type is required", 415)

    @app.errorhandler(NotFound)
    def not_found(_: NotFound):
        return _error("not found", 404)

    @app.errorhandler(InteractionPathError)
    def invalid_path(_: InteractionPathError):
        return _error("not found", 404)

    @app.errorhandler(KeyError)
    def unknown_stage(_: KeyError):
        return _error("not found", 404)

    @app.errorhandler(InteractionConflict)
    def conflict(error: InteractionConflict):
        message = str(error)
        reason_code = (
            message
            if message
            and all(
                character.isupper()
                or character.isdigit()
                or character == "_"
                for character in message
            )
            else ""
        )
        return _error(
            message,
            409,
            reason_code=reason_code,
            message=message,
        )

    @app.errorhandler(PersistenceAccessDenied)
    def persistence_access_denied(error: PersistenceAccessDenied):
        return _error(
            error.reason_code,
            503,
            reason_code=error.reason_code,
            message="本地状态文件暂时无法更新，请重试；不会创建重复 revision。",
            next_action="重试当前保存或提交；若持续失败，请检查工作目录权限。",
        )

    @app.errorhandler(LocalResourceIdentityMismatch)
    def local_identity_mismatch(error: LocalResourceIdentityMismatch):
        return _error(
            "local resource identity mismatch",
            409,
            reason_code=error.reason_code,
            message="本机文件访问身份已变化，请从已授权的桌面入口重启工作台。",
            next_action="重启桌面工作台后重新检测路径。",
        )

    @app.get("/")
    def index():
        session_id = request.args.get("session_id", "")
        legacy_session = not bool(session_id)
        if session_id:
            try:
                legacy_session = (
                    store.load_session(session_id).get("workflow_profile")
                    != "deterministic-manual-v1"
                )
            except (InteractionConflict, InteractionPathError):
                legacy_session = False
        rendered_stages = tuple(
            stage for stage in STAGES if legacy_session or stage.visible
        )
        stage_registry = [asdict(stage) for stage in rendered_stages]
        task_directory = (
            store.runs_root.resolve() / session_id
            if session_id
            else store.runs_root.resolve()
        )
        folder_index_root = runtime.folder_index_root
        cached_sources_root = folder_index_root / "team-cache" / "sources"
        folder_index_ready = bool(
            cached_sources_root.is_dir()
            and any(
                (source_root / "current.json").is_file()
                for source_root in cached_sources_root.iterdir()
                if source_root.is_dir()
            )
        )
        folder_index_status = "ready" if folder_index_ready else "pending"
        folder_index_label = "可复用" if folder_index_ready else "首次任务待建立"
        folder_index_detail = "系统会自动建立或复用，无需操作。"
        progress_path = folder_index_root / "folder-index-progress.json"
        if not folder_index_ready and progress_path.is_file():
            try:
                progress = read_json(progress_path)
            except (OSError, ValueError):
                progress = {}
            progress_status = str(progress.get("status", ""))
            if progress_status == "running":
                folder_index_status = "running"
                folder_index_label = "正在建立"
                folder_index_detail = (
                    f"已发现 {int(progress.get('folders_discovered', 0))} 个文件夹；"
                    f"当前：{progress.get('current_relative_path', '.')}"
                )
            elif progress_status in {"partial", "failed"}:
                folder_index_status = progress_status
                folder_index_label = "需要续跑" if progress_status == "failed" else "部分完成"
                folder_index_detail = (
                    f"已发现 {int(progress.get('folders_discovered', 0))} 个文件夹，"
                    f"错误 {int(progress.get('error_count', 0))} 项。"
                )
        return render_template(
            "index.html",
            stages=rendered_stages,
            stage_registry=stage_registry,
            session_id=session_id,
            image_sources=runtime.image_sources,
            nas_sources=tuple(configured_nas_sources().values()),
            runs_root=str(store.runs_root.resolve()),
            task_directory=str(task_directory),
            static_asset_version=static_asset_version,
            setup_inputs={
                "products_csv": str(runtime.products.path or ""),
                "products_available": bool(runtime.products.path and runtime.products.path.is_file()),
                "products_status": runtime.products.status,
                "input_quality": _input_quality_summary(
                    runtime.products.path
                ),
                "rules_csv": str(runtime.rules.path or ""),
                "rules_available": bool(runtime.rules.path and runtime.rules.path.is_file()),
                "rules_status": runtime.rules.status,
                "selectors_file": str(runtime.selectors_file or ""),
                "cdp_url": runtime.cdp_url,
                "material_center_url": runtime.material_center_url,
                "folder_index_root": str(folder_index_root),
                "folder_index_available": folder_index_ready,
                "folder_index_status": folder_index_status,
                "folder_index_label": folder_index_label,
                "folder_index_detail": folder_index_detail,
                "image_sources_configured": bool(runtime.image_sources),
                "image_config_path": str(
                    runtime.config_path
                    or runtime.workspace_root
                    / "upload-search-materials"
                    / "config"
                    / "local-paths.json"
                ),
            },
        )

    @app.get("/api")
    def api_index():
        return jsonify(service="upload-search-materials interaction API")

    @app.get("/api/health")
    def health():
        identity = service_identity or {}
        return jsonify(
            service="upload-search-materials interaction API",
            healthy=True,
            pid=os.getpid(),
            ownership_token=str(identity.get("ownership_token", "")),
            runtime_identity=identity.get("runtime_identity"),
            workflow_dispatcher=(
                workflow_dispatcher.public_status()
                if workflow_dispatcher is not None
                else {"online": False, "status": "unmanaged"}
            ),
        )

    @app.get("/api/health/sessions/<session_id>")
    def session_health(session_id: str):
        state = store.load_session(session_id)
        return jsonify(
            healthy=True,
            readable=True,
            session_id=session_id,
            current_stage=state["current_stage"],
            revision=state["stages"][state["current_stage"]]["revision"],
        )

    @app.post(
        "/api/internal/sessions/<session_id>/collection/start"
    )
    def start_desktop_collection(session_id: str):
        identity = service_identity or {}
        supplied = str(request.headers.get("X-Ownership-Token", ""))
        expected = str(identity.get("ownership_token", ""))
        if not expected or not secrets.compare_digest(supplied, expected):
            return _error(
                "service ownership mismatch",
                403,
                reason_code="SERVICE_OWNERSHIP_MISMATCH",
            )
        require_desktop_identity()
        payload = _json_object()
        claimant_id = str(
            payload.get("claimant_id", "collection-worker")
        ).strip() or "collection-worker"
        selected = str(payload.get("selectors_path", "")).strip()
        selected_cdp_url = str(payload.get("cdp_url", "")).strip()
        try:
            result = launch_collection_worker(
                runs_root=store.runs_root,
                session_id=session_id,
                runtime=runtime,
                selectors_path=Path(selected) if selected else None,
                cdp_url=selected_cdp_url or None,
                claimant_id=claimant_id,
                local_resource_identity=expected_runtime_identity,
            )
        except (
            CollectionRuntimeError,
            InteractionConflict,
            OSError,
            SelectorConfigError,
        ) as error:
            return _error(
                "desktop collection start failed",
                409,
                reason_code=str(error).split(":", 1)[0],
                message=str(error),
            )
        return jsonify(result)

    @app.post(
        "/api/sessions/<session_id>/agent-actions/<action>"
    )
    def process_bounded_agent_action(session_id: str, action: str):
        """Run one handoff-bound normal action in the desktop service identity."""

        require_desktop_identity()
        payload = _json_object()
        stage_by_action = {
            "process-setup": "setup",
            "process-product-selection": "completeness",
            "process-final-material-handoff": "asset_matching",
        }
        stage_id = stage_by_action.get(action)
        if stage_id is None:
            return _error(
                "unsupported agent action",
                404,
                reason_code="AGENT_ACTION_UNSUPPORTED",
            )
        expected_revision = payload.get("revision")
        expected_sha256 = str(payload.get("input_sha256", "")).strip()
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or not expected_sha256
        ):
            return _validation_error(
                {
                    "revision": "must be an integer",
                    "input_sha256": "is required",
                }
            )
        input_document = store.read_optional_stage_document(
            session_id, stage_id, "input"
        )
        if not isinstance(input_document, dict):
            raise InteractionConflict("AGENT_ACTION_INPUT_REQUIRED")
        input_path = store._stage_path(session_id, stage_id) / "input.json"
        actual_input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
        if (
            int(input_document.get("revision", -1)) != expected_revision
            or actual_input_sha256 != expected_sha256
        ):
            raise InteractionConflict("AGENT_ACTION_IDENTITY_MISMATCH")

        claimant_id = str(
            payload.get("claimant_id", "codex-agent")
        ).strip() or "codex-agent"
        if action == "process-setup":
            result = launch_collection_worker(
                runs_root=store.runs_root,
                session_id=session_id,
                runtime=runtime,
                selectors_path=runtime.selectors_file,
                cdp_url=runtime.cdp_url,
                claimant_id=claimant_id,
                local_resource_identity=expected_runtime_identity,
            )
        elif action == "process-product-selection":
            try:
                result = process_product_selection_handoff(
                    store,
                    session_id,
                    folder_index_root=runtime.folder_index_root,
                    claimant_id=claimant_id,
                    config_path=runtime.config_path,
                    runtime=runtime,
                )
            except ProductSelectionProcessingError as error:
                return _error(
                    "product selection processing failed",
                    409,
                    reason_code=error.reason_code,
                    message=str(error),
                    diagnostic_path=str(error.diagnostic_path),
                )
        else:
            result = process_final_material_handoff(
                store,
                session_id,
                claimant_id=claimant_id,
            )
        return jsonify(
            action=action,
            stage_id=stage_id,
            revision=expected_revision,
            input_sha256=expected_sha256,
            result=result,
        )

    @app.get("/api/runtime/image-sources")
    def get_runtime_image_sources():
        return jsonify(
            image_sources=runtime.image_sources,
            config_path=str(
                runtime.config_path
                or runtime.workspace_root
                / "upload-search-materials"
                / "config"
                / "local-paths.json"
            ),
        )

    @app.get("/api/runtime/collection")
    def get_runtime_collection():
        selector_status: dict[str, Any] = {
            "configured": False,
            "path": str(runtime.selectors_file or ""),
            "reason_code": "SELECTOR_PROFILE_NOT_FOUND",
        }
        if runtime.selectors_file is not None:
            try:
                profile = load_selector_profile(
                    runtime.selectors_file,
                    purpose="high_value_collection",
                    production=True,
                )
                selector_status = {
                    "configured": True,
                    "path": str(profile.path),
                    "profile_name": profile.name,
                    "profile_version": profile.version,
                    "profile_sha256": profile.sha256,
                    "purpose": profile.purpose,
                    "reason_code": "",
                }
            except SelectorConfigError as error:
                selector_status["reason_code"] = str(error).split(":", 1)[0]
                selector_status["detail"] = str(error)
        cdp = inspect_cdp_endpoint(runtime.cdp_url, timeout_seconds=0.25)
        session_status: dict[str, Any] = {
            "session_id": "",
            "login_state": "unknown",
            "observed_store": "",
            "observed_url": "",
            "page_identity": "",
            "next_action": "",
        }
        selected_session = str(request.args.get("session_id", "")).strip()
        expected_store = str(request.args.get("expected_store", "")).strip()
        dom_evidence: dict[str, Any] = {}
        if selected_session:
            session_path = store._session_path(selected_session)
            readiness_evidence = (
                session_path
                / "collected"
                / "promotion"
                / "collection-readiness-evidence.json"
            )
            if readiness_evidence.is_file():
                try:
                    dom_evidence = json.loads(
                        readiness_evidence.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    dom_evidence = {}
            page_evidence = (
                session_path
                / "collected"
                / "promotion"
                / "store-page-evidence.json"
            )
            login_evidence = (
                session_path / "collected" / "login-required.json"
            )
            session_status["session_id"] = selected_session
            if page_evidence.is_file():
                try:
                    document = json.loads(
                        page_evidence.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    document = {}
                    session_status.update(
                        {
                            "login_state": "unknown",
                            "next_action": (
                                "页面证据尚未完整写入，请稍后刷新"
                            ),
                        }
                    )
                session_status.update(
                    {
                        "login_state": (
                            "authenticated"
                            if document
                            else session_status["login_state"]
                        ),
                        "observed_store": str(
                            document.get("observed_store", "")
                        ),
                        "observed_url": str(
                            document.get("page_url", "")
                        ),
                        "page_identity": str(
                            document.get("page_identity", "")
                        ),
                        "next_action": "继续受管搜推高价值采集",
                    }
                )
            elif login_evidence.is_file():
                try:
                    document = json.loads(
                        login_evidence.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    document = {}
                reason_code = str(document.get("reason_code", ""))
                session_status.update(
                    {
                        "login_state": (
                            "human_check"
                            if reason_code == "HUMAN_CHECK"
                            else (
                                "interaction_required"
                                if document
                                else "unknown"
                            )
                        ),
                        "observed_url": str(
                            document.get("material_center_url", "")
                        ),
                        "next_action": (
                            (
                                "请在 CDP Chrome 完成登录或人机验证，"
                                "然后恢复同一任务"
                            )
                            if document
                            else "登录证据尚未完整写入，请稍后刷新"
                        ),
                    }
                )
        return jsonify(
            selector_profile=selector_status,
            cdp={
                "connected": cdp.connected,
                "endpoint": cdp.endpoint,
                "reason_code": cdp.reason_code,
                "next_action": cdp.next_action,
                "pages": list(cdp.pages),
            },
            material_center_url=runtime.material_center_url,
            session=session_status,
            boundary=(
                "本页面用于任务配置；CDP Chrome 用于用户登录和只读采集。"
            ),
            collection_readiness=build_collection_readiness(
                runtime,
                dom_evidence=dom_evidence,
                expected_store=expected_store,
            ),
        )

    @app.post("/api/runtime/selector-profile/bootstrap")
    def bootstrap_runtime_selector_profile():
        payload = _json_object()
        selected = str(payload.get("selectors_file", "")).strip()
        target = (
            Path(selected)
            if selected
            else (
                runtime.user_data_root
                or runtime.workspace_root / ".local-cache"
            ) / "config" / "selectors.local.yaml"
        )
        if target.is_file():
            try:
                existing = load_selector_profile(
                    target,
                    purpose="high_value_collection",
                    production=False,
                )
            except SelectorConfigError as error:
                return _validation_error(
                    {"selectors_file": str(error)}
                )
            return jsonify(
                status="existing",
                production=bool(
                    yaml.safe_load(
                        target.read_text(encoding="utf-8-sig")
                    ).get("production")
                ),
                path=str(existing.path),
                profile_name=existing.name,
                profile_version=existing.version,
                profile_sha256=existing.sha256,
            )
        try:
            created = create_selector_candidate(
                target,
                material_center_url=runtime.material_center_url,
            )
        except OSError as error:
            return _validation_error(
                {"selectors_file": str(error)}
            )
        return jsonify(created), 201

    @app.post("/api/runtime/collection/validate")
    def validate_runtime_collection():
        nonlocal runtime
        payload = _json_object()
        session_id = str(payload.get("session_id", "")).strip()
        expected_store = str(payload.get("expected_store", "")).strip()
        selected = str(payload.get("selectors_file", "")).strip()
        if not session_id:
            return _validation_error({"session_id": "is required"})
        if not expected_store:
            return _validation_error({"expected_store": "is required"})
        selector_path = (
            Path(selected)
            if selected
            else runtime.selectors_file
            or (
                runtime.workspace_root
                / "upload-search-materials"
                / "config"
                / "selectors.local.yaml"
            )
        )
        try:
            with open_cdp_page(
                runtime.cdp_url,
                runtime.material_center_url,
            ) as page:
                navigation_profile = load_selector_profile(
                    selector_path,
                    purpose="high_value_collection",
                    production=False,
                )
                prepare_high_value_validation_page(
                    page,
                    navigation_profile.selectors,
                )
                validation = validate_selector_candidate(
                    selector_path,
                    page,
                    expected_store=expected_store,
                )
        except (OSError, RuntimeError, SelectorConfigError) as error:
            return _error(
                "collection readiness validation failed",
                409,
                reason_code=str(error).split(":", 1)[0],
                message=str(error),
                next_action=(
                    "在 CDP Chrome 完成登录并打开官方素材中心后重新验证"
                ),
            )
        session_path = store._session_path(session_id)
        evidence_path = (
            session_path
            / "collected"
            / "promotion"
            / "collection-readiness-evidence.json"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        promoted = None
        if validation["ready"]:
            profile_document = yaml.safe_load(
                selector_path.read_text(encoding="utf-8-sig")
            ) or {}
            if profile_document.get("production") is not True:
                promoted = promote_selector_candidate(
                    selector_path,
                    validation,
                )
            runtime = save_selector_profile_path(runtime, selector_path)
            active_profile = load_selector_profile(
                runtime.selectors_file,
                purpose="high_value_collection",
                production=True,
            )
            validation["page_evidence"]["selector_profile"] = {
                "name": active_profile.name,
                "version": active_profile.version,
                "sha256": active_profile.sha256,
                "purpose": active_profile.purpose,
            }
        store._write_json_atomic(
            evidence_path, validation["page_evidence"]
        )
        readiness = build_collection_readiness(
            runtime,
            dom_evidence=validation["page_evidence"],
            expected_store=expected_store,
        )
        return jsonify(
            validation=validation,
            promoted=promoted,
            collection_readiness=readiness,
            evidence_path=str(evidence_path),
        )

    @app.post("/api/runtime/collection/login-browser")
    def open_collection_login_browser():
        require_desktop_identity()
        try:
            result = ensure_cdp_browser(
                executable=runtime.browser_executable,
                profile_dir=runtime.browser_profile_dir,
                cdp_url=runtime.cdp_url,
                material_center_url=runtime.material_center_url,
            )
        except CdpUnavailable as error:
            return _error(
                "login browser unavailable",
                503,
                reason_code=str(error).split(":", 1)[0],
                message=str(error),
                next_action="请检查 Chrome 或 Edge 是否已安装，然后重试。",
            )
        return jsonify(
            status=result.get("status", "connected"),
            connected=result.get("status") == "connected",
            reused=bool(result.get("reused")),
            endpoint=result.get("endpoint", runtime.cdp_url),
            page_count=len(result.get("pages", [])),
        )

    @app.put("/api/runtime/selector-profile")
    def put_runtime_selector_profile():
        nonlocal runtime
        payload = _json_object()
        selected = str(payload.get("selectors_file", "")).strip()
        try:
            profile = load_selector_profile(
                Path(selected),
                purpose="high_value_collection",
                production=True,
            )
            runtime = save_selector_profile_path(runtime, profile.path)
            active_profile = load_selector_profile(
                runtime.selectors_file,
                purpose="high_value_collection",
                production=True,
            )
        except (OSError, ValueError, SelectorConfigError) as error:
            return _validation_error(
                {"selectors_file": str(error)}
            )
        return jsonify(
            saved=True,
            selectors_file=str(active_profile.path),
            profile_name=active_profile.name,
            profile_version=active_profile.version,
            profile_sha256=active_profile.sha256,
            purpose=active_profile.purpose,
        )

    @app.post("/api/runtime/image-sources/check")
    def check_runtime_image_sources():
        require_desktop_identity()
        payload = _json_object()
        try:
            sources = inspect_image_sources(runtime, payload.get("image_sources"))
        except ValueError as error:
            return _validation_error({"image_sources": str(error)})
        projected = []
        for source in sources:
            item = dict(source)
            if (
                item.get("available")
                and isinstance(expected_runtime_identity, dict)
            ):
                item["last_verified_sid"] = str(
                    expected_runtime_identity.get("sid", "")
                )
                item["last_verified_at"] = item.get("checked_at")
                item["last_status"] = "available"
            projected.append(item)
        return jsonify(image_sources=projected)

    @app.get("/api/runtime/nas-sources")
    def get_runtime_nas_sources():
        require_desktop_identity()
        try:
            sources = configured_nas_sources()
            statuses = [check_nas_source(source).as_dict() for source in sources.values()]
        except (OSError, yaml.YAMLError, AssetSourceUnavailable) as error:
            reason_code = getattr(error, "reason_code", "NAS_CONFIG_INVALID")
            return _error(
                "NAS source check failed", 409, reason_code=reason_code,
                message=str(error), next_action="检查 NAS 配置或当前系统挂载状态。",
            )
        return jsonify(
            nas_sources=[
                {
                    "source_id": source.source_id,
                    "label": source.label,
                    "host": source.host,
                    "share": source.share,
                    "canonical_unc": source.windows_path,
                    "subpaths": list(source.subpaths),
                    "status": status,
                }
                for source, status in zip(sources.values(), statuses, strict=True)
            ],
            config_path=str(runtime.nas_sources_file or ""),
        )

    @app.post("/api/runtime/nas-sources/<source_id>/connect")
    def connect_runtime_nas_source(source_id: str):
        require_desktop_identity()
        try:
            source = selected_nas_source(source_id)
            status = check_nas_source(source)
            if status.state == "not_mounted":
                launch_nas_mount(source)
        except (OSError, yaml.YAMLError, AssetSourceUnavailable) as error:
            reason_code = getattr(error, "reason_code", "NAS_MOUNT_LAUNCH_FAILED")
            return _error(
                "NAS connection failed", 409, reason_code=reason_code,
                message=str(error), next_action="在系统窗口完成 NAS 登录后重新检测。",
            )
        return jsonify(
            **status.as_dict(),
            launched=status.state == "not_mounted",
            next_action=(
                "在系统窗口完成 NAS 登录后点击重新检测。"
                if status.state == "not_mounted" else status.message
            ),
        )

    @app.get("/api/runtime/nas-sources/<source_id>/directories")
    def browse_runtime_nas_source(source_id: str):
        require_desktop_identity()
        relative_path = str(request.args.get("relative_path", ""))
        try:
            directories = browse_nas_folders(
                selected_nas_source(source_id), relative_path=relative_path
            )
        except (OSError, yaml.YAMLError, AssetSourceUnavailable) as error:
            reason_code = getattr(error, "reason_code", "ASSET_ROOT_IO_ERROR")
            return _error(
                "NAS browse failed", 409, reason_code=reason_code,
                message=str(error), next_action="连接 NAS 并确认目录权限后重试。",
            )
        return jsonify(source_id=source_id, directories=directories)

    @app.put("/api/runtime/image-sources")
    def put_runtime_image_sources():
        nonlocal runtime
        payload = _json_object()
        try:
            runtime = save_image_sources(runtime, payload.get("image_sources"))
        except (OSError, ValueError) as error:
            return _validation_error({"image_sources": str(error)})
        return jsonify(
            image_sources=runtime.image_sources,
            config_path=str(runtime.config_path),
            saved=True,
        )

    @app.post("/api/runtime/folder-picker")
    def open_runtime_folder_picker():
        require_desktop_identity()
        payload = _json_object()
        initial_path = payload.get("initial_path")
        if initial_path is not None and not isinstance(initial_path, str):
            return _validation_error({"initial_path": "must be a string"})
        try:
            selected = choose_directory(initial_path)
        except FolderPickerError as error:
            return _error(
                "folder picker unavailable",
                503,
                reason_code=error.reason_code,
                message=error.message,
                next_action=error.message,
                detail=error.detail,
            )
        return jsonify(
            cancelled=selected is None,
            path=selected or "",
            reason_code=(
                "FOLDER_PICKER_CANCELLED"
                if selected is None
                else "FOLDER_PICKER_SELECTED"
            ),
        )

    @app.post("/api/sessions")
    def create_session():
        _json_object()
        session = store.create_session()
        return jsonify(session_id=session.session_id), 201

    @app.get("/api/sessions/<session_id>")
    def get_session(session_id: str):
        state = store.load_session(session_id)
        return jsonify(
            session=_project_upload_results_session(store, session_id, state)
        )

    @app.errorhandler(Exception)
    def unexpected_workflow_error(error: Exception):
        """Keep technical failures out of the business UI and route them to Codex."""

        view_args = request.view_args or {}
        session_id = str(view_args.get("session_id", "")).strip()
        diagnostic = None
        if session_id:
            try:
                state = store.load_session(session_id)
                stage_id = str(
                    view_args.get("stage_id") or state.get("current_stage") or "setup"
                )
                diagnostic = write_exception_diagnostic(
                    store,
                    session_id,
                    stage_id,
                    processor=f"interaction-{request.endpoint or 'request'}",
                    phase=f"http_{request.method.casefold()}",
                    error=error,
                )
            except Exception:
                diagnostic = None
        return _error(
            "workflow processing failed",
            500,
            message="系统暂时未能完成当前操作，Codex 已收到诊断信息。",
            diagnostic_status=(
                "open" if isinstance(diagnostic, dict) else "unavailable"
            ),
        )

    @app.get("/api/runtime/login-status")
    def get_runtime_login_status():
        """Return a safe, user-facing state for the automatic login gate."""

        require_desktop_identity()
        state = inspect_login_browser(runtime)
        ready = state.get("ready") is True
        login_state = str(state.get("login_state", "preparing"))
        public_status = {
            "browser_unavailable": "chrome_unavailable",
            "opening_material_center": "opening_material_center",
            "interaction_required": "waiting_for_login",
            "human_check": "waiting_for_login",
            "store_unrecognized": "store_unrecognized",
            "authenticated": "ready",
        }.get(login_state, "opening_material_center")
        public_message = {
            "chrome_unavailable": "专用 Chrome 未启动",
            "opening_material_center": "已连接 Chrome，正在打开千牛",
            "waiting_for_login": "千牛页面已打开，等待用户登录",
            "store_unrecognized": "已登录，但暂未识别店铺",
            "ready": "已识别店铺，可以配置",
        }[public_status]
        return jsonify(
            ready=ready,
            status=public_status,
            message=public_message,
            login_state=login_state,
            reason_code=str(state.get("reason_code", "")),
            observed_store=(
                str(state.get("observed_store", "")) if ready else ""
            ),
        )

    @app.get("/api/sessions/<session_id>/stages/<stage_id>")
    def get_stage_route(session_id: str, stage_id: str):
        if stage_id == "asset_matching":
            migrated_job, migrated = migrate_legacy_gallery_handoff(
                store, session_id
            )
            if migrated and migrated_job is not None:
                request_material_executor(session_id, migrated_job)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        result = (
            _upload_results_result(store, session_id, state)
            if stage_id == "results"
            else _current_result(store, session_id, stage_id, state)
        )
        stage_state = state["stages"][stage_id]
        if stage_id == "results" and result is not None:
            stage_state = {
                **stage_state,
                "status": str(result.get("status") or "completed"),
            }
        response = {
            "stage": asdict(stage),
            "state": stage_state,
            "input": _current_input(store, session_id, stage, state),
            "result": result,
            "submission": _current_submission(
                store, session_id, stage_id, state
            ),
            "processing_claim": store.processing_claim(
                session_id, stage_id
            ),
            "handoff_status": store.handoff_display_state(
                session_id, stage_id
            ),
            "stage_transaction": store.stage_transaction_status(
                session_id, stage_id
            ),
            "workflow_dispatch": workflow_dispatch_status(session_id),
        }
        if stage_id == "setup":
            authoritative = get_collection_status(
                store.runs_root, session_id
            )
            response["collection_status"] = authoritative
            response["result"] = authoritative["current_result"]
            response["result_history"] = authoritative["history"]
        elif stage_id == "asset_matching":
            response["gallery_job"] = reconcile_gallery_job(
                store, session_id
            )
        return jsonify(response)

    @app.post(
        "/api/sessions/<session_id>/stages/asset_matching/prepare-gallery"
    )
    def prepare_asset_gallery(session_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        stage = get_stage("asset_matching")
        values = _normalize_stage_values(
            store,
            session_id,
            "asset_matching",
            state,
            _values(payload),
        )
        if state["stages"]["asset_matching"]["status"] not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            raise InteractionConflict(
                "stage status does not allow local gallery preparation"
            )
        field_errors = (
            _unknown_value_errors(stage, values)
            | _value_errors(stage, values)
        )
        current_result = _current_result(
            store, session_id, "asset_matching", state
        )
        current_data = (
            current_result.get("data")
            if isinstance(current_result, dict)
            and isinstance(current_result.get("data"), dict)
            else {}
        )
        candidate_products = {
            str(item.get("product_id", ""))
            for item in current_data.get("folder_candidates", [])
            if isinstance(item, dict) and item.get("product_id")
        }
        confirmed_products = {
            str(item.get("product_id", ""))
            for item in values.get("folder_decisions", [])
            if isinstance(item, dict)
            and item.get("decision") == "confirmed"
            and item.get("product_id")
        }
        missing_products = sorted(candidate_products - confirmed_products)
        if candidate_products and missing_products:
            field_errors["folder_decisions"] = (
                f"{ALL_FOLDERS_REJECTED}：以下商品至少采用一个候选文件夹"
                "后才能加载图片："
                + "、".join(missing_products)
            )
        if field_errors:
            return _validation_error(field_errors)
        expected_revision = payload.get("revision")
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            return _validation_error({"revision": "must be an integer"})
        local_commit = store.save_local_input(
            session_id,
            "asset_matching",
            _allowlisted_values(stage, values),
            _user_notes(payload),
            expected_revision=expected_revision,
            request_id=_persistence_request_id(payload),
        )
        job, _created = create_or_reuse_gallery_job(
            store,
            session_id,
            local_commit,
            values.get("folder_decisions", []),
        )
        executor_launch = None
        if _created:
            job, executor_launch = request_material_executor(
                session_id, job
            )
        return jsonify(
            status="local_processing",
            revision=local_commit["revision"],
            input_sha256=local_commit["input_sha256"],
            gallery_job=job,
            executor_launch=executor_launch,
        ), 202

    @app.get(
        "/api/sessions/<session_id>/stages/asset_matching/folder-image-counts"
    )
    def get_asset_folder_image_counts(session_id: str):
        state = store.load_session(session_id)
        current_result = _current_result(
            store, session_id, "asset_matching", state
        )
        data = (
            current_result.get("data")
            if isinstance(current_result, dict)
            and isinstance(current_result.get("data"), dict)
            else {}
        )
        counts = []
        for candidate in data.get("folder_candidates", []):
            if not isinstance(candidate, dict):
                continue
            folder_id = str(candidate.get("folder_id", "")).strip()
            if not folder_id:
                continue
            ready = candidate.get("image_count_status") == "ready"
            counts.append(
                {
                    "folder_id": folder_id,
                    "image_count_status": (
                        "ready" if ready else "unknown"
                    ),
                    "raw_recursive_image_count": (
                        candidate.get("raw_recursive_image_count")
                        if ready
                        else None
                    ),
                    "image_count_reason_code": (
                        ""
                        if ready
                        else "MATERIAL_EXECUTOR_REQUIRED"
                    ),
                }
            )
        return jsonify(folder_counts=counts)

    @app.get(
        "/api/sessions/<session_id>/stages/asset_matching/gallery-job"
    )
    def get_asset_gallery_job(session_id: str):
        store.load_session(session_id)
        return jsonify(
            gallery_job=reconcile_gallery_job(store, session_id)
        )

    @app.post(
        "/api/sessions/<session_id>/stages/asset_matching/gallery-job/retry"
    )
    def retry_asset_gallery_job(session_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        current = reconcile_gallery_job(store, session_id)
        if current is None or current.get("status") not in {"failed", "stale"}:
            raise InteractionConflict("GALLERY_JOB_NOT_RETRYABLE")
        revision = int(state["stages"]["asset_matching"]["revision"])
        input_path = (
            store._stage_path(session_id, "asset_matching") / "input.json"
        )
        input_document = store._read_json(input_path, "input")
        values = input_document.get("values", {})
        local_commit = {
            "revision": revision,
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        }
        job, _ = create_or_reuse_gallery_job(
            store,
            session_id,
            local_commit,
            values.get("folder_decisions", []),
            retry=True,
        )
        job, executor_launch = request_material_executor(
            session_id, job
        )
        return jsonify(
            status="local_processing",
            gallery_job=job,
            executor_launch=executor_launch,
        ), 202

    @app.get(
        "/api/sessions/<session_id>/stages/asset_matching/selection-preflights"
    )
    def get_asset_selection_preflights(session_id: str):
        state = store.load_session(session_id)
        data = current_asset_gallery(session_id, state)
        _, policy_sha256 = selection_preflight_policy()
        candidates = {
            str(item.get("asset_id", "")): item
            for item in data.get("asset_candidates", [])
            if isinstance(item, dict) and item.get("asset_id")
        }
        entries = []
        for asset_id, entry in read_selection_preflight_cache(session_id)[
            "entries"
        ].items():
            candidate = candidates.get(str(asset_id))
            if not isinstance(entry, dict) or candidate is None:
                continue
            if entry.get("identity_sha256") != selection_preflight_identity(
                candidate, policy_sha256
            ):
                continue
            entries.append(public_selection_preflight(entry))
        return jsonify(entries=entries)

    @app.post(
        "/api/sessions/<session_id>/stages/asset_matching/assets/"
        "<asset_id>/selection-preflight"
    )
    def preflight_asset_selection(session_id: str, asset_id: str):
        state = store.load_session(session_id)
        if state["stages"]["asset_matching"]["status"] not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            raise InteractionConflict(
                "asset matching does not accept selection preflight"
            )
        data = current_asset_gallery(session_id, state)
        candidate = next(
            (
                dict(item)
                for item in data.get("asset_candidates", [])
                if isinstance(item, dict)
                and str(item.get("asset_id", "")) == str(asset_id)
            ),
            None,
        )
        if candidate is None:
            raise InteractionConflict("selected asset is not in current gallery")
        if not (
            candidate.get("validation_status") == "valid"
            and isinstance(candidate.get("preflight"), dict)
            and candidate["preflight"].get("selectable") is True
        ):
            return _error(
                "asset is not selectable",
                422,
                reason_code="ASSET_NOT_SELECTABLE",
                message="这张图片未通过原图硬性检查，不能采用。",
            )
        try:
            entry = build_single_selection_preflight(
                session_id, state, candidate
            )
        except (OSError, ValueError, LocalResourceIdentityMismatch):
            return _error(
                "selection preflight failed",
                422,
                reason_code="ASSET_SELECTION_PREFLIGHT_FAILED",
                message="这张图片暂时无法完成裁剪预检，请稍后重试或更换图片。",
            )
        return jsonify(**public_selection_preflight(entry))

    @app.post(
        "/api/sessions/<session_id>/stages/<stage_id>/recover-processing"
    )
    def recover_processing(session_id: str, stage_id: str):
        payload = _json_object()
        claimant_id = str(
            payload.get("claimant_id", "codex-agent")
        ).strip() or "codex-agent"
        current = store.processing_claim(session_id, stage_id)
        handoff = store.read_optional_stage_document(
            session_id, stage_id, "handoff"
        )
        specialized_processor = (
            "process-product-selection"
            if stage_id == "completeness"
            else (
                "process-final-material-handoff"
                if stage_id == "asset_matching"
                and isinstance(handoff, dict)
                and handoff.get("handoff_kind") == "final_material_selection"
                else ""
            )
        )
        if specialized_processor:
            resolved = store.resolve_recovery_state(session_id, stage_id)
            return jsonify(
                status="specialized_processor_required",
                reason_code="SPECIALIZED_PROCESSOR_REQUIRED",
                claim_deferred=True,
                processor=specialized_processor,
                recovery=resolved,
                processing_claim=current,
                next_action=(
                    f"Agent must run {specialized_processor} directly; "
                    "the page does not claim this handoff."
                ),
            )
        if current and not current.get("expired"):
            if stage_id != "setup":
                raise InteractionConflict("PROCESSING_CLAIM_ACTIVE")
            authoritative = get_collection_status(
                store.runs_root, session_id
            )
            if authoritative["status"] != "recoverable":
                raise InteractionConflict("PROCESSING_CLAIM_ACTIVE")
            attempt_id = str(
                authoritative.get("attempt_id", "")
            ).strip()
            if not attempt_id:
                raise InteractionConflict(
                    "PROCESSING_RECOVERY_IDENTITY_REQUIRED"
                )
            store.mark_processing_claim_recoverable(
                session_id,
                stage_id,
                attempt_id=attempt_id,
            )
        try:
            handoff = store.wait_for_handoff(
                session_id,
                stage_id,
                timeout_seconds=0.5,
                claimant_id=claimant_id,
                reclaim_expired=True,
            )
        except TimeoutError as error:
            raise InteractionConflict(
                "PROCESSING_RECOVERY_NOT_AVAILABLE"
            ) from error
        return jsonify(
            status="processing",
            handoff=handoff,
            processing_claim=store.processing_claim(
                session_id, stage_id
            ),
        )

    @app.post(
        "/api/sessions/<session_id>/stages/<stage_id>/heartbeat"
    )
    def renew_processing(session_id: str, stage_id: str):
        payload = _json_object()
        claim_id = str(payload.get("claim_id", "")).strip()
        if not claim_id:
            return _validation_error({"claim_id": "is required"})
        claim = store.renew_processing_claim(
            session_id, stage_id, claim_id
        )
        return jsonify(status="processing", processing_claim=claim)

    @app.post(
        "/api/sessions/<session_id>/stages/<stage_id>/agent-wait"
    )
    def manage_agent_wait(session_id: str, stage_id: str):
        payload = _json_object()
        action = str(payload.get("action", "listen")).strip()
        wait_seconds_value = payload.get(
            "wait_seconds",
            AGENT_WAIT_SEGMENT_SECONDS if action == "listen" else 0,
        )
        maximum_wait_seconds = (
            AGENT_WAIT_SEGMENT_SECONDS if action == "listen" else 30
        )
        if (
            isinstance(wait_seconds_value, bool)
            or not isinstance(wait_seconds_value, (int, float))
            or not 0 <= float(wait_seconds_value) <= maximum_wait_seconds
        ):
            return _validation_error(
                {
                    "wait_seconds": (
                        "must be a number between 0 and "
                        f"{maximum_wait_seconds}"
                    )
                }
            )
        wait_seconds = float(wait_seconds_value)
        if action == "listen":
            expected_revision = payload.get("expected_revision")
            if expected_revision is not None and (
                isinstance(expected_revision, bool)
                or not isinstance(expected_revision, int)
            ):
                return _validation_error(
                    {"expected_revision": "must be an integer when supplied"}
                )
            result = store.listen_for_handoff(
                session_id,
                stage_id,
                claimant_id=str(
                    payload.get("claimant_id", "codex-agent")
                ),
                wait_seconds=wait_seconds,
                expected_revision=expected_revision,
            )
            return jsonify(**result)
        if action == "create":
            expected_revision = payload.get("expected_revision")
            if isinstance(expected_revision, bool) or not isinstance(
                expected_revision, int
            ):
                return _validation_error(
                    {"expected_revision": "must be an integer"}
                )
            wait = store.create_agent_wait(
                session_id,
                stage_id,
                expected_revision=expected_revision,
                claimant_id=str(
                    payload.get("claimant_id", "codex-agent")
                ),
            )
        elif action == "renew":
            wait_id = str(payload.get("wait_id", "")).strip()
            if not wait_id:
                return _validation_error({"wait_id": "is required"})
            wait = store.renew_agent_wait(session_id, wait_id)
        elif action == "clear":
            wait_id = str(payload.get("wait_id", "")).strip()
            if not wait_id:
                return _validation_error({"wait_id": "is required"})
            store.clear_agent_wait(session_id, wait_id=wait_id)
            wait = None
            wait_seconds = 0
        else:
            return _validation_error(
                {"action": "must be listen, create, renew, or clear"}
            )
        handoff_status = store.handoff_display_state(session_id, stage_id)
        deadline = time.monotonic() + wait_seconds
        while (
            wait_seconds > 0
            and handoff_status.get("handoff_identity") is None
            and handoff_status.get("base_status")
            in {"draft", "needs_user_input", "blocked"}
            and time.monotonic() < deadline
        ):
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
            handoff_status = store.handoff_display_state(session_id, stage_id)
        return jsonify(
            agent_wait=wait,
            handoff_status=handoff_status,
        )

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/chat-fallback")
    def write_chat_fallback(session_id: str, stage_id: str):
        payload = _json_object()
        supplied_values = _values(payload)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        if state["stages"][stage_id]["status"] not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            raise InteractionConflict(
                "stage status does not allow chat fallback"
            )
        reason_code = str(payload.get("reason_code", "")).strip()
        reason_detail = str(payload.get("reason_detail", "")).strip()
        actor = str(payload.get("actor", "codex-agent")).strip()
        mode = str(payload.get("mode", "draft")).strip()
        expected_revision = payload.get("revision")
        if reason_code not in FALLBACK_REASON_CODES:
            return _validation_error(
                {"reason_code": "an allowed stable fallback reason is required"}
            )
        if not reason_detail:
            return _validation_error({"reason_detail": "is required"})
        if not actor:
            return _validation_error({"actor": "is required"})
        if mode not in {"draft", "submit"}:
            return _validation_error({"mode": "must be draft or submit"})
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            return _validation_error({"revision": "must be an integer"})
        if expected_revision != int(state["stages"][stage_id]["revision"]):
            raise InteractionConflict("expected revision is stale")
        if (
            mode == "submit"
            and stage.previous_stage
            and state["stages"][stage.previous_stage]["status"]
            != "completed"
        ):
            raise InteractionConflict(
                f"complete previous stage '{stage.previous_stage}' first"
            )

        field_map = {field.name: field for field in stage.fields}
        unknown = sorted(set(supplied_values) - set(field_map))
        if unknown:
            if reason_code == "SCHEMA_GAP":
                store._write_json_atomic(
                    store._stage_path(session_id, stage_id)
                    / "frontend-gap.json",
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "stage_id": stage_id,
                        "revision": expected_revision,
                        "reason_code": reason_code,
                        "missing_fields": unknown,
                        "detail": reason_detail,
                        "created_at": datetime.now().astimezone().isoformat(),
                        "status": "frontend_schema_task_required",
                    },
                )
            return _validation_error(
                {
                    name: (
                        "page schema does not support this field; "
                        "frontend-gap.json was recorded"
                    )
                    for name in unknown
                }
            )
        disallowed = {
            name: "frontend is required for this field"
            for name, field in field_map.items()
            if name in supplied_values
            and (
                field.interaction_policy == "frontend_required"
                or reason_code not in field.fallback_reason_codes
            )
        }
        if disallowed:
            return _validation_error(disallowed)

        current = _current_input(store, session_id, stage, state)
        merged = dict((current or {}).get("values", {}))
        merged.update(supplied_values)
        field_errors: dict[str, str] = {}
        for name in supplied_values:
            error = _field_error(field_map[name], merged.get(name), True)
            if error:
                field_errors[name] = error
        if mode == "submit":
            field_errors |= _value_errors(stage, merged)
        if field_errors:
            return _validation_error(field_errors)

        value_sha = hashlib.sha256(
            json.dumps(
                merged,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        audit = {
            "interaction_channel": "chat_fallback",
            "fallback_reason_code": reason_code,
            "fallback_detail": reason_detail,
            "recorded_at": datetime.now().astimezone().isoformat(),
            "recorded_by": actor,
            "session_id": session_id,
            "stage_id": stage_id,
            "base_revision": expected_revision,
            "input_sha256": value_sha,
        }
        if stage_id in {"approval", "production_confirmation"}:
            checklist = safety_context(
                store, session_id, stage_id, merged
            )
            audit["safety_checklist"] = checklist
            audit["safety_checklist_sha256"] = hashlib.sha256(
                json.dumps(
                    checklist,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            audit["safety_checklist_fields"] = sorted(checklist)
        if mode == "draft":
            document = store.save_draft(
                session_id,
                stage_id,
                merged,
                _user_notes(payload),
                expected_revision=expected_revision,
                interaction_audit=audit,
                request_id=_persistence_request_id(payload),
            )
            return jsonify(
                status="draft",
                revision=document["revision"],
                interaction_audit=audit,
            )
        handoff = store.save_input(
            session_id,
            stage_id,
            merged,
            _user_notes(payload),
            expected_revision=expected_revision + 1,
            allowed_current_statuses={"draft", "needs_user_input", "blocked"},
            interaction_audit=audit,
            request_id=_persistence_request_id(payload),
        )
        return jsonify(
            status="ready_for_agent",
            revision=handoff["revision"],
            input_sha256=handoff["input_sha256"],
            interaction_audit=audit,
        )

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/draft")
    def save_draft(session_id: str, stage_id: str):
        payload = _json_object()
        values = _values(payload)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        if stage.read_only:
            raise InteractionConflict("read-only stage does not accept submissions")
        values = _normalize_stage_values(
            store, session_id, stage_id, state, values
        )
        if state["stages"][stage_id]["status"] not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            raise InteractionConflict("submitted stage is frozen; withdraw it before editing")
        field_errors = _unknown_value_errors(stage, values)
        if field_errors:
            return _validation_error(field_errors)
        expected_revision = payload.get("revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            return _validation_error({"revision": "must be an integer"})
        previous_input = store.read_optional_stage_document(
            session_id, stage_id, "input"
        )
        previous_values = (
            previous_input.get("values", {})
            if isinstance(previous_input, dict)
            else {}
        )
        if stage_id == "slots_copy":
            # Copy drafts are hydrated into the stage input while the request
            # is running.  The input may still contain the pre-confirmation
            # slot draft, so comparing only with previous_values can falsely
            # supersede the request on its first progress autosave.  The
            # confirmed current plan is authoritative once it exists.
            current_plan = read_current_slot_plan(store, session_id)
            authoritative_assignments = (
                current_plan.get("slot_assignments")
                if isinstance(current_plan, dict)
                else previous_values.get("slot_assignments")
            )
            slot_plan_changed = (
                authoritative_assignments != values.get("slot_assignments")
            )
        else:
            slot_plan_changed = True
        document = store.save_draft(
            session_id,
            stage_id,
            _allowlisted_values(stage, values),
            _user_notes(payload),
            expected_revision=expected_revision,
            request_id=_persistence_request_id(payload),
        )
        # Copy drafts are incrementally hydrated from a running copy request.
        # Autosaving those fields must not invalidate the request that produced
        # them; only a change to the slot/image plan makes its output stale.
        if slot_plan_changed:
            _supersede_slot_requests_after_revision_change(
                store, session_id, stage_id
            )
        if stage_id == "asset_matching":
            invalidate_gallery_if_scope_expands(
                store,
                session_id,
                values.get("folder_decisions", []),
            )
        return jsonify(status="draft", revision=document["revision"])

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/submit")
    def submit(session_id: str, stage_id: str):
        payload = _json_object()
        values = _values(payload)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        if stage.read_only:
            raise InteractionConflict("read-only stage does not accept submissions")
        values = _normalize_stage_values(
            store, session_id, stage_id, state, values
        )
        request_id = _persistence_request_id(payload)
        if (
            state["stages"][stage_id]["status"] == "ready_for_agent"
            and request_id is not None
        ):
            normalized_request_id = SessionStore._persistence_request_id(
                request_id
            )
            completed = store._completed_stage_transaction(
                store._stage_path(session_id, stage_id),
                normalized_request_id,
                store._stage_payload_sha(
                    "submit",
                    _allowlisted_values(stage, values),
                    _user_notes(payload),
                ),
            )
            if completed is not None:
                return jsonify(**completed, next_stage=None), 202
        if state["stages"][stage_id]["status"] not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            raise InteractionConflict("stage status does not allow a new submission")
        if (
            enforce_stage_order
            and stage.previous_stage
            and state["stages"][stage.previous_stage]["status"] != "completed"
        ):
            return _validation_error(
                {"stage": f"complete previous stage '{stage.previous_stage}' first"}
            )
        field_errors = _unknown_value_errors(stage, values) | _value_errors(stage, values)
        if not field_errors and stage_id == "completeness":
            field_errors |= _completeness_selection_errors(
                store, session_id, state, values
            )
        if not field_errors and stage_id == "setup":
            labels = values.get("image_source_labels", [])
            roots = values.get("image_roots", [])
            submitted_sources = [
                {"label": str(label), "path": str(root)}
                for label, root in zip(labels, roots, strict=False)
            ]
            try:
                source_diagnostics = inspect_image_sources(
                    runtime, submitted_sources
                )
            except ValueError as error:
                field_errors["image_roots"] = str(error)
            else:
                unavailable = [
                    source
                    for source in source_diagnostics
                    if source.get("status") != "available"
                ]
                if unavailable:
                    field_errors["image_roots"] = "；".join(
                        (
                            f"{source.get('label', '图片源')}："
                            f"{source.get('message') or source.get('reason_code') or '路径不可访问'}"
                        )
                        for source in unavailable
                    )
        asset_matching_result = None
        asset_matching_data: dict[str, Any] = {}
        asset_matching_step = FOLDER_REVIEW
        if stage_id == "asset_matching":
            asset_matching_result = _current_result(
                store, session_id, "asset_matching", state
            )
            candidate_data = (
                asset_matching_result.get("data")
                if isinstance(asset_matching_result, dict)
                else None
            )
            if isinstance(candidate_data, dict):
                asset_matching_data = candidate_data
            asset_matching_step = infer_workflow_step(
                asset_matching_data,
                status=str(state["stages"]["asset_matching"]["status"]),
            )
            if (
                asset_matching_step == FOLDER_REVIEW
                and (
                    asset_matching_data.get("workflow_step") == FOLDER_REVIEW
                    or asset_matching_data.get("folder_candidates")
                )
            ):
                return _error(
                    "folder confirmation is a local page action",
                    409,
                    reason_code="LOCAL_GALLERY_ACTION_REQUIRED",
                    message="请使用“确认文件夹并加载图片”；这一步不会提交给 Codex。",
                    next_action="确认文件夹并加载图片",
                )
            confirmed_folders = [
                item
                for item in values.get("folder_decisions", [])
                if isinstance(item, dict)
                and item.get("decision") == "confirmed"
            ]
            has_folder_boundary = bool(
                isinstance(asset_matching_data.get("folder_candidates"), list)
                and asset_matching_data.get("folder_candidates")
            )
            candidate_products = {
                str(item.get("product_id", ""))
                for item in asset_matching_data.get("folder_candidates", [])
                if isinstance(item, dict) and item.get("product_id")
            }
            confirmed_products = {
                str(item.get("product_id", ""))
                for item in confirmed_folders
                if item.get("product_id")
            }
            products_without_folder = sorted(
                candidate_products - confirmed_products
            )
            if (
                not field_errors
                and has_folder_boundary
                and products_without_folder
            ):
                field_errors["folder_decisions"] = (
                    f"{ALL_FOLDERS_REJECTED}：以下商品至少采用一个"
                    "候选文件夹后才能加载图片："
                    + "、".join(products_without_folder)
                )
        if not field_errors and stage_id == "image_review":
            review_context = _current_result(
                store, session_id, "image_review", state
            )
            review_data = (
                review_context.get("data")
                if isinstance(review_context, dict)
                else None
            )
            if not isinstance(review_data, dict):
                field_errors["decisions"] = (
                    "Agent must prepare current image review data first"
                )
            else:
                try:
                    values["decisions"] = normalize_review_decisions(
                        values.get("decisions"),
                        review_data,
                    )
                except (TypeError, ValueError) as error:
                    field_errors["decisions"] = str(error)
        selected_asset_bundle = None
        asset_validation_feedback = None
        if (
            not field_errors
            and stage_id == "asset_matching"
            and asset_matching_step == IMAGE_SELECTION
            and values.get("asset_decisions")
        ):
            current_data = asset_matching_data
            if (
                isinstance(current_data, dict)
                and isinstance(current_data.get("asset_candidates"), list)
            ):
                has_folder_boundary = bool(
                    current_data.get("folder_candidates")
                )
                if has_folder_boundary and not gallery_covers_folder_decisions(
                    current_data.get("gallery_identity"),
                    values.get("folder_decisions", []),
                    session_id=session_id,
                ):
                    field_errors["folder_decisions"] = (
                        f"{GALLERY_IDENTITY_STALE}：文件夹采用范围已扩大，"
                        "请重新确认文件夹并加载图片"
                    )
                    current_data = {}
            if (
                not field_errors
                and isinstance(current_data, dict)
                and isinstance(current_data.get("asset_candidates"), list)
            ):
                policy, policy_sha256 = selection_preflight_policy()
                prospective_revision = int(
                    state["stages"]["asset_matching"]["revision"]
                ) + 1
                preflight = combine_cached_selection_preflights(
                    session_id,
                    current_data.get("asset_candidates", []),
                    values.get("asset_decisions", []),
                    policy=policy,
                    policy_sha256=policy_sha256,
                    asset_matching_revision=prospective_revision,
                )
                if preflight is None:
                    # Backward-compatible safety fallback for API clients and
                    # historical drafts. The normal UI fills the per-image
                    # cache while the user selects each image.
                    preflight = build_image_review_data(
                        current_data.get("asset_candidates", []),
                        values.get("asset_decisions", []),
                        policy=policy,
                        policy_sha256=policy_sha256,
                        asset_matching_revision=prospective_revision,
                        inspection_cache_path=(
                            store._stage_path(session_id, "asset_matching")
                            / "source-inspection-cache.json"
                        ),
                    )
                asset_validation_feedback = (
                    _selected_asset_validation_feedback(preflight)
                )
                usable_by_product: dict[str, int] = {}
                ratios_by_product: dict[str, dict[str, int]] = {}
                required_products = {
                    str(item.get("product_id", ""))
                    for item in current_data.get("requirements", [])
                    if isinstance(item, dict)
                    and item.get("product_id")
                }
                for item in preflight.get("assets", []):
                    if (
                        isinstance(item, dict)
                        and item.get("status") != "blocked"
                        and item.get("duplicate") is not True
                    ):
                        product_id = str(item.get("product_id", ""))
                        usable_by_product[product_id] = (
                            usable_by_product.get(product_id, 0) + 1
                        )
                        ratio_counts = ratios_by_product.setdefault(
                            product_id, {"3:4": 0, "1:1": 0}
                        )
                        for ratio in ("3:4", "1:1"):
                            option = item.get("ratio_options", {}).get(ratio, {})
                            if isinstance(option, dict) and option.get("feasible") is True:
                                ratio_counts[ratio] += 1
                shortages = {
                    product_id: 3 - usable_by_product.get(product_id, 0)
                    for product_id in required_products
                    if usable_by_product.get(product_id, 0) < 3
                }
                ratio_shortages = {
                    product_id
                    for product_id in required_products
                    if max(
                        ratios_by_product.get(
                            product_id, {"3:4": 0, "1:1": 0}
                        ).values()
                    ) < 3
                }
                blocking_count = int(
                    asset_validation_feedback.get("blocking_count", 0)
                )
                if not required_products:
                    field_errors["asset_decisions"] = (
                        "当前画廊没有有效的商品边界，请重新加载图片"
                    )
                elif not values.get("asset_decisions"):
                    field_errors["asset_decisions"] = (
                        f"{PRODUCT_IMAGE_SHORTAGE}：每个商品至少采用 3 张图片"
                    )
                elif blocking_count:
                    shortage_detail = "；".join(
                        f"{product_id} 还差 {count} 张"
                        for product_id, count in sorted(shortages.items())
                    )
                    field_errors["asset_decisions"] = (
                        f"有 {blocking_count} 张已选素材需要处理，"
                        "请查看红色卡片并取消或更换图片"
                        + (
                            f"；处理后仍需补充：{shortage_detail}"
                            if shortage_detail
                            else ""
                        )
                    )
                elif shortages:
                    detail = "；".join(
                        f"{product_id} 还差 {count} 张"
                        for product_id, count in sorted(shortages.items())
                    )
                    field_errors["asset_decisions"] = (
                        f"{PRODUCT_IMAGE_SHORTAGE}：完整坑位至少需要 3 张"
                        f"可用且不重复的图片：{detail}"
                    )
                elif ratio_shortages:
                    field_errors["asset_decisions"] = (
                        "每个商品至少需要 3 张共同支持同一比例的图片："
                        + "、".join(sorted(ratio_shortages))
                    )
                else:
                    missing_slots = {
                        str(item.get("product_id", "")): int(
                            item.get("missing_materials") or 0
                        )
                        for item in current_data.get("requirements", [])
                        if isinstance(item, dict)
                    }
                    selected_asset_bundle = {
                        "current_data": current_data,
                        "preflight": preflight,
                        "missing_slots": missing_slots,
                    }
        elif (
            not field_errors
            and stage_id == "asset_matching"
            and asset_matching_step == IMAGE_SELECTION
        ):
            field_errors["asset_decisions"] = (
                f"{PRODUCT_IMAGE_SHORTAGE}：每个商品至少采用 3 张图片"
            )
        if not field_errors and stage_id == "slots_copy":
            slot_context = _current_result(
                store, session_id, "slots_copy", state
            )
            slot_data = (
                slot_context.get("data")
                if isinstance(slot_context, dict)
                else None
            )
            if not isinstance(slot_data, dict):
                field_errors["slot_assignments"] = (
                    "Agent must prepare the current slot board first"
                )
            else:
                try:
                    values["slot_assignments"] = validate_slot_assignments(
                        values.get("slot_assignments"),
                        slot_data,
                    )
                except (TypeError, ValueError) as error:
                    field_errors["slot_assignments"] = str(error)
        if field_errors:
            return _validation_error(
                field_errors,
                **(
                    {"asset_validation": asset_validation_feedback}
                    if asset_validation_feedback is not None
                    else {}
                ),
            )

        expected_revision = payload.get("revision")
        if "revision" in payload and (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
        ):
            return _validation_error({"revision": "must be an integer"})

        if stage_id == "slots_copy":
            slot_context = _current_result(
                store, session_id, "slots_copy", state
            )
            slot_data = (
                slot_context.get("data")
                if isinstance(slot_context, dict)
                else None
            )
            if isinstance(slot_data, dict) and int(
                slot_data.get("schema_version", 1)
            ) >= 2:
                try:
                    processed = SessionStore._read_json(
                        store._stage_path(session_id, "slots_copy")
                        / "processed-outputs.json",
                        "processed-outputs",
                    )
                except InteractionConflict as error:
                    return _validation_error(
                        {
                            "slot_assignments": (
                                "confirm the slot plan and finish image processing first: "
                                + str(error)
                            )
                        }
                    )
                if processed.get("plan_sha256") != slot_plan_sha256(
                    values["slot_assignments"]
                ):
                    return _validation_error(
                        {"slot_assignments": "processed outputs are stale"}
                    )
                output_sha_by_slot = {
                    str(slot.get("slot_id", "")): [
                        str(output.get("output_sha256", ""))
                        for output in slot.get("outputs", [])
                        if isinstance(output, dict)
                    ]
                    for slot in processed.get("slots", [])
                    if isinstance(slot, dict)
                }
                copy_by_slot = {
                    str(item.get("slot_id", "")): dict(item)
                    for item in values.get("copy_edits", [])
                    if isinstance(item, dict) and item.get("slot_id")
                }
                normalized_copy = []
                for assignment in values["slot_assignments"]:
                    slot_id = str(assignment["slot_id"])
                    copy = copy_by_slot.get(slot_id)
                    if (
                        not copy
                        or not str(copy.get("title", "")).strip()
                        or not str(copy.get("description", "")).strip()
                        or copy.get("confirmed") is not True
                    ):
                        return _validation_error(
                            {
                                "copy_edits": (
                                    f"{slot_id}: title, description and confirmation "
                                    "are required after outputs are ready"
                                )
                            }
                        )
                    prior_output_identity = copy.get("output_sha256")
                    if (
                        isinstance(prior_output_identity, list)
                        and [
                            str(value) for value in prior_output_identity
                        ]
                        != output_sha_by_slot.get(slot_id, [])
                    ):
                        return _validation_error(
                            {
                                "copy_edits": (
                                    f"{slot_id}: copy is stale because this "
                                    "slot's ordered outputs changed"
                                )
                            }
                        )
                    normalized_copy.append(
                        {
                            **copy,
                            "slot_id": slot_id,
                            "plan_sha256": processed["plan_sha256"],
                            "output_sha256": output_sha_by_slot.get(slot_id, []),
                            "status": "confirmed",
                        }
                    )
                values["copy_edits"] = normalized_copy
                copy_identity = {
                    "schema_version": 1,
                    "session_id": session_id,
                    "slot_plan_sha256": processed["plan_sha256"],
                    "status": "confirmed",
                    "copy_drafts": normalized_copy,
                    "confirmed_at": datetime.now().astimezone().isoformat(),
                }
                store._write_json_atomic(
                    store._stage_path(session_id, "slots_copy")
                    / "confirmed-copy-drafts.json",
                    copy_identity,
                )
                current_plan = read_current_slot_plan(store, session_id)
                if current_plan is not None:
                    save_current_slot_plan(
                        store,
                        session_id,
                        slot_data,
                        current_plan["slot_assignments"],
                        decision_source=str(
                            current_plan["decision_source"]
                        ),
                        context_revision=int(
                            current_plan["context_revision"]
                        ),
                        expected_plan_revision=int(
                            current_plan["plan_revision"]
                        ),
                        workflow_state="completed",
                        confirmed=True,
                        request_id=current_plan.get("agent_request_id"),
                        response_sha256=current_plan.get(
                            "agent_response_sha256"
                        ),
                        fallback_reason=current_plan.get(
                            "fallback_reason"
                        ),
                        actor="user",
                    )

        final_material_identity_sha256 = None
        if stage_id == "asset_matching" and selected_asset_bundle is not None:
            preflight = selected_asset_bundle["preflight"]
            preliminary_revision = int(
                expected_revision
                if expected_revision is not None
                else state["stages"]["asset_matching"]["revision"] + 1
            )
            stage_path = store._stage_path(session_id, "asset_matching")
            material_identity = {
                "folder_decisions_sha256": (
                    selected_asset_bundle["current_data"]
                    .get("gallery_identity", {})
                    .get("folder_decisions_sha256")
                ),
                "folder_decisions": values.get("folder_decisions", []),
                "missing_slots_by_product": selected_asset_bundle[
                    "missing_slots"
                ],
                "policy_sha256": preflight["policy_sha256"],
                "assets": preflight.get("assets", []),
            }
            final_material_identity_sha256 = hashlib.sha256(
                json.dumps(
                    material_identity,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            store._write_json_atomic(
                stage_path / "selected-asset-preflight.json",
                {
                    "schema_version": 1,
                    "session_id": session_id,
                    "stage_id": "asset_matching",
                    "revision": preliminary_revision,
                    "policy_sha256": preflight["policy_sha256"],
                    "data": preflight,
                },
            )
            store._write_json_atomic(
                stage_path / "final-material-package.json",
                {
                    "schema_version": 1,
                    "record_type": "final_material_selection",
                    "session_id": session_id,
                    "stage_id": "asset_matching",
                    "revision": preliminary_revision,
                    "input_sha256": None,
                    "folder_decisions_sha256": (
                        selected_asset_bundle["current_data"]
                        .get("gallery_identity", {})
                        .get("folder_decisions_sha256")
                    ),
                    "folder_decisions": values.get(
                        "folder_decisions", []
                    ),
                    "policy_sha256": preflight["policy_sha256"],
                    "selected_count": preflight["selected_count"],
                    "reviewable_count": preflight["reviewable_count"],
                    "blocked_count": preflight["blocked_count"],
                    "duplicate_count": preflight["duplicate_count"],
                    "missing_slots_by_product": selected_asset_bundle[
                        "missing_slots"
                    ],
                    "assets": preflight.get("assets", []),
                    "created_at": datetime.now().astimezone().isoformat(),
                    "status": "prepared",
                    "material_identity_sha256": (
                        final_material_identity_sha256
                    ),
                },
            )
        approval_authorization = None
        if stage_id == "approval":
            approval_authorization = safety_context(
                store, session_id, stage_id, values
            )
            approval_authorization.update(
                {
                    "action": "approve_and_publish_exact_tasks",
                    "confirmed_by": values.get("confirmed_by"),
                    "final_confirmation": True,
                }
            )
        handoff = store.save_input(
            session_id,
            stage_id,
            _allowlisted_values(stage, values),
            _user_notes(payload),
            expected_revision=expected_revision,
            allowed_current_statuses=(
                RESULTS_USER_ACTION_STATUSES if stage_id == "results" else None
            ),
            request_id=_persistence_request_id(payload),
            handoff_data=(
                {
                    "handoff_kind": "final_material_selection",
                    "final_material_package": {
                        "path": str(
                            store._stage_path(
                                session_id, "asset_matching"
                            )
                            / "final-material-package.json"
                        ),
                        "selected_count": selected_asset_bundle[
                            "preflight"
                        ]["selected_count"],
                        "material_identity_sha256": (
                            final_material_identity_sha256
                        ),
                        "folder_decisions_sha256": (
                            selected_asset_bundle["current_data"]
                            .get("gallery_identity", {})
                            .get("folder_decisions_sha256")
                        ),
                        "selected_asset_ids": [
                            str(item.get("asset_id", ""))
                            for item in selected_asset_bundle[
                                "preflight"
                            ].get("assets", [])
                            if isinstance(item, dict)
                            and item.get("status") != "blocked"
                            and item.get("duplicate") is not True
                        ],
                        "product_ids": sorted(
                            selected_asset_bundle["missing_slots"]
                        ),
                    },
                }
                if stage_id == "asset_matching"
                and selected_asset_bundle is not None
                else (
                    {
                        "handoff_kind": "publish_authorization",
                        "authorization": approval_authorization,
                    }
                    if approval_authorization is not None
                    else None
                )
            ),
        )
        _supersede_slot_requests_after_revision_change(
            store, session_id, stage_id
        )
        if stage_id == "asset_matching" and selected_asset_bundle is not None:
            preflight = selected_asset_bundle["preflight"]
            stage_path = store._stage_path(session_id, "asset_matching")
            slot_stage_path = store._stage_path(session_id, "slots_copy")
            invalidated_path = (
                slot_stage_path
                / "invalidated"
                / f"asset-matching-r{handoff['revision']:04d}"
            )
            stale_names = (
                "current-slot-plan.json",
                "crop-preflight.json",
                "processed-outputs.json",
                "slot-plan.snapshot.json",
                "confirmed-copy-drafts.json",
            )
            stale_paths = [
                slot_stage_path / name
                for name in stale_names
                if (slot_stage_path / name).is_file()
            ]
            if stale_paths:
                invalidated_path.mkdir(parents=True, exist_ok=True)
                for stale_path in stale_paths:
                    stale_path.replace(invalidated_path / stale_path.name)
            store._write_json_atomic(
                stage_path / "selected-asset-preflight.json",
                {
                    "schema_version": 1,
                    "session_id": session_id,
                    "stage_id": "asset_matching",
                    "revision": handoff["revision"],
                    "policy_sha256": preflight["policy_sha256"],
                    "data": preflight,
                },
            )
            material_package = {
                "schema_version": 1,
                "record_type": "final_material_selection",
                "session_id": session_id,
                "stage_id": "asset_matching",
                "revision": handoff["revision"],
                "input_sha256": handoff["input_sha256"],
                "folder_decisions_sha256": (
                    selected_asset_bundle["current_data"]
                    .get("gallery_identity", {})
                    .get("folder_decisions_sha256")
                ),
                "policy_sha256": preflight["policy_sha256"],
                "selected_count": preflight["selected_count"],
                "reviewable_count": preflight["reviewable_count"],
                "blocked_count": preflight["blocked_count"],
                "duplicate_count": preflight["duplicate_count"],
                "missing_slots_by_product": selected_asset_bundle[
                    "missing_slots"
                ],
                "folder_decisions": values.get("folder_decisions", []),
                "assets": preflight.get("assets", []),
                "selected_asset_ids": [
                    str(item.get("asset_id", ""))
                    for item in preflight.get("assets", [])
                    if isinstance(item, dict)
                    and item.get("status") != "blocked"
                    and item.get("duplicate") is not True
                ],
                "created_at": datetime.now().astimezone().isoformat(),
                "status": "ready_for_agent",
                "material_identity_sha256": (
                    final_material_identity_sha256
                ),
            }
            package_path = stage_path / "final-material-package.json"
            store._write_json_atomic(package_path, material_package)
            package_sha256 = hashlib.sha256(
                package_path.read_bytes()
            ).hexdigest()
            store._append_event(
                store._session_path(session_id),
                "final_material_handoff_created",
                session_id=session_id,
                stage_id="asset_matching",
                revision=handoff["revision"],
                input_sha256=handoff["input_sha256"],
                material_identity_sha256=(
                    final_material_identity_sha256
                ),
            )
            notify_workflow_dispatcher(session_id)
            return jsonify(
                revision=handoff["revision"],
                input_sha256=handoff["input_sha256"],
                created_at=handoff["created_at"],
                status="ready_for_agent",
                handoff_kind="final_material_selection",
                final_material_package={
                    **handoff["final_material_package"],
                    "sha256": package_sha256,
                },
                next_stage=None,
            ), 202
        if stage_id == "slots_copy":
            advanced = advance_slots_copy_after_submit(store, session_id)
            document = advanced["document"]
            return jsonify(
                revision=handoff["revision"],
                input_sha256=handoff["input_sha256"],
                created_at=handoff["created_at"],
                status="completed",
                next_stage=advanced["next_stage"],
                dry_run_status=advanced["status"],
                dry_run={
                    "product_count": document["product_count"],
                    "task_count": document["task_count"],
                    "media_count": document["media_count"],
                    "blocking_reasons": document["blocking_reasons"],
                    "warnings": document["warnings"],
                },
            ), 202
        if stage_id in {"setup", "completeness", "approval"}:
            notify_workflow_dispatcher(session_id)
        return jsonify(
            revision=handoff["revision"],
            input_sha256=handoff["input_sha256"],
            created_at=handoff["created_at"],
            status="ready_for_agent",
            next_stage=None,
        ), 202

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/withdraw")
    def withdraw_submission(session_id: str, stage_id: str):
        payload = _json_object()
        get_stage(stage_id)
        expected_revision = payload.get("revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            return _validation_error({"revision": "must be an integer"})
        result = store.withdraw_handoff(
            session_id, stage_id, expected_revision=expected_revision
        )
        return jsonify(result)

    @app.get("/api/sessions/<session_id>/stages/<stage_id>/status")
    def status(session_id: str, stage_id: str):
        state = store.load_session(session_id)
        get_stage(stage_id)
        payload = dict(state["stages"][stage_id])
        payload["handoff_status"] = store.handoff_display_state(
            session_id, stage_id
        )
        payload["stage_transaction"] = store.stage_transaction_status(
            session_id, stage_id
        )
        payload["workflow_dispatch"] = workflow_dispatch_status(session_id)
        claim = store.processing_claim(session_id, stage_id)
        if claim is not None:
            payload["processing_claim"] = claim
        if stage_id == "setup":
            payload["collection_status"] = get_collection_status(
                store.runs_root, session_id
            )
        return jsonify(payload)

    @app.get("/api/sessions/<session_id>/stages/<stage_id>/recovery")
    def recovery(session_id: str, stage_id: str):
        get_stage(stage_id)
        return jsonify(instruction=store.recovery_instruction(session_id, stage_id))

    @app.get(
        "/api/sessions/<session_id>/stages/asset_matching/assets/<asset_id>"
    )
    def asset_preview(session_id: str, asset_id: str):
        state = store.load_session(session_id)
        stage = get_stage("asset_matching")
        result = _current_result(
            store,
            session_id,
            "asset_matching",
            state,
        )
        current_input = _current_input(store, session_id, stage, state)
        if result is None or current_input is None:
            raise NotFound()
        candidates = (result.get("data") or {}).get("asset_candidates")
        roots = current_input["values"].get("image_roots")
        if not isinstance(candidates, list) or not isinstance(roots, list):
            raise NotFound()
        candidate = next(
            (
                value
                for value in candidates
                if isinstance(value, dict)
                and str(value.get("asset_id", "")) == asset_id
            ),
            None,
        )
        if candidate is None:
            raise NotFound()
        preview_cache = (
            store._stage_path(session_id, "asset_matching") / "preview-cache"
        )
        preview_path = preview_cache / f"{asset_id}.jpg"
        if preview_path.is_file():
            response = send_file(
                preview_path,
                conditional=True,
                max_age=300,
                mimetype="image/jpeg",
            )
            response.headers["Cache-Control"] = "private, max-age=300"
            return response
        try:
            source_path = Path(str(candidate.get("source_path", ""))).resolve()
            allowed_roots = [Path(str(root)).resolve() for root in roots]
            folder_decisions = current_input["values"].get("folder_decisions")
            if isinstance(folder_decisions, list):
                allowed_roots.extend(
                    Path(str(decision.get("folder_path", ""))).resolve()
                    for decision in folder_decisions
                    if isinstance(decision, dict)
                    and decision.get("decision") == "confirmed"
                    and str(decision.get("folder_path", "")).strip()
                )
            if (
                source_path.suffix.casefold()
                not in {".jpg", ".jpeg", ".png", ".webp"}
                or not source_path.is_file()
                or not any(source_path.is_relative_to(root) for root in allowed_roots)
            ):
                raise NotFound()
        except (OSError, RuntimeError, ValueError):
            raise NotFound() from None
        try:
            build_image_preview(source_path, preview_path)
        except (OSError, RuntimeError, ValueError):
            raise NotFound() from None
        response = send_file(
            preview_path,
            conditional=True,
            max_age=300,
            mimetype="image/jpeg",
        )
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    @app.get(
        "/api/sessions/<session_id>/stages/image_review/assets/<asset_id>"
    )
    def image_review_preview(session_id: str, asset_id: str):
        state = store.load_session(session_id)
        context = _current_result(store, session_id, "image_review", state)
        data = context.get("data") if isinstance(context, dict) else None
        assets = data.get("assets") if isinstance(data, dict) else None
        if not isinstance(assets, list):
            raise NotFound()
        asset = next(
            (
                item
                for item in assets
                if isinstance(item, dict)
                and str(item.get("asset_id", "")) == asset_id
            ),
            None,
        )
        if asset is None:
            raise NotFound()
        preview = (
            store._stage_path(session_id, "image_review")
            / "preview-cache"
            / f"{asset_id}.jpg"
        )
        if preview.is_file():
            response = send_file(
                preview,
                conditional=True,
                max_age=300,
                mimetype="image/jpeg",
            )
            response.headers["Cache-Control"] = "private, max-age=300"
            return response
        try:
            source_path = Path(str(asset.get("source_path", ""))).resolve()
            matching_input = store.read_optional_stage_document(
                session_id, "asset_matching", "input"
            )
            values = matching_input.get("values") if matching_input else {}
            roots = list(values.get("image_roots", [])) if isinstance(values, dict) else []
            if isinstance(values, dict) and isinstance(
                values.get("folder_decisions"), list
            ):
                roots.extend(
                    item.get("folder_path")
                    for item in values["folder_decisions"]
                    if isinstance(item, dict)
                    and item.get("decision") == "confirmed"
                    and item.get("folder_path")
                )
            allowed_roots = [Path(str(root)).resolve() for root in roots]
            if (
                source_path.suffix.casefold()
                not in {".jpg", ".jpeg", ".png", ".webp"}
                or not source_path.is_file()
                or not any(source_path.is_relative_to(root) for root in allowed_roots)
            ):
                raise NotFound()
        except (OSError, RuntimeError, TypeError, ValueError):
            raise NotFound() from None
        try:
            build_image_preview(source_path, preview)
        except (OSError, RuntimeError, ValueError):
            raise NotFound() from None
        response = send_file(
            preview,
            conditional=True,
            max_age=300,
            mimetype="image/jpeg",
        )
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    @app.post(
        "/api/sessions/<session_id>/stages/image_review/assets/<asset_id>/process"
    )
    def image_review_process_preview(session_id: str, asset_id: str):
        """Reject the legacy stage-four materialization endpoint."""

        store.load_session(session_id)
        return _error(
            "SLOT_PLAN_REQUIRED: confirm a stage-five slot ratio before processing",
            409,
        )

    @app.get(
        "/api/sessions/<session_id>/stages/image_review/outputs/<asset_id>"
    )
    def image_review_processed_output(session_id: str, asset_id: str):
        expected_sha256 = str(request.args.get("sha256", "")).strip()
        if len(expected_sha256) != 64:
            raise NotFound()
        state = store.load_session(session_id)
        context = _current_result(store, session_id, "image_review", state)
        data = context.get("data") if isinstance(context, dict) else None
        policy = data.get("policy") if isinstance(data, dict) else None
        derived_name = (
            policy.get("output", {}).get("derived_directory", "derived")
            if isinstance(policy, dict)
            else "derived"
        )
        root = (
            store._stage_path(session_id, "image_review") / str(derived_name)
        ).resolve()
        if not root.is_dir():
            raise NotFound()
        output = next(
            (
                path
                for path in root.glob(f"{asset_id}-*.jpg")
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()
                == expected_sha256
            ),
            None,
        )
        if output is None:
            raise NotFound()
        response = send_file(
            output,
            conditional=True,
            max_age=60,
            mimetype="image/jpeg",
        )
        response.headers["Cache-Control"] = "private, max-age=60"
        return response

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/assets/<asset_id>"
    )
    def slot_output_preview(session_id: str, asset_id: str):
        state = store.load_session(session_id)
        context = _current_result(store, session_id, "slots_copy", state)
        data = context.get("data") if isinstance(context, dict) else None
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list):
            raise NotFound()
        output = next(
            (
                item
                for product in products
                if isinstance(product, dict)
                for item in product.get("outputs", [])
                if isinstance(item, dict)
                and str(item.get("asset_id", "")) == asset_id
            ),
            None,
        )
        if output is None:
            raise NotFound()
        preview = (
            store._stage_path(session_id, "slots_copy")
            / "preview-cache"
            / f"{asset_id}.jpg"
        )
        if preview.is_file():
            response = send_file(
                preview,
                conditional=True,
                max_age=300,
                mimetype="image/jpeg",
            )
            response.headers["Cache-Control"] = "private, max-age=300"
            return response
        review_preview = (
            store._stage_path(session_id, "image_review")
            / "preview-cache"
            / f"{asset_id}.jpg"
        )
        if review_preview.is_file():
            response = send_file(
                review_preview,
                conditional=True,
                max_age=300,
                mimetype="image/jpeg",
            )
            response.headers["Cache-Control"] = "private, max-age=300"
            return response
        try:
            source_path = Path(
                str(output.get("output_path") or output.get("source_path", ""))
            ).resolve()
            stage_root = store._stage_path(session_id, "image_review").resolve()
            matching_input = store.read_optional_stage_document(
                session_id, "asset_matching", "input"
            )
            matching_values = matching_input.get("values") if matching_input else {}
            roots = (
                [Path(str(value)).resolve() for value in matching_values.get("image_roots", [])]
                if isinstance(matching_values, dict)
                and isinstance(matching_values.get("image_roots"), list)
                else []
            )
            if (
                not source_path.is_file()
                or source_path.suffix.casefold()
                not in {".jpg", ".jpeg", ".png", ".webp"}
                or not (
                    source_path.is_relative_to(stage_root)
                    or any(source_path.is_relative_to(root) for root in roots)
                )
            ):
                raise NotFound()
        except (OSError, RuntimeError, TypeError, ValueError):
            raise NotFound() from None
        if not preview.is_file():
            try:
                build_image_preview(source_path, preview)
            except (OSError, RuntimeError, ValueError):
                raise NotFound() from None
        response = send_file(
            preview,
            conditional=True,
            max_age=300,
            mimetype="image/jpeg",
        )
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    def _slot_processing_request(
        session_id: str, payload: dict[str, Any]
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any] | None,
        list[dict[str, Any]],
        dict[str, Any],
        Path,
        str,
    ]:
        state = store.load_session(session_id)
        if state["stages"]["slots_copy"]["status"] not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            raise InteractionConflict("slot plan is not editable")
        context = _current_result(store, session_id, "slots_copy", state)
        data = context.get("data") if isinstance(context, dict) else None
        if not isinstance(data, dict):
            raise ValueError("prepare the current slot board first")
        current_plan = read_current_slot_plan(store, session_id)
        submitted_assignments = payload.get("slot_assignments")
        if current_plan is not None:
            if (
                not current_plan.get("confirmed")
                or current_plan.get("workflow_state")
                not in {
                    "plan_confirmed",
                    "processing",
                    "outputs_ready",
                    "copy_generating",
                    "copy_review",
                }
            ):
                raise ValueError(
                    "confirm the current slot plan before processing"
                )
            submitted_assignments = current_plan["slot_assignments"]
        assignments = validate_slot_assignments(submitted_assignments, data)
        stage_path = store._stage_path(session_id, "slots_copy")
        crop_parameters = (
            payload.get("crop_parameters")
            if isinstance(payload.get("crop_parameters"), dict)
            else {}
        )
        requested_processing_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "plan_sha256": slot_plan_sha256(assignments),
                    "crop_parameters": crop_parameters,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return (
            state,
            data,
            current_plan,
            assignments,
            crop_parameters,
            stage_path,
            requested_processing_sha256,
        )

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/crop-preflight"
    )
    def get_slot_crop_preflight(session_id: str):
        store.load_session(session_id)
        path = (
            store._stage_path(session_id, "slots_copy")
            / "crop-preflight.json"
        )
        if not path.is_file():
            return jsonify(workflow_state="not_checked", slots=[])
        return jsonify(SessionStore._read_json(path, "crop-preflight"))

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/crop-preflight"
    )
    def preflight_confirmed_slot_plan(session_id: str):
        payload = _json_object()
        try:
            (
                _state,
                data,
                current_plan,
                assignments,
                crop_parameters,
                stage_path,
                requested_processing_sha256,
            ) = _slot_processing_request(session_id, payload)
            preflight_path = stage_path / "crop-preflight.json"
            if preflight_path.is_file():
                existing = SessionStore._read_json(
                    preflight_path, "crop-preflight"
                )
                if (
                    existing.get("processing_sha256")
                    == requested_processing_sha256
                ):
                    return jsonify(existing)
            processed = materialize_confirmed_slot_plan(
                assignments,
                data,
                derived_root=stage_path / "derived",
                crop_parameters=crop_parameters,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return _validation_error({"slot_assignments": str(error)})
        preflight = {
            **processed,
            "workflow_state": "crop_preflight_passed",
            "crop_parameters": crop_parameters,
            "checked_count": sum(
                len(slot.get("outputs", []))
                for slot in processed.get("slots", [])
                if isinstance(slot, dict)
            ),
            "minimum_size_bytes": 204800,
            "checked_at": datetime.now().astimezone().isoformat(),
        }
        if current_plan is not None:
            updated = save_current_slot_plan(
                store,
                session_id,
                data,
                current_plan["slot_assignments"],
                decision_source=str(current_plan["decision_source"]),
                context_revision=int(current_plan["context_revision"]),
                expected_plan_revision=int(current_plan["plan_revision"]),
                workflow_state="processing",
                confirmed=True,
                request_id=current_plan.get("agent_request_id"),
                response_sha256=current_plan.get("agent_response_sha256"),
                fallback_reason=current_plan.get("fallback_reason"),
                actor="system",
            )
            preflight["plan_revision"] = int(updated["plan_revision"])
        store._write_json_atomic(preflight_path, preflight)
        return jsonify(preflight)

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/process-plan"
    )
    def process_confirmed_slot_plan(session_id: str):
        payload = _json_object()
        try:
            (
                _state,
                data,
                current_plan,
                assignments,
                crop_parameters,
                stage_path,
                requested_processing_sha256,
            ) = _slot_processing_request(session_id, payload)
            preflight_path = stage_path / "crop-preflight.json"
            if not preflight_path.is_file():
                return _error(
                    "validation failed",
                    422,
                    {"crop_preflight": "请先点击“裁剪预校验”"},
                    reason_code="CROP_PREFLIGHT_REQUIRED",
                )
            preflight = SessionStore._read_json(
                preflight_path, "crop-preflight"
            )
            if (
                preflight.get("workflow_state")
                != "crop_preflight_passed"
                or preflight.get("processing_sha256")
                != requested_processing_sha256
            ):
                return _error(
                    "validation failed",
                    422,
                    {"crop_preflight": "裁剪参数已变化，请重新执行预校验"},
                    reason_code="CROP_PREFLIGHT_STALE",
                )
            root = (stage_path / "derived").resolve()
            verified_slots: list[dict[str, Any]] = []
            for slot in preflight.get("slots", []):
                if not isinstance(slot, dict):
                    continue
                verified_outputs = []
                for output in slot.get("outputs", []):
                    if not isinstance(output, dict):
                        continue
                    verified = validate_final_output(
                        output, policy=data.get("policy", default_image_policy())
                    )
                    if not Path(verified["output_path"]).resolve().is_relative_to(root):
                        raise ValueError("OUTPUT_PATH_OUTSIDE_TASK")
                    verified_outputs.append(verified)
                verified_slots.append({**slot, "outputs": verified_outputs})
            processed = {
                **preflight,
                "workflow_state": "outputs_ready",
                "crop_parameters": crop_parameters,
                "slots": verified_slots,
                "finalized_at": datetime.now().astimezone().isoformat(),
            }
            processed_path = stage_path / "processed-outputs.json"
            existing_processed = (
                SessionStore._read_json(processed_path, "processed-outputs")
                if processed_path.is_file()
                else None
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return _validation_error({"slot_assignments": str(error)})

        if (
            isinstance(existing_processed, dict)
            and existing_processed.get("processing_sha256")
            == requested_processing_sha256
            and existing_processed.get("workflow_state") == "outputs_ready"
        ):
            try:
                copy_request = create_copy_draft_request(
                    store,
                    session_id,
                    copy_provider="qianniu_builtin_ai",
                    regenerate=False,
                    board_data=data,
                )
                notify_workflow_dispatcher(session_id)
            except (
                AgentRequestError,
                InteractionConflict,
                OSError,
                TypeError,
                ValueError,
            ) as error:
                return _validation_error({"copy_request": str(error)})
            return jsonify(
                {
                    **existing_processed,
                    "copy_request_id": copy_request["request_id"],
                    "copy_request_status": copy_request["status"],
                    "copy_request": copy_request,
                }
            )

        store._write_json_atomic(processed_path, processed)
        invalidated_copy_slots: set[str] = set()
        if isinstance(existing_processed, dict):
            previous_outputs = {
                (
                    str(slot.get("slot_id", "")),
                    str(output.get("asset_id", "")),
                ): str(output.get("output_sha256", ""))
                for slot in existing_processed.get("slots", [])
                if isinstance(slot, dict)
                for output in slot.get("outputs", [])
                if isinstance(output, dict)
            }
            for slot in processed.get("slots", []):
                if not isinstance(slot, dict):
                    continue
                slot_id = str(slot.get("slot_id", ""))
                if any(
                    previous_outputs.get(
                        (slot_id, str(output.get("asset_id", "")))
                    )
                    != str(output.get("output_sha256", ""))
                    for output in slot.get("outputs", [])
                    if isinstance(output, dict)
                ):
                    invalidated_copy_slots.add(slot_id)
        confirmed_copy_path = stage_path / "confirmed-copy-drafts.json"
        if invalidated_copy_slots and confirmed_copy_path.is_file():
            confirmed_copy = SessionStore._read_json(
                confirmed_copy_path, "confirmed-copy-drafts"
            )
            confirmed_copy["copy_drafts"] = [
                item
                for item in confirmed_copy.get("copy_drafts", [])
                if isinstance(item, dict)
                and str(item.get("slot_id", "")) not in invalidated_copy_slots
            ]
            confirmed_copy["invalidated_slot_ids"] = sorted(
                invalidated_copy_slots
            )
            confirmed_copy["invalidation_reason"] = "IMAGE_OUTPUT_CHANGED"
            store._write_json_atomic(confirmed_copy_path, confirmed_copy)
        store._write_json_atomic(
            stage_path / "slot-plan.snapshot.json",
            {
                "schema_version": 1,
                "session_id": session_id,
                "stage_id": "slots_copy",
                "workflow_state": "outputs_ready",
                "image_review_revision": data.get("image_review_revision"),
                "policy_sha256": data.get("policy_sha256"),
                "plan_sha256": processed["plan_sha256"],
                "slot_assignments": assignments,
            },
        )
        if current_plan is not None:
            save_current_slot_plan(
                store,
                session_id,
                data,
                current_plan["slot_assignments"],
                decision_source=str(current_plan["decision_source"]),
                context_revision=int(current_plan["context_revision"]),
                expected_plan_revision=int(current_plan["plan_revision"]),
                workflow_state="outputs_ready",
                confirmed=True,
                request_id=current_plan.get("agent_request_id"),
                response_sha256=current_plan.get(
                    "agent_response_sha256"
                ),
                fallback_reason=current_plan.get("fallback_reason"),
                actor="system",
            )
        try:
            copy_request = create_copy_draft_request(
                store,
                session_id,
                copy_provider="qianniu_builtin_ai",
                regenerate=False,
                board_data=data,
            )
            notify_workflow_dispatcher(session_id)
            processed["copy_request_id"] = copy_request["request_id"]
            processed["copy_request_status"] = copy_request["status"]
            store._write_json_atomic(processed_path, processed)
        except (AgentRequestError, InteractionConflict, OSError, TypeError, ValueError) as error:
            return _validation_error({"copy_request": str(error)})
        return jsonify({**processed, "copy_request": copy_request})

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/processed-outputs"
    )
    def get_processed_slot_outputs(session_id: str):
        store.load_session(session_id)
        path = (
            store._stage_path(session_id, "slots_copy")
            / "processed-outputs.json"
        )
        if not path.is_file():
            return jsonify(workflow_state="planning", slots=[])
        return jsonify(SessionStore._read_json(path, "processed-outputs"))

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/"
        "processed-assets/<slot_id>/<int:order>"
    )
    def get_processed_slot_asset(
        session_id: str, slot_id: str, order: int
    ):
        path = (
            store._stage_path(session_id, "slots_copy")
            / "processed-outputs.json"
        )
        processed = SessionStore._read_json(path, "processed-outputs")
        output = next(
            (
                output
                for slot in processed.get("slots", [])
                if str(slot.get("slot_id", "")) == slot_id
                for output in slot.get("outputs", [])
                if int(output.get("order", -1)) == order
            ),
            None,
        )
        if not isinstance(output, dict):
            raise NotFound()
        output_path = Path(str(output.get("output_path", ""))).resolve()
        derived = (
            store._stage_path(session_id, "slots_copy") / "derived"
        ).resolve()
        if (
            not output_path.is_relative_to(derived)
            or not output_path.is_file()
        ):
            raise NotFound()
        response = send_file(output_path, conditional=True, max_age=300)
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    @app.get(
        "/api/sessions/<session_id>/stages/<stage_id>/decisions/"
        "<decision_id>/mode"
    )
    def get_stage_decision_mode(
        session_id: str, stage_id: str, decision_id: str
    ):
        boundary = get_decision_boundary(stage_id, decision_id)
        value = store.read_decision_mode(
            session_id, stage_id, decision_id
        )
        return jsonify(mode=value, boundary=boundary.as_dict())

    @app.post(
        "/api/sessions/<session_id>/stages/<stage_id>/decisions/"
        "<decision_id>/mode"
    )
    def set_stage_decision_mode(
        session_id: str, stage_id: str, decision_id: str
    ):
        payload = _json_object()
        state = store.load_session(session_id)
        expected_revision = int(
            payload.get(
                "revision", state["stages"][stage_id]["revision"]
            )
        )
        written = store.write_decision_mode(
            session_id,
            stage_id,
            decision_id,
            str(payload.get("mode", "")),
            selected_by="user",
            expected_revision=expected_revision,
        )
        if (
            stage_id == "slots_copy"
            and decision_id == "slot_plan"
            and written["mode"] != "agent_assisted"
        ):
            _cancel_open_slot_agent_requests(
                store,
                session_id,
                reason_code="AGENT_REQUEST_CANCELLED",
            )
        return jsonify(mode=written)

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/agent-requests"
    )
    def create_slot_agent_request(session_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        if state.get("workflow_profile") == "deterministic-manual-v1":
            return _error(
                "new tasks do not support AI slot planning",
                410,
                {"agent_request": "请使用自动坑位草稿或人工调整"},
                reason_code="SLOT_AI_PLANNING_REMOVED",
                message="新任务仅使用确定性编排和人工调整",
                allowed_actions=["edit_current_slot_plan"],
            )
        context = _current_result(store, session_id, "slots_copy", state)
        data = context.get("data") if isinstance(context, dict) else None
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list):
            return _validation_error(
                {"agent_request": "prepare the current slot board first"}
            )
        current_revision = int(state["stages"]["slots_copy"]["revision"])
        requested_kind = str(
            payload.get("kind", "slot_plan")
        )
        current_plan = read_current_slot_plan(store, session_id)
        force_replan = payload.get("force_replan") is True
        if force_replan:
            expected_plan_revision = int(payload.get("plan_revision", -1))
            actual_plan_revision = int(
                current_plan.get("plan_revision", 0)
                if current_plan
                else 0
            )
            if expected_plan_revision != actual_plan_revision:
                raise InteractionConflict("CURRENT_SLOT_PLAN_REVISION_STALE")
            _cancel_open_slot_agent_requests(
                store,
                session_id,
                reason_code="AGENT_REQUEST_SUPERSEDED",
            )
        else:
            existing = find_equivalent_agent_request(
                store,
                session_id,
                kind=requested_kind,
                context_revision=current_revision,
            )
            if existing is not None:
                return jsonify(existing)
        store.write_decision_mode(
            session_id,
            "slots_copy",
            "slot_plan",
            "agent_assisted",
            selected_by="user",
            expected_revision=current_revision,
        )
        requested_ids = payload.get("asset_ids")
        requested_ids = (
            {str(value) for value in requested_ids}
            if isinstance(requested_ids, list)
            else None
        )
        candidates_by_id: dict[str, dict[str, Any]] = {}
        for product in products:
            if not isinstance(product, dict):
                continue
            for candidate in product.get("outputs", []):
                if not isinstance(candidate, dict):
                    continue
                asset_id = str(candidate.get("asset_id", ""))
                if requested_ids is not None and asset_id not in requested_ids:
                    continue
                current = candidates_by_id.setdefault(
                    asset_id,
                    {
                        "asset_id": asset_id,
                        "product_id": str(candidate.get("product_id", "")),
                        "product_title": str(
                            candidate.get("product_title")
                            or product.get("product_title", "")
                        ),
                        "source_path": str(candidate.get("source_path", "")),
                        "source_sha256": str(candidate.get("source_sha256", "")),
                        "source_system": str(candidate.get("source_system", "")),
                        "width": candidate.get("width"),
                        "height": candidate.get("height"),
                        "size_bytes": candidate.get("size_bytes"),
                        "size_display": str(candidate.get("size_display", "")),
                        "original_ratio": str(
                            candidate.get("original_ratio", "")
                        ),
                        "format": str(candidate.get("format", "")),
                        "ratio_options": {},
                    },
                )
                current["ratio_options"][str(candidate.get("target_ratio", ""))] = {
                    "crop_box": candidate.get("crop_box"),
                    "native_ratio": bool(candidate.get("native_ratio")),
                    "requires_compression": bool(
                        candidate.get("requires_compression")
                    ),
                }
        try:
            created = create_agent_request(
                store,
                session_id,
                kind=requested_kind,
                candidates=candidates_by_id.values(),
                context_revision=int(state["stages"]["slots_copy"]["revision"]),
                max_images=int(payload.get("max_images", 30)),
                max_edge=int(payload.get("thumbnail_max_edge", 768)),
                max_proposals=int(payload.get("max_proposals", 2)),
                request_context={
                    "image_review_revision": data.get(
                        "image_review_revision"
                    ),
                    "policy_sha256": data.get("policy_sha256"),
                    "remaining_slots": {
                        str(product.get("product_id", "")): int(
                            product.get("remaining_slots", 9)
                        )
                        for product in products
                        if isinstance(product, dict)
                    },
                    "baseline_plan_revision": int(
                        current_plan.get("plan_revision", 0)
                        if current_plan
                        else 0
                    ),
                    "allow_replace": force_replan,
                },
            )
        except AgentRequestError as error:
            return _error(
                "agent request failed",
                422,
                {"agent_request": error.message},
                reason_code=error.reason_code,
                message=error.message,
                allowed_actions=list(error.allowed_actions),
            )
        except (OSError, TypeError, ValueError) as error:
            return _validation_error({"agent_request": str(error)})
        return jsonify(created), 201

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/agent-requests"
    )
    def list_slot_agent_requests(session_id: str):
        root = (
            store._stage_path(session_id, "slots_copy") / "agent-requests"
        )
        requests = []
        if root.is_dir():
            for path in sorted(root.iterdir(), reverse=True):
                if not path.is_dir():
                    continue
                try:
                    requests.append(
                        read_agent_request(store, session_id, path.name)
                    )
                except (InteractionConflict, ValueError):
                    continue
        return jsonify(requests=requests)

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/agent-requests/<request_id>"
    )
    def get_slot_agent_request(session_id: str, request_id: str):
        state = store.load_session(session_id)
        value = read_agent_request(store, session_id, request_id)
        response_path = (
            store._stage_path(session_id, "slots_copy")
            / "agent-requests"
            / request_id
            / "response.json"
        )
        response = None
        if response_path.is_file():
            try:
                response = SessionStore._read_json(response_path, "response")
            except InteractionConflict:
                response = {"status": "invalid", "reason_code": "AGENT_RESPONSE_INVALID"}
        progress_path = (
            store._stage_path(session_id, "slots_copy")
            / "agent-requests"
            / request_id
            / "progress.json"
        )
        progress = None
        if progress_path.is_file():
            try:
                progress = SessionStore._read_json(
                    progress_path, "copy-progress"
                )
            except InteractionConflict:
                progress = {
                    "status": "invalid",
                    "reason_code": "COPY_PROGRESS_INVALID",
                }
        if (
            state.get("workflow_profile") != "deterministic-manual-v1"
            and value.get("kind") == "slot_plan_with_analysis"
            and value.get("status") == "completed"
            and isinstance(response, dict)
            and isinstance(response.get("result"), dict)
        ):
            context = _current_result(
                store, session_id, "slots_copy", state
            )
            data = context.get("data") if isinstance(context, dict) else None
            if isinstance(data, dict):
                current = read_current_slot_plan(store, session_id)
                request_context = value.get("request_context", {})
                if not isinstance(request_context, dict):
                    request_context = {}
                baseline_plan_revision = int(
                    request_context.get("baseline_plan_revision", 0)
                )
                can_replace = (
                    request_context.get("allow_replace") is True
                    and current is not None
                    and int(current.get("plan_revision", -1))
                    == baseline_plan_revision
                )
                can_load = current is None or can_replace or (
                    current.get("agent_request_id") == request_id
                    and current.get("decision_source") != "manual_override"
                )
                if can_load and (
                    current is None
                    or can_replace
                    or int(current.get("context_revision", -1))
                    != int(value.get("context_revision", -2))
                    or not current.get("slot_assignments")
                ):
                    ai_values = ai_assignments(
                        response["result"].get("slot_plan", []),
                        request_id=request_id,
                    )
                    if ai_values:
                        save_current_slot_plan(
                            store,
                            session_id,
                            data,
                            ai_values,
                            decision_source="agent_assisted",
                            context_revision=int(
                                value["context_revision"]
                            ),
                            request_id=request_id,
                            response_sha256=response_sha256(response_path),
                            actor="system",
                        )
        return jsonify(request=value, response=response, progress=progress)

    @app.get(
        "/api/sessions/<session_id>/stages/slots_copy/current-slot-plan"
    )
    def get_current_slot_plan(session_id: str):
        state = store.load_session(session_id)
        current = read_current_slot_plan(store, session_id)
        deterministic_profile = (
            state.get("workflow_profile") == "deterministic-manual-v1"
        )
        workflow_state = (
            current.get("workflow_state") if current else None
        )
        return jsonify(
            current_slot_plan=current,
            ai_default=(
                False
                if deterministic_profile
                else ai_default_slot_planning_enabled()
            ),
            three_step_ui=(
                False
                if deterministic_profile
                else three_step_slot_ui_enabled()
            ),
            workflow_page=(
                two_step_workflow_page(workflow_state)
                if deterministic_profile
                else workflow_page(workflow_state)
            ),
            stage_revision=state["stages"]["slots_copy"]["revision"],
        )

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/current-slot-plan"
    )
    def update_current_slot_plan(session_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        context = _current_result(store, session_id, "slots_copy", state)
        data = context.get("data") if isinstance(context, dict) else None
        if not isinstance(data, dict):
            return _validation_error(
                {"current_slot_plan": "prepare the current slot board first"}
            )
        _cancel_open_slot_agent_requests(
            store,
            session_id,
            reason_code="AGENT_REQUEST_SUPERSEDED",
        )
        try:
            written = mark_manual_override(
                store,
                session_id,
                data,
                payload.get("slot_assignments", []),
                context_revision=int(
                    state["stages"]["slots_copy"]["revision"]
                ),
                expected_plan_revision=int(
                    payload.get("plan_revision", 0)
                ),
            )
        except (TypeError, ValueError) as error:
            return _validation_error(
                {"slot_assignments": str(error)}
            )
        if (
            current := read_current_slot_plan(store, session_id)
        ) is not None and int(current.get("plan_revision", 0)) == int(
            written["plan_revision"]
        ):
            # The written plan is authoritative. Any prior output/copy metadata
            # is now stale and is archived without deleting derived source files.
            _archive_slot_output_metadata(
                store,
                session_id,
                label=f"manual-r{written['plan_revision']:04d}",
            )
        return jsonify(current_slot_plan=written)

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/current-slot-plan/replan"
    )
    def replan_current_slot_plan(session_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        if state.get("workflow_profile") != "deterministic-manual-v1":
            raise InteractionConflict(
                "deterministic replan is available only for new tasks"
            )
        context = _current_result(store, session_id, "slots_copy", state)
        board_data = context.get("data") if isinstance(context, dict) else None
        current = read_current_slot_plan(store, session_id)
        if not isinstance(board_data, dict) or current is None:
            return _validation_error(
                {"current_slot_plan": "prepare the current slot board first"}
            )
        expected_revision = int(payload.get("plan_revision", -1))
        if expected_revision != int(current["plan_revision"]):
            raise InteractionConflict("CURRENT_SLOT_PLAN_REVISION_STALE")
        prior_plan = board_data.get("deterministic_plan", {})
        missing_slots = {
            str(item.get("product_id", "")): int(
                item.get("missing_slots") or 0
            )
            for item in prior_plan.get("products", [])
            if isinstance(item, dict)
        }
        # Build before changing any active artifact so a deterministic failure
        # leaves the current manual or automatic draft untouched.
        try:
            deterministic = build_deterministic_slot_plan(
                board_data,
                missing_slots_by_product=missing_slots,
            )
        except (TypeError, ValueError) as error:
            return _validation_error(
                {
                    "current_slot_plan": (
                        f"自动编排失败，当前草稿保持不变：{error}"
                    )
                }
            )
        board_data["deterministic_plan"] = deterministic
        stage_path = store._stage_path(session_id, "slots_copy")
        invalidated = (
            stage_path
            / "invalidated"
            / f"replan-r{expected_revision + 1:04d}"
        )
        stale_paths = [
            stage_path / name
            for name in (
                "crop-preflight.json",
                "processed-outputs.json",
                "slot-plan.snapshot.json",
                "confirmed-copy-drafts.json",
            )
            if (stage_path / name).is_file()
        ]
        if stale_paths:
            invalidated.mkdir(parents=True, exist_ok=True)
            for stale_path in stale_paths:
                stale_path.replace(invalidated / stale_path.name)
        written = save_current_slot_plan(
            store,
            session_id,
            board_data,
            deterministic["assignments"],
            decision_source="deterministic",
            context_revision=int(
                state["stages"]["slots_copy"]["revision"]
            ),
            expected_plan_revision=expected_revision,
            workflow_state="plan_review",
            confirmed=False,
            actor="user",
        )
        store.write_review_context(session_id, "slots_copy", context)
        return jsonify(
            current_slot_plan=written,
            deterministic_plan=deterministic,
        )

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/current-slot-plan/confirm"
    )
    def confirm_current_slot_plan(session_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        context = _current_result(store, session_id, "slots_copy", state)
        data = context.get("data") if isinstance(context, dict) else None
        current = read_current_slot_plan(store, session_id)
        if not isinstance(data, dict) or current is None:
            return _validation_error(
                {"current_slot_plan": "a current plan is required"}
            )
        expected_revision = int(
            payload.get("plan_revision", current["plan_revision"])
        )
        if expected_revision != int(current["plan_revision"]):
            raise InteractionConflict("CURRENT_SLOT_PLAN_REVISION_STALE")
        if (
            current.get("confirmed") is True
            and current.get("workflow_state")
            in {
                "plan_confirmed",
                "processing",
                "outputs_ready",
                "copy_generating",
                "copy_review",
                "completed",
            }
        ):
            return jsonify(current_slot_plan=current)
        try:
            written = save_current_slot_plan(
                store,
                session_id,
                data,
                current["slot_assignments"],
                decision_source=str(current["decision_source"]),
                context_revision=int(current["context_revision"]),
                expected_plan_revision=expected_revision,
                workflow_state="plan_confirmed",
                confirmed=True,
                request_id=current.get("agent_request_id"),
                response_sha256=current.get("agent_response_sha256"),
                fallback_reason=current.get("fallback_reason"),
                actor="user",
            )
        except (TypeError, ValueError) as error:
            return _validation_error(
                {"slot_assignments": str(error)}
            )
        return jsonify(current_slot_plan=written)

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/agent-requests/"
        "<request_id>/cancel"
    )
    def cancel_slot_agent_request(session_id: str, request_id: str):
        value = cancel_agent_request(
            store, session_id, request_id, actor="user"
        )
        return jsonify(request=value)

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/agent-requests/"
        "<request_id>/adopt"
    )
    def adopt_slot_agent_proposal(session_id: str, request_id: str):
        payload = _json_object()
        state = store.load_session(session_id)
        request_document = read_agent_request(
            store, session_id, request_id
        )
        if request_document["status"] != "completed":
            raise InteractionConflict("Agent request is not completed")
        response_path = (
            store._stage_path(session_id, "slots_copy")
            / "agent-requests"
            / request_id
            / "response.json"
        )
        response_document = SessionStore._read_json(
            response_path, "response"
        )
        proposals = response_document.get("result", {}).get(
            "proposals", []
        )
        proposal_index = int(payload.get("proposal_index", 0))
        if not 0 <= proposal_index < len(proposals):
            return _validation_error(
                {"proposal_index": "AI proposal does not exist"}
            )
        proposal = proposals[proposal_index]
        context = _current_result(
            store, session_id, "slots_copy", state
        )
        data = context.get("data") if isinstance(context, dict) else None
        if not isinstance(data, dict):
            raise InteractionConflict("slot board is unavailable")
        assignment = {
            "slot_id": str(
                proposal.get("slot_id")
                or f"{proposal.get('product_id')}-slot-1"
            ),
            "product_id": str(proposal.get("product_id", "")),
            "target_ratio": str(proposal.get("target_ratio", "")),
            "asset_ids": [
                str(value)
                for value in proposal.get("ordered_asset_ids", [])
            ],
            "plan_source": "agent_assisted",
            "agent_request_id": request_id,
            "agent_response_model": response_document.get(
                "response_model"
            ),
        }
        validate_slot_assignments([assignment], data)
        current_input = store.read_optional_stage_document(
            session_id, "slots_copy", "input"
        )
        values = (
            dict(current_input.get("values", {}))
            if current_input
            else {}
        )
        assignments = [
            item
            for item in values.get("slot_assignments", [])
            if isinstance(item, dict)
            and str(item.get("product_id", ""))
            != assignment["product_id"]
        ]
        assignments.append(assignment)
        values["slot_assignments"] = assignments
        revision = int(state["stages"]["slots_copy"]["revision"])
        written = store.save_draft(
            session_id,
            "slots_copy",
            values,
            str(values.get("user_notes", "")),
            expected_revision=revision,
        )
        adoption = {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "slots_copy",
            "request_id": request_id,
            "response_sha256": hashlib.sha256(
                response_path.read_bytes()
            ).hexdigest(),
            "proposal_index": proposal_index,
            "product_id": assignment["product_id"],
            "adopted_at": datetime.now().astimezone().isoformat(),
            "draft_revision": written["revision"],
        }
        adoption_path = (
            store._stage_path(session_id, "slots_copy")
            / "agent-requests"
            / request_id
            / "adoption.json"
        )
        store._write_json_atomic(adoption_path, adoption)
        store.append_decision_event(
            session_id,
            "slots_copy",
            "slot_plan",
            "agent_proposal_adopted",
            request_id=request_id,
            proposal_index=proposal_index,
            product_id=assignment["product_id"],
            revision=written["revision"],
        )
        return jsonify(
            status="draft",
            revision=written["revision"],
            assignment=assignment,
            adoption=adoption,
        )

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/agent-requests/"
        "<request_id>/reject"
    )
    def reject_slot_agent_proposal(session_id: str, request_id: str):
        payload = _json_object()
        read_agent_request(store, session_id, request_id)
        rejection = {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "slots_copy",
            "request_id": request_id,
            "proposal_index": int(payload.get("proposal_index", 0)),
            "reason": str(payload.get("reason", "")),
            "rejected_at": datetime.now().astimezone().isoformat(),
        }
        path = (
            store._stage_path(session_id, "slots_copy")
            / "agent-requests"
            / request_id
            / "rejection.json"
        )
        store._write_json_atomic(path, rejection)
        store.append_decision_event(
            session_id,
            "slots_copy",
            "slot_plan",
            "agent_proposal_rejected",
            request_id=request_id,
            proposal_index=rejection["proposal_index"],
        )
        return jsonify(rejection=rejection)

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/copy-request"
    )
    def create_slot_copy_request(session_id: str):
        payload = _json_object()
        copy_provider = str(
            payload.get("provider", "qianniu_builtin_ai")
        ).strip()
        try:
            state = store.load_session(session_id)
            context = _current_result(store, session_id, "slots_copy", state)
            board_data = (
                context.get("data") if isinstance(context, dict) else {}
            )
            created = create_copy_draft_request(
                store,
                session_id,
                copy_provider=copy_provider,
                regenerate=payload.get("regenerate") is True,
                board_data=board_data,
            )
            notify_workflow_dispatcher(session_id)
        except (
            AgentRequestError,
            InteractionConflict,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            return _validation_error({"copy_request": str(error)})
        return jsonify(created), 201

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/copy-drafts/confirm"
    )
    def confirm_slot_copy_drafts(session_id: str):
        payload = _json_object()
        request_id = str(payload.get("request_id", ""))
        request_document = read_agent_request(store, session_id, request_id)
        if (
            request_document.get("kind") != "copy_draft"
            or request_document.get("status") != "completed"
        ):
            raise InteractionConflict("copy request is not completed")
        response_path = (
            store._stage_path(session_id, "slots_copy")
            / "agent-requests"
            / request_id
            / "response.json"
        )
        response_document = SessionStore._read_json(
            response_path, "copy-response"
        )
        originals = {
            str(item.get("slot_id", "")): item
            for item in response_document.get("result", {}).get(
                "copy_drafts", []
            )
            if isinstance(item, dict)
        }
        edits = payload.get("copy_edits")
        if not isinstance(edits, list) or set(originals) != {
            str(item.get("slot_id", ""))
            for item in edits
            if isinstance(item, dict)
        }:
            return _validation_error(
                {"copy_edits": "all current slots must be present"}
            )
        trusted_slots = {
            str(item.get("slot_id", "")): item
            for item in request_document.get("request_context", {}).get(
                "slots", []
            )
            if isinstance(item, dict)
        }
        normalized = []
        for item in edits:
            slot_id = str(item.get("slot_id", ""))
            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            reasons = validate_copy(
                title,
                description,
                trusted_slots.get(slot_id, {}).get(
                    "trusted_source_fields", {}
                ),
                [],
            )
            if reasons or item.get("confirmed") is not True:
                return _validation_error(
                    {
                        "copy_edits": (
                            f"{slot_id}: copy must be valid and confirmed "
                            f"({','.join(reasons)})"
                        )
                    }
                )
            normalized.append(
                {
                    **originals[slot_id],
                    "title": title,
                    "description": description,
                    "confirmed": True,
                    "source": (
                        "manual_override"
                        if (
                            title != originals[slot_id].get("title")
                            or description
                            != originals[slot_id].get("description")
                        )
                        else str(
                            originals[slot_id].get("source")
                            or "agent_assisted"
                        )
                    ),
                }
            )
        document = {
            "schema_version": 1,
            "session_id": session_id,
            "request_id": request_id,
            "slot_plan_revision": request_document.get(
                "request_context", {}
            ).get("slot_plan_revision"),
            "final_outputs_sha256": request_document.get(
                "request_context", {}
            ).get("final_outputs_sha256"),
            "status": "confirmed",
            "copy_drafts": normalized,
            "confirmed_at": datetime.now().astimezone().isoformat(),
        }
        stage_path = store._stage_path(session_id, "slots_copy")
        store._write_json_atomic(
            stage_path / "confirmed-copy-drafts.json", document
        )
        state = store.load_session(session_id)
        context = _current_result(store, session_id, "slots_copy", state)
        board_data = (
            context.get("data") if isinstance(context, dict) else {}
        )
        current = read_current_slot_plan(store, session_id)
        if current is not None:
            save_current_slot_plan(
                store,
                session_id,
                board_data,
                current["slot_assignments"],
                decision_source=str(current["decision_source"]),
                context_revision=int(current["context_revision"]),
                expected_plan_revision=int(current["plan_revision"]),
                workflow_state="completed",
                confirmed=True,
                request_id=current.get("agent_request_id"),
                response_sha256=current.get(
                    "agent_response_sha256"
                ),
                fallback_reason=current.get("fallback_reason"),
                actor="user",
            )
        return jsonify(document)

    if service_identity is not None and managed_session_id:
        workflow_dispatcher = WorkflowDispatcher(
            store,
            managed_session_id,
            WorkflowProcessor(
                store,
                runtime,
                local_resource_identity=expected_runtime_identity,
            ),
        )
        workflow_dispatcher.start()
        app.extensions["tmall_workflow_dispatcher"] = workflow_dispatcher

    return app


def _normalize_stage_values(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    state: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(values)
    if stage_id == "setup":
        # Older cached setup pages still submit this removed field. Ignoring it
        # keeps an already-open page usable without retaining it in handoffs.
        normalized.pop("month", None)
        return normalized
    if stage_id == "approval":
        # Older cached pages included a second confirmation checkbox. The
        # clearly labelled submit action is now the sole publish authority.
        normalized.pop("acknowledgement", None)
        return normalized
    if stage_id != "asset_matching":
        return normalized
    normalized.pop("aliases", None)
    # The production workflow is image-only.  Older pages exposed this as a
    # required user field and could persist an empty list during hydration.
    prior_input = store.read_optional_stage_document(
        session_id, stage_id, "input"
    )
    prior_source_types = (
        prior_input.get("values", {}).get("source_types")
        if isinstance(prior_input, dict)
        and isinstance(prior_input.get("values"), dict)
        else None
    )
    normalized["source_types"] = (
        list(prior_source_types)
        if isinstance(prior_source_types, list)
        and any(str(item).strip() for item in prior_source_types)
        else ["image"]
    )
    result = _current_result(store, session_id, stage_id, state) or {}
    data = result.get("data") if isinstance(result, dict) else {}
    data = data if isinstance(data, dict) else {}
    folders = data.get("folder_candidates")
    supported_folder_keys: set[tuple[str, str]] | None = None
    supported_products: set[str] | None = None
    folder_rows: list[dict[str, Any]] = []
    if isinstance(folders, list):
        folder_rows = [
            item
            for item in folders
            if isinstance(item, dict)
            and item.get("match_type") != "confirmed_alias"
            and item.get("product_id")
            and item.get("folder_id")
        ]
        supported_folder_keys = {
            (
                str(item.get("product_id", "")),
                str(item.get("folder_id", "")),
            )
            for item in folder_rows
        }
        supported_products = {key[0] for key in supported_folder_keys}

    decisions = normalized.get("folder_decisions")
    decision_rows = decisions if isinstance(decisions, list) else []
    decision_by_key = {
        (
            str(item.get("product_id", "")),
            str(item.get("folder_id", "")),
        ): item
        for item in decision_rows
        if isinstance(item, dict)
        and item.get("product_id")
        and item.get("folder_id")
    }

    def folder_decision_for(item: dict[str, Any]) -> str:
        key = (
            str(item.get("product_id", "")),
            str(item.get("folder_id", "")),
        )
        submitted = str(decision_by_key.get(key, {}).get("decision", ""))
        if submitted in {"confirmed", "rejected"}:
            return submitted
        if (
            str(item.get("decision", "")) == "rejected"
            or str(item.get("match_type", ""))
            in {"fuzzy_name_candidate", "short_split_name_candidate"}
        ):
            return "rejected"
        return "confirmed"

    if folder_rows:
        normalized["folder_decisions"] = [
            {
                "folder_id": str(item.get("folder_id", "")),
                "product_id": str(item.get("product_id", "")),
                "source_system": str(item.get("source_system", "")),
                "source_id": str(
                    item.get("source_id") or item.get("source_system", "")
                ),
                "relative_path": str(item.get("relative_path", "")),
                "folder_path": str(item.get("folder_path", "")),
                "decision": folder_decision_for(item),
                "note": str(
                    decision_by_key.get(
                        (
                            str(item.get("product_id", "")),
                            str(item.get("folder_id", "")),
                        ),
                        {},
                    ).get("note", "")
                ),
            }
            for item in folder_rows
        ]
    elif isinstance(decisions, list):
        normalized["folder_decisions"] = [
            {
                "folder_id": str(item.get("folder_id", "")),
                "product_id": str(item.get("product_id", "")),
                "source_system": str(item.get("source_system", "")),
                "source_id": str(
                    item.get("source_id") or item.get("source_system", "")
                ),
                "relative_path": str(item.get("relative_path", "")),
                "folder_path": str(item.get("folder_path", "")),
                "decision": (
                    "rejected"
                    if str(item.get("decision", "")) == "rejected"
                    else "confirmed"
                ),
                "note": str(item.get("note", "")),
            }
            for item in decisions
            if isinstance(item, dict)
            and item.get("folder_id")
            and item.get("product_id")
        ]

    candidates = data.get("asset_candidates")
    if isinstance(candidates, list):
        rejected_folder_keys = {
            (
                str(item.get("product_id", "")),
                str(item.get("folder_id", "")),
            )
            for item in normalized.get("folder_decisions", [])
            if isinstance(item, dict) and item.get("decision") == "rejected"
        }
        candidate_by_asset_id = {
            str(item.get("asset_id", "")): item
            for item in candidates
            if isinstance(item, dict)
            and item.get("match_type") != "confirmed_alias"
            and (
                supported_products is None
                or str(item.get("product_id", "")) in supported_products
            )
        }
        allowed_asset_ids = {
            asset_id
            for asset_id, item in candidate_by_asset_id.items()
            if (
                str(item.get("product_id", "")),
                _candidate_folder_id(item, folder_rows),
            )
            not in rejected_folder_keys
            and str(item.get("validation_status", "")) == "valid"
            and isinstance(item.get("preflight"), dict)
            and item["preflight"].get("selectable") is True
            and isinstance(item.get("source_inspection"), dict)
            and item["source_inspection"].get("size_bytes") is not None
            and item["source_inspection"].get("width")
            and item["source_inspection"].get("height")
        }
        license_rows = normalized.get("license_decisions")
        if isinstance(license_rows, list):
            normalized["license_decisions"] = [
                {
                    "asset_id": str(item.get("asset_id", "")),
                    "status": "confirmed",
                }
                for item in license_rows
                if isinstance(item, dict)
                and item.get("status") == "confirmed"
                and str(item.get("asset_id", "")) in allowed_asset_ids
            ]
        asset_rows = normalized.get("asset_decisions")
        if isinstance(asset_rows, list):
            retained = [
                item
                for item in asset_rows
                if isinstance(item, dict)
                and item.get("decision") == "selected"
                and str(item.get("asset_id", "")) in allowed_asset_ids
            ]
            product_order: dict[str, int] = {}
            normalized_rows = []
            for item in retained:
                candidate = candidate_by_asset_id[str(item.get("asset_id", ""))]
                product_id = str(candidate.get("product_id", ""))
                product_order[product_id] = product_order.get(product_id, 0) + 1
                normalized_rows.append(
                    {
                        "product_id": product_id,
                        "asset_id": str(item.get("asset_id", "")),
                        "sha256": str(candidate.get("sha256", "")),
                        "folder_id": _candidate_folder_id(
                            candidate,
                            folder_rows,
                        ),
                        "folder_path": str(
                            candidate.get("folder_path", "")
                            or candidate.get("candidate_directory", "")
                        ),
                        "source_system": str(candidate.get("source_system", "")),
                        "source_path": str(candidate.get("source_path", "")),
                        "decision": "selected",
                        "selection_order": product_order[product_id],
                    }
                )
            normalized["asset_decisions"] = normalized_rows
            normalized["license_decisions"] = [
                {
                    "asset_id": item["asset_id"],
                    "status": "confirmed",
                }
                for item in normalized_rows
            ]
    return normalized


def _normalized_asset_path(value: Any) -> str:
    return str(value or "").replace("/", "\\").rstrip("\\").casefold()


def _candidate_folder_id(
    candidate: dict[str, Any],
    folders: list[dict[str, Any]],
) -> str:
    explicit = str(candidate.get("folder_id", "")).strip()
    if explicit:
        return explicit
    source_path = _normalized_asset_path(candidate.get("source_path", ""))
    source_system = str(candidate.get("source_system", ""))
    matches = [
        item
        for item in folders
        if (
            not item.get("source_system")
            or not source_system
            or str(item.get("source_system")) == source_system
        )
        and _normalized_asset_path(item.get("folder_path", ""))
        and (
            source_path == _normalized_asset_path(item.get("folder_path", ""))
            or source_path.startswith(
                f"{_normalized_asset_path(item.get('folder_path', ''))}\\"
            )
        )
    ]
    if not matches:
        return ""
    matches.sort(
        key=lambda item: len(_normalized_asset_path(item.get("folder_path", ""))),
        reverse=True,
    )
    return str(matches[0].get("folder_id", ""))


def _current_result(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Return only the result bound to the stage's current durable revision."""

    try:
        result = store.read_optional_stage_document(session_id, stage_id, "result")
        if result is not None:
            input_sha256 = hashlib.sha256(
                (store._stage_path(session_id, stage_id) / "input.json").read_bytes()
            ).hexdigest()
            if (
                result.get("session_id") == session_id
                and result.get("stage_id") == stage_id
                and result.get("revision") == state["stages"][stage_id]["revision"]
                and result.get("input_sha256") == input_sha256
            ):
                return result
    except (FileNotFoundError, OSError):
        pass
    if stage_id not in {
        "completeness",
        "asset_matching",
        "image_review",
        "slots_copy",
        "dry_run",
        "approval",
    }:
        return None
    context = store.read_optional_stage_document(session_id, stage_id, "review-context")
    if (
        context is None
        or context.get("session_id") != session_id
        or context.get("stage_id") != stage_id
    ):
        return None
    if stage_id == "image_review" and review_context_is_stale(
        context,
        current_asset_matching_revision=state["stages"]["asset_matching"][
            "revision"
        ],
    ):
        stale = dict(context)
        stale["status"] = "blocked"
        stale["summary"] = "第三阶段选择已变化，当前图片审查结果已过期"
        stale["blocking_reasons"] = ["UPSTREAM_REVISION_STALE"]
        stale["next_action"] = "请 Agent 重新准备第四阶段图片审查"
        stale_data = dict(stale.get("data") or {})
        stale_data["stale"] = True
        stale["data"] = stale_data
        return stale
    if stage_id == "slots_copy" and slot_context_is_stale(
        context,
        current_image_review_revision=state["stages"]["image_review"][
            "revision"
        ],
    ):
        stale = dict(context)
        stale["status"] = "blocked"
        stale["summary"] = "第四阶段图片决定已变化，当前坑位结果已过期"
        stale["blocking_reasons"] = ["UPSTREAM_REVISION_STALE"]
        stale["next_action"] = "请 Agent 重新准备第五阶段坑位编排"
        stale_data = dict(stale.get("data") or {})
        stale_data["stale"] = True
        stale["data"] = stale_data
        return stale
    return context


def _upload_results_result(
    store: SessionStore,
    session_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Project the authoritative approval output into a product-level result."""

    approval_state = state["stages"]["approval"]
    if approval_state.get("status") != "completed":
        return None
    approval = store.read_optional_stage_document(
        session_id, "approval", "result"
    )
    if not isinstance(approval, dict):
        return None
    if int(approval.get("revision", -1)) != int(
        approval_state.get("revision", -2)
    ):
        return None
    approval_data = approval.get("data")
    if not isinstance(approval_data, dict):
        return None
    task_results = [
        item
        for item in approval_data.get("tasks", [])
        if isinstance(item, dict) and str(item.get("task_id", "")).strip()
    ]
    if not task_results:
        return None

    session_path = store._session_path(session_id)
    manifest_entries: list[dict[str, Any]] = []
    try:
        manifest = json.loads(
            (session_path / "approval-manifest.json").read_text(encoding="utf-8")
        )
        manifest_entries = [
            item
            for item in manifest.get("entries", [])
            if isinstance(item, dict)
        ]
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        manifest_entries = []
    manifest_by_task = {
        str(item.get("task_id", "")): item for item in manifest_entries
    }

    product_names: dict[str, str] = {}
    try:
        product_names = {
            str(product.product_id): str(product.title or "").strip()
            for product in read_product_csv(session_path / "inputs" / "products.csv")
        }
    except (FileNotFoundError, OSError, UnicodeError, SchemaError):
        product_names = {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    product_order: list[str] = []
    for task in task_results:
        task_id = str(task.get("task_id", "")).strip()
        product_id = str(
            manifest_by_task.get(task_id, {}).get("product_id", "")
        ).strip()
        if not product_id:
            evidence = str(task.get("evidence", ""))
            marker = evidence.partition("product=")[2]
            product_id = marker.partition(";")[0].strip()
        if not product_id:
            product_id = "未知商品"
        if product_id not in grouped:
            grouped[product_id] = []
            product_order.append(product_id)
        grouped[product_id].append(task)

    success_statuses = {"submitted", "under_review", "success"}
    products: list[dict[str, Any]] = []
    for product_id in product_order:
        tasks = grouped[product_id]
        materials: list[dict[str, Any]] = []
        for task in tasks:
            task_id = str(task.get("task_id", "")).strip()
            manifest_entry = manifest_by_task.get(task_id, {})
            remote_status = str(task.get("status", "")).strip()
            remote_material_id = str(
                task.get("remote_material_id") or ""
            ).strip()
            uploaded = (
                remote_status in success_statuses
                and bool(remote_material_id)
            )
            if uploaded:
                material_status, material_status_label = (
                    "success",
                    "上传成功",
                )
            elif remote_status == "publish_uncertain":
                material_status, material_status_label = (
                    "uncertain",
                    "状态待确认",
                )
            else:
                material_status, material_status_label = (
                    "failed",
                    "上传失败",
                )
            slot_index = manifest_entry.get("slot_index")
            materials.append(
                {
                    "slot_index": (
                        int(slot_index)
                        if isinstance(slot_index, int)
                        or (
                            isinstance(slot_index, str)
                            and slot_index.isdigit()
                        )
                        else None
                    ),
                    "remote_material_id": remote_material_id,
                    "status": material_status,
                    "status_label": material_status_label,
                }
            )
        materials.sort(
            key=lambda item: (
                item["slot_index"] is None,
                item["slot_index"] or 0,
            )
        )
        success_count = sum(
            item["status"] == "success" for item in materials
        )
        task_count = len(tasks)
        if success_count == task_count:
            status, status_label = "success", "上传成功"
        elif success_count:
            status, status_label = "partial", "部分成功"
        else:
            status, status_label = "failed", "上传失败"
        products.append(
            {
                "product_id": product_id,
                "product_name": product_names.get(product_id, ""),
                "status": status,
                "status_label": status_label,
                "task_count": task_count,
                "success_count": success_count,
                "failed_count": task_count - success_count,
                "materials": materials,
            }
        )

    succeeded = sum(item["status"] == "success" for item in products)
    incomplete = len(products) - succeeded
    if not incomplete:
        summary = f"上传完成：{succeeded} 个商品均已成功提交"
    else:
        summary = f"上传结束：{succeeded} 个商品成功，{incomplete} 个商品未全部成功"
    return {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "results",
        "revision": state["stages"]["results"]["revision"],
        "status": str(approval.get("status") or "completed"),
        "summary": summary,
        "blocking_reasons": list(approval.get("blocking_reasons") or []),
        "evidence": [],
        "next_action": None,
        "created_at": approval.get("completed_at") or approval.get("created_at"),
        "data": {
            "store": approval_data.get("store"),
            "product_count": len(products),
            "success_product_count": succeeded,
            "incomplete_product_count": incomplete,
            "task_count": sum(item["task_count"] for item in products),
            "products": products,
        },
    }


def _project_upload_results_session(
    store: SessionStore,
    session_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Expose completed uploads as the final stage without rewriting history."""

    result = _upload_results_result(store, session_id, state)
    if result is None:
        return state
    projected = {
        **state,
        "stages": {
            **state["stages"],
            "results": {
                **state["stages"]["results"],
                "status": str(result.get("status") or "completed"),
            },
        },
    }
    if state.get("current_stage") in {"approval", "results"}:
        projected["current_stage"] = "results"
    return projected


def _completeness_selection_errors(
    store: SessionStore,
    session_id: str,
    state: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, str]:
    """Reject excluded or stale product IDs when stage two is submitted."""

    result = _current_result(store, session_id, "completeness", state)
    products = result.get("data", {}).get("products", []) if result else []
    if not isinstance(products, list) or not products:
        return {}
    allowed_ids = {
        str(product.get("product_id", ""))
        for product in products
        if isinstance(product, dict)
        and product.get("selectable") is not False
        and product.get("status") != "excluded"
    }
    selected_ids = {
        str(product_id).strip()
        for product_id in values.get("selected_product_ids", [])
        if str(product_id).strip()
    }
    invalid_ids = sorted(selected_ids - allowed_ids)
    if not invalid_ids:
        return {}
    preview = "、".join(invalid_ids[:10])
    suffix = " 等" if len(invalid_ids) > 10 else ""
    return {
        "selected_product_ids": (
            f"包含已自动排除或不在当前巡检结果中的商品：{preview}{suffix}"
        )
    }


def _current_input(
    store: SessionStore,
    session_id: str,
    stage: StageDefinition,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Return allowlisted values only when input matches the durable revision."""

    document = store.read_optional_stage_document(session_id, stage.id, "input")
    if document is None:
        return None
    if not isinstance(document.get("values"), dict):
        return None
    revision = state["stages"][stage.id]["revision"]
    if (
        document.get("session_id") != session_id
        or document.get("stage_id") != stage.id
        or document.get("revision") != revision
    ):
        return None
    response = {
        "revision": revision,
        "values": _allowlisted_values(stage, document["values"]),
    }
    interaction_history = [
        dict(item)
        for item in document.get("interaction_history", [])
        if isinstance(item, dict)
    ]
    if interaction_history:
        response["interaction_history"] = interaction_history
    return response


def _current_submission(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    state: dict[str, Any],
) -> dict[str, str] | None:
    """Expose a submission time only for the current durable handoff."""

    stage_path = store._stage_path(session_id, stage_id)
    try:
        handoff = store.read_optional_stage_document(session_id, stage_id, "handoff")
        if handoff is None:
            return None
        input_bytes = (stage_path / "input.json").read_bytes()
        input_document = store.read_optional_stage_document(session_id, stage_id, "input")
        if input_document is None:
            return None
    except (FileNotFoundError, OSError):
        return None
    revision = state["stages"][stage_id]["revision"]
    if (
        handoff.get("session_id") != session_id
        or handoff.get("stage_id") != stage_id
        or handoff.get("revision") != revision
        or handoff.get("input_sha256") != hashlib.sha256(input_bytes).hexdigest()
        or input_document.get("session_id") != session_id
        or input_document.get("stage_id") != stage_id
        or input_document.get("revision") != revision
        or not isinstance(handoff.get("created_at"), str)
    ):
        return None
    return {"created_at": handoff["created_at"]}


def _json_object() -> dict[str, Any]:
    payload = request.get_json()
    if not isinstance(payload, dict):
        raise BadRequest("JSON body must be an object")
    return payload


def _values(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values", {})
    if not isinstance(values, dict):
        raise BadRequest("values must be an object")
    return values


def _user_notes(payload: dict[str, Any]) -> str:
    notes = payload.get("user_notes", "")
    if not isinstance(notes, str):
        raise BadRequest("user_notes must be a string")
    return notes


def _persistence_request_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("request_id")
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequest("request_id must be a string")
    return value


def _value_errors(stage: StageDefinition, values: dict[str, Any]) -> dict[str, str]:
    """Validate JSON input from the declarative stage schema before handoff."""

    errors: dict[str, str] = {}
    for field in stage.fields:
        error = _field_error(field, values.get(field.name), field.name in values)
        if error:
            errors[field.name] = error

    for constraint in stage.exactly_one_constraints:
        selected = [name for name in constraint.field_names if _has_value(values.get(name))]
        if len(selected) != 1:
            message = "exactly one value is required"
            for name in constraint.field_names:
                errors[name] = message

    return errors


def _unknown_value_errors(stage: StageDefinition, values: dict[str, Any]) -> dict[str, str]:
    allowed_names = {field.name for field in stage.fields}
    return {
        name: "is not allowed for this stage"
        for name in values
        if name not in allowed_names
    }


def _allowlisted_values(stage: StageDefinition, values: dict[str, Any]) -> dict[str, Any]:
    allowed_names = {field.name for field in stage.fields}
    return {name: value for name, value in values.items() if name in allowed_names}


def _field_error(field: FieldDefinition, value: Any, present: bool) -> str | None:
    if not present:
        return "is required" if field.required else None

    if field.component in {
        "text",
        "textarea",
        "select",
        "radio",
        "month",
        "datetime",
        "date",
        "auto_path",
        "hidden",
    }:
        if not isinstance(value, str) or (field.required and not value.strip()):
            return "must be a non-empty string" if field.required else "must be a string"
        if not value.strip():
            return None
        if field.component == "month" and not _is_iso_month(value):
            return "must be an ISO month"
        if field.component == "date" and not _is_iso_date(value):
            return "must be an ISO date"
        is_datetime_field = field.component == "datetime" or (
            field.component == "hidden"
            and field.name in {"confirmed_at", "valid_until"}
        )
        if is_datetime_field and not _is_iso_datetime(value):
            return "must be an ISO datetime"
        return None

    if field.component == "checkbox":
        if not isinstance(value, bool):
            return "must be a boolean"
        if field.required and value is not True:
            return "must be confirmed"
        return None

    if field.component == "number":
        if value is None and not field.required:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "must be a number"
        return None

    if field.component == "path":
        if not isinstance(value, str) or (field.required and not value.strip()):
            return "must be a readable path" if field.required else "must be a string path"
        if value.strip() and not _is_readable_path(value):
            return "must be a readable path"
        return None

    if field.component in {"multi_select", "path_list", "auto_path_list", "table"}:
        if not isinstance(value, list):
            return "must be a list"
        if field.required and not value:
            return "must not be empty"
        if field.component == "path_list" and any(
            not isinstance(path, str) or not path.strip() or not _is_readable_path(path)
            for path in value
        ):
            return "must contain only readable paths"
        return None

    return None


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return value is not None


def _is_iso_month(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        return False
    return len(value) == 7


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return "T" in value or " " in value


def _is_readable_path(value: str) -> bool:
    try:
        path = Path(value).expanduser()
        if path.is_file():
            with path.open("rb"):
                return True
        if path.is_dir():
            next(path.iterdir(), None)
            return True
    except OSError:
        return False
    return False


def _cancel_open_slot_agent_requests(
    store: SessionStore,
    session_id: str,
    *,
    reason_code: str,
) -> None:
    root = (
        store._stage_path(session_id, "slots_copy") / "agent-requests"
    )
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("tmp-"):
            continue
        try:
            request_document = read_agent_request(
                store, session_id, path.name
            )
            if request_document["status"] in {
                "pending_agent",
                "processing",
            }:
                cancel_agent_request(
                    store,
                    session_id,
                    path.name,
                    actor="user",
                    reason_code=reason_code,
                )
        except (InteractionConflict, ValueError):
            continue


def _archive_slot_output_metadata(
    store: SessionStore,
    session_id: str,
    *,
    label: str,
) -> None:
    """Make stale output/copy metadata inactive while retaining audit files."""

    stage_path = store._stage_path(session_id, "slots_copy")
    targets = [
        stage_path / name
        for name in (
            "crop-preflight.json",
            "processed-outputs.json",
            "slot-plan.snapshot.json",
            "confirmed-copy-drafts.json",
        )
        if (stage_path / name).is_file()
    ]
    if not targets:
        return
    destination = stage_path / "invalidated" / label
    destination.mkdir(parents=True, exist_ok=True)
    for target in targets:
        target.replace(destination / target.name)


def _supersede_slot_requests_after_revision_change(
    store: SessionStore,
    session_id: str,
    changed_stage_id: str,
) -> None:
    """Invalidate open slot suggestions when their input revision can change."""

    stage_ids = [stage.id for stage in STAGES]
    if stage_ids.index(changed_stage_id) > stage_ids.index("slots_copy"):
        return
    root = (
        store._stage_path(session_id, "slots_copy") / "agent-requests"
    )
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("tmp-"):
            continue
        try:
            request_document = read_agent_request(
                store, session_id, path.name
            )
            if request_document["status"] in {
                "pending_agent",
                "processing",
            }:
                supersede_agent_request(
                    store,
                    session_id,
                    path.name,
                    actor="system",
                    reason_code="AGENT_REQUEST_SUPERSEDED",
                )
        except (InteractionConflict, ValueError):
            continue


def _selected_asset_validation_feedback(
    preflight: dict[str, Any],
) -> dict[str, Any]:
    """Build a path-free, business-facing summary for selected asset cards."""

    items: list[dict[str, Any]] = []
    for asset in preflight.get("assets", []):
        if not isinstance(asset, dict):
            continue
        reason_codes = set(asset.get("reason_codes", []))
        ratio_options = asset.get("ratio_options", {})
        feasible_ratios = [
            ratio
            for ratio in ("3:4", "1:1")
            if isinstance(ratio_options.get(ratio), dict)
            and ratio_options[ratio].get("feasible") is True
        ]
        blocked = asset.get("status") == "blocked" or asset.get("duplicate") is True
        if asset.get("duplicate") is True or "DUPLICATE_ASSET" in reason_codes:
            issue_type = "duplicate"
            message = "与另一张已选图片重复，请取消其中一张"
        elif "SOURCE_SHA_CHANGED" in reason_codes:
            issue_type = "source_changed"
            message = "这张图片在选择后发生了变化，请重新确认"
        elif reason_codes.intersection(
            {
                "SOURCE_UNREADABLE",
                "ASSET_UNREADABLE",
                "ASSET_NOT_FOUND",
                "ZERO_BYTE_ASSET",
                "SOURCE_METADATA_MISSING",
            }
        ):
            issue_type = "source_unreadable"
            message = "这张图片暂时无法读取，请更换图片或稍后重试"
        elif "OUTPUT_SIZE_BELOW_MINIMUM" in reason_codes:
            issue_type = "output_size"
            message = "3:4、1:1 裁剪后均小于 200 KiB，请更换图片"
        elif "IMAGE_SIZE_EXCEEDED" in reason_codes:
            issue_type = "output_size"
            message = "原图或预裁剪结果超过 20 MiB，请更换图片"
        elif "OUTPUT_DIMENSIONS_BELOW_MINIMUM" in reason_codes:
            issue_type = "output_dimensions"
            message = "无法裁出宽高均不少于 720px 的合规图片，请更换图片"
        elif "SELECTED_ASSET_NOT_IN_CURRENT_RESULT" in reason_codes:
            issue_type = "not_current"
            message = "这张图片已不在当前候选中，请取消后重新选择"
        elif blocked:
            issue_type = "blocked"
            message = "这张图片未通过最终检查，请更换图片"
        elif len(feasible_ratios) == 1:
            issue_type = "ratio_limited"
            message = f"仅支持 {feasible_ratios[0]}，后续只会用于该比例坑位"
        else:
            issue_type = "ok"
            message = "检查通过"
        severity = "blocked" if blocked else (
            "warning" if len(feasible_ratios) == 1 else "ok"
        )
        items.append(
            {
                "asset_id": str(asset.get("asset_id", "")),
                "product_id": str(asset.get("product_id", "")),
                "severity": severity,
                "issue_type": issue_type,
                "message": message,
                "feasible_ratios": feasible_ratios,
            }
        )
    return {
        "selected_count": int(preflight.get("selected_count", 0)),
        "blocking_count": sum(
            item["severity"] == "blocked" for item in items
        ),
        "warning_count": sum(
            item["severity"] == "warning" for item in items
        ),
        "items": items,
    }


def _validation_error(field_errors: dict[str, str], **details: Any):
    return _error("validation failed", 422, field_errors, **details)


def _error(
    error: str,
    status_code: int,
    field_errors: dict[str, str] | None = None,
    **details: Any,
):
    return jsonify(
        error=error,
        field_errors=field_errors or {},
        **details,
    ), status_code
