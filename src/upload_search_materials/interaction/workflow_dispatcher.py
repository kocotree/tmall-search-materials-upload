"""Durable workbench-owned dispatch for normal workflow handoffs.

The managed Windows workbench is the long-lived owner of deterministic
workflow processing.  Codex may still inspect diagnostics after an abnormal
failure, but normal stage submissions and bounded copy requests are claimed
and processed here without a chat-side listener or terminal command.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from ..agent_diagnostics import write_exception_diagnostic
from ..agent_handoff import list_agent_requests
from ..collection_worker import collection_status, launch_collection_worker
from ..copy_draft_workflow import process_copy_draft_request
from ..final_material_handoff import process_final_material_handoff
from ..product_selection_handoff import process_product_selection_handoff
from ..runtime_config import RuntimeConfig
from .session import InteractionConflict, SessionStore


DISPATCH_SCHEMA_VERSION = 1
WORKBENCH_CLAIMANT_ID = "workbench-dispatcher"
SUPPORTED_HANDOFF_ACTIONS = frozenset(
    {
        "process-setup",
        "process-product-selection",
        "process-final-material-handoff",
        "process-publish-authorization",
    }
)


def _selector_failure_is_recoverable(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    reasons = result.get("blocking_reasons", [])
    if not isinstance(reasons, list):
        return False
    codes = {
        str(reason).split(":", 1)[0].strip()
        for reason in reasons
    }
    return any(
        code.startswith("SELECTOR_")
        or code == "PAGINATION_ORIGIN_UNVERIFIED"
        for code in codes
    )


def _team_index_failure_is_recoverable(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    reasons = result.get("blocking_reasons", [])
    if not isinstance(reasons, list):
        return False
    codes = {
        str(reason).split(":", 1)[0].strip()
        for reason in reasons
    }
    return bool(
        codes
        & {
            "TEAM_INDEX_NO_VALID_LOCAL_CACHE",
            "TEAM_INDEX_AUTHENTICATION_REQUIRED",
            "TEAM_INDEX_NO_SOURCES",
        }
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reason_code(error: BaseException) -> str:
    explicit = str(getattr(error, "reason_code", "")).strip()
    if explicit:
        return explicit
    candidate = str(error).split(":", 1)[0].strip()
    if (
        candidate
        and candidate.upper() == candidate
        and candidate.replace("_", "").isalnum()
    ):
        return candidate
    return "WORKBENCH_WORKFLOW_PROCESSOR_FAILED"


@dataclass(frozen=True)
class WorkflowTask:
    session_id: str
    stage_id: str
    action: str
    revision: int
    input_sha256: str
    request_id: str = ""
    generation: str = "ready"

    @property
    def logical_key(self) -> str:
        return "|".join(
            (
                self.session_id,
                self.stage_id,
                self.action,
                str(self.revision),
                self.input_sha256,
                self.request_id,
            )
        )

    @property
    def attempt_key(self) -> str:
        return f"{self.logical_key}|g{self.generation}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stage_id": self.stage_id,
            "action": self.action,
            "revision": self.revision,
            "input_sha256": self.input_sha256,
            "request_id": self.request_id or None,
            "generation": self.generation,
        }


class WorkflowProcessorError(RuntimeError):
    """A fixed processor returned a non-success terminal code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"{reason_code}:{message}")
        self.reason_code = reason_code
        self.diagnostic_already_written = True


