"""HTTP routes for the durable local interaction session store."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

from ..assets import build_image_preview
from ..image_review import (
    materialize_review_decisions,
    normalize_review_decisions,
    review_context_is_stale,
)
from ..slot_planning import (
    slot_context_is_stale,
    validate_slot_assignments,
)
from werkzeug.exceptions import BadRequest, NotFound, UnsupportedMediaType

from ..runtime_config import (
    RuntimeConfig,
    inspect_image_sources,
    load_runtime_config,
    save_image_sources,
)
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
        stage_registry = [asdict(stage) for stage in STAGES]
        session_id = request.args.get("session_id", "")
        task_directory = (
            store.runs_root.resolve() / session_id
            if session_id
            else store.runs_root.resolve()
        )
        return render_template(
            "index.html",
            stages=STAGES,
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

        if stage_id == "image_review":
            review_context = _current_result(
                store, session_id, "image_review", state
            )
            review_data = review_context["data"]
            try:
                values["decisions"] = materialize_review_decisions(
                    values["decisions"],
                    review_data,
                    derived_root=(
                        store._stage_path(session_id, "image_review")
                        / str(
                            review_data.get("policy", {})
                            .get("output", {})
                            .get("derived_directory", "derived")
                        )
                    ),
                )
                for decision in values["decisions"]:
                    decision["image_review_revision"] = (
                        expected_revision
                        if isinstance(expected_revision, int)
                        else int(state["stages"]["image_review"]["revision"]) + 1
                    )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                return _validation_error({"decisions": str(error)})

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
        return jsonify(
            revision=handoff["revision"],
            input_sha256=handoff["input_sha256"],
            created_at=handoff["created_at"],
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
        preview_cache = (
            store._stage_path(session_id, "asset_matching") / "preview-cache"
        )
        preview_path = preview_cache / f"{asset_id}.jpg"
        if not preview_path.is_file():
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
        preview = (
            store._stage_path(session_id, "image_review")
            / "preview-cache"
            / f"{asset_id}.jpg"
        )
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
        "/api/sessions/<session_id>/stages/image_review/assets/<asset_id>/process"
    )
    def image_review_process_preview(session_id: str, asset_id: str):
        """Generate a task-local output so the user can inspect it before submit."""

        state = store.load_session(session_id)
        context = _current_result(store, session_id, "image_review", state)
        review_data = context.get("data") if isinstance(context, dict) else None
        assets = review_data.get("assets") if isinstance(review_data, dict) else None
        if not isinstance(assets, list):
            raise NotFound()
        payload = _json_object()
        requested = {
            "asset_id": asset_id,
            "action": payload.get("action"),
            "target_ratio": payload.get("target_ratio"),
            "crop_box": payload.get("crop_box"),
        }
        raw_decisions = []
        found = False
        for asset in assets:
            if not isinstance(asset, dict) or not asset.get("asset_id"):
                continue
            current_id = str(asset["asset_id"])
            if current_id == asset_id:
                raw_decisions.append(requested)
                found = True
            else:
                raw_decisions.append({
                    "asset_id": current_id,
                    "action": (
                        "excluded"
                        if asset.get("status") == "blocked"
                        else "candidate_only"
                    ),
                })
        if not found:
            raise NotFound()
        try:
            normalized = normalize_review_decisions(raw_decisions, review_data)
            materialized = materialize_review_decisions(
                normalized,
                review_data,
                derived_root=(
                    store._stage_path(session_id, "image_review")
                    / str(
                        review_data.get("policy", {})
                        .get("output", {})
                        .get("derived_directory", "derived")
                    )
                ),
            )
            decision = next(
                item for item in materialized if item["asset_id"] == asset_id
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return _validation_error({"decision": str(error)})
        return jsonify({
            "asset_id": asset_id,
            "action": decision["action"],
            "output": decision.get("output"),
        })

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
        try:
            source_path = Path(str(output.get("output_path", ""))).resolve()
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
        preview = (
            store._stage_path(session_id, "slots_copy")
            / "preview-cache"
            / f"{asset_id}.jpg"
        )
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


def _validation_error(field_errors: dict[str, str]):
    return _error("validation failed", 422, field_errors)


def _error(message: str, status_code: int, field_errors: dict[str, str] | None = None):
    return jsonify(error=message, field_errors=field_errors or {}), status_code
