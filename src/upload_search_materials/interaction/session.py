"""Durable, filesystem-backed interaction sessions."""

from __future__ import annotations

import hashlib
import json
import os
import time
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .stages import STAGES, get_stage
from ..agent_diagnostics import (
    resolve_agent_diagnostic,
    write_agent_diagnostic,
)
from ..decision_modes import get_decision_boundary
from ..time_utils import iso_timestamp
from ..persistence import atomic_write_json, read_json


SCHEMA_VERSION = 1
CURRENT_WORKFLOW_PROFILE = "deterministic-manual-v1"
LEGACY_AI_COMPATIBILITY_PROFILE = "legacy-ai-compat-v1"
LEGACY_WORKFLOW_MIGRATION_ID = "deterministic-manual-history-v1"
STAGE_STATUSES = frozenset(
    {
        "draft",
        "ready_for_agent",
        "processing",
        "needs_user_input",
        "completed",
        "blocked",
    }
)
STAGE_TRANSACTION_SCHEMA_VERSION = 1
PERSISTENCE_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")
HANDOFF_AGENT_ACTIONS = {
    "setup": ("process-setup", "workbench_api"),
    "completeness": ("process-product-selection", "workbench_api"),
    "approval": ("process-publish-authorization", "plugin_cli"),
}
AGENT_WAIT_LEASE_SECONDS = 30
AGENT_WAIT_SEGMENT_SECONDS = 15


class InteractionPathError(ValueError):
    """Raised when a supplied identifier could address data outside a session."""


class InteractionConflict(RuntimeError):
    """Raised when durable interaction state cannot be safely updated."""


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    path: Path


