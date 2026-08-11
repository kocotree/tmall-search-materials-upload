"""Durable identities and authoritative status for long collection attempts."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .time_utils import iso_timestamp
from .persistence import atomic_write_json, read_json


COLLECTION_RUNTIME_SCHEMA_VERSION = 1
COLLECTION_PURPOSE = "high_value_collection"
COLLECTION_PHASES = frozenset(
    {
        "validating_profile",
        "connecting_cdp",
        "settling_popups",
        "opening_promotion",
        "selecting_high_value",
        "normalizing_pagination",
        "collecting_page",
        "random_action",
        "waiting_human_check",
        "verifying_terminal",
        "writing_checkpoint",
        "building_completeness",
        "completed",
        "failed",
    }
)
TERMINAL_WORKER_STATUSES = frozenset({"completed", "failed", "stopped"})
QUARANTINED_PAGINATION_SESSIONS = frozenset(
    {"20260730_032916", "20260730_051941"}
)


class CollectionRuntimeError(RuntimeError):
    """A stable collection runtime contract failure."""


@dataclass(frozen=True)
class CollectionBinding:
    session_id: str
    stage_id: str
    revision: int
    input_sha256: str
    selector_sha256: str
    target_store: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stage_id": self.stage_id,
            "revision": self.revision,
            "input_sha256": self.input_sha256,
            "selector_sha256": self.selector_sha256,
            "target_store": self.target_store,
        }


def new_attempt_id() -> str:
    return uuid.uuid4().hex


def attempt_root(session_path: Path) -> Path:
    return session_path / "collected" / "promotion" / "attempts"


def attempt_path(session_path: Path, attempt_id: str) -> Path:
    if not attempt_id or any(value in attempt_id for value in ("/", "\\", "..")):
        raise CollectionRuntimeError("COLLECTION_ATTEMPT_ID_INVALID")
    # Keep Windows paths comfortably below legacy MAX_PATH while the full
    # identity remains inside every envelope.
    return attempt_root(session_path) / f"a-{attempt_id[:12]}"


def write_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    atomic_write_json(path, document)


def read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = read_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise CollectionRuntimeError(
            f"COLLECTION_RUNTIME_DOCUMENT_INVALID:{path.name}"
        ) from error
    if not isinstance(document, dict):
        raise CollectionRuntimeError(
            f"COLLECTION_RUNTIME_DOCUMENT_INVALID:{path.name}"
        )
    return document


def create_attempt_document(
    binding: CollectionBinding,
    *,
    attempt_id: str,
    claim_id: str,
    claimant_id: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or iso_timestamp()
    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "purpose": COLLECTION_PURPOSE,
        **binding.as_dict(),
        "claim_id": claim_id,
        "claimant_id": claimant_id,
        "status": "accepted",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def create_worker_document(
    attempt: Mapping[str, Any],
    *,
    pid: int,
    ownership_token: str,
    process_identity: str,
    log_path: Path,
    started_at: str | None = None,
) -> dict[str, Any]:
    timestamp = started_at or iso_timestamp()
    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "attempt_id": attempt["attempt_id"],
        "purpose": COLLECTION_PURPOSE,
        "pid": int(pid),
        "ownership_token": ownership_token,
        "process_identity": process_identity,
        "session_id": attempt["session_id"],
        "stage_id": attempt["stage_id"],
        "revision": attempt["revision"],
        "input_sha256": attempt["input_sha256"],
        "selector_sha256": attempt["selector_sha256"],
        "claim_id": attempt["claim_id"],
        "started_at": timestamp,
        "heartbeat_at": timestamp,
        "phase": "validating_profile",
        "action": "validate_selector_profile",
        "target": "high_value_collection",
        "retry_count": 0,
        "elapsed_ms": 0,
        "current_page": None,
        "last_completed_page": None,
        "row_count": None,
        "last_checkpoint_at": None,
        "pagination_origin_page": None,
        "observed_page": None,
        "terminal_page": None,
        "terminal_proof": False,
        "pagination_reason_code": None,
        "next_recovery": "检查运行环境和选择器诊断后恢复同一 session",
        "log_path": str(log_path),
        "terminal_status": None,
        "updated_at": timestamp,
    }


def validate_attempt_binding(
    document: Mapping[str, Any],
    binding: CollectionBinding,
    *,
    attempt_id: str | None = None,
) -> None:
    expected = binding.as_dict()
    if attempt_id is not None:
        expected["attempt_id"] = attempt_id
    for field, value in expected.items():
        if document.get(field) != value:
            raise CollectionRuntimeError(
                f"COLLECTION_ATTEMPT_BINDING_MISMATCH:{field}"
            )


def update_worker_progress(
    path: Path,
    *,
    attempt_id: str,
    ownership_token: str,
    phase: str,
    action: str | None = None,
    target: str | None = None,
    retry_count: int | None = None,
    elapsed_ms: int | None = None,
    current_page: int | None = None,
    last_completed_page: int | None = None,
    row_count: int | None = None,
    last_checkpoint_at: str | None = None,
    pagination_origin_page: int | None = None,
    observed_page: int | None = None,
    terminal_page: int | None = None,
    terminal_proof: bool | None = None,
    pagination_reason_code: str | None = None,
    terminal_status: str | None = None,
    next_recovery: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    document = read_json_object(path)
    if document is None:
        raise CollectionRuntimeError("COLLECTION_WORKER_MANIFEST_REQUIRED")
    if document.get("attempt_id") != attempt_id:
        raise CollectionRuntimeError("COLLECTION_ATTEMPT_STALE")
    if document.get("ownership_token") != ownership_token:
        raise CollectionRuntimeError("COLLECTION_WORKER_OWNERSHIP_MISMATCH")
    if phase not in COLLECTION_PHASES:
        raise CollectionRuntimeError(f"COLLECTION_PHASE_INVALID:{phase}")
    if document.get("terminal_status") in TERMINAL_WORKER_STATUSES:
        raise CollectionRuntimeError("COLLECTION_WORKER_ALREADY_TERMINAL")
    if terminal_status is not None and terminal_status not in TERMINAL_WORKER_STATUSES:
        raise CollectionRuntimeError(
            f"COLLECTION_WORKER_TERMINAL_STATUS_INVALID:{terminal_status}"
        )
    timestamp = now or iso_timestamp()
    document.update(
        {
            "phase": phase,
            "heartbeat_at": timestamp,
            "updated_at": timestamp,
        }
    )
    if current_page is not None:
        document["current_page"] = int(current_page)
    if action is not None:
        document["action"] = str(action)
    if target is not None:
        document["target"] = str(target)
    if retry_count is not None:
        document["retry_count"] = max(0, int(retry_count))
    if elapsed_ms is not None:
        document["elapsed_ms"] = max(0, int(elapsed_ms))
    if last_completed_page is not None:
        document["last_completed_page"] = int(last_completed_page)
    if row_count is not None:
        document["row_count"] = int(row_count)
    if last_checkpoint_at is not None:
        document["last_checkpoint_at"] = last_checkpoint_at
    if pagination_origin_page is not None:
        document["pagination_origin_page"] = int(
            pagination_origin_page
        )
    if observed_page is not None:
        document["observed_page"] = int(observed_page)
    if terminal_page is not None:
        document["terminal_page"] = int(terminal_page)
    if terminal_proof is not None:
        document["terminal_proof"] = bool(terminal_proof)
    if pagination_reason_code is not None:
        document["pagination_reason_code"] = str(
            pagination_reason_code
        )
    if next_recovery is not None:
        document["next_recovery"] = str(next_recovery)
    if terminal_status is not None:
        document["terminal_status"] = terminal_status
        document["finished_at"] = timestamp
    write_json_atomic(path, document)
    return document


def worker_liveness(
    worker: Mapping[str, Any] | None,
    *,
    ownership_token: str | None,
    process_identity: str | None,
    process_probe: Callable[[int], bool | None],
) -> str:
    """Return live, dead, indeterminate, or absent without trusting a PID alone."""

    if not worker:
        return "absent"
    if worker.get("terminal_status") in TERMINAL_WORKER_STATUSES:
        return "dead"
    if (
        not ownership_token
        or worker.get("ownership_token") != ownership_token
        or not process_identity
        or worker.get("process_identity") != process_identity
    ):
        return "indeterminate"
    try:
        probed = process_probe(int(worker["pid"]))
    except (KeyError, TypeError, ValueError, OSError):
        return "indeterminate"
    if probed is True:
        return "live"
    if probed is False:
        return "dead"
    return "indeterminate"


def _is_claim_expired(claim: Mapping[str, Any] | None) -> bool:
    if not claim:
        return True
    value = claim.get("lease_expires_at")
    if not isinstance(value, str):
        return True
    try:
        expiry = datetime.fromisoformat(value)
    except ValueError:
        return True
    if expiry.tzinfo is None:
        return True
    return expiry <= datetime.now(timezone.utc)


def resolve_collection_status(
    *,
    stage_status: str,
    current_attempt: Mapping[str, Any] | None,
    current_result: Mapping[str, Any] | None,
    worker: Mapping[str, Any] | None,
    worker_state: str,
    processing_claim: Mapping[str, Any] | None,
    historical_results: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve one authoritative current state and separate immutable history."""

    attempt_id = (
        str(current_attempt.get("attempt_id"))
        if current_attempt and current_attempt.get("attempt_id")
        else None
    )
    history = []
    for item in historical_results or []:
        copy = dict(item)
        copy["superseded"] = copy.get("attempt_id") != attempt_id
        history.append(copy)

    bound_result = (
        current_result
        if current_result
        and (
            attempt_id is None
            or current_result.get("attempt_id") == attempt_id
        )
        else None
    )
    quarantined = (
        str((current_attempt or {}).get("session_id", ""))
        in QUARANTINED_PAGINATION_SESSIONS
    )
    if quarantined:
        status = "blocked"
        source = "pagination_audit"
        recovery_action = "start_fresh_timestamp_session"
    elif bound_result and bound_result.get("status") == "completed":
        status = "completed"
        source = "result"
        recovery_action = None
    elif worker_state == "live":
        status = "processing"
        source = "worker"
        recovery_action = None
    elif worker_state == "dead" and current_attempt:
        status = "recoverable"
        source = "worker"
        recovery_action = "resume_exact_session"
    elif worker_state == "indeterminate" and not _is_claim_expired(
        processing_claim
    ):
        status = "processing_indeterminate"
        source = "lease"
        recovery_action = "wait_for_lease_expiry"
    elif processing_claim and _is_claim_expired(processing_claim):
        status = "recoverable"
        source = "lease"
        recovery_action = "validate_and_resume"
    elif bound_result:
        status = str(bound_result.get("status") or "blocked")
        source = "result"
        recovery_action = bound_result.get("next_action")
    else:
        status = stage_status
        source = "stage"
        recovery_action = None

    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "status": status,
        "source": source,
        "worker": dict(worker) if worker else None,
        "processing_claim": (
            dict(processing_claim) if processing_claim else None
        ),
        "current_result": dict(bound_result) if bound_result else None,
        "history": history,
        "recovery_action": recovery_action,
        "resolved_at": iso_timestamp(),
    }
