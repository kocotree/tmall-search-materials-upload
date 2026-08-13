from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
from urllib.request import ProxyHandler

import pytest

import upload_search_materials.interaction.service as service_module
from upload_search_materials.interaction.service import (
    ManagedServiceError,
    SERVICE_STATE_FILE,
    start_service,
    restart_service,
    status_service,
    stop_service,
)


def test_managed_ui_health_check_bypasses_system_proxy_for_loopback():
    assert not any(
        isinstance(handler, ProxyHandler)
        for handler in service_module._LOOPBACK_OPENER.handlers
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
        assert started["runtime_identity"]["sid"]
        assert isinstance(
            started["runtime_identity"]["login_session_id"], int
        )
        assert isinstance(
            started["runtime_identity"]["interactive_desktop"], bool
        )
        assert isinstance(
            started["runtime_identity"]["remote_drive_letters"], list
        )

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


@pytest.mark.skipif(
    service_module.os.name == "nt",
    reason="POSIX launcher detachment regression",
)
def test_managed_service_survives_launcher_process_exit(tmp_path):
    port = _free_port()
    project = Path(__file__).parents[1]
    script = (
        "import json, sys; "
        "from pathlib import Path; "
        "from upload_search_materials.interaction.service import start_service; "
        "print(json.dumps(start_service(Path(sys.argv[1]), "
        "port_start=int(sys.argv[2]), port_end=int(sys.argv[2]), "
        "startup_timeout=10)))"
    )
    launched = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(port)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    started = json.loads(launched.stdout.strip().splitlines()[-1])
    try:
        status = status_service(tmp_path, started["session_id"])
        assert status["status"] == "healthy"
        assert status["pid"] == started["pid"]
    finally:
        stop_service(tmp_path, started["session_id"])


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
    popen_kwargs = []

    class ExitedProcess:
        pid = 12346
        returncode = 17

        def poll(self):
            return 17

    def capture(command, **kwargs):
        commands.append(command)
        popen_kwargs.append(kwargs)
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
    assert popen_kwargs[0]["start_new_session"] is (service_module.os.name != "nt")
    if service_module.os.name != "nt":
        inherited = popen_kwargs[0]["env"]["TMALL_DESKTOP_LOGIN_SESSION_ID"]
        assert inherited == str(
            service_module.current_runtime_identity()["login_session_id"]
        )


def test_macos_managed_desktop_submits_launchd_job(tmp_path, monkeypatch):
    port = _free_port()
    commands = []
    states = []
    project_root = tmp_path / "plugin-version"
    workspace_root = tmp_path / "runtime-workspace"

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(service_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        service_module.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or Completed(),
    )
    monkeypatch.setattr(
        service_module,
        "_claim_started_identity",
        lambda state: states.append(dict(state)) or state.update(
            {
                "pid": 4567,
                "runtime_identity": state["launcher_runtime_identity"],
            }
        )
        is None,
    )
    monkeypatch.setattr(service_module, "_session_is_readable", lambda *_args: True)

    started = start_service(
        tmp_path,
        port_start=port,
        port_end=port,
        startup_timeout=1,
        managed_desktop=True,
        project_root=project_root,
        workspace_root=workspace_root,
    )

    assert started["healthy"] is True
    assert started["pid"] == 4567
    assert started["service_launch_channel"] == "macos_launchd"
    assert started["service_launch_label"].startswith(
        f"com.kocotree.tmall-materials.ui.{started['session_id']}."
    )
    command = commands[0]
    assert command[:3] == ["/bin/launchctl", "submit", "-l"]
    assert "/usr/bin/env" in command
    assert any(value.startswith("TMALL_DESKTOP_LOGIN_SESSION_ID=") for value in command)
    assert f"TMALL_PLUGIN_ROOT={project_root.resolve()}" in command
    assert f"TMALL_WORKSPACE_ROOT={workspace_root.resolve()}" in command
    assert not any(value.startswith("GITHUB_TOKEN=") for value in command)
    assert started["project_root"] == str(project_root.resolve())
    assert started["workspace_root"] == str(workspace_root.resolve())
    assert states