class WorkflowProcessor:
    """Invoke only the fixed processors declared by the workbench contract."""

    def __init__(
        self,
        store: SessionStore,
        runtime: RuntimeConfig,
        *,
        local_resource_identity: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.local_resource_identity = local_resource_identity

    def __call__(self, task: WorkflowTask) -> dict[str, Any]:
        if task.action == "process-setup":
            return launch_collection_worker(
                runs_root=self.store.runs_root,
                session_id=task.session_id,
                runtime=self.runtime,
                selectors_path=self.runtime.selectors_file,
                cdp_url=self.runtime.cdp_url,
                claimant_id=WORKBENCH_CLAIMANT_ID,
                local_resource_identity=self.local_resource_identity,
            )
        if task.action == "process-product-selection":
            return process_product_selection_handoff(
                self.store,
                task.session_id,
                folder_index_root=self.runtime.folder_index_root,
                claimant_id=WORKBENCH_CLAIMANT_ID,
                config_path=self.runtime.config_path,
                runtime=self.runtime,
            )
        if task.action == "process-final-material-handoff":
            return process_final_material_handoff(
                self.store,
                task.session_id,
                claimant_id=WORKBENCH_CLAIMANT_ID,
            )
        if task.action == "process-copy-request":
            return process_copy_draft_request(
                self.store,
                task.session_id,
                task.request_id,
                runtime=self.runtime,
                actor=WORKBENCH_CLAIMANT_ID,
            )
        if task.action == "process-publish-authorization":
            # The publish implementation still lives beside the legacy CLI
            # argument adapter.  Importing it lazily avoids a module cycle while
            # keeping execution in this managed service process and identity.
            from argparse import Namespace
            from ..cli import _process_publish_authorization

            code = _process_publish_authorization(
                Namespace(
                    runs_root=str(self.store.runs_root),
                    session=task.session_id,
                    config=(
                        str(self.runtime.config_path)
                        if self.runtime.config_path is not None
                        else None
                    ),
                    selectors=(
                        str(self.runtime.selectors_file)
                        if self.runtime.selectors_file is not None
                        else None
                    ),
                    cdp_url=self.runtime.cdp_url,
                    claimant_id=WORKBENCH_CLAIMANT_ID,
                ),
                page=None,
                page_factory=None,
            )
            if code:
                raise WorkflowProcessorError(
                    "PUBLISH_AUTHORIZATION_PROCESSING_FAILED",
                    f"processor exited with code {code}",
                )
            return {"status": "completed"}
        raise InteractionConflict("WORKBENCH_WORKFLOW_ACTION_UNSUPPORTED")


class WorkflowDispatcher:
    """Single-session durable queue owned by the managed workbench."""

    def __init__(
        self,
        store: SessionStore,
        session_id: str,
        processor: Callable[[WorkflowTask], dict[str, Any]],
        *,
        poll_seconds: float = 2.0,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.processor = processor
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._queue: queue.Queue[WorkflowTask | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._active_logical_keys: set[str] = set()
        self._handled_attempt_keys: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def status_path(self) -> Path:
        return self.store._session_path(self.session_id) / "workbench-dispatch.json"

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.store.load_session(self.session_id)
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"tmall-workflow-{self.session_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def notify(self) -> int:
        """Discover durable pending work and signal the in-process queue."""

        enqueued = 0
        for task in self._discover_tasks():
            if self._enqueue(task):
                enqueued += 1
        return enqueued

    def replace_runtime_and_retry(self, runtime: RuntimeConfig) -> int:
        """Refresh machine-local paths and retry the current recoverable handoff once."""

        if isinstance(self.processor, WorkflowProcessor):
            self.processor.runtime = runtime
        tasks = self._discover_tasks()
        with self._state_lock:
            for task in tasks:
                self._handled_attempt_keys.discard(task.attempt_key)
        return sum(1 for task in tasks if self._enqueue(task))

    def public_status(self) -> dict[str, Any]:
        current: dict[str, Any] = {}
        if self.status_path.is_file():
            try:
                current = SessionStore._read_json(
                    self.status_path, "workbench-dispatch"
                )
            except (InteractionConflict, OSError, ValueError):
                current = {}
        return {
            "online": self.is_alive(),
            "status": str(current.get("status") or "idle"),
            "stage_id": str(current.get("stage_id") or ""),
            "action": str(current.get("action") or ""),
            "reason_code": str(current.get("reason_code") or ""),
            "updated_at": current.get("updated_at"),
        }

    def _discover_tasks(self) -> list[WorkflowTask]:
        tasks: list[WorkflowTask] = []
        state = self.store.load_session(self.session_id)
        stage_id = str(state.get("current_stage", ""))
        stage_state = state.get("stages", {}).get(stage_id, {})
        status = str(stage_state.get("status", ""))
        claim = self.store.processing_claim(self.session_id, stage_id)
        handoff_recoverable = status == "ready_for_agent"
        handoff_generation = f"ready-r{stage_state.get('revision', 0)}"
        if status == "needs_user_input" and stage_id == "setup":
            result = self.store.read_optional_stage_document(
                self.session_id, "setup", "result"
            )
            handoff_recoverable = _selector_failure_is_recoverable(result)
        if status == "blocked" and stage_id == "completeness":
            result = self.store.read_optional_stage_document(
                self.session_id, "completeness", "result"
            )
            handoff_recoverable = _team_index_failure_is_recoverable(result)
            handoff_generation = f"blocked-r{stage_state.get('revision', 0)}"
        if (
            status == "processing"
            and isinstance(claim, dict)
            and claim.get("expired") is True
        ):
            handoff_recoverable = True
            handoff_generation = "expired-{}-{}".format(
                claim.get("claim_id") or claim.get("attempt_id") or "claim",
                claim.get("recovery_count", 0),
            )
        elif status == "processing" and stage_id == "setup":
            setup_status = collection_status(
                self.store.runs_root, self.session_id
            )
            if setup_status.get("status") == "recoverable":
                worker = setup_status.get("worker") or {}
                handoff_recoverable = True
                handoff_generation = "worker-{}-{}-{}".format(
                    setup_status.get("attempt_id") or "attempt",
                    worker.get("pid") or "pid",
                    worker.get("started_at") or worker.get("updated_at") or "time",
                )
        if handoff_recoverable:
            identity = self.store.durable_handoff_identity(
                self.session_id, stage_id
            )
            if (
                isinstance(identity, dict)
                and identity.get("allowed_action") in SUPPORTED_HANDOFF_ACTIONS
            ):
                tasks.append(
                    WorkflowTask(
                        session_id=self.session_id,
                        stage_id=stage_id,
                        action=str(identity["allowed_action"]),
                        revision=int(identity["revision"]),
                        input_sha256=str(identity["input_sha256"]),
                        generation=handoff_generation,
                    )
                )

        for request in list_agent_requests(
            self.store,
            self.session_id,
            statuses={"pending_agent", "processing"},
        ):
            if (
                request.get("kind") != "copy_draft"
                or request.get("request_context", {}).get("processor")
                != "process-copy-request"
            ):
                continue
            transitions = request.get("transitions", [])
            tasks.append(
                WorkflowTask(
                    session_id=self.session_id,
                    stage_id="slots_copy",
                    action="process-copy-request",
                    revision=int(request.get("context_revision", 0)),
                    input_sha256=str(
                        request.get("request_context", {}).get(
                            "context_fingerprint", ""
                        )
                    ),
                    request_id=str(request.get("request_id", "")),
                    generation="request-{}".format(
                        len(transitions) if isinstance(transitions, list) else 1
                    ),
                )
            )
        return tasks

    def _enqueue(self, task: WorkflowTask) -> bool:
        with self._state_lock:
            if (
                task.logical_key in self._active_logical_keys
                or task.attempt_key in self._handled_attempt_keys
            ):
                return False
            self._active_logical_keys.add(task.logical_key)
        self._safe_write_status(task, "queued")
        self._queue.put(task)
        return True

    def _run(self) -> None:
        try:
            self.notify()
        except (InteractionConflict, OSError, TypeError, ValueError):
            # Keep the dispatcher alive so a later periodic scan can recover
            # after a partially-written or briefly unavailable session file.
            pass
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=self.poll_seconds)
            except queue.Empty:
                try:
                    self.notify()
                except (InteractionConflict, OSError, TypeError, ValueError):
                    pass
                continue
            if task is None:
                break
            self._execute(task)
            self._queue.task_done()

    def _execute(self, task: WorkflowTask) -> None:
        self._safe_write_status(task, "running", started_at=_now())
        try:
            result = self.processor(task)
        except Exception as error:
            reason_code = _reason_code(error)
            if not getattr(error, "diagnostic_already_written", False):
                try:
                    write_exception_diagnostic(
                        self.store,
                        task.session_id,
                        task.stage_id,
                        processor=task.action,
                        phase="workbench_dispatch",
                        error=error,
                        handoff_kind=(
                            "copy_draft"
                            if task.action == "process-copy-request"
                            else ""
                        ),
                    )
                except (InteractionConflict, OSError, ValueError):
                    pass
            self._safe_write_status(
                task,
                "failed",
                reason_code=reason_code,
                completed_at=_now(),
            )
        else:
            self._safe_write_status(
                task,
                "completed",
                result_status=str(result.get("status", "completed")),
                completed_at=_now(),
            )
        finally:
            with self._state_lock:
                self._active_logical_keys.discard(task.logical_key)
                self._handled_attempt_keys.add(task.attempt_key)

    def _write_status(
        self,
        task: WorkflowTask,
        status: str,
        **details: Any,
    ) -> None:
        now = _now()
        self.store._write_json_atomic(
            self.status_path,
            {
                "schema_version": DISPATCH_SCHEMA_VERSION,
                **task.as_dict(),
                "status": status,
                "reason_code": str(details.pop("reason_code", "")),
                "updated_at": now,
                **details,
            },
        )

    def _safe_write_status(
        self,
        task: WorkflowTask,
        status: str,
        **details: Any,
    ) -> None:
        try:
            self._write_status(task, status, **details)
        except (InteractionConflict, OSError, TypeError, ValueError):
            # Dispatch status is observational. A transient sidecar write must
            # never prevent the already-persisted business handoff from running.
            pass


__all__ = [
    "DISPATCH_SCHEMA_VERSION",
    "WORKBENCH_CLAIMANT_ID",
    "WorkflowDispatcher",
    "WorkflowProcessor",
    "WorkflowProcessorError",
    "WorkflowTask",
]
