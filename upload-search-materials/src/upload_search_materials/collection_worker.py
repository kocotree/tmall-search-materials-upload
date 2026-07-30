"""Owned background worker lifecycle for setup collection."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from typing import Any

from .browser.config import SelectorConfigError, load_selector_profile
from .collection_runtime import (
    CollectionBinding,
    CollectionRuntimeError,
    attempt_path,
    create_attempt_document,
    create_worker_document,
    read_json_object,
    resolve_collection_status,
    update_worker_progress,
    worker_liveness,
    write_json_atomic,
)
from .interaction.session import InteractionConflict, SessionStore
from .runtime_config import RuntimeConfig, load_runtime_config
from .runtime_preflight import environment_fingerprint
from .runtime_identity import (
    LocalResourceIdentityMismatch,
    require_local_resource_identity,
    runtime_identity_for_pid,
    same_local_resource_identity,
)
from .setup_collection import _claim_setup, process_setup_collection
from .time_utils import iso_timestamp


WORKER_MANIFEST_NAME = "worker.private.json"
PUBLIC_WORKER_NAME = "worker.json"
ATTEMPT_DOCUMENT_NAME = "attempt.json"
CURRENT_ATTEMPT_NAME = "current-attempt.json"


def _windows_process_creation_identity(pid: int) -> str | None:
    if os.name != "nt":
        return None

    class FileTime(ctypes.Structure):
        _fields_ = [
            ("low", ctypes.c_ulong),
            ("high", ctypes.c_ulong),
        ]

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, int(pid)
    )
    if not handle:
        if ctypes.get_last_error() == 5:
            raise PermissionError(
                f"process identity access denied for pid {pid}"
            )
        return None
    try:
        creation = FileTime()
        exit_time = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (int(creation.high) << 32) | int(creation.low)
        return f"windows:{pid}:{ticks}"
    finally:
        kernel32.CloseHandle(handle)


def process_identity(pid: int) -> str | None:
    if int(pid) <= 0:
        return None
    if os.name == "nt":
        return _windows_process_creation_identity(pid)
    stat = Path(f"/proc/{int(pid)}/stat")
    try:
        fields = stat.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return f"proc:{pid}:{fields[21]}" if len(fields) > 21 else None


def _probe_process(pid: int) -> bool | None:
    identity = process_identity(pid)
    return False if identity is None else True


def _public_worker(worker: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in worker.items()
        if key
        not in {
            "ownership_token",
            "process_identity",
            "local_resource_identity",
        }
    }


def _worker_paths(
    session_path: Path, attempt_id: str
) -> tuple[Path, Path, Path, Path]:
    root = attempt_path(session_path, attempt_id)
    return (
        root / ATTEMPT_DOCUMENT_NAME,
        root / WORKER_MANIFEST_NAME,
        root / PUBLIC_WORKER_NAME,
        root / "worker.log",
    )


def _load_current_attempt(session_path: Path) -> dict[str, Any] | None:
    return read_json_object(
        session_path
        / "collected"
        / "promotion"
        / CURRENT_ATTEMPT_NAME
    )


def _save_current_attempt(
    session_path: Path, attempt: dict[str, Any]
) -> None:
    write_json_atomic(
        session_path
        / "collected"
        / "promotion"
        / CURRENT_ATTEMPT_NAME,
        attempt,
    )


def _worker_state(worker: dict[str, Any] | None) -> str:
    if not worker:
        return "absent"
    try:
        current_identity = process_identity(int(worker.get("pid", 0)))
    except PermissionError:
        return "indeterminate"
    if current_identity is None:
        return "dead"
    return worker_liveness(
        worker,
        ownership_token=str(worker.get("ownership_token", "")),
        process_identity=current_identity,
        process_probe=_probe_process,
    )


def collection_status(
    runs_root: Path,
    session_id: str,
) -> dict[str, Any]:
    store = SessionStore(runs_root)
    session_path = store._session_path(session_id)
    state = store.load_session(session_id)
    current_attempt = _load_current_attempt(session_path)
    worker = None
    if current_attempt:
        _, private_path, _, _ = _worker_paths(
            session_path, str(current_attempt["attempt_id"])
        )
        worker = read_json_object(private_path)
    result = store.read_optional_stage_document(
        session_id, "setup", "result"
    )
    history: list[dict[str, Any]] = []
    history_path = store._stage_path(session_id, "setup") / "results"
    if history_path.is_dir():
        for path in sorted(history_path.glob("*.json")):
            document = read_json_object(path)
            if document:
                history.append(document)
    if (
        result
        and current_attempt
        and result.get("attempt_id") != current_attempt.get("attempt_id")
    ):
        history.append(result)
        result = None
    return resolve_collection_status(
        stage_status=str(state["stages"]["setup"]["status"]),
        current_attempt=current_attempt,
        current_result=result,
        worker=_public_worker(worker) if worker else None,
        worker_state=_worker_state(worker),
        processing_claim=store.processing_claim(session_id, "setup"),
        historical_results=history,
    )


def launch_collection_worker(
    *,
    runs_root: Path,
    session_id: str,
    runtime: RuntimeConfig,
    selectors_path: Path | None = None,
    cdp_url: str | None = None,
    claimant_id: str = "collection-worker",
    popen: Any = subprocess.Popen,
    identity_provider: Any = process_identity,
    local_resource_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start or reuse one detached worker and return without waiting for scan."""

    from .runtime_preflight import (
        RuntimePreflightError,
        preflight_runtime_environment,
    )

    if local_resource_identity is not None:
        require_local_resource_identity(local_resource_identity)
    selected_profile = selectors_path or runtime.selectors_file
    if selected_profile is None:
        raise SelectorConfigError("SELECTOR_PROFILE_NOT_FOUND")
    try:
        preflight = preflight_runtime_environment(
            runtime,
            selectors_path=selected_profile,
            cdp_url=cdp_url or runtime.cdp_url,
        )
    except RuntimePreflightError as error:
        raise InteractionConflict(str(error)) from error
    environment = preflight["environment"]
    profile = load_selector_profile(
        selected_profile,
        purpose="high_value_collection",
        production=True,
    )
    store = SessionStore(runs_root)
    handoff, claim = _claim_setup(store, session_id, claimant_id)
    if "completed_result" in claim:
        return {
            "status": "completed",
            "reused": True,
            "result": claim["completed_result"],
        }
    attempt_id = str(claim["attempt_id"])
    session_path = store._session_path(session_id)
    attempt_file, private_file, public_file, log_file = _worker_paths(
        session_path, attempt_id
    )
    current_worker = read_json_object(private_file)
    state = _worker_state(current_worker)
    if state == "live":
        return {
            "status": "processing",
            "reused": True,
            "attempt_id": attempt_id,
            "worker": _public_worker(current_worker or {}),
        }
    if state == "indeterminate":
        raise InteractionConflict("COLLECTION_WORKER_OWNERSHIP_INDETERMINATE")

    setup_input = store.read_optional_stage_document(
        session_id, "setup", "input"
    )
    target_store = str(
        (setup_input or {}).get("values", {}).get("store", "")
    ).strip()
    binding = CollectionBinding(
        session_id=session_id,
        stage_id="setup",
        revision=int(handoff["revision"]),
        input_sha256=str(handoff["input_sha256"]),
        selector_sha256=profile.sha256,
        target_store=target_store,
    )
    attempt = create_attempt_document(
        binding,
        attempt_id=attempt_id,
        claim_id=str(claim["claim_id"]),
        claimant_id=str(claim["claimant_id"]),
    )
    if attempt_file.is_file():
        prior = read_json_object(attempt_file) or {}
        for field, expected in binding.as_dict().items():
            if prior.get(field) != expected:
                raise InteractionConflict(
                    f"COLLECTION_ATTEMPT_BINDING_MISMATCH:{field}"
                )
        attempt["created_at"] = prior.get(
            "created_at", attempt["created_at"]
        )
        attempt["recovery_count"] = int(
            claim.get("recovery_count", 0)
        )
    write_json_atomic(attempt_file, attempt)
    _save_current_attempt(session_path, attempt)

    ownership_token = secrets.token_urlsafe(32)
    argv = [
        str(environment["python"]),
        "-m",
        "upload_search_materials.cli",
        "collection-worker",
        "--runs-root",
        str(Path(runs_root).resolve()),
        "--session",
        session_id,
        "--attempt-id",
        attempt_id,
        "--ownership-token",
        ownership_token,
        "--claimant-id",
        claimant_id,
        "--selectors",
        str(profile.path),
        "--cdp-url",
        cdp_url or runtime.cdp_url,
    ]
    if runtime.config_path is not None:
        argv.extend(["--config", str(runtime.config_path)])
    if local_resource_identity is not None:
        argv.extend(
            [
                "--expected-windows-sid",
                str(local_resource_identity["sid"]),
                "--expected-login-session-id",
                str(local_resource_identity["login_session_id"]),
            ]
        )
        if local_resource_identity.get("interactive_desktop"):
            argv.append("--require-interactive-desktop")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    with log_file.open("ab", buffering=0) as log_stream:
        process = popen(
            argv,
            cwd=str(
                runtime.workspace_root / "upload-search-materials"
            ),
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            close_fds=True,
            creationflags=creationflags,
        )
    if local_resource_identity is not None:
        child_identity = runtime_identity_for_pid(int(process.pid))
        if (
            child_identity is None
            or not same_local_resource_identity(
                local_resource_identity, child_identity
            )
        ):
            try:
                process.terminate()
            except (AttributeError, OSError):
                pass
            raise LocalResourceIdentityMismatch(
                "collection_worker_identity"
            )
    identity = identity_provider(int(process.pid))
    if identity is None:
        try:
            process.terminate()
        except (AttributeError, OSError):
            pass
        raise CollectionRuntimeError("COLLECTION_WORKER_IDENTITY_UNAVAILABLE")
    worker = create_worker_document(
        attempt,
        pid=int(process.pid),
        ownership_token=ownership_token,
        process_identity=identity,
        log_path=log_file,
    )
    if local_resource_identity is not None:
        worker["local_resource_identity"] = {
            "sid": local_resource_identity["sid"],
            "login_session_id": local_resource_identity[
                "login_session_id"
            ],
            "interactive_desktop": bool(
                local_resource_identity.get("interactive_desktop")
            ),
        }
    write_json_atomic(private_file, worker)
    write_json_atomic(public_file, _public_worker(worker))
    return {
        "status": "processing",
        "reused": False,
        "attempt_id": attempt_id,
        "worker": _public_worker(worker),
    }


