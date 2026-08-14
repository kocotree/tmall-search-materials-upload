import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from upload_search_materials.browser.session import CdpStatus
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig
from upload_search_materials.runtime_preflight import (
    BOOTSTRAP_ACTION,
    RuntimePreflightError,
    preflight_runtime_environment,
)


def write_prepared_runtime(project: Path, runtime_root: Path, *, windows: bool) -> Path:
    binary = (
        runtime_root / ".venv" / "Scripts" / "python.exe"
        if windows
        else runtime_root / ".venv" / "bin" / "python"
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"prepared")
    launcher = project / "scripts" / "run-plugin.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("# launcher\n", encoding="utf-8")
    lock = project / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    (runtime_root / "environment-fingerprint.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "python": str(binary.resolve()),
                "dependency_mode": "no-install-project",
                "launch_mode": "current-plugin-source",
            }
        ),
        encoding="utf-8",
    )
    return binary


def prepared(tmp_path):
    workspace = tmp_path / "workspace"
    project = workspace / "upload-search-materials"
    (project / "src" / "upload_search_materials").mkdir(parents=True)
    write_prepared_runtime(
        project, workspace / "user-data" / "runtime", windows=True
    )
    selectors = project / "config" / "selectors.local.yaml"
    selectors.parent.mkdir()
    selectors.write_text("schema_version: 1\n", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=project,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=workspace / "runs",
        user_data_root=workspace / "user-data",
    )
    return runtime, selectors


def prepared_posix(tmp_path):
    project = tmp_path / "workspace"
    (project / "src" / "upload_search_materials").mkdir(parents=True)
    write_prepared_runtime(
        project, project / "user-data" / "runtime", windows=False
    )
    selectors = project / "config" / "selectors.local.yaml"
    selectors.parent.mkdir()
    selectors.write_text("schema_version: 1\n", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=project,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=project / "runs",
        user_data_root=project / "user-data",
    )
    return runtime, selectors


def successful_runner(commands):
    def run(argv, **_kwargs):
        commands.append(argv)
        return SimpleNamespace(
            returncode=0, stdout='{"major": 3, "minor": 11}', stderr=""
        )

    return run


def connected(url):
    return CdpStatus(connected=True, endpoint=url)


def test_preflight_is_offline_and_never_invokes_uv_npm_or_powershell(tmp_path):
    runtime, selectors = prepared(tmp_path)
    commands = []

    result = preflight_runtime_environment(
        runtime,
        selectors_path=selectors,
        cdp_url="http://127.0.0.1:9222",
        runner=successful_runner(commands),
        cdp_probe=connected,
    )

    assert result["ready"] is True
    assert result["offline"] is True
    assert len(commands) == 1
    assert Path(commands[0][0]).parts[-3:] == (
        ".venv",
        "Scripts",
        "python.exe",
    )
    assert all(
        token not in " ".join(commands[0]).casefold()
        for token in ("uv ", "npm", "powershell", ".ps1")
    )


def test_preflight_accepts_posix_project_environment(tmp_path):
    runtime, selectors = prepared_posix(tmp_path)
    commands = []

    result = preflight_runtime_environment(
        runtime,
        selectors_path=selectors,
        cdp_url="http://127.0.0.1:9222",
        runner=successful_runner(commands),
        cdp_probe=connected,
    )

    assert result["ready"] is True
    assert commands[0][0].endswith("/user-data/runtime/.venv/bin/python")


@pytest.mark.parametrize(
    ("returncode", "stdout", "reason"),
    [
        (1, "", "ENVIRONMENT_IMPORTS_MISSING"),
        (0, '{"major": 3, "minor": 10}', "ENVIRONMENT_PYTHON_UNSUPPORTED"),
    ],
)
def test_preflight_reports_one_bootstrap_action_for_bad_environment(
    tmp_path, returncode, stdout, reason
):
    runtime, selectors = prepared(tmp_path)

    with pytest.raises(RuntimePreflightError) as caught:
        preflight_runtime_environment(
            runtime,
            selectors_path=selectors,
            cdp_url="http://127.0.0.1:9222",
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=returncode, stdout=stdout, stderr="missing"
            ),
            cdp_probe=connected,
        )

    assert caught.value.reason_code == reason
    assert caught.value.next_action == BOOTSTRAP_ACTION


def test_preflight_rejects_protected_project_cache(tmp_path, monkeypatch):
    runtime, selectors = prepared(tmp_path)
    monkeypatch.setattr(
        "upload_search_materials.runtime_preflight.tempfile.mkstemp",
        lambda **_kwargs: (_ for _ in ()).throw(
            PermissionError("protected cache")
        ),
    )

    with pytest.raises(
        RuntimePreflightError, match="PROJECT_CACHE_NOT_WRITABLE"
    ):
        preflight_runtime_environment(
            runtime,
            selectors_path=selectors,
            cdp_url="http://127.0.0.1:9222",
            runner=successful_runner([]),
            cdp_probe=connected,
        )


def test_preflight_checks_cdp_before_business_claim(tmp_path):
    runtime, selectors = prepared(tmp_path)

    with pytest.raises(RuntimePreflightError, match="CDP_UNAVAILABLE"):
        preflight_runtime_environment(
            runtime,
            selectors_path=selectors,
            cdp_url="http://127.0.0.1:9222",
            runner=successful_runner([]),
            cdp_probe=lambda url: CdpStatus(
                connected=False,
                endpoint=url,
                reason_code="CDP_UNAVAILABLE",
                next_action="启动 CDP Chrome",
            ),
        )
