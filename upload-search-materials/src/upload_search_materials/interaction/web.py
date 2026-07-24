"""HTTP routes for the durable local interaction session store."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
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
        if field_errors:
            return _validation_error(field_errors)

        expected_revision = payload.get("revision")
        if "revision" in payload and (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
        ):
            return _validation_error({"revision": "must be an integer"})

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
            if (
                source_path.suffix.casefold()
                not in {".jpg", ".jpeg", ".png", ".webp"}
                or not source_path.is_file()
                or not any(source_path.is_relative_to(root) for root in allowed_roots)
            ):
                raise NotFound()
        except (OSError, RuntimeError, ValueError):
            raise NotFound() from None
        response = send_file(source_path, conditional=True, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


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
    if stage_id != "completeness":
        return None
    context = store.read_optional_stage_document(session_id, stage_id, "review-context")
    if (
        context is None
        or context.get("session_id") != session_id
        or context.get("stage_id") != stage_id
    ):
        return None
    return context


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
