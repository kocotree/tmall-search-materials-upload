"""HTTP routes for the durable local interaction session store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

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
from ..deterministic_slot_planning import build_deterministic_slot_plan
from ..image_compliance import default_image_policy
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
    validate_slot_assignments,
)
from ..slot_workflow import (
    ai_assignments,
    final_outputs_sha256,
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
)
from ..decision_modes import get_decision_boundary
from .folder_picker import choose_directory
from .session import InteractionConflict, InteractionPathError, SessionStore
from .stages import STAGES, FieldDefinition, StageDefinition, get_stage


RESULTS_USER_ACTION_STATUSES = frozenset({"needs_user_input", "blocked"})


def create_app(
    runs_root: Path,
    runtime_config: RuntimeConfig | None = None,
    *,
    enforce_stage_order: bool = True,
) -> Flask:
    """Create the local interaction UI and JSON API backed by ``runs_root``."""

    app = Flask(__name__)
    store = SessionStore(runs_root)
    runtime = runtime_config or load_runtime_config()
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
        return _error(str(error), 409)

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
        return render_template(
            "index.html",
            stages=rendered_stages,
            stage_registry=stage_registry,
            session_id=session_id,
            image_sources=runtime.image_sources,
            runs_root=str(store.runs_root.resolve()),
            task_directory=str(task_directory),
            default_month=date.today().strftime("%Y-%m"),
            static_asset_version=static_asset_version,
            setup_inputs={
                "products_csv": str(runtime.products.path or ""),
                "products_available": bool(runtime.products.path and runtime.products.path.is_file()),
                "products_status": runtime.products.status,
                "rules_csv": str(runtime.rules.path or ""),
                "rules_available": bool(runtime.rules.path and runtime.rules.path.is_file()),
                "rules_status": runtime.rules.status,
                "folder_index_root": str(runtime.folder_index_root),
                "folder_index_available": (
                    runtime.folder_index_root / "folder-index.sqlite3"
                ).is_file(),
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

    @app.post("/api/runtime/image-sources/check")
    def check_runtime_image_sources():
        payload = _json_object()
        try:
            sources = inspect_image_sources(runtime, payload.get("image_sources"))
        except ValueError as error:
            return _validation_error({"image_sources": str(error)})
        return jsonify(image_sources=sources)

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
        payload = _json_object()
        initial_path = payload.get("initial_path")
        if initial_path is not None and not isinstance(initial_path, str):
            return _validation_error({"initial_path": "must be a string"})
        try:
            selected = choose_directory(initial_path)
        except (OSError, RuntimeError) as error:
            return _error(f"folder picker unavailable: {error}", 503)
        return jsonify(cancelled=selected is None, path=selected or "")

    @app.post("/api/sessions")
    def create_session():
        _json_object()
        session = store.create_session()
        return jsonify(session_id=session.session_id), 201

    @app.get("/api/sessions/<session_id>")
    def get_session(session_id: str):
        return jsonify(session=store.load_session(session_id))

    @app.get("/api/sessions/<session_id>/stages/<stage_id>")
    def get_stage_route(session_id: str, stage_id: str):
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        return jsonify(
            stage=asdict(stage),
            state=state["stages"][stage_id],
            input=_current_input(store, session_id, stage, state),
            result=_current_result(store, session_id, stage_id, state),
            submission=_current_submission(store, session_id, stage_id, state),
        )

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/draft")
    def save_draft(session_id: str, stage_id: str):
        payload = _json_object()
        values = _values(payload)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
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
        document = store.save_draft(
            session_id,
            stage_id,
            _allowlisted_values(stage, values),
            _user_notes(payload),
            expected_revision=expected_revision,
        )
        _supersede_slot_requests_after_revision_change(
            store, session_id, stage_id
        )
        return jsonify(status="draft", revision=document["revision"])

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/submit")
    def submit(session_id: str, stage_id: str):
        payload = _json_object()
        values = _values(payload)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        values = _normalize_stage_values(
            store, session_id, stage_id, state, values
        )
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
        if (
            not field_errors
            and stage_id == "asset_matching"
            and values.get("asset_decisions")
        ):
            current_result = _current_result(
                store, session_id, "asset_matching", state
            )
            current_data = (
                current_result.get("data")
                if isinstance(current_result, dict)
                else None
            )
            if (
                isinstance(current_data, dict)
                and isinstance(current_data.get("asset_candidates"), list)
            ):
                policy = default_image_policy()
                policy_sha256 = hashlib.sha256(
                    json.dumps(
                        policy,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                prospective_revision = int(
                    state["stages"]["asset_matching"]["revision"]
                ) + 1
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
                usable_by_product: dict[str, int] = {}
                selected_products = {
                    str(item.get("product_id", ""))
                    for item in values.get("asset_decisions", [])
                    if isinstance(item, dict)
                    and item.get("decision") == "selected"
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
                shortages = {
                    product_id: 3 - usable_by_product.get(product_id, 0)
                    for product_id in selected_products
                    if usable_by_product.get(product_id, 0) < 3
                }
                if not selected_products:
                    field_errors["asset_decisions"] = "每个商品至少采用 3 张图片"
                elif shortages:
                    detail = "；".join(
                        f"{product_id} 还差 {count} 张"
                        for product_id, count in sorted(shortages.items())
                    )
                    field_errors["asset_decisions"] = (
                        f"完整坑位至少需要 3 张可用且不重复的图片：{detail}"
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
            return _validation_error(field_errors)

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

        handoff = store.save_input(
            session_id,
            stage_id,
            _allowlisted_values(stage, values),
            _user_notes(payload),
            expected_revision=expected_revision,
            allowed_current_statuses=(
                RESULTS_USER_ACTION_STATUSES if stage_id == "results" else None
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
            completed_data = dict(selected_asset_bundle["current_data"])
            completed_data["selected_asset_preflight"] = {
                "revision": handoff["revision"],
                "selected_count": preflight["selected_count"],
                "reviewable_count": preflight["reviewable_count"],
                "blocked_count": preflight["blocked_count"],
                "duplicate_count": preflight["duplicate_count"],
                "policy_sha256": preflight["policy_sha256"],
            }
            store.write_result(
                session_id,
                "asset_matching",
                handoff["revision"],
                handoff["input_sha256"],
                status="completed",
                summary=(
                    f"已确认 {preflight['reviewable_count']} 张可用图片，"
                    "并自动生成确定性坑位草稿"
                ),
                evidence=[str(stage_path / "selected-asset-preflight.json")],
                next_action="检查坑位草稿，确认后进行图片裁剪和压缩",
                data=completed_data,
            )
            planning_decisions = [
                {
                    "asset_id": str(item.get("asset_id", "")),
                    "decision": (
                        "excluded"
                        if item.get("status") == "blocked"
                        or item.get("duplicate") is True
                        else "selected"
                    ),
                    "candidate_ratios": list(
                        item.get("crop_options", {}).keys()
                    ),
                }
                for item in preflight.get("assets", [])
                if isinstance(item, dict)
            ]
            board_data = build_rule_slot_plan(
                preflight,
                planning_decisions,
                image_review_revision=0,
            )
            planning_error = None
            try:
                deterministic = build_deterministic_slot_plan(
                    board_data,
                    missing_slots_by_product=selected_asset_bundle[
                        "missing_slots"
                    ],
                )
            except (TypeError, ValueError) as error:
                planning_error = str(error)
                deterministic = {
                    "schema_version": 1,
                    "record_type": "deterministic_slot_plan",
                    "assignments": [],
                    "unused_assets": [],
                    "products": [],
                    "error": planning_error,
                }
            board_data["deterministic_plan"] = deterministic
            context = {
                "schema_version": 1,
                "session_id": session_id,
                "stage_id": "slots_copy",
                "revision": int(
                    store.load_session(session_id)["stages"]["slots_copy"][
                        "revision"
                    ]
                ),
                "status": "needs_user_input",
                "summary": (
                    f"已自动创建 {len(deterministic['assignments'])} 个完整坑位"
                    if planning_error is None
                    else "自动编排失败，已保留空草稿供人工处理"
                ),
                "blocking_reasons": (
                    [] if planning_error is None else [
                        "DETERMINISTIC_SLOT_PLANNING_FAILED"
                    ]
                ),
                "evidence": [
                    str(stage_path / "selected-asset-preflight.json")
                ],
                "next_action": (
                    "检查并可人工调整坑位，然后确认进入裁剪"
                    if planning_error is None
                    else f"请人工添加坑位；自动编排错误：{planning_error}"
                ),
                "created_at": datetime.now().astimezone().isoformat(),
                "data": board_data,
            }
            store.write_review_context(session_id, "slots_copy", context)
            if planning_error is None:
                save_current_slot_plan(
                    store,
                    session_id,
                    board_data,
                    deterministic["assignments"],
                    decision_source="deterministic",
                    context_revision=int(context["revision"]),
                    workflow_state="plan_review",
                    confirmed=False,
                    actor="system",
                )
            next_state = store.load_session(session_id)
            next_state["current_stage"] = "slots_copy"
            store._write_session_state(session_id, next_state)
        return jsonify(
            revision=handoff["revision"],
            input_sha256=handoff["input_sha256"],
            created_at=handoff["created_at"],
            status=(
                "completed"
                if stage_id == "asset_matching"
                and selected_asset_bundle is not None
                else "ready_for_agent"
            ),
            next_stage=(
                "slots_copy"
                if stage_id == "asset_matching"
                and selected_asset_bundle is not None
                else None
            ),
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
        return jsonify(state["stages"][stage_id])

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

    @app.post(
        "/api/sessions/<session_id>/stages/slots_copy/process-plan"
    )
    def process_confirmed_slot_plan(session_id: str):
        payload = _json_object()
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
            return _validation_error(
                {"slot_assignments": "prepare the current slot board first"}
            )
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
                return _validation_error(
                    {
                        "current_slot_plan": (
                            "confirm the current slot plan before processing"
                        )
                    }
                )
            submitted_assignments = current_plan["slot_assignments"]
        try:
            assignments = validate_slot_assignments(
                submitted_assignments, data
            )
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
            processed_path = stage_path / "processed-outputs.json"
            existing_processed = None
            if processed_path.is_file():
                existing_processed = SessionStore._read_json(
                    processed_path, "processed-outputs"
                )
                if (
                    existing_processed.get("processing_sha256")
                    == requested_processing_sha256
                ):
                    return jsonify(existing_processed)
            processed = materialize_confirmed_slot_plan(
                assignments,
                data,
                derived_root=stage_path / "derived",
                crop_parameters=crop_parameters,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return _validation_error({"slot_assignments": str(error)})
        store._write_json_atomic(
            processed_path, processed
        )
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
        return jsonify(processed)

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
        return jsonify(request=value, response=response)

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
        state = store.load_session(session_id)
        current = read_current_slot_plan(store, session_id)
        processed_path = (
            store._stage_path(session_id, "slots_copy")
            / "processed-outputs.json"
        )
        if (
            current is None
            or current.get("workflow_state")
            not in {"outputs_ready", "copy_generating", "copy_review"}
            or not processed_path.is_file()
        ):
            return _validation_error(
                {"copy_request": "final image outputs are not ready"}
            )
        processed = SessionStore._read_json(
            processed_path, "processed-outputs"
        )
        outputs_identity = final_outputs_sha256(processed)
        existing = None
        if payload.get("regenerate") is not True:
            existing = find_equivalent_agent_request(
                store,
                session_id,
                kind="copy_draft",
                context_revision=int(
                    state["stages"]["slots_copy"]["revision"]
                ),
                context_fingerprint=outputs_identity,
            )
        if existing is not None:
            return jsonify(existing)
        context = _current_result(store, session_id, "slots_copy", state)
        board_data = (
            context.get("data") if isinstance(context, dict) else {}
        )
        product_titles = {
            str(item.get("product_id", "")): str(
                item.get("product_title", "")
            )
            for item in board_data.get("products", [])
            if isinstance(item, dict)
        }
        slots = []
        for slot in processed.get("slots", []):
            product_id = str(slot.get("product_id", ""))
            slots.append(
                {
                    "slot_id": str(slot.get("slot_id", "")),
                    "product_id": product_id,
                    "target_ratio": str(slot.get("target_ratio", "")),
                    "theme": str(slot.get("theme", "")),
                    "ordered_outputs": [
                        {
                            "asset_id": str(output.get("asset_id", "")),
                            "output_sha256": str(
                                output.get("output_sha256", "")
                            ),
                            "order": int(output.get("order", 0)),
                        }
                        for output in slot.get("outputs", [])
                    ],
                    "trusted_source_fields": {
                        "商品标题": product_titles.get(product_id, "")
                    },
                }
            )
        try:
            created = create_agent_request(
                store,
                session_id,
                kind="copy_draft",
                candidates=[],
                context_revision=int(
                    state["stages"]["slots_copy"]["revision"]
                ),
                request_context={
                    "slot_plan_revision": int(current["plan_revision"]),
                    "final_outputs_sha256": final_outputs_sha256(processed),
                    "context_fingerprint": outputs_identity,
                    "slots": slots,
                    "prohibited_terms": [],
                },
            )
        except (AgentRequestError, TypeError, ValueError) as error:
            return _validation_error({"copy_request": str(error)})
        save_current_slot_plan(
            store,
            session_id,
            board_data,
            current["slot_assignments"],
            decision_source=str(current["decision_source"]),
            context_revision=int(current["context_revision"]),
            expected_plan_revision=int(current["plan_revision"]),
            workflow_state="copy_generating",
            confirmed=True,
            request_id=current.get("agent_request_id"),
            response_sha256=current.get("agent_response_sha256"),
            fallback_reason=current.get("fallback_reason"),
            actor="system",
        )
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
                        else "agent_assisted"
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

    return app


def _normalize_stage_values(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    state: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(values)
    if stage_id != "asset_matching":
        return normalized
    normalized.pop("aliases", None)
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
    if folder_rows:
        normalized["folder_decisions"] = [
            {
                "folder_id": str(item.get("folder_id", "")),
                "product_id": str(item.get("product_id", "")),
                "source_system": str(item.get("source_system", "")),
                "folder_path": str(item.get("folder_path", "")),
                "decision": (
                    "rejected"
                    if str(
                        decision_by_key.get(
                            (
                                str(item.get("product_id", "")),
                                str(item.get("folder_id", "")),
                            ),
                            {},
                        ).get("decision", "")
                    )
                    == "rejected"
                    else "confirmed"
                ),
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
    return {
        "revision": revision,
        "values": _allowlisted_values(stage, document["values"]),
    }


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
    }:
        if not isinstance(value, str) or (field.required and not value.strip()):
            return "must be a non-empty string" if field.required else "must be a string"
        if not value.strip():
            return None
        if field.component == "month" and not _is_iso_month(value):
            return "must be an ISO month"
        if field.component == "date" and not _is_iso_date(value):
            return "must be an ISO date"
        if field.component == "datetime" and not _is_iso_datetime(value):
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


def _validation_error(field_errors: dict[str, str]):
    return _error("validation failed", 422, field_errors)


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
    final_outputs_sha256,
