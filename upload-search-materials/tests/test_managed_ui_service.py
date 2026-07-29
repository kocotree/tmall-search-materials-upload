from __future__ import annotations

import json
from pathlib import Path
import socket

import pytest

from upload_search_materials.interaction.service import (
    ManagedServiceError,
    SERVICE_STATE_FILE,
    start_service,
    restart_service,
    status_service,
    stop_service,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_managed_service_starts_reuses_reports_and_stops(tmp_path):
    port = _free_port()
    started = start_service(
        tmp_path,
        port_start=port,
        port_end=port,
        startup_timeout=10,
    )
    try:
        assert started["healthy"] is True
        assert started["session_readable"] is True
        assert started["url"].endswith(started["session_id"])
        assert started["browser_channel"] == "codex_in_app_preferred"
        assert "ownership_token" not in started

        repeated = start_service(
            tmp_path,
            session_id=started["session_id"],
            port_start=port,
            port_end=port,
        )
        assert repeated["reused"] is True
        assert repeated["pid"] == started["pid"]
        assert status_service(tmp_path, started["session_id"])["status"] == "healthy"
    finally:
        stopped = stop_service(tmp_path, started["session_id"])
    assert stopped["stopped"] is True
    assert Path(stopped["stdout_log"]).is_file()
    assert Path(stopped["stderr_log"]).is_file()


def test_unknown_occupied_port_is_skipped(tmp_path):
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    first = int(occupied.getsockname()[1])
    second = _free_port()
    if second < first:
        first, second = second, first
        occupied.close()
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", first))
        occupied.listen()
    started = start_service(
        tmp_path,
        port_start=first,
        port_end=second,
        startup_timeout=10,
    )
    try:
        assert started["port"] != first
    finally:
        stop_service(tmp_path, started["session_id"])
        occupied.close()


def test_stop_refuses_reused_or_foreign_pid_identity(tmp_path):
    port = _free_port()
    started = start_service(
        tmp_path, port_start=port, port_end=port, startup_timeout=10
    )
    state_path = (
        tmp_path / started["session_id"] / SERVICE_STATE_FILE
    )
    original = json.loads(state_path.read_text(encoding="utf-8"))
    corrupted = {**original, "ownership_token": "not-the-server-token"}
    state_path.write_text(json.dumps(corrupted), encoding="utf-8")
    try:
        with pytest.raises(ManagedServiceError) as captured:
            stop_service(tmp_path, started["session_id"])
        assert captured.value.reason_code == "SERVICE_OWNERSHIP_MISMATCH"
    finally:
        state_path.write_text(json.dumps(original), encoding="utf-8")
        stop_service(tmp_path, started["session_id"])


def test_browser_failure_is_separate_from_service_health(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "upload_search_materials.interaction.service.webbrowser.open",
        lambda *_args, **_kwargs: False,
    )
    port = _free_port()
    started = start_service(
        tmp_path,
        port_start=port,
        port_end=port,
        startup_timeout=10,
        open_system_browser=True,
    )
    try:
        assert started["healthy"] is True
        assert started["browser_opened"] is False
        assert started["browser_failure_reason_code"] == "BROWSER_OPEN_FAILED"
    finally:
        stop_service(tmp_path, started["session_id"])


def test_restart_reuses_exact_session_with_new_owned_service(tmp_path):
    first_port = _free_port()
    started = start_service(
        tmp_path,
        port_start=first_port,
        port_end=first_port,
        startup_timeout=10,
    )
    second_port = _free_port()
    restarted = restart_service(
        tmp_path,
        started["session_id"],
        port_start=second_port,
        port_end=second_port,
        startup_timeout=10,
    )
    try:
        assert restarted["session_id"] == started["session_id"]
        assert restarted["pid"] != started["pid"]
        assert restarted["healthy"] is True
    finally:
        stop_service(tmp_path, restarted["session_id"])


def test_child_exit_before_readiness_returns_stable_failure(
    tmp_path, monkeypatch
):
    class ExitedProcess:
        pid = 12345
        returncode = 17

        def poll(self):
            return 17

    monkeypatch.setattr(
        "upload_search_materials.interaction.service.subprocess.Popen",
        lambda *_args, **_kwargs: ExitedProcess(),
    )
    port = _free_port()
    with pytest.raises(ManagedServiceError) as captured:
        start_service(
            tmp_path,
            port_start=port,
            port_end=port,
            startup_timeout=1,
        )
    assert captured.value.reason_code == "UI_START_FAILED"
    state_files = list(tmp_path.glob(f"*/{SERVICE_STATE_FILE}"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text(encoding="utf-8"))
    assert state["exit_code"] == 17


def test_ownership_token_starting_with_dash_is_passed_as_one_argument(
    tmp_path, monkeypatch
):
    commands = []

    class ExitedProcess:
        pid = 12346
        returncode = 17

        def poll(self):
            return 17

    def capture(command, **_kwargs):
        commands.append(command)
        return ExitedProcess()

    monkeypatch.setattr(
        "upload_search_materials.interaction.service.secrets.token_urlsafe",
        lambda _size: "-leading-token",
    )
    monkeypatch.setattr(
        "upload_search_materials.interaction.service.subprocess.Popen",
        capture,
    )

    port = _free_port()
    with pytest.raises(ManagedServiceError):
        start_service(
            tmp_path,
            port_start=port,
            port_end=port,
            startup_timeout=1,
        )

    assert "--ownership-token=-leading-token" in commands[0]