class WorkerReporter:
    def __init__(
        self,
        private_path: Path,
        public_path: Path,
        *,
        attempt_id: str,
        ownership_token: str,
    ) -> None:
        self.private_path = private_path
        self.public_path = public_path
        self.attempt_id = attempt_id
        self.ownership_token = ownership_token
        self.phase = "validating_profile"
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"collection-heartbeat-{attempt_id[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def update(self, phase: str, **progress: Any) -> dict[str, Any]:
        with self._lock:
            self.phase = phase
            worker = update_worker_progress(
                self.private_path,
                attempt_id=self.attempt_id,
                ownership_token=self.ownership_token,
                phase=phase,
                **progress,
            )
            write_json_atomic(self.public_path, _public_worker(worker))
            return worker

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(10):
            try:
                self.update(self.phase)
            except (CollectionRuntimeError, OSError):
                return


def run_collection_worker(
    *,
    runs_root: Path,
    session_id: str,
    attempt_id: str,
    ownership_token: str,
    claimant_id: str,
    selectors_path: Path,
    cdp_url: str,
    config_path: str | Path | None = None,
    local_resource_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if local_resource_identity is not None:
        require_local_resource_identity(local_resource_identity)
    store = SessionStore(runs_root)
    session_path = store._session_path(session_id)
    _, private_path, public_path, _ = _worker_paths(
        session_path, attempt_id
    )
    deadline = time.monotonic() + 5
    worker = read_json_object(private_path)
    while worker is None and time.monotonic() < deadline:
        time.sleep(0.05)
        worker = read_json_object(private_path)
    if worker is None:
        raise CollectionRuntimeError("COLLECTION_WORKER_MANIFEST_REQUIRED")
    if worker.get("ownership_token") != ownership_token:
        raise CollectionRuntimeError("COLLECTION_WORKER_OWNERSHIP_MISMATCH")
    current_pid = os.getpid()
    current_identity = process_identity(current_pid)
    if current_identity is None:
        raise CollectionRuntimeError("COLLECTION_WORKER_IDENTITY_UNAVAILABLE")
    if (
        int(worker.get("pid", 0)) != current_pid
        or worker.get("process_identity") != current_identity
    ):
        launcher_pid = int(worker.get("pid", 0))
        launcher_identity = str(worker.get("process_identity", ""))
        if (
            not launcher_identity
            or process_identity(launcher_pid) != launcher_identity
        ):
            raise CollectionRuntimeError("COLLECTION_WORKER_PROCESS_MISMATCH")
        worker = {
            **worker,
            "launcher_pid": launcher_pid,
            "launcher_process_identity": launcher_identity,
            "pid": current_pid,
            "process_identity": current_identity,
            "updated_at": iso_timestamp(),
        }
        write_json_atomic(private_path, worker)
        write_json_atomic(public_path, _public_worker(worker))

    reporter = WorkerReporter(
        private_path,
        public_path,
        attempt_id=attempt_id,
        ownership_token=ownership_token,
    )
    reporter.start()
    try:
        reporter.update("validating_profile")
        runtime = load_runtime_config(config_path)
        result = process_setup_collection(
            runs_root=runs_root,
            session_id=session_id,
            runtime=runtime,
            selectors_path=selectors_path,
            cdp_url=cdp_url,
            claimant_id=claimant_id,
            attempt_id=attempt_id,
            progress_callback=reporter.update,
        )
        terminal = (
            "completed"
            if result.get("status") == "completed"
            else "failed"
        )
        reporter.update(
            "completed" if terminal == "completed" else "failed",
            terminal_status=terminal,
        )
        return result
    except BaseException:
        try:
            reporter.update("failed", terminal_status="failed")
        except (CollectionRuntimeError, OSError):
            pass
        raise
    finally:
        reporter.stop()
