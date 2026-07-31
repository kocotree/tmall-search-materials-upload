from pathlib import Path
from types import SimpleNamespace

import pytest

import upload_search_materials.material_executor_launcher as launcher_module
from upload_search_materials.material_executor_launcher import (
    MaterialExecutorLaunchError,
    launch_material_executor,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


def _runtime(tmp_path: Path, config: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
        config_path=config,
    )


def test_windows_launcher_delegates_to_desktop_shell_script(
    tmp_path, monkeypatch
):
    project = tmp_path / "upload-search-materials"
    python = project / ".venv" / "Scripts" / "pythonw.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    script = project / "scripts" / "launch-material-executor-desktop.ps1"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    config = project / "config" / "local-paths.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launcher_module, "_project_root", lambda: project)
    monkeypatch.setattr(launcher_module.os, "name", "nt")
    monkeypatch.setattr(launcher_module.subprocess, "run", fake_run)

    result = launch_material_executor(
        _runtime(tmp_path, config),
        "20260731_004205",
    )

    assert result["launch_channel"] == "windows_explorer"
    assert str(script) in captured["command"]
    assert "20260731_004205" in captured["command"]
    assert captured["kwargs"]["creationflags"] is not None


def test_launcher_rejects_untrusted_session_id(tmp_path):
    config = tmp_path / "local-paths.json"
    config.write_text("{}", encoding="utf-8")

    with pytest.raises(MaterialExecutorLaunchError, match="session_id_invalid"):
        launch_material_executor(_runtime(tmp_path, config), "../other")
