"""Durable, filesystem-backed interaction sessions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .stages import STAGES, get_stage


SCHEMA_VERSION = 1
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

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = Path(runs_root).expanduser()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._runs_root = self.runs_root.resolve()

    def create_session(self, now: datetime | None = None) -> SessionRecord:
        created = now or datetime.now()
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
                "last_agent_heartbeat": None,
            }
            self._write_json_atomic(session_path / "session.json", state)
            self._append_event(session_path, "session_created", session_id=session_id)
        return SessionRecord(session_id=session_id, path=session_path)

    def load_session(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        try:
            with (path / "session.json").open(encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError as error:
            raise InteractionPathError(f"session does not exist: {session_id}") from error
        if not isinstance(state, dict):
            raise InteractionConflict("session.json must contain an object")
        self._validate_schema_version(state, "session")
        if state.get("session_id") != session_id:
            raise InteractionConflict("session.json identity does not match its path")
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
    ) -> dict[str, Any]:
        self._stage_index(stage_id)
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            stage_state = state["stages"][stage_id]
            if (
                allowed_current_statuses is not None
                and stage_state["status"] not in allowed_current_statuses
            ):
                raise InteractionConflict("stage status does not allow input submission")
            revision = int(stage_state["revision"]) + 1
            if expected_revision is not None and expected_revision != revision:
                raise InteractionConflict("expected revision is stale")
            created_at = self._iso_timestamp(datetime.now(timezone.utc))

            self._invalidate_after_edit(session_id, stage_id, state)
            input_document = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": revision,
                "created_at": created_at,
                "submitted_at": created_at,
                "values": values,
                "user_notes": user_notes,
            }
            input_path = stage_path / "input.json"
            self._write_json_atomic(input_path, input_document)
            self._write_revision_snapshot(stage_path, revision, "input", input_document)
            handoff = {
                "schema_version": SCHEMA_VERSION,
                "status": "ready_for_agent",
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": revision,
                "created_at": created_at,
                "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            }
            self._write_json_atomic(stage_path / "handoff.json", handoff)
            self._write_revision_snapshot(stage_path, revision, "handoff", handoff)
            stage_state["revision"] = revision
            stage_state["status"] = "ready_for_agent"
            state["current_stage"] = stage_id
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "input_saved",
                session_id=session_id,
                stage_id=stage_id,
                revision=revision,
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
    ) -> dict[str, Any]:
        """Persist an editable revision and atomically revoke its handoff."""

        self._stage_index(stage_id)
        with self._session_lock(session_id):
            stage_path = self._stage_path(session_id, stage_id)
            state = self.load_session(session_id)
            stage_state = state["stages"][stage_id]
            if expected_revision != stage_state["revision"]:
                raise InteractionConflict("expected revision is stale")
            revision = int(stage_state["revision"]) + 1
            created_at = self._iso_timestamp(datetime.now(timezone.utc))

            self._invalidate_after_edit(
                session_id, stage_id, state, remove_current_handoff=True
            )
            document = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "stage_id": stage_id,
                "revision": revision,
                "created_at": created_at,
                "submitted_at": created_at,
                "values": values,
                "user_notes": user_notes,
            }
            self._write_json_atomic(stage_path / "input.json", document)
            self._write_revision_snapshot(stage_path, revision, "input", document)
            stage_state["revision"] = revision
            stage_state["status"] = "draft"
            state["current_stage"] = stage_id
            self._write_session_state(session_id, state)
            self._append_event(
                self._session_path(session_id),
                "draft_saved",
                session_id=session_id,
                stage_id=stage_id,
                revision=revision,
            )
            return document

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
    ) -> dict[str, Any]:
        """Persist an agent-owned result bound to the current user handoff."""

        self._stage_index(stage_id)
        if status not in STAGE_STATUSES:
            raise InteractionConflict("result status is not supported")
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
            if data is not None:
                if not isinstance(data, dict):
                    raise InteractionConflict("result data must be an object")
                result["data"] = data
            self._write_json_atomic(stage_path / "result.json", result)
            review_context_path = stage_path / "review-context.json"
            if stage_id == "completeness" and status in {"needs_user_input", "blocked"}:
                self._write_json_atomic(review_context_path, result)
            elif status == "completed" and review_context_path.is_file():
                review_context_path.unlink()
            state["stages"][stage_id]["status"] = status
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

    def wait_for_handoff(
        self,
        session_id: str,
        stage_id: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Wait for a valid handoff, then claim it for the interaction agent."""

        stage_path = self._stage_path(session_id, stage_id)
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        handoff_path = stage_path / "handoff.json"
        while True:
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
                        if state["stages"][stage_id]["status"] == "ready_for_agent":
                            heartbeat = self._iso_timestamp(datetime.now(timezone.utc))
                            state["stages"][stage_id]["status"] = "processing"
                            state["last_agent_heartbeat"] = heartbeat
                            self._write_session_state(session_id, state)
                            self._append_event(
                                self._session_path(session_id),
                                "handoff_claimed",
                                session_id=session_id,
                                stage_id=stage_id,
                                revision=handoff["revision"],
                            )
                            return handoff
                except TimeoutError as error:
                    raise TimeoutError(
                        f"timed out waiting for handoff for stage {stage_id}"
                    ) from error
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for handoff for stage {stage_id}")
            time.sleep(0.25)

    def recovery_instruction(self, session_id: str, stage_id: str) -> str:
        """Describe the durable files an agent must inspect before recovering work."""

        stage_path = self._stage_path(session_id, stage_id)
        state = self.load_session(session_id)
        revision = state["stages"][stage_id]["revision"]
        return (
            "Continue upload-search-materials without creating a new session. "
            f"Use session_id='{session_id}', stage_id='{stage_id}', revision={revision}, "
            f"runs_root='{self._runs_root}'. "
            f"Read {stage_path / 'handoff.json'} and verify its input_sha256 against "
            f"the exact bytes of {stage_path / 'input.json'} before continuing. "
            "Do not select another session by recency."
        )

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
            with path.open(encoding="utf-8") as stream:
                document = json.load(stream)
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
        with self._file_lock(session_path / ".session.lock", deadline=deadline):
            yield session_path

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
        if moment.tzinfo is None:
            return moment.isoformat(timespec="seconds")
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
        payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _append_event(session_path: Path, event: str, **details: Any) -> None:
        payload = {"event": event, "created_at": SessionStore._iso_timestamp(datetime.now(timezone.utc))}
        payload.update(details)
        with (session_path / "events.ndjson").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
