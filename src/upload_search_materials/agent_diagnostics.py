"""Agent-only diagnostics for recoverable workflow failures.

The business UI deliberately does not render the technical payload written here.
Codex reads one stable document, repairs the environment or implementation, and
then reruns the recorded idempotent entry point.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import traceback
from typing import Any, Iterable

from .persistence import atomic_write_json, read_json


SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}(?:_\d{2})?$")
USER_ACTION_REASONS = frozenset(
    {
        "HUMAN_CHECK",
        "LOGIN_INTERACTION_REQUIRED",
        "MATERIAL_PAGE_REQUIRED",
        "STORE_IDENTITY_MISMATCH",
    }
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reason_code(value: object) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[0] if text else "WORKFLOW_RUNTIME_FAILED"


def retry_command(
    stage_id: str,
    runs_root: Path,
    session_id: str,
    *,
    processor: str = "",
) -> str:
    commands = {
        "setup": "process-setup",
        "completeness": "process-product-selection",
        "final_material_selection": "process-final-material-handoff",
        "publish_authorization": "process-publish-authorization",
    }
    command = processor or commands.get(stage_id, "resume-session")
    return (
        "scripts\\run-plugin.cmd "
        f'{command} --runs-root "{Path(runs_root)}" --session "{session_id}"'
    )


def write_agent_diagnostic(
    session_path: Path,
    *,
    session_id: str,
    stage_id: str,
    revision: int,
    input_sha256: str,
    reason_codes: Iterable[object],
    phase: str,
    message: str,
    evidence: Iterable[object] = (),
    status: str = "open",
    processor: str = "",
    handoff_kind: str = "",
    error_type: str = "",
    traceback_text: str = "",
) -> dict[str, Any]:
    """Persist a single current diagnostic plus immutable timestamped history."""

    session_path = Path(session_path)
    codes = list(dict.fromkeys(_reason_code(item) for item in reason_codes))
    if not codes:
        codes = ["WORKFLOW_RUNTIME_FAILED"]
    now = _timestamp()
    runs_root = session_path.parent
    document = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "stage_id": stage_id,
        "revision": int(revision),
        "input_sha256": str(input_sha256),
        "status": status,
        "phase": str(phase),
        "processor": str(processor),
        "handoff_kind": str(handoff_kind),
        "reason_code": codes[0],
        "reason_codes": codes,
        "owner": (
            "user_via_codex"
            if any(code in USER_ACTION_REASONS for code in codes)
            else "codex"
        ),
        "user_action_required": any(
            code in USER_ACTION_REASONS for code in codes
        ),
        "message": str(message),
        "error_type": str(error_type),
        "traceback": str(traceback_text),
        "evidence": [str(item) for item in evidence],
        "retry": {
            "idempotent": True,
            "command": retry_command(
                stage_id,
                runs_root,
                session_id,
                processor=processor,
            ),
        },
        "updated_at": now,
    }
    root = session_path / "agent-diagnostics"
    current = root / "current.json"
    if current.is_file():
        try:
            prior = read_json(current)
        except (OSError, ValueError):
            prior = {}
        same_identity = (
            prior.get("stage_id") == stage_id
            and int(prior.get("revision", -1)) == int(revision)
            and prior.get("input_sha256") == input_sha256
        )
        created_at = str(prior.get("created_at", "")).strip()
        if same_identity and created_at:
            document["created_at"] = created_at
    document.setdefault("created_at", now)
    atomic_write_json(current, document)
    history_name = (
        now.replace(":", "").replace("+", "_")
        + f"-{stage_id}-r{int(revision)}.json"
    )
    history = root / "history" / history_name
    if not history.is_file():
        atomic_write_json(history, document)
    return document


def write_exception_diagnostic(
    store: Any,
    session_id: str,
    stage_id: str,
    *,
    processor: str,
    phase: str,
    error: Exception,
    handoff_kind: str = "",
    evidence: Iterable[object] = (),
) -> dict[str, Any]:
    """Turn any processor exception into the same Codex-readable contract."""

    state = store.load_session(session_id)
    stage_state = state["stages"][stage_id]
    handoff = store.read_optional_stage_document(
        session_id, stage_id, "handoff"
    )
    revision = int(
        handoff.get("revision", stage_state["revision"])
        if isinstance(handoff, dict)
        else stage_state["revision"]
    )
    input_sha256 = str(
        handoff.get("input_sha256", "")
        if isinstance(handoff, dict)
        else ""
    )
    stable = _reason_code(error)
    if stable == "WORKFLOW_RUNTIME_FAILED":
        stable = f"{processor.replace('-', '_').upper()}_FAILED"
    return write_agent_diagnostic(
        store._session_path(session_id),
        session_id=session_id,
        stage_id=stage_id,
        revision=revision,
        input_sha256=input_sha256,
        reason_codes=[stable],
        phase=phase,
        message=str(error),
        evidence=evidence,
        processor=processor,
        handoff_kind=(
            handoff_kind
            or (
                str(handoff.get("handoff_kind", ""))
                if isinstance(handoff, dict)
                else ""
            )
        ),
        error_type=type(error).__name__,
        traceback_text=traceback.format_exc(),
    )


def resolve_agent_diagnostic(
    session_path: Path,
    *,
    stage_id: str,
    revision: int,
) -> dict[str, Any] | None:
    current = Path(session_path) / "agent-diagnostics" / "current.json"
    if not current.is_file():
        return None
    try:
        document = read_json(current)
    except (OSError, ValueError):
        return None
    if (
        document.get("stage_id") != stage_id
        or int(document.get("revision", -1)) != int(revision)
    ):
        return document
    if document.get("status") != "resolved":
        document["status"] = "resolved"
        document["resolved_at"] = _timestamp()
        document["updated_at"] = document["resolved_at"]
        atomic_write_json(current, document)
    return document


def read_agent_diagnostic(runs_root: Path, session_id: str) -> dict[str, Any]:
    if not SESSION_ID_PATTERN.fullmatch(str(session_id)):
        raise ValueError("SESSION_ID_INVALID")
    path = Path(runs_root) / str(session_id) / "agent-diagnostics" / "current.json"
    if not path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": str(session_id),
            "status": "clear",
            "diagnostic_path": str(path),
        }
    return {**read_json(path), "diagnostic_path": str(path)}