class SessionStore:
    """Create sessions and hand user input to an interaction agent."""

    _process_locks_guard = threading.Lock()
    _process_locks: dict[str, threading.RLock] = {}
    _lock_state = threading.local()

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = Path(runs_root).expanduser()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._runs_root = self.runs_root.resolve()

    def create_session(self, now: datetime | None = None) -> SessionRecord:
        created = now or datetime.now(timezone.utc)
        base_id = created.strftime("%Y%m%d_%H%M%S")
        session_path: Path | None = None
        session_id = base_id
        with self._file_lock(self._runs_root / ".session-create.lock"):
            for number in range(1, 10_000):
                session_id = base_id if number == 1 else f"{base_id}_{number:02d}"
                candidate = self._runs_root / session_id
                try:
                    candidate.mkdir()
                except FileExistsError:
                    continue
                session_path = self._safe_path(candidate)
                break
            if session_path is None:
                raise InteractionConflict("unable to allocate a unique session identifier")

            for index, stage in enumerate(STAGES, start=1):
                (session_path / self._stage_directory_name(index, stage.id)).mkdir()

            created_at = self._iso_timestamp(created)
            state = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "created_at": created_at,
                "updated_at": created_at,
                "current_stage": "setup",
                "stages": {
                    stage.id: {"revision": 0, "status": "draft"} for stage in STAGES
                },
                "runs_root": str(self._runs_root),
                "video_test_deferred": True,
                "workflow_profile": CURRENT_WORKFLOW_PROFILE,
                "last_agent_heartbeat": None,
                "agent_wait": None,
                "processing_claim": None,
            }
            self._write_json_atomic(session_path / "session.json", state)
            self._append_event(session_path, "session_created", session_id=session_id)
        return SessionRecord(session_id=session_id, path=session_path)

    def load_session(self, session_id: str) -> dict[str, Any]:
        state = self._load_session_file(session_id)
        if state.get("workflow_profile") not in {
            CURRENT_WORKFLOW_PROFILE,
            LEGACY_AI_COMPATIBILITY_PROFILE,
        }:
            state = self._migrate_legacy_workflow(session_id, state)
        return self._reconcile_pending_stage_transaction(session_id, state)

    def _load_session_file(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        try:
            state = read_json(path / "session.json")
        except FileNotFoundError as error:
            raise InteractionPathError(f"session does not exist: {session_id}") from error
        if not isinstance(state, dict):
            raise InteractionConflict("session.json must contain an object")
        self._validate_schema_version(state, "session")
        if state.get("session_id") != session_id:
            raise InteractionConflict("session.json identity does not match its path")
        return state

    def _migrate_legacy_workflow(
        self, session_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        """Idempotently map historical stages into the deterministic workflow.

        Historical files remain where they are.  The migration only writes a
        selected-asset preflight compatibility snapshot, annotates the slot
        review context, and updates the durable session navigation profile.
        """

        with self._session_lock(session_id):
            state = self._load_session_file(session_id)
            if state.get("workflow_profile") == CURRENT_WORKFLOW_PROFILE:
                return state

            original_stage = str(state.get("current_stage", "setup"))
            image_state = state.get("stages", {}).get("image_review", {})
            image_status = str(image_state.get("status", "draft"))
            asset_stage_path = self._stage_path(session_id, "asset_matching")
            image_stage_path = self._stage_path(session_id, "image_review")
            slot_stage_path = self._stage_path(session_id, "slots_copy")
            migrated_preflight = False

            preflight_path = asset_stage_path / "selected-asset-preflight.json"
            if not preflight_path.is_file():
                legacy_context_path = image_stage_path / "review-context.json"
                if legacy_context_path.is_file():
                    legacy_context = self._read_json(
                        legacy_context_path, "review-context"
                    )
                    legacy_data = legacy_context.get("data")
                    if (
                        isinstance(legacy_data, dict)
                        and isinstance(legacy_data.get("assets"), list)
                        and isinstance(legacy_data.get("policy_sha256"), str)
                    ):
                        asset_revision = int(
                            legacy_data.get(
                                "asset_matching_revision",
                                state["stages"]["asset_matching"]["revision"],
                            )
                        )
                        self._write_json_atomic(
                            preflight_path,
                            {
                                "schema_version": SCHEMA_VERSION,
                                "session_id": session_id,
                                "stage_id": "asset_matching",
                                "revision": asset_revision,
                                "policy_sha256": legacy_data["policy_sha256"],
                                "data": legacy_data,
                                "migration": {
                                    "migration_id": LEGACY_WORKFLOW_MIGRATION_ID,
                                    "source_stage_id": "image_review",
                                    "source_revision": int(
                                        legacy_context.get(
                                            "revision",
                                            image_state.get("revision", 0),
                                        )
                                    ),
                                    "source_path": str(legacy_context_path),
                                },
                            },
                        )
                        migrated_preflight = True

            slot_context_path = slot_stage_path / "review-context.json"
            if slot_context_path.is_file():
                slot_context = self._read_json(
                    slot_context_path, "review-context"
                )
                slot_data = slot_context.get("data")
                if isinstance(slot_data, dict) and not slot_data.get(
                    "two_page_workflow"
                ):
                    slot_data["two_page_workflow"] = True
                    slot_context["migration"] = {
                        "migration_id": LEGACY_WORKFLOW_MIGRATION_ID,
                        "legacy_page_count": 3,
                        "current_page_count": 2,
                    }
                    self._write_json_atomic(slot_context_path, slot_context)

            current_plan_exists = (
                slot_stage_path / "current-slot-plan.json"
            ).is_file()
            slot_context_exists = slot_context_path.is_file()
            if original_stage == "image_review":
                if image_status == "completed" and (
                    current_plan_exists or slot_context_exists
                ):
                    state["current_stage"] = "slots_copy"
                else:
                    state["current_stage"] = "asset_matching"
                    asset_state = state["stages"]["asset_matching"]
                    if asset_state.get("status") == "completed":
                        asset_state["status"] = "needs_user_input"

            state["workflow_profile"] = CURRENT_WORKFLOW_PROFILE
            state["workflow_migration"] = {
                "migration_id": LEGACY_WORKFLOW_MIGRATION_ID,
                "original_current_stage": original_stage,
                "mapped_current_stage": state.get("current_stage"),
                "legacy_image_review_status": image_status,
                "selected_asset_preflight_migrated": migrated_preflight,
                "legacy_files_preserved": True,
            }
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "workflow_migrated",
                session_id=session_id,
                migration_id=LEGACY_WORKFLOW_MIGRATION_ID,
                original_current_stage=original_stage,
                mapped_current_stage=state.get("current_stage"),
                selected_asset_preflight_migrated=migrated_preflight,
            )
            return state

    def save_input(
        self,
        session_id: str,
        stage_id: str,
        values: dict[str, Any],
        user_notes: str = "",
        *,
        expected_revision: int | None = None,
        allowed_current_statuses: frozenset[str] | set[str] | None = None,
        interaction_audit: dict[str, Any] | None = None,
        request_id: str | None = None,
        handoff_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._stage_index(stage_id)
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            stage_state = state["stages"][stage_id]
            persistence_request_id = self._persistence_request_id(request_id)
            payload_sha = self._stage_payload_sha(
                "submit", values, user_notes
            )
            completed = self._completed_stage_transaction(
                stage_path, persistence_request_id, payload_sha
            )
            if completed is not None:
                return completed
            if (
                allowed_current_statuses is not None
                and stage_state["status"] not in allowed_current_statuses
            ):
                raise InteractionConflict("stage status does not allow input submission")
            revision = int(stage_state["revision"]) + 1
            if expected_revision is not None and expected_revision != revision:
                raise InteractionConflict("expected revision is stale")
            transaction, input_document = self._prepare_stage_transaction(
                session_id=session_id,
                stage_id=stage_id,
                stage_path=stage_path,
                operation="submit",
                request_id=persistence_request_id,
                base_revision=int(stage_state["revision"]),
                target_revision=revision,
                payload_sha=payload_sha,
                values=values,
                user_notes=user_notes,
                interaction_audit=interaction_audit,
            )
            transaction["handoff_data"] = dict(handoff_data or {})
            self._write_json_atomic(
                self._stage_transaction_path(stage_path), transaction
            )
            created_at = str(input_document["created_at"])

            self._invalidate_after_edit(session_id, stage_id, state)
            input_path = stage_path / "input.json"
            self._write_json_atomic(input_path, input_document)
            self._advance_stage_transaction(stage_path, transaction, "content_written")
            self._write_revision_snapshot_idempotent(
                stage_path, revision, "input", input_document
            )
            self._advance_stage_transaction(stage_path, transaction, "snapshot_written")
            handoff = {
                "schema_version": SCHEMA_VERSION,
                "status": "ready_for_agent",
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": revision,
                "created_at": created_at,
                "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                **dict(handoff_data or {}),
            }
            self._write_json_atomic(stage_path / "handoff.json", handoff)
            self._write_revision_snapshot_idempotent(
                stage_path, revision, "handoff", handoff
            )
            stage_state["revision"] = revision
            stage_state["status"] = "ready_for_agent"
            state["current_stage"] = stage_id
            self._write_session_state(session_id, state)
            self._complete_stage_transaction(
                stage_path,
                transaction,
                response=handoff,
                event="input_saved",
            )
            return handoff

    def save_draft(
        self,
        session_id: str,
        stage_id: str,
        values: dict[str, Any],
        user_notes: str = "",
        *,
        expected_revision: int,
        interaction_audit: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an editable revision and atomically revoke its handoff."""

        self._stage_index(stage_id)
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            stage_state = state["stages"][stage_id]
            persistence_request_id = self._persistence_request_id(request_id)
            payload_sha = self._stage_payload_sha(
                "draft", values, user_notes
            )
            completed = self._completed_stage_transaction(
                stage_path, persistence_request_id, payload_sha
            )
            if completed is not None:
                return completed
            if expected_revision != stage_state["revision"]:
                raise InteractionConflict("expected revision is stale")
            revision = int(stage_state["revision"]) + 1
            transaction, document = self._prepare_stage_transaction(
                session_id=session_id,
                stage_id=stage_id,
                stage_path=stage_path,
                operation="draft",
                request_id=persistence_request_id,
                base_revision=int(stage_state["revision"]),
                target_revision=revision,
                payload_sha=payload_sha,
                values=values,
                user_notes=user_notes,
                interaction_audit=interaction_audit,
            )

            self._invalidate_after_edit(
                session_id, stage_id, state, remove_current_handoff=True
            )
            self._write_json_atomic(stage_path / "input.json", document)
            self._advance_stage_transaction(stage_path, transaction, "content_written")
            self._write_revision_snapshot_idempotent(
                stage_path, revision, "input", document
            )
            self._advance_stage_transaction(stage_path, transaction, "snapshot_written")
            stage_state["revision"] = revision
            stage_state["status"] = "draft"
            state["current_stage"] = stage_id
            self._write_session_state(session_id, state)
            self._complete_stage_transaction(
                stage_path,
                transaction,
                response=document,
                event="draft_saved",
            )
            return document

    def save_local_input(
        self,
        session_id: str,
        stage_id: str,
        values: dict[str, Any],
        user_notes: str = "",
        *,
        expected_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Commit an authoritative local action without creating an Agent handoff."""

        self._stage_index(stage_id)
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            stage_state = state["stages"][stage_id]
            persistence_request_id = self._persistence_request_id(request_id)
            payload_sha = self._stage_payload_sha(
                "local_action", values, user_notes
            )
            completed = self._completed_stage_transaction(
                stage_path, persistence_request_id, payload_sha
            )
            if completed is not None:
                return completed
            if stage_state["status"] not in {
                "draft",
                "needs_user_input",
                "blocked",
            }:
                raise InteractionConflict(
                    "stage status does not allow a local action"
                )
            if expected_revision != int(stage_state["revision"]):
                raise InteractionConflict("expected revision is stale")
            revision = expected_revision + 1
            transaction, document = self._prepare_stage_transaction(
                session_id=session_id,
                stage_id=stage_id,
                stage_path=stage_path,
                operation="local_action",
                request_id=persistence_request_id,
                base_revision=expected_revision,
                target_revision=revision,
                payload_sha=payload_sha,
                values=values,
                user_notes=user_notes,
                interaction_audit=None,
            )
            self._invalidate_after_edit(
                session_id, stage_id, state, remove_current_handoff=True
            )
            input_path = stage_path / "input.json"
            self._write_json_atomic(input_path, document)
            self._advance_stage_transaction(
                stage_path, transaction, "content_written"
            )
            self._write_revision_snapshot_idempotent(
                stage_path, revision, "input", document
            )
            self._advance_stage_transaction(
                stage_path, transaction, "snapshot_written"
            )
            stage_state["revision"] = revision
            stage_state["status"] = "draft"
            state["current_stage"] = stage_id
            self._write_session_state(session_id, state)
            response = {
                "schema_version": SCHEMA_VERSION,
                "status": "local_committed",
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": revision,
                "input_sha256": hashlib.sha256(
                    input_path.read_bytes()
                ).hexdigest(),
                "created_at": document["created_at"],
            }
            self._complete_stage_transaction(
                stage_path,
                transaction,
                response=response,
                event="local_input_saved",
            )
            return response

    def withdraw_handoff(
        self, session_id: str, stage_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        """Revoke an unclaimed handoff and reopen its exact input as a draft."""

        self._stage_index(stage_id)
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            stage_state = state["stages"][stage_id]
            if stage_state["status"] != "ready_for_agent":
                raise InteractionConflict("only an unclaimed handoff can be withdrawn")
            if expected_revision != stage_state["revision"]:
                raise InteractionConflict("expected revision is stale")
            handoff_path = stage_path / "handoff.json"
            handoff = self._read_json(handoff_path, "handoff")
            self._validate_handoff(session_id, stage_id, stage_path, handoff)
            handoff_path.unlink()
            stage_state["status"] = "draft"
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "handoff_withdrawn",
                session_id=session_id,
                stage_id=stage_id,
                revision=expected_revision,
            )
            return {"status": "draft", "revision": expected_revision}

    def write_result(
        self,
        session_id: str,
        stage_id: str,
        revision: int,
        input_sha256: str,
        *,
        status: str,
        summary: str,
        blocking_reasons: tuple[str, ...] | list[str] = (),
        evidence: tuple[Any, ...] | list[Any] = (),
        next_action: str | None = None,
        data: dict[str, Any] | None = None,
        claim_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an agent-owned result bound to the current user handoff."""

        self._stage_index(stage_id)
        if status not in STAGE_STATUSES:
            raise InteractionConflict("result status is not supported")
        for field_name, value in (
            ("summary", summary),
            ("next_action", next_action),
        ):
            if isinstance(value, str) and re.search(r"\?{3,}", value):
                raise InteractionConflict(
                    f"RESULT_TEXT_ENCODING_INVALID:{field_name}"
                )
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            handoff = self._read_json(stage_path / "handoff.json", "handoff")
            self._read_json(stage_path / "input.json", "input")
            expected_revision = state["stages"][stage_id]["revision"]
            if revision != expected_revision or handoff.get("revision") != revision:
                raise InteractionConflict("result revision does not match the current handoff")
            if (
                input_sha256 != handoff.get("input_sha256")
                or hashlib.sha256((stage_path / "input.json").read_bytes()).hexdigest()
                != input_sha256
            ):
                raise InteractionConflict("result input_sha256 does not match the current input")
            if handoff.get("session_id") != session_id or handoff.get("stage_id") != stage_id:
                raise InteractionConflict("handoff identity does not match its path")
            result_path = stage_path / "result.json"
            if result_path.is_file():
                existing_result = self._read_json(
                    result_path, "result"
                )
                if (
                    existing_result.get("revision") != revision
                    or existing_result.get("input_sha256") != input_sha256
                ):
                    raise InteractionConflict(
                        "BOUND_RESULT_IDENTITY_MISMATCH"
                    )
                if existing_result.get("status") == "completed":
                    return existing_result
            active_claim = state.get("processing_claim")
            if isinstance(active_claim, dict):
                if (
                    active_claim.get("stage_id") != stage_id
                    or int(active_claim.get("revision", -1)) != revision
                    or active_claim.get("input_sha256") != input_sha256
                ):
                    raise InteractionConflict(
                        "processing claim identity does not match result"
                    )
                if claim_id and active_claim.get("claim_id") != claim_id:
                    raise InteractionConflict("PROCESSING_CLAIM_STALE")
                active_attempt_id = str(
                    active_claim.get("attempt_id", "")
                ).strip()
                if (
                    attempt_id
                    and active_attempt_id
                    and attempt_id != active_attempt_id
                ):
                    raise InteractionConflict("COLLECTION_ATTEMPT_STALE")
                if not attempt_id and active_attempt_id:
                    attempt_id = active_attempt_id

            if result_path.is_file():
                existing_result = self._read_json(
                    result_path, "result"
                )
                existing_attempt_id = str(
                    existing_result.get("attempt_id", "")
                ).strip()
                if (
                    existing_attempt_id
                    and attempt_id
                    and existing_attempt_id != attempt_id
                ):
                    history_path = (
                        stage_path
                        / "results"
                        / f"{existing_attempt_id}.json"
                    )
                    if not history_path.is_file():
                        self._write_json_atomic(
                            history_path, existing_result
                        )

            completed_at = self._iso_timestamp(datetime.now(timezone.utc))
            result = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": revision,
                "input_sha256": input_sha256,
                "status": status,
                "summary": summary,
                "blocking_reasons": list(blocking_reasons),
                "evidence": list(evidence),
                "next_action": next_action,
                "created_at": completed_at,
                "completed_at": completed_at,
            }
            if attempt_id:
                result["attempt_id"] = attempt_id
            if data is not None:
                if not isinstance(data, dict):
                    raise InteractionConflict("result data must be an object")
                result["data"] = data
            if status in {"blocked", "needs_user_input"} and blocking_reasons:
                diagnostic = write_agent_diagnostic(
                    self._session_path(session_id),
                    session_id=session_id,
                    stage_id=stage_id,
                    revision=revision,
                    input_sha256=input_sha256,
                    reason_codes=blocking_reasons,
                    phase=f"{stage_id}_result",
                    message=summary,
                    evidence=evidence,
                )
                result["agent_diagnostic"] = {
                    "status": diagnostic["status"],
                    "owner": diagnostic["owner"],
                    "user_action_required": diagnostic[
                        "user_action_required"
                    ],
                }
            elif status == "completed":
                resolve_agent_diagnostic(
                    self._session_path(session_id),
                    stage_id=stage_id,
                    revision=revision,
                )
            self._write_json_atomic(stage_path / "result.json", result)
            review_context_path = stage_path / "review-context.json"
            if stage_id in {"completeness", "asset_matching"} and status in {
                "needs_user_input",
                "blocked",
            }:
                self._write_json_atomic(review_context_path, result)
            elif status == "completed" and review_context_path.is_file():
                review_context_path.unlink()
            state["stages"][stage_id]["status"] = status
            state["processing_claim"] = None
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "result_written",
                session_id=session_id,
                stage_id=stage_id,
                revision=revision,
                status=status,
            )
            return result

    def write_review_context(
        self,
        session_id: str,
        stage_id: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist agent-prepared UI context before the user submits a stage."""

        self._stage_index(stage_id)
        if not isinstance(document, dict):
            raise InteractionConflict("review context must be an object")
        with self._session_lock(session_id):
            state = self.load_session(session_id)
            revision = int(state["stages"][stage_id]["revision"])
            if (
                document.get("session_id") != session_id
                or document.get("stage_id") != stage_id
                or int(document.get("revision", -1)) != revision
            ):
                raise InteractionConflict(
                    "review context identity does not match current stage"
                )
            stage_path = self._stage_path(session_id, stage_id)
            self._write_json_atomic(stage_path / "review-context.json", document)
            if state["stages"][stage_id]["status"] in {"draft", "blocked"}:
                state["stages"][stage_id]["status"] = "needs_user_input"
                self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "review_context_written",
                session_id=session_id,
                stage_id=stage_id,
                revision=revision,
            )
            return document

    def _next_interaction_history(
        self,
        stage_path: Path,
        interaction_audit: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        current_path = stage_path / "input.json"
        if current_path.is_file():
            current = self._read_json(current_path, "input")
            existing = current.get("interaction_history", [])
            if isinstance(existing, list):
                history = [
                    dict(item) for item in existing if isinstance(item, dict)
                ]
        if interaction_audit:
            history.append(dict(interaction_audit))
        return history

    def wait_for_handoff(
        self,
        session_id: str,
        stage_id: str,
        timeout_seconds: float | None = None,
        *,
        claimant_id: str = "codex-agent",
        lease_seconds: int = 300,
        reclaim_expired: bool = False,
        resume_needs_user_input: bool = False,
        resume_blocked: bool = False,
    ) -> dict[str, Any]:
        """Wait for a valid handoff, then claim it for the interaction agent."""

        stage_path = self._stage_path(session_id, stage_id)
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        handoff_path = stage_path / "handoff.json"
        while True:
            state = self.load_session(session_id)
            if state.get("session_id") != session_id:
                raise InteractionConflict("HANDOFF_SESSION_CHANGED")
            if state.get("current_stage") != stage_id:
                raise InteractionConflict("HANDOFF_STAGE_CHANGED")
            if handoff_path.is_file():
                try:
                    with self._session_lock(session_id, deadline=deadline):
                        stage_path = self._stage_path(session_id, stage_id)
                        handoff_path = stage_path / "handoff.json"
                        if not handoff_path.is_file():
                            continue
                        handoff = self._read_json(handoff_path, "handoff")
                        self._validate_handoff(session_id, stage_id, stage_path, handoff)
                        state = self.load_session(session_id)
                        if state["stages"][stage_id]["revision"] != handoff["revision"]:
                            raise InteractionConflict("handoff revision does not match session state")
                        current_status = state["stages"][stage_id]["status"]
                        can_claim = current_status == "ready_for_agent"
                        if (
                            current_status == "needs_user_input"
                            and resume_needs_user_input
                        ):
                            can_claim = True
                        if current_status == "blocked" and resume_blocked:
                            can_claim = True
                        if (
                            current_status == "processing"
                            and reclaim_expired
                            and self._claim_is_expired(
                                state.get("processing_claim")
                            )
                        ):
                            can_claim = True
                        if can_claim:
                            now = datetime.now(timezone.utc)
                            heartbeat = self._iso_timestamp(now)
                            claim_id = uuid.uuid4().hex
                            prior_claim = state.get("processing_claim")
                            prior_attempt_id = (
                                str(prior_claim.get("attempt_id", "")).strip()
                                if isinstance(prior_claim, dict)
                                else ""
                            )
                            attempt_id = (
                                prior_attempt_id
                                if current_status == "processing"
                                and reclaim_expired
                                and prior_attempt_id
                                else uuid.uuid4().hex
                            )
                            state["stages"][stage_id]["status"] = "processing"
                            state["last_agent_heartbeat"] = heartbeat
                            state["processing_claim"] = {
                                "claim_id": claim_id,
                                "attempt_id": attempt_id,
                                "claimant_id": str(claimant_id).strip()
                                or "codex-agent",
                                "session_id": session_id,
                                "stage_id": stage_id,
                                "revision": int(handoff["revision"]),
                                "input_sha256": str(handoff["input_sha256"]),
                                "claimed_at": heartbeat,
                                "heartbeat_at": heartbeat,
                                "lease_expires_at": self._iso_timestamp(
                                    now
                                    + timedelta(
                                        seconds=max(1, int(lease_seconds))
                                    )
                                ),
                                "recovery_count": (
                                    int(prior_claim.get("recovery_count", 0))
                                    + 1
                                    if (
                                        current_status == "processing"
                                        and isinstance(prior_claim, dict)
                                    )
                                    else 0
                                ),
                            }
                            wait = state.get("agent_wait")
                            if (
                                isinstance(wait, dict)
                                and wait.get("session_id") == session_id
                                and wait.get("stage_id") == stage_id
                            ):
                                state["agent_wait"] = None
                            self._write_session_state(session_id, state)
                            self._append_event(
                                self._session_path(session_id),
                                "handoff_claimed",
                                session_id=session_id,
                                stage_id=stage_id,
                                revision=handoff["revision"],
                                claim_id=claim_id,
                                attempt_id=attempt_id,
                                reclaimed=current_status == "processing",
                            )
                            return handoff
                except TimeoutError as error:
                    raise TimeoutError(
                        f"timed out waiting for handoff for stage {stage_id}"
                    ) from error
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for handoff for stage {stage_id}")
            time.sleep(0.25)

    def create_agent_wait(
        self,
        session_id: str,
        stage_id: str,
        *,
        expected_revision: int,
        claimant_id: str = "codex-agent",
        lease_seconds: int = AGENT_WAIT_LEASE_SECONDS,
        budget_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Create an advisory wait lease without granting processing rights."""

        self._stage_index(stage_id)
        with self._session_lock(session_id):
            state = self.load_session(session_id)
            if state.get("current_stage") != stage_id:
                raise InteractionConflict("AGENT_WAIT_STAGE_MISMATCH")
            stage_state = state["stages"][stage_id]
            current_revision = int(stage_state["revision"])
            valid_expected_revision = (
                current_revision
                if stage_state["status"] in {
                    "ready_for_agent",
                    "processing",
                    "completed",
                }
                else current_revision + 1
            )
            if valid_expected_revision != expected_revision:
                raise InteractionConflict("AGENT_WAIT_REVISION_MISMATCH")
            now = datetime.now(timezone.utc)
            timestamp = self._iso_timestamp(now)
            normalized_claimant = str(claimant_id).strip() or "codex-agent"
            current_wait = state.get("agent_wait")
            if isinstance(current_wait, dict) and not self._wait_is_expired(
                current_wait
            ):
                same_waiter = (
                    current_wait.get("claimant_id") == normalized_claimant
                    and current_wait.get("session_id") == session_id
                    and current_wait.get("stage_id") == stage_id
                    and current_wait.get("expected_revision")
                    == int(expected_revision)
                )
                if not same_waiter:
                    raise InteractionConflict("AGENT_WAIT_BUSY")
                budget_expires_at = datetime.fromisoformat(
                    current_wait.get(
                        "budget_expires_at", current_wait["expires_at"]
                    )
                )
                current_wait.setdefault(
                    "budget_expires_at",
                    self._iso_timestamp(budget_expires_at),
                )
                current_wait["heartbeat_at"] = timestamp
                current_wait["expires_at"] = self._iso_timestamp(
                    min(
                        now
                        + timedelta(
                            seconds=min(
                                AGENT_WAIT_LEASE_SECONDS,
                                max(1, int(lease_seconds)),
                            )
                        ),
                        budget_expires_at,
                    )
                )
                self._write_session_state(session_id, state)
                self._append_event(
                    self._session_path(session_id),
                    "agent_wait_renewed",
                    session_id=session_id,
                    stage_id=stage_id,
                    expected_revision=expected_revision,
                    wait_id=current_wait["wait_id"],
                )
                return dict(current_wait)
            total_budget = (
                self.watch_budget_seconds(stage_id)
                if budget_seconds is None
                else max(0.0, float(budget_seconds))
            )
            budget_expires_at = now + timedelta(seconds=total_budget)
            wait = {
                "schema_version": 1,
                "wait_id": uuid.uuid4().hex,
                "claimant_id": normalized_claimant,
                "session_id": session_id,
                "stage_id": stage_id,
                "expected_revision": int(expected_revision),
                "started_at": timestamp,
                "heartbeat_at": timestamp,
                "expires_at": self._iso_timestamp(
                    min(
                        now
                        + timedelta(
                            seconds=min(
                                AGENT_WAIT_LEASE_SECONDS,
                                max(1, int(lease_seconds)),
                            )
                        ),
                        budget_expires_at,
                    )
                ),
                "budget_expires_at": self._iso_timestamp(budget_expires_at),
            }
            state["agent_wait"] = wait
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "agent_wait_created",
                session_id=session_id,
                stage_id=stage_id,
                expected_revision=expected_revision,
                wait_id=wait["wait_id"],
            )
            return dict(wait)

    def renew_agent_wait(
        self,
        session_id: str,
        wait_id: str,
        *,
        lease_seconds: int = AGENT_WAIT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        with self._session_lock(session_id):
            state = self.load_session(session_id)
            wait = state.get("agent_wait")
            if (
                not isinstance(wait, dict)
                or wait.get("wait_id") != wait_id
                or self._wait_is_expired(wait)
            ):
                raise InteractionConflict("AGENT_WAIT_STALE")
            budget_expires_at = datetime.fromisoformat(
                wait.get("budget_expires_at", wait["expires_at"])
            )
            wait.setdefault(
                "budget_expires_at",
                self._iso_timestamp(budget_expires_at),
            )
            if budget_expires_at <= datetime.now(timezone.utc):
                raise InteractionConflict("AGENT_WAIT_BUDGET_EXPIRED")
            stage_state = state["stages"][wait["stage_id"]]
            current_revision = int(stage_state["revision"])
            valid_expected_revision = (
                current_revision
                if stage_state["status"] in {
                    "ready_for_agent",
                    "processing",
                    "completed",
                }
                else current_revision + 1
            )
            if (
                wait.get("session_id") != session_id
                or wait.get("stage_id") != state.get("current_stage")
                or wait.get("expected_revision")
                != valid_expected_revision
            ):
                raise InteractionConflict("AGENT_WAIT_IDENTITY_MISMATCH")
            now = datetime.now(timezone.utc)
            wait["heartbeat_at"] = self._iso_timestamp(now)
            wait["expires_at"] = self._iso_timestamp(
                min(
                    now
                    + timedelta(
                        seconds=min(
                            AGENT_WAIT_LEASE_SECONDS,
                            max(1, int(lease_seconds)),
                        )
                    ),
                    budget_expires_at,
                )
            )
            self._write_session_state(session_id, state)
            return dict(wait)

    def listen_for_handoff(
        self,
        session_id: str,
        stage_id: str,
        *,
        claimant_id: str = "codex-agent",
        wait_seconds: float = AGENT_WAIT_SEGMENT_SECONDS,
        expected_revision: int | None = None,
        budget_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Return an existing handoff or wait one bounded segment for it.

        The advisory wait is created or renewed before durable handoff state is
        inspected.  A submission that predates this call is therefore returned
        immediately, while a later submission is discovered during the same
        bounded request.  This method never claims processing authority.
        """

        if (
            isinstance(wait_seconds, bool)
            or not isinstance(wait_seconds, (int, float))
            or not 0 <= float(wait_seconds) <= AGENT_WAIT_SEGMENT_SECONDS
        ):
            raise InteractionConflict("AGENT_WAIT_SEGMENT_INVALID")
        state = self.load_session(session_id)
        if state.get("current_stage") != stage_id:
            raise InteractionConflict("AGENT_WAIT_STAGE_MISMATCH")
        stage_state = state["stages"][stage_id]
        current_revision = int(stage_state["revision"])
        resolved_revision = (
            current_revision
            if stage_state["status"]
            in {"ready_for_agent", "processing", "completed"}
            else current_revision + 1
        )
        if (
            expected_revision is not None
            and int(expected_revision) != resolved_revision
        ):
            raise InteractionConflict("AGENT_WAIT_REVISION_MISMATCH")
        wait = self.create_agent_wait(
            session_id,
            stage_id,
            expected_revision=resolved_revision,
            claimant_id=claimant_id,
            lease_seconds=AGENT_WAIT_LEASE_SECONDS,
            budget_seconds=budget_seconds,
        )
        deadline = time.monotonic() + float(wait_seconds)
        handoff_status = self.handoff_display_state(session_id, stage_id)
        while (
            wait_seconds > 0
            and handoff_status.get("handoff_identity") is None
            and handoff_status.get("base_status")
            in {"draft", "needs_user_input", "blocked"}
            and time.monotonic() < deadline
        ):
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
            handoff_status = self.handoff_display_state(session_id, stage_id)

        if handoff_status.get("handoff_identity") is not None:
            current_wait = self.agent_wait(session_id, stage_id)
            if current_wait is not None and current_wait.get("budget_expired"):
                self.clear_agent_wait(session_id, wait_id=wait["wait_id"])
                wait = None
                handoff_status = self.handoff_display_state(session_id, stage_id)
            listen_status = "handoff_ready"
        elif handoff_status.get("base_status") not in {
            "draft",
            "needs_user_input",
            "blocked",
        }:
            try:
                self.clear_agent_wait(session_id, wait_id=wait["wait_id"])
            except InteractionConflict:
                pass
            wait = None
            handoff_status = self.handoff_display_state(session_id, stage_id)
            listen_status = "stage_changed"
        else:
            current_wait = self.agent_wait(session_id, stage_id)
            if current_wait is not None and current_wait.get("budget_expired"):
                self.clear_agent_wait(session_id, wait_id=wait["wait_id"])
                wait = None
                handoff_status = self.handoff_display_state(session_id, stage_id)
                listen_status = "budget_expired"
            else:
                wait = self.renew_agent_wait(
                    session_id,
                    wait["wait_id"],
                    lease_seconds=AGENT_WAIT_LEASE_SECONDS,
                )
                handoff_status = self.handoff_display_state(session_id, stage_id)
                listen_status = "waiting"
        return {
            "status": listen_status,
            "agent_wait": wait,
            "handoff_status": handoff_status,
            "segment_seconds": float(wait_seconds),
            "lease_seconds": AGENT_WAIT_LEASE_SECONDS,
        }

    def agent_wait(
        self, session_id: str, stage_id: str | None = None
    ) -> dict[str, Any] | None:
        state = self.load_session(session_id)
        wait = state.get("agent_wait")
        if not isinstance(wait, dict):
            return None
        if stage_id is not None and wait.get("stage_id") != stage_id:
            return None
        expired = self._wait_is_expired(wait)
        budget_expires_at = datetime.fromisoformat(
            wait.get("budget_expires_at", wait["expires_at"])
        )
        now = datetime.now(timezone.utc)
        budget_expired = budget_expires_at <= now
        budget_remaining = max(
            0,
            int(
                (
                    budget_expires_at - now
                ).total_seconds()
            ),
        )
        remaining = 0
        if not expired:
            remaining = max(
                0,
                int(
                    (
                        datetime.fromisoformat(wait["expires_at"])
                        - now
                    ).total_seconds()
                ),
            )
        return {
            **wait,
            "expired": expired,
            "remaining_seconds": remaining,
            "budget_expired": budget_expired,
            "budget_remaining_seconds": budget_remaining,
        }

    def clear_agent_wait(
        self, session_id: str, *, wait_id: str
    ) -> bool:
        with self._session_lock(session_id):
            state = self.load_session(session_id)
            wait = state.get("agent_wait")
            if not isinstance(wait, dict):
                return False
            if wait.get("wait_id") != wait_id:
                raise InteractionConflict("AGENT_WAIT_STALE")
            state["agent_wait"] = None
            self._write_session_state(session_id, state)
            return True

    def stage_transaction_status(
        self, session_id: str, stage_id: str
    ) -> dict[str, Any] | None:
        transaction = self._read_optional_transaction(
            self._stage_transaction_path(
                self._stage_path(session_id, stage_id)
            )
        )
        if transaction is None:
            return None
        return {
            key: transaction.get(key)
            for key in (
                "transaction_schema_version",
                "session_id",
                "stage_id",
                "operation",
                "request_id",
                "base_revision",
                "target_revision",
                "state",
                "created_at",
                "updated_at",
                "recovered_legacy_snapshot",
            )
        } | {
            "recoverable": transaction.get("state") != "completed",
            "reason_code": "STAGE_PERSISTENCE_INCOMPLETE",
            "next_action": "重试原保存/提交请求，或刷新页面让服务完成一致性恢复。",
        }

    def resolve_recovery_state(
        self, session_id: str, stage_id: str | None = None
    ) -> dict[str, Any]:
        """Resolve one explicit session using a deterministic precedence."""

        state = self.load_session(session_id)
        resolved_stage = stage_id or str(state.get("current_stage", ""))
        self._stage_index(resolved_stage)
        if state.get("current_stage") != resolved_stage:
            raise InteractionConflict("RECOVERY_STAGE_NOT_CURRENT")
        stage_state = state["stages"][resolved_stage]
        revision = int(stage_state["revision"])
        result = self.read_optional_stage_document(
            session_id, resolved_stage, "result"
        )
        input_path = self._stage_path(session_id, resolved_stage) / "input.json"
        input_sha = (
            hashlib.sha256(input_path.read_bytes()).hexdigest()
            if input_path.is_file()
            else None
        )
        if (
            isinstance(result, dict)
            and result.get("status") == "completed"
            and result.get("revision") == revision
            and result.get("input_sha256") == input_sha
        ):
            return {
                "status": "completed",
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": revision,
                "result": result,
            }
        claim = self.processing_claim(session_id, resolved_stage)
        if claim and not claim["expired"]:
            return {
                "status": "processing",
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": revision,
                "processing_claim": claim,
            }
        if stage_state["status"] == "processing":
            return {
                "status": "recoverable",
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": revision,
                "processing_claim": claim,
            }
        if stage_state["status"] == "ready_for_agent":
            handoff = self.read_optional_stage_document(
                session_id, resolved_stage, "handoff"
            )
            if handoff is None:
                raise InteractionConflict("RECOVERY_HANDOFF_MISSING")
            self._validate_handoff(
                session_id,
                resolved_stage,
                self._stage_path(session_id, resolved_stage),
                handoff,
            )
            if handoff.get("revision") != revision:
                raise InteractionConflict("RECOVERY_HANDOFF_REVISION_MISMATCH")
            return {
                "status": "ready",
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": revision,
                "handoff": handoff,
            }
        if stage_state["status"] in {"needs_user_input", "blocked"}:
            return {
                "status": stage_state["status"],
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": revision,
                "result": result,
            }
        return {
            "status": "draft",
            "reason_code": "NOT_FORMALLY_SUBMITTED",
            "session_id": session_id,
            "stage_id": resolved_stage,
            "revision": revision,
        }

    def handoff_display_state(
        self, session_id: str, stage_id: str | None = None
    ) -> dict[str, Any]:
        """Project authoritative recovery and advisory wait state for the UI."""

        state = self.load_session(session_id)
        resolved_stage = stage_id or str(state.get("current_stage", ""))
        if resolved_stage != state.get("current_stage"):
            raw_status = state["stages"][resolved_stage]["status"]
            return {
                "status": (
                    "ready" if raw_status == "ready_for_agent" else raw_status
                ),
                "base_status": (
                    "ready" if raw_status == "ready_for_agent" else raw_status
                ),
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": state["stages"][resolved_stage]["revision"],
                "agent_wait": None,
                "handoff_identity": None,
                "resume_prompt": None,
            }
        try:
            resolved = self.resolve_recovery_state(session_id, resolved_stage)
        except InteractionConflict as error:
            return {
                "status": "blocked",
                "base_status": "blocked",
                "reason_code": str(error),
                "session_id": session_id,
                "stage_id": resolved_stage,
                "revision": state["stages"][resolved_stage]["revision"],
                "agent_wait": self.agent_wait(session_id, resolved_stage),
                "handoff_identity": None,
                "resume_prompt": None,
            }
        wait = self.agent_wait(session_id, resolved["stage_id"])
        base_status = resolved["status"]
        display_status = base_status
        if base_status in {"draft", "ready"} and wait is not None:
            display_status = (
                "waiting_expired" if wait["expired"] else "waiting"
            )
        prompt = None
        if base_status == "ready" and (
            wait is None or wait.get("expired")
        ):
            prompt = "Codex 当前未监听；请在当前聊天输入“已提交”继续。"
        handoff_identity = None
        if base_status == "ready" and isinstance(resolved.get("handoff"), dict):
            handoff_identity = self._handoff_action_identity(resolved["handoff"])
        return {
            "status": display_status,
            "base_status": base_status,
            "session_id": session_id,
            "stage_id": resolved["stage_id"],
            "revision": resolved["revision"],
            "agent_wait": wait,
            "handoff_identity": handoff_identity,
            "resume_prompt": prompt,
        }

    def durable_handoff_identity(
        self, session_id: str, stage_id: str
    ) -> dict[str, Any] | None:
        """Return a validated current handoff identity without claiming it."""

        state = self.load_session(session_id)
        if state.get("current_stage") != stage_id:
            raise InteractionConflict("HANDOFF_STAGE_CHANGED")
        handoff = self.read_optional_stage_document(
            session_id, stage_id, "handoff"
        )
        if handoff is None:
            return None
        stage_path = self._stage_path(session_id, stage_id)
        self._validate_handoff(session_id, stage_id, stage_path, handoff)
        current_revision = int(state["stages"][stage_id]["revision"])
        if int(handoff["revision"]) != current_revision:
            raise InteractionConflict("HANDOFF_REVISION_CHANGED")
        return self._handoff_action_identity(handoff)

    @staticmethod
    def _handoff_action_identity(
        handoff: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return only the verified identity needed for the next fixed processor."""

        stage_id = str(handoff.get("stage_id", ""))
        handoff_kind = str(handoff.get("handoff_kind", ""))
        action_contract = HANDOFF_AGENT_ACTIONS.get(stage_id)
        if (
            stage_id == "asset_matching"
            and handoff_kind == "final_material_selection"
        ):
            action_contract = ("process-final-material-handoff", "workbench_api")
        if stage_id == "approval" and handoff_kind != "publish_authorization":
            action_contract = None
        if action_contract is None:
            return None
        allowed_action, transport = action_contract
        identity = {
            "session_id": str(handoff.get("session_id", "")),
            "stage_id": stage_id,
            "revision": int(handoff["revision"]),
            "input_sha256": str(handoff["input_sha256"]),
            "handoff_kind": handoff_kind,
            "allowed_action": allowed_action,
            "transport": transport,
        }
        if transport == "workbench_api":
            identity["endpoint"] = (
                f"/api/sessions/{identity['session_id']}/agent-actions/"
                f"{allowed_action}"
            )
            identity["claimant_id"] = "codex-agent"
        return identity

    @staticmethod
    def watch_budget_seconds(stage_id: str) -> int:
        if stage_id == "setup":
            return 2 * 60
        if stage_id in {"asset_matching", "image_review", "slots_copy"}:
            return 15 * 60
        if stage_id in {"completeness", "approval", "production_confirmation"}:
            return 10 * 60
        return 5 * 60

    def processing_claim(
        self, session_id: str, stage_id: str
    ) -> dict[str, Any] | None:
        state = self.load_session(session_id)
        claim = state.get("processing_claim")
        if not isinstance(claim, dict) or claim.get("stage_id") != stage_id:
            return None
        return {**claim, "expired": self._claim_is_expired(claim)}

    def renew_processing_claim(
        self,
        session_id: str,
        stage_id: str,
        claim_id: str,
        *,
        lease_seconds: int = 300,
    ) -> dict[str, Any]:
        with self._session_lock(session_id):
            state = self.load_session(session_id)
            claim = state.get("processing_claim")
            if (
                not isinstance(claim, dict)
                or claim.get("stage_id") != stage_id
                or claim.get("claim_id") != claim_id
                or self._claim_is_expired(claim)
            ):
                raise InteractionConflict("PROCESSING_CLAIM_STALE")
            now = datetime.now(timezone.utc)
            heartbeat = self._iso_timestamp(now)
            claim["heartbeat_at"] = heartbeat
            claim["lease_expires_at"] = self._iso_timestamp(
                now + timedelta(seconds=max(1, int(lease_seconds)))
            )
            state["last_agent_heartbeat"] = heartbeat
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "processing_heartbeat",
                session_id=session_id,
                stage_id=stage_id,
                claim_id=claim_id,
            )
            return dict(claim)

    def mark_processing_claim_recoverable(
        self,
        session_id: str,
        stage_id: str,
        *,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Expire only the exact proven-dead attempt; never act on PID alone."""

        with self._session_lock(session_id):
            state = self.load_session(session_id)
            claim = state.get("processing_claim")
            if (
                not isinstance(claim, dict)
                or claim.get("stage_id") != stage_id
                or claim.get("attempt_id") != attempt_id
            ):
                raise InteractionConflict(
                    "PROCESSING_CLAIM_IDENTITY_MISMATCH"
                )
            claim["lease_expires_at"] = self._iso_timestamp(
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            claim["recovery_reason"] = "OWNED_WORKER_CONFIRMED_DEAD"
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "processing_marked_recoverable",
                session_id=session_id,
                stage_id=stage_id,
                attempt_id=attempt_id,
            )
            return dict(claim)

    @staticmethod
    def _claim_is_expired(claim: object) -> bool:
        if not isinstance(claim, dict):
            return True
        value = claim.get("lease_expires_at")
        if not isinstance(value, str) or not value:
            return True
        try:
            expires = datetime.fromisoformat(value)
        except ValueError:
            return True
        if expires.tzinfo is None:
            return True
        return expires <= datetime.now(timezone.utc)

    @staticmethod
    def _wait_is_expired(wait: object) -> bool:
        if not isinstance(wait, dict):
            return True
        value = wait.get("expires_at")
        if not isinstance(value, str) or not value:
            return True
        try:
            expires = datetime.fromisoformat(value)
        except ValueError:
            return True
        return expires.tzinfo is None or expires <= datetime.now(timezone.utc)

    def recovery_instruction(self, session_id: str, stage_id: str) -> str:
        """Describe the fixed normal path before any exception-only diagnosis."""

        session_path = self._session_path(session_id)
        state = self.load_session(session_id)
        revision = state["stages"][stage_id]["revision"]
        ui_first = (
            "First run the managed UI launcher with this exact runs_root and "
            f"session_id (session_path='{session_path}'), open the returned "
            "current-stage URL, and wait for its "
            "handoff through the stage status and agent-wait APIs. Use only "
            "handoff_status.handoff_identity for revision, input_sha256, and "
            "allowed_action. Do not inspect project source, search routes, scan "
            "the Plugin or runs directory, or read handoff/input files while the "
            "official API and processor remain healthy. Source diagnosis is "
            "allowed only after an official processor persists a stable error "
            "reason and the Agent has read diagnose-session. Do not ask for "
            "store, month, image roots, or other "
            "structured fields in chat while the page is available. Chat "
            "fallback requires an allowed recorded reason code. "
        )
        if (
            state.get("workflow_profile") == CURRENT_WORKFLOW_PROFILE
            and stage_id == "slots_copy"
        ):
            return (
                ui_first
                +
                "Continue upload-search-materials without creating a new session. "
                f"Use session_id='{session_id}', stage_id='slots_copy', "
                f"revision={revision}, runs_root='{self._runs_root}'. "
                "Continue through the current workbench page and its declared "
                "agent-request endpoints. Do not wait for, claim, retry, or "
                "create a slot-planning Agent request. Continue with the "
                "deterministic draft or user manual edits; only final "
                "copywriting may create an Agent request."
            )
        if (
            state.get("workflow_profile") == CURRENT_WORKFLOW_PROFILE
            and stage_id == "completeness"
        ):
            return (
                ui_first
                +
                "Continue upload-search-materials without creating a new session. "
                f"Use session_id='{session_id}', stage_id='completeness', "
                f"revision={revision}, runs_root='{self._runs_root}'. "
                "Obtain handoff_status.handoff_identity through the mandatory "
                "backlog-first agent-wait action=listen (or the fixed "
                "listen-handoff fallback), then run exactly its "
                "process-product-selection workbench action. Do not use legacy "
                "wait-handoff or resume-session to claim first; the specialized "
                "action validates and claims the exact handoff itself."
            )
        if (
            state.get("workflow_profile") == CURRENT_WORKFLOW_PROFILE
            and stage_id == "asset_matching"
        ):
            handoff = self.read_optional_stage_document(
                session_id, stage_id, "handoff"
            )
            if (
                isinstance(handoff, dict)
                and handoff.get("handoff_kind") == "final_material_selection"
            ):
                return (
                    ui_first
                    +
                    "Continue upload-search-materials without creating a new session. "
                    f"Use session_id='{session_id}', stage_id='asset_matching', "
                    f"revision={revision}, runs_root='{self._runs_root}'. "
                    "Obtain handoff_status.handoff_identity through the mandatory "
                    "backlog-first agent-wait action=listen (or the fixed "
                    "listen-handoff fallback), then run exactly its "
                    "process-final-material-handoff workbench action. Do not use "
                    "legacy wait-handoff or resume-session to claim first; the "
                    "specialized action validates and claims the exact handoff itself."
                )
        return (
            ui_first
            +
            "Continue upload-search-materials without creating a new session. "
            f"Use session_id='{session_id}', stage_id='{stage_id}', revision={revision}, "
            f"runs_root='{self._runs_root}'. "
            "Read the exact stage status and use its verified handoff_identity; "
            "do not inspect durable files or project source during normal flow. "
            "Do not select another session by recency."
        )

    def read_decision_mode(
        self,
        session_id: str,
        stage_id: str,
        decision_id: str,
    ) -> dict[str, Any]:
        """Return the persisted mode or the deterministic Skill default."""

        boundary = get_decision_boundary(stage_id, decision_id)
        path = self._decision_mode_path(session_id, stage_id, decision_id)
        if path.is_file():
            document = self._read_json(path, "decision-mode")
            self._validate_decision_mode(
                document, session_id, stage_id, decision_id
            )
            return document
        state = self.load_session(session_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "stage_id": stage_id,
            "decision_id": decision_id,
            "mode": boundary.default_mode,
            "selected_by": "skill_default",
            "selected_at": None,
            "bound_revision": int(state["stages"][stage_id]["revision"]),
            "persisted": False,
        }

    def write_decision_mode(
        self,
        session_id: str,
        stage_id: str,
        decision_id: str,
        mode: str,
        *,
        selected_by: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Persist an explicit branch choice bound to the current revision."""

        boundary = get_decision_boundary(stage_id, decision_id)
        if mode not in boundary.allowed_modes:
            raise InteractionConflict("DECISION_MODE_NOT_ALLOWED")
        if selected_by not in {"user", "agent", "migration"}:
            raise InteractionConflict("decision selected_by is not supported")
        with self._session_lock(session_id):
            state = self.load_session(session_id)
            revision = int(state["stages"][stage_id]["revision"])
            if revision != int(expected_revision):
                raise InteractionConflict("DECISION_REVISION_STALE")
            previous = self.read_decision_mode(
                session_id, stage_id, decision_id
            )
            selected_at = self._iso_timestamp(datetime.now(timezone.utc))
            document = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "stage_id": stage_id,
                "decision_id": decision_id,
                "mode": mode,
                "selected_by": selected_by,
                "selected_at": selected_at,
                "bound_revision": revision,
                "persisted": True,
                "previous_mode": previous.get("mode"),
            }
            path = self._decision_mode_path(
                session_id, stage_id, decision_id
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(path, document)
            event = (
                "decision_mode_selected"
                if previous.get("mode") != mode
                else "decision_mode_reaffirmed"
            )
            self._append_event(
                self._session_path(session_id),
                event,
                session_id=session_id,
                stage_id=stage_id,
                decision_id=decision_id,
                mode=mode,
                previous_mode=previous.get("mode"),
                selected_by=selected_by,
                revision=revision,
            )
            return document

    def append_decision_event(
        self,
        session_id: str,
        stage_id: str,
        decision_id: str,
        event: str,
        **details: Any,
    ) -> None:
        """Append an auditable decision event without changing stage data."""

        get_decision_boundary(stage_id, decision_id)
        self._append_event(
            self._session_path(session_id),
            event,
            session_id=session_id,
            stage_id=stage_id,
            decision_id=decision_id,
            **details,
        )

    def _decision_mode_path(
        self, session_id: str, stage_id: str, decision_id: str
    ) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", decision_id):
            raise InteractionPathError("invalid decision identifier")
        return self._safe_path(
            self._stage_path(session_id, stage_id)
            / "decision-modes"
            / f"{decision_id}.json"
        )

    @staticmethod
    def _validate_decision_mode(
        document: dict[str, Any],
        session_id: str,
        stage_id: str,
        decision_id: str,
    ) -> None:
        boundary = get_decision_boundary(stage_id, decision_id)
        if (
            document.get("session_id") != session_id
            or document.get("stage_id") != stage_id
            or document.get("decision_id") != decision_id
            or document.get("mode") not in boundary.allowed_modes
            or not isinstance(document.get("bound_revision"), int)
        ):
            raise InteractionConflict("decision-mode identity is invalid")

    def _write_revision_snapshot(
        self,
        stage_path: Path,
        revision: int,
        document_name: str,
        document: dict[str, Any],
    ) -> None:
        revision_path = stage_path / "revisions" / f"{revision:04d}"
        revision_path.mkdir(parents=True, exist_ok=True)
        target = revision_path / f"{document_name}.json"
        if target.exists():
            raise InteractionConflict("revision snapshot already exists")
        self._write_json_atomic(target, document)

    @staticmethod
    def _persistence_request_id(value: str | None) -> str:
        request_id = value or uuid.uuid4().hex
        if not PERSISTENCE_REQUEST_ID_PATTERN.fullmatch(request_id):
            raise InteractionConflict("PERSISTENCE_REQUEST_ID_INVALID")
        return request_id

    @staticmethod
    def _stage_payload_sha(
        operation: str, values: dict[str, Any], user_notes: str
    ) -> str:
        payload = {
            "operation": operation,
            "values": values,
            "user_notes": user_notes,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _stage_transaction_path(stage_path: Path) -> Path:
        return stage_path / "stage-transaction.json"

    @staticmethod
    def _stage_transaction_history_path(
        stage_path: Path, request_id: str
    ) -> Path:
        return stage_path / "transactions" / f"{request_id}.json"

    def _read_optional_transaction(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        document = self._read_json(path, "stage-transaction")
        if document.get("transaction_schema_version") != (
            STAGE_TRANSACTION_SCHEMA_VERSION
        ):
            raise InteractionConflict("STAGE_TRANSACTION_SCHEMA_UNSUPPORTED")
        return document

    def _completed_stage_transaction(
        self,
        stage_path: Path,
        request_id: str,
        payload_sha: str,
    ) -> dict[str, Any] | None:
        candidates = (
            self._stage_transaction_history_path(stage_path, request_id),
            self._stage_transaction_path(stage_path),
        )
        for path in candidates:
            transaction = self._read_optional_transaction(path)
            if not transaction or transaction.get("request_id") != request_id:
                continue
            if transaction.get("payload_sha256") != payload_sha:
                raise InteractionConflict("REVISION_CONTENT_CONFLICT")
            response = transaction.get("response")
            if transaction.get("state") == "completed" and isinstance(
                response, dict
            ):
                return response
        return None

    @staticmethod
    def _input_document_matches(
        document: dict[str, Any],
        *,
        session_id: str,
        stage_id: str,
        revision: int,
        values: dict[str, Any],
        user_notes: str,
    ) -> bool:
        return bool(
            document.get("session_id") == session_id
            and document.get("stage_id") == stage_id
            and document.get("revision") == revision
            and document.get("values") == values
            and document.get("user_notes", "") == user_notes
        )

    def _prepare_stage_transaction(
        self,
        *,
        session_id: str,
        stage_id: str,
        stage_path: Path,
        operation: str,
        request_id: str,
        base_revision: int,
        target_revision: int,
        payload_sha: str,
        values: dict[str, Any],
        user_notes: str,
        interaction_audit: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        path = self._stage_transaction_path(stage_path)
        current = self._read_optional_transaction(path)
        if current is not None:
            identity = (
                current.get("request_id") == request_id
                and current.get("operation") == operation
                and current.get("base_revision") == base_revision
                and current.get("target_revision") == target_revision
                and current.get("payload_sha256") == payload_sha
            )
            if not identity:
                raise InteractionConflict("STAGE_TRANSACTION_ACTIVE")
            stored = current.get("input_document")
            if not isinstance(stored, dict):
                raise InteractionConflict("STAGE_TRANSACTION_INVALID")
            return current, stored

        snapshot_path = (
            stage_path
            / "revisions"
            / f"{target_revision:04d}"
            / "input.json"
        )
        if snapshot_path.is_file():
            document = self._read_json(snapshot_path, "input")
            if not self._input_document_matches(
                document,
                session_id=session_id,
                stage_id=stage_id,
                revision=target_revision,
                values=values,
                user_notes=user_notes,
            ):
                raise InteractionConflict("REVISION_CONTENT_CONFLICT")
            recovered_legacy_snapshot = True
        else:
            created_at = self._iso_timestamp(datetime.now(timezone.utc))
            document = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": target_revision,
                "created_at": created_at,
                "submitted_at": created_at,
                "values": values,
                "user_notes": user_notes,
                "interaction_history": self._next_interaction_history(
                    stage_path, interaction_audit
                ),
                "persistence_request_id": request_id,
            }
            recovered_legacy_snapshot = False
        now = self._iso_timestamp(datetime.now(timezone.utc))
        transaction = {
            "schema_version": SCHEMA_VERSION,
            "transaction_schema_version": STAGE_TRANSACTION_SCHEMA_VERSION,
            "session_id": session_id,
            "stage_id": stage_id,
            "operation": operation,
            "request_id": request_id,
            "base_revision": base_revision,
            "target_revision": target_revision,
            "payload_sha256": payload_sha,
            "state": "prepared",
            "created_at": now,
            "updated_at": now,
            "input_document": document,
            "recovered_legacy_snapshot": recovered_legacy_snapshot,
        }
        self._write_json_atomic(path, transaction)
        return transaction, document

    def _advance_stage_transaction(
        self,
        stage_path: Path,
        transaction: dict[str, Any],
        state: str,
    ) -> None:
        transaction["state"] = state
        transaction["updated_at"] = self._iso_timestamp(
            datetime.now(timezone.utc)
        )
        self._write_json_atomic(
            self._stage_transaction_path(stage_path), transaction
        )

    def _event_for_request_exists(
        self, session_path: Path, event: str, request_id: str
    ) -> bool:
        path = session_path / "events.ndjson"
        if not path.is_file():
            return False
        try:
            with path.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(value, dict)
                        and value.get("event") == event
                        and value.get("persistence_request_id") == request_id
                    ):
                        return True
        except OSError:
            return False
        return False

    def _complete_stage_transaction(
        self,
        stage_path: Path,
        transaction: dict[str, Any],
        *,
        response: dict[str, Any],
        event: str,
    ) -> None:
        transaction["response"] = response
        self._advance_stage_transaction(
            stage_path, transaction, "state_committed"
        )
        session_path = self._session_path(str(transaction["session_id"]))
        request_id = str(transaction["request_id"])
        if not self._event_for_request_exists(
            session_path, event, request_id
        ):
            self._append_event(
                session_path,
                event,
                session_id=transaction["session_id"],
                stage_id=transaction["stage_id"],
                revision=transaction["target_revision"],
                persistence_request_id=request_id,
            )
        transaction["completed_at"] = self._iso_timestamp(
            datetime.now(timezone.utc)
        )
        self._advance_stage_transaction(stage_path, transaction, "completed")
        history = self._stage_transaction_history_path(
            stage_path, request_id
        )
        self._write_json_atomic(history, transaction)
        current = self._stage_transaction_path(stage_path)
        if current.is_file():
            current.unlink()

    def _write_revision_snapshot_idempotent(
        self,
        stage_path: Path,
        revision: int,
        document_name: str,
        document: dict[str, Any],
    ) -> None:
        revision_path = stage_path / "revisions" / f"{revision:04d}"
        revision_path.mkdir(parents=True, exist_ok=True)
        target = revision_path / f"{document_name}.json"
        if target.is_file():
            existing = self._read_json(target, document_name)
            if existing != document:
                raise InteractionConflict("REVISION_CONTENT_CONFLICT")
            return
        self._write_json_atomic(target, document)

    def _reconcile_pending_stage_transaction(
        self, session_id: str, initial_state: dict[str, Any]
    ) -> dict[str, Any]:
        pending_stage = None
        for stage in STAGES:
            if self._stage_transaction_path(
                self._stage_path(session_id, stage.id)
            ).is_file():
                pending_stage = stage.id
                break
        if pending_stage is None:
            return initial_state
        with self._session_lock(session_id):
            state = self._load_session_file(session_id)
            stage_path = self._stage_path(session_id, pending_stage)
            transaction = self._read_optional_transaction(
                self._stage_transaction_path(stage_path)
            )
            if transaction is None:
                return state
            document = transaction.get("input_document")
            if not isinstance(document, dict):
                return state
            base_revision = int(transaction.get("base_revision", -1))
            target_revision = int(transaction.get("target_revision", -1))
            stage_state = state["stages"][pending_stage]
            current_revision = int(stage_state["revision"])
            operation = str(transaction.get("operation", ""))
            expected_status = (
                "ready_for_agent" if operation == "submit" else "draft"
            )
            if current_revision == target_revision:
                if stage_state.get("status") != expected_status:
                    return state
            elif current_revision == base_revision:
                self._write_json_atomic(stage_path / "input.json", document)
                self._write_revision_snapshot_idempotent(
                    stage_path, target_revision, "input", document
                )
                if operation == "submit":
                    input_path = stage_path / "input.json"
                    handoff = {
                        "schema_version": SCHEMA_VERSION,
                        "status": "ready_for_agent",
                        "session_id": session_id,
                        "stage_id": pending_stage,
                        "revision": target_revision,
                        "created_at": document["created_at"],
                        "input_sha256": hashlib.sha256(
                            input_path.read_bytes()
                        ).hexdigest(),
                        **dict(transaction.get("handoff_data") or {}),
                    }
                    self._write_json_atomic(
                        stage_path / "handoff.json", handoff
                    )
                    self._write_revision_snapshot_idempotent(
                        stage_path, target_revision, "handoff", handoff
                    )
                    response = handoff
                elif operation in {"draft", "local_action"}:
                    response = document
                else:
                    return state
                stage_state["revision"] = target_revision
                stage_state["status"] = expected_status
                state["current_stage"] = pending_stage
                self._write_session_state(session_id, state)
            else:
                return state
            if operation == "submit":
                response = self._read_json(
                    stage_path / "handoff.json", "handoff"
                )
            elif operation == "local_action":
                input_path = stage_path / "input.json"
                response = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "local_committed",
                    "session_id": session_id,
                    "stage_id": pending_stage,
                    "revision": target_revision,
                    "input_sha256": hashlib.sha256(
                        input_path.read_bytes()
                    ).hexdigest(),
                    "created_at": document["created_at"],
                }
            else:
                response = document
            self._complete_stage_transaction(
                stage_path,
                transaction,
                response=response,
                event=(
                    "input_saved"
                    if operation == "submit"
                    else (
                        "local_input_saved"
                        if operation == "local_action"
                        else "draft_saved"
                    )
                ),
            )
            return self._load_session_file(session_id)

    def read_optional_stage_document(
        self, session_id: str, stage_id: str, document_name: str
    ) -> dict[str, Any] | None:
        """Read a versioned protocol document, or return ``None`` when absent."""

        if document_name not in {"input", "handoff", "result", "review-context"}:
            raise KeyError(document_name)
        path = self._stage_path(session_id, stage_id) / f"{document_name}.json"
        if not path.is_file():
            return None
        return self._read_json(path, document_name)

    def _session_path(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        candidate = self._safe_path(self._runs_root / session_id)
        if not candidate.is_dir():
            raise InteractionPathError(f"session does not exist: {session_id}")
        return candidate

    def _stage_path(self, session_id: str, stage_id: str) -> Path:
        try:
            stage_index = next(
                index for index, stage in enumerate(STAGES, start=1) if stage.id == stage_id
            )
            get_stage(stage_id)
        except (KeyError, StopIteration):
            raise KeyError(stage_id) from None
        session_path = self._session_path(session_id)
        preferred = self._safe_path(
            session_path / self._stage_directory_name(stage_index, stage_id)
        )
        if preferred.is_dir():
            return preferred
        legacy_suffix = stage_id.replace("_", "-")
        legacy_matches = sorted(session_path.glob(f"[0-9][0-9]-{legacy_suffix}"))
        if len(legacy_matches) == 1:
            return self._safe_path(legacy_matches[0])
        return preferred

    def _invalidate_after_edit(
        self,
        session_id: str,
        stage_id: str,
        state: dict[str, Any],
        *,
        remove_current_handoff: bool = False,
    ) -> None:
        """Remove results derived from an edited input and all later handoffs."""

        stage_index = self._stage_index(stage_id)
        session_path = self._session_path(session_id)
        affected = STAGES[stage_index - 1 :]
        for offset, stage in enumerate(affected, start=stage_index):
            artifact_names = ("result.json", "approval.json")
            if stage.id != stage_id or remove_current_handoff:
                artifact_names = ("handoff.json", *artifact_names)
            if stage.id != stage_id:
                artifact_names = ("review-context.json", *artifact_names)
                state["stages"][stage.id]["status"] = "draft"
            stage_path = self._stage_path(session_id, stage.id)
            for artifact_name in artifact_names:
                artifact_path = stage_path / artifact_name
                if artifact_path.is_file():
                    metadata = self._artifact_metadata(artifact_path, artifact_name)
                    artifact_path.unlink()
                    self._append_event(
                        session_path,
                        "artifact_invalidated",
                        session_id=session_id,
                        stage_id=stage.id,
                        artifact=artifact_name,
                        **metadata,
                    )

    @staticmethod
    def _artifact_metadata(path: Path, artifact_name: str) -> dict[str, Any]:
        """Retain only identity-safe fields from an invalidated artifact."""

        try:
            document = SessionStore._read_json(path, artifact_name.removesuffix(".json"))
        except InteractionConflict:
            return {}
        metadata: dict[str, Any] = {}
        if isinstance(document.get("revision"), int):
            metadata["revision"] = document["revision"]
        if isinstance(document.get("status"), str):
            metadata["status"] = document["status"]
        if isinstance(document.get("input_sha256"), str):
            metadata["input_sha256"] = document["input_sha256"]
        return metadata

    @staticmethod
    def _read_json(path: Path, document_name: str) -> dict[str, Any]:
        try:
            document = read_json(path)
        except FileNotFoundError as error:
            raise InteractionConflict(f"{document_name}.json is missing") from error
        except json.JSONDecodeError as error:
            raise InteractionConflict(f"{document_name}.json is invalid") from error
        if not isinstance(document, dict):
            raise InteractionConflict(f"{document_name}.json must contain an object")
        SessionStore._validate_schema_version(document, document_name)
        return document

    @staticmethod
    def _validate_schema_version(document: dict[str, Any], document_name: str) -> None:
        if document.get("schema_version") != SCHEMA_VERSION:
            raise InteractionConflict(
                f"{document_name}.json schema_version is not supported"
            )

    def _write_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        state["updated_at"] = self._iso_timestamp(datetime.now(timezone.utc))
        self._write_json_atomic(self._session_path(session_id) / "session.json", state)

    @staticmethod
    def _stage_index(stage_id: str) -> int:
        try:
            return next(index for index, stage in enumerate(STAGES, start=1) if stage.id == stage_id)
        except StopIteration:
            raise KeyError(stage_id) from None

    @staticmethod
    def _validate_handoff(
        session_id: str, stage_id: str, stage_path: Path, handoff: dict[str, Any]
    ) -> None:
        SessionStore._validate_schema_version(handoff, "handoff")
        if handoff.get("session_id") != session_id or handoff.get("stage_id") != stage_id:
            raise InteractionConflict("handoff identity does not match its path")
        actual_hash = hashlib.sha256((stage_path / "input.json").read_bytes()).hexdigest()
        if handoff.get("input_sha256") != actual_hash:
            raise InteractionConflict("handoff input_sha256 does not match input.json")
        SessionStore._read_json(stage_path / "input.json", "input")

    @contextmanager
    def _session_lock(self, session_id: str, deadline: float | None = None):
        """Serialize mutations from local UI and Agent processes for one session."""

        session_path = self._session_path(session_id)
        key = os.path.normcase(str(session_path.resolve()))
        held_keys = getattr(self._lock_state, "session_keys", None)
        if held_keys is None:
            held_keys = set()
            self._lock_state.session_keys = held_keys
        already_held = key in held_keys
        with self._process_locks_guard:
            process_lock = self._process_locks.setdefault(
                key, threading.RLock()
            )
        acquired = process_lock.acquire(
            timeout=-1
            if deadline is None
            else max(0.0, deadline - time.monotonic())
        )
        if not acquired:
            raise TimeoutError("timed out acquiring session lock")
        try:
            if already_held:
                yield session_path
                return
            with self._file_lock(
                session_path / ".session.lock", deadline=deadline
            ):
                held_keys.add(key)
                try:
                    yield session_path
                finally:
                    held_keys.discard(key)
        finally:
            process_lock.release()

    @staticmethod
    @contextmanager
    def _file_lock(path: Path, deadline: float | None = None):
        """Use an OS advisory byte-range lock that releases when a process exits."""

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if deadline is not None and time.monotonic() >= deadline:
                            raise TimeoutError("timed out acquiring session lock")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if deadline is not None and time.monotonic() >= deadline:
                            raise TimeoutError("timed out acquiring session lock")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self._runs_root)
        except ValueError as error:
            raise InteractionPathError("path escapes runs_root") from error
        return resolved

    @staticmethod
    def _stage_directory_name(index: int, stage_id: str) -> str:
        return f"{index:02d}-{stage_id.replace('_', '-')}"

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        normalized = session_id.replace("\\", "/")
        if (
            not session_id
            or normalized != session_id
            or "/" in normalized
            or normalized in {".", ".."}
            or ".." in normalized.split("_")
            or Path(session_id).is_absolute()
            or (len(session_id) >= 2 and session_id[1] == ":")
        ):
            raise InteractionPathError("invalid session identifier")

    @staticmethod
    def _iso_timestamp(moment: datetime) -> str:
        return iso_timestamp(moment)

    @staticmethod
    def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
        atomic_write_json(path, document)

    @staticmethod
    def _append_event(session_path: Path, event: str, **details: Any) -> None:
        payload = {"event": event, "created_at": SessionStore._iso_timestamp(datetime.now(timezone.utc))}
        payload.update(details)
        with (session_path / "events.ndjson").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
