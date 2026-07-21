"""HTTP routes for the durable local interaction session store."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, NotFound, UnsupportedMediaType

from .session import InteractionConflict, InteractionPathError, SessionStore
from .stages import STAGES, FieldDefinition, StageDefinition, get_stage


IMAGE_SOURCES: tuple[dict[str, str], ...] = (
    {"label": "视觉部 · 模特图", "path": r"Y:\视觉部\1-模特图"},
    {
        "label": "小红书 KOC 置换 · 淘宝买家秀",
        "path": r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀",
    },
    {
        "label": "小红书 KOC 置换 · 买家秀",
        "path": r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀",
    },
)

RESULTS_USER_ACTION_STATUSES = frozenset({"needs_user_input", "blocked"})


def create_app(runs_root: Path) -> Flask:
    """Create the local interaction UI and JSON API backed by ``runs_root``."""

    app = Flask(__name__)
    store = SessionStore(runs_root)

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
        return render_template(
            "index.html",
            stages=STAGES,
            stage_registry=stage_registry,
            session_id=request.args.get("session_id", ""),
            image_sources=IMAGE_SOURCES,
            runs_root=str(store.runs_root.resolve()),
        )

    @app.get("/api")
    def api_index():
        return jsonify(service="upload-search-materials interaction API")

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
            result=_current_result(store, session_id, stage_id, state),
        )

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/draft")
    def save_draft(session_id: str, stage_id: str):
        payload = _json_object()
        values = _values(payload)
        store.load_session(session_id)
        stage = get_stage(stage_id)
        field_errors = _unknown_value_errors(stage, values)
        if field_errors:
            return _validation_error(field_errors)
        store.save_draft(
            session_id,
            stage_id,
            _allowlisted_values(stage, values),
            _user_notes(payload),
        )
        return jsonify(status="draft")

    @app.post("/api/sessions/<session_id>/stages/<stage_id>/submit")
    def submit(session_id: str, stage_id: str):
        payload = _json_object()
        values = _values(payload)
        state = store.load_session(session_id)
        stage = get_stage(stage_id)
        if (
            stage_id == "results"
            and state["stages"][stage_id]["status"] not in RESULTS_USER_ACTION_STATUSES
        ):
            return _error("results stage is not awaiting user action", 409)
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
        )
        return jsonify(revision=handoff["revision"], input_sha256=handoff["input_sha256"]), 202

    @app.get("/api/sessions/<session_id>/stages/<stage_id>/status")
    def status(session_id: str, stage_id: str):
        state = store.load_session(session_id)
        get_stage(stage_id)
        return jsonify(state["stages"][stage_id])

    @app.get("/api/sessions/<session_id>/stages/<stage_id>/recovery")
    def recovery(session_id: str, stage_id: str):
        get_stage(stage_id)
        return jsonify(instruction=store.recovery_instruction(session_id, stage_id))

    return app


def _current_result(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Return only the result bound to the stage's current durable revision."""

    result_path = store._stage_path(session_id, stage_id) / "result.json"
    try:
        with result_path.open(encoding="utf-8") as stream:
            result = json.load(stream)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    if (
        result.get("session_id") != session_id
        or result.get("stage_id") != stage_id
        or result.get("revision") != state["stages"][stage_id]["revision"]
    ):
        return None
    return result


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

    overrides = values.get("overrides")
    if isinstance(overrides, list) and any(
        not isinstance(item, dict) or not _has_value(item.get("reason")) for item in overrides
    ):
        errors["overrides"] = "each override requires a reason"
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

    if field.component in {"text", "textarea", "select", "radio", "month", "datetime", "date"}:
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
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "must be a number"
        return None

    if field.component == "path":
        if not isinstance(value, str) or (field.required and not value.strip()):
            return "must be a readable path" if field.required else "must be a string path"
        if value.strip() and not _is_readable_path(value):
            return "must be a readable path"
        return None

    if field.component in {"multi_select", "path_list", "table"}:
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
