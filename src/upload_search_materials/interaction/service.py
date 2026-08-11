"""Managed lifecycle for the local interaction UI."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, Request
import webbrowser

from .session import InteractionPathError, SessionStore
from ..persistence import atomic_write_json, read_json
from ..runtime_identity import (
    current_runtime_identity,
    same_local_resource_identity,
)


SERVICE_SCHEMA_VERSION = 1
DEFAULT_PORT_START = 8765
DEFAULT_PORT_END = 8795
SERVICE_STATE_FILE = ".ui-service.json"
_LOOPBACK_OPENER = build_opener(ProxyHandler({}))


class ManagedServiceError(RuntimeError):
    """A stable managed-service failure."""

    def __init__(self, reason_code: str, message: str, recovery_command: str = ""):
        super().__init__(message)
        self.reason_code = reason_code
        self.recovery_command = recovery_command


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(store: SessionStore, session_id: str) -> Path:
    return store._session_path(session_id) / SERVICE_STATE_FILE


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    atomic_write_json(path, document, sort_keys=True)


def _read_state(store: SessionStore, session_id: str) -> dict[str, Any] | None:
    path = _state_path(store, session_id)
    if not path.is_file():
        return None
    try:
        document = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _url_json(url: str, timeout: float = 1.0) -> dict[str, Any] | None:
    try:
        with _LOOPBACK_OPENER.open(url, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def dispatch_collection_start(
    runs_root: Path,
    session_id: str,
    *,
    claimant_id: str,
) -> dict[str, Any] | None:
    """Ask the owned desktop service to create its collection child."""

    store = SessionStore(runs_root)
    state = _read_state(store, session_id)
    if state is None:
        return None
    if not _healthy_identity(state):
        if state.get("status") in {"starting", "healthy"}:
            raise ManagedServiceError(
                "LOCAL_RESOURCE_IDENTITY_MISMATCH",
                "managed desktop service identity cannot be verified",
            )
        return None
    payload = json.dumps(
        {"claimant_id": claimant_id},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        (
            f"http://127.0.0.1:{state['port']}/api/internal/"
            f"sessions/{session_id}/collection/start"
        ),
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Ownership-Token": str(state["ownership_token"]),
        },
    )
    try:
        with _LOOPBACK_OPENER.open(request, timeout=15) as response:
            value = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except (ValueError, OSError):
            detail = {}
        raise ManagedServiceError(
            str(detail.get("reason_code", "DESKTOP_COLLECTION_START_FAILED")),
            str(detail.get("message", "desktop collection start failed")),
        ) from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise ManagedServiceError(
            "DESKTOP_COLLECTION_START_FAILED",
            str(error),
        ) from error
    return value if isinstance(value, dict) else None


def _healthy_identity(state: dict[str, Any]) -> bool:
    health = _url_json(f"http://127.0.0.1:{state.get('port')}/api/health")
    return bool(
        health
        and health.get("healthy") is True
        and int(health.get("pid", -1)) == int(state.get("pid", -2))
        and secrets.compare_digest(
            str(health.get("ownership_token", "")),
            str(state.get("ownership_token", "")),
        )
        and same_local_resource_identity(
            state.get("runtime_identity", {}),
            health.get("runtime_identity", {}),
        )
    )


def _claim_started_identity(state: dict[str, Any]) -> bool:
    """Bind state to the actual server PID after a Python launcher shim."""

    health = _url_json(f"http://127.0.0.1:{state.get('port')}/api/health")
    if not (
        health
        and health.get("healthy") is True
        and secrets.compare_digest(
            str(health.get("ownership_token", "")),
            str(state.get("ownership_token", "")),
        )
    ):
        return False
    pid = health.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    state["launcher_pid"] = state.get("pid")
    state["pid"] = pid
    runtime_identity = health.get("runtime_identity")
    if not isinstance(runtime_identity, dict):
        return False
    launcher_identity = state.get("launcher_runtime_identity", {})
    if not same_local_resource_identity(
        launcher_identity, runtime_identity
    ):
        return False
    state["runtime_identity"] = runtime_identity
    return True


def _session_is_readable(state: dict[str, Any], session_id: str) -> bool:
    health = _url_json(
        f"http://127.0.0.1:{state.get('port')}/api/health/sessions/{session_id}"
    )
    return bool(
        health
        and health.get("healthy") is True
        and health.get("session_id") == session_id
        and health.get("readable") is True
    )


def _port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _select_port(start: int, end: int) -> int:
    if not (1 <= start <= end <= 65535):
        raise ManagedServiceError("PORT_RANGE_INVALID", "invalid managed port range")
    for port in range(start, end + 1):
        if _port_available(port):
            return port
    raise ManagedServiceError(
        "UI_START_FAILED",
        f"no free loopback port in {start}-{end}",
        "choose another bounded --port-start/--port-end range",
    )


def _result(state: dict[str, Any]) -> dict[str, Any]:
    public = dict(state)
    public.pop("ownership_token", None)
    public["service_state_path"] = str(
        Path(state["runs_root"]) / state["session_id"] / SERVICE_STATE_FILE
    )
    return public


def status_service(runs_root: Path, session_id: str) -> dict[str, Any]:
    store = SessionStore(runs_root)
    store.load_session(session_id)
    state = _read_state(store, session_id)
    if state is None:
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "session_id": session_id,
            "runs_root": str(store._runs_root),
            "status": "not_started",
            "healthy": False,
        }
    healthy = _healthy_identity(state)
    readable = healthy and _session_is_readable(state, session_id)
    state = {
        **state,
        "status": "healthy" if healthy and readable else "unreachable",
        "healthy": healthy,
        "session_readable": readable,
        "checked_at": _now(),
    }
    _write_json_atomic(_state_path(store, session_id), state)
    return _result(state)


def start_service(
    runs_root: Path,
    *,
    session_id: str | None = None,
    port_start: int = DEFAULT_PORT_START,
    port_end: int = DEFAULT_PORT_END,
    startup_timeout: float = 15.0,
    open_system_browser: bool = False,
    config: str | None = None,
) -> dict[str, Any]:
    store = SessionStore(runs_root)
    launcher_runtime_identity = current_runtime_identity()
    if session_id:
        store.load_session(session_id)
    else:
        session_id = store.create_session().session_id

    existing = _read_state(store, session_id)
    if existing and _healthy_identity(existing) and _session_is_readable(
        existing, session_id
    ):
        if same_local_resource_identity(
            launcher_runtime_identity,
            existing.get("runtime_identity", {}),
        ):
            existing["status"] = "healthy"
            existing["healthy"] = True
            existing["session_readable"] = True
            existing["reused"] = True
            return _result(existing)
        stop_service(runs_root, session_id)

    port = _select_port(port_start, port_end)
    token = secrets.token_urlsafe(32)
    session_path = store._session_path(session_id)
    logs_path = session_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_path / "ui-service.stdout.log"
    stderr_path = logs_path / "ui-service.stderr.log"
    query = urlencode({"session_id": session_id})
    url = f"http://127.0.0.1:{port}/?{query}"
    module_root = Path(__file__).resolve().parents[3]
    prepared_python = module_root / ".venv" / "Scripts" / "python.exe"
    runtime_python = (
        prepared_python if prepared_python.is_file() else Path(sys.executable)
    )
    command = [
        str(runtime_python),
        "-m",
        "upload_search_materials.cli",
        "interact",
        "--runs-root",
        str(store._runs_root),
        "--session",
        session_id,
        "--port",
        str(port),
        f"--ownership-token={token}",
    ]
    if config:
        command.extend(["--config", config])
    creationflags = 0
    child_environment = os.environ.copy()
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        child_environment["TMALL_DESKTOP_LOGIN_SESSION_ID"] = str(
            launcher_runtime_identity["login_session_id"]
        )
    with stdout_path.open("ab", buffering=0) as stdout, stderr_path.open(
        "ab", buffering=0
    ) as stderr:
        process = subprocess.Popen(
            command,
            cwd=module_root,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
            env=child_environment,
            start_new_session=os.name != "nt",
        )

    state = {
        "schema_version": SERVICE_SCHEMA_VERSION,
        "session_id": session_id,
        "runs_root": str(store._runs_root),
        "pid": process.pid,
        "launcher_runtime_identity": launcher_runtime_identity,
        "ownership_token": token,
        "port": port,
        "url": url,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "started_at": _now(),
        "status": "starting",
        "healthy": False,
        "session_readable": False,
        "browser_channel": "not_attempted",
        "browser_opened": False,
        "reused": False,
    }
    _write_json_atomic(_state_path(store, session_id), state)
    deadline = time.monotonic() + max(0.5, startup_timeout)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            state["status"] = "failed"
            state["failure_reason_code"] = "UI_START_FAILED"
            state["exit_code"] = process.returncode
            _write_json_atomic(_state_path(store, session_id), state)
            raise ManagedServiceError(
                "UI_START_FAILED",
                f"interaction service exited with code {process.returncode}; see {stderr_path}",
                f"tmall-materials ui-restart --runs-root \"{store._runs_root}\" --session {session_id}",
            )
        if _claim_started_identity(state) and _session_is_readable(
            state, session_id
        ):
            break
        time.sleep(0.1)
    else:
        health = _url_json(f"http://127.0.0.1:{port}/api/health")
        if (
            health
            and secrets.compare_digest(
                str(health.get("ownership_token", "")), token
            )
            and isinstance(health.get("pid"), int)
        ):
            try:
                os.kill(int(health["pid"]), signal.SIGTERM)
            except ProcessLookupError:
                pass
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        state["status"] = "failed"
        state["failure_reason_code"] = "UI_START_FAILED"
        state["stopped_at"] = _now()
        _write_json_atomic(_state_path(store, session_id), state)
        raise ManagedServiceError(
            "UI_START_FAILED",
            f"interaction service did not become ready within {startup_timeout}s; see {stderr_path}",
            f"tmall-materials ui-restart --runs-root \"{store._runs_root}\" --session {session_id}",
        )

    state["status"] = "healthy"
    state["healthy"] = True
    state["session_readable"] = True
    state["ready_at"] = _now()
    if open_system_browser:
        opened = bool(webbrowser.open(url, new=2))
        state["browser_channel"] = "system_default"
        state["browser_opened"] = opened
        if not opened:
            state["browser_failure_reason_code"] = "BROWSER_OPEN_FAILED"
    else:
        state["browser_channel"] = "codex_in_app_preferred"
    _write_json_atomic(_state_path(store, session_id), state)
    return _result(state)


def stop_service(runs_root: Path, session_id: str) -> dict[str, Any]:
    store = SessionStore(runs_root)
    store.load_session(session_id)
    state = _read_state(store, session_id)
    if state is None:
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "session_id": session_id,
            "status": "not_started",
            "stopped": False,
        }
    if not _healthy_identity(state):
        raise ManagedServiceError(
            "SERVICE_OWNERSHIP_MISMATCH",
            "recorded PID no longer matches the ownership token and listening endpoint",
            "inspect the recorded logs and start a new managed service",
        )
    try:
        os.kill(int(state["pid"]), signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _healthy_identity(state):
        time.sleep(0.1)
    if _healthy_identity(state):
        raise ManagedServiceError(
            "UI_STOP_FAILED",
            "owned service did not stop within the timeout",
            "retry ui-stop after inspecting the service logs",
        )
    state["status"] = "stopped"
    state["healthy"] = False
    state["session_readable"] = False
    state["stopped_at"] = _now()
    _write_json_atomic(_state_path(store, session_id), state)
    return _result({**state, "stopped": True})


def restart_service(runs_root: Path, session_id: str, **kwargs: Any) -> dict[str, Any]:
    existing = _read_state(SessionStore(runs_root), session_id)
    if existing and _healthy_identity(existing):
        stop_service(runs_root, session_id)
    return start_service(runs_root, session_id=session_id, **kwargs)
