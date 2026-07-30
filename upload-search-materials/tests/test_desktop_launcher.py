from pathlib import Path

import pytest

from upload_search_materials.desktop_launcher import (
    DesktopLauncherError,
    validate_desktop_launch,
)
from upload_search_materials.runtime_identity import (
    LocalResourceIdentityMismatch,
    require_local_resource_identity,
    same_local_resource_identity,
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "repo" / "upload-search-materials"
    (project / "config").mkdir(parents=True)
    return project


def test_desktop_launcher_accepts_only_structured_project_arguments(tmp_path):
    project = _project(tmp_path)
    runs = project.parent / "runs"
    config = project / "config" / "local-paths.json"
    config.write_text("{}", encoding="utf-8")

    result = validate_desktop_launch(
        runs_root=runs,
        session_id="20260730_014500",
        port_start=8765,
        port_end=8795,
        config=config,
        project_root=project,
    )

    assert result["runs_root"] == runs.resolve()
    assert result["config"] == config.resolve()
    assert set(result) == {
        "project_root",
        "runs_root",
        "session_id",
        "port_start",
        "port_end",
        "config",
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"session_id": "bad; whoami"}, "session_id_invalid"),
        ({"port_start": 80}, "port_range_invalid"),
        ({"port_end": 9000}, "port_range_invalid"),
    ],
)
def test_desktop_launcher_rejects_command_shaped_or_unbounded_values(
    tmp_path, overrides, reason
):
    project = _project(tmp_path)
    arguments = {
        "runs_root": project.parent / "runs",
        "session_id": "20260730_014500",
        "port_start": 8765,
        "port_end": 8795,
        "config": None,
        "project_root": project,
    } | overrides

    with pytest.raises(DesktopLauncherError, match=reason):
        validate_desktop_launch(**arguments)


def test_desktop_launcher_rejects_paths_outside_fixed_project(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    config = outside / "config.json"
    config.write_text("{}", encoding="utf-8")

    with pytest.raises(DesktopLauncherError, match="runs_root_outside"):
        validate_desktop_launch(
            runs_root=outside,
            session_id="20260730_014500",
            port_start=8765,
            port_end=8795,
            config=None,
            project_root=project,
        )
    with pytest.raises(DesktopLauncherError, match="config_path_invalid"):
        validate_desktop_launch(
            runs_root=project.parent / "runs",
            session_id="20260730_014500",
            port_start=8765,
            port_end=8795,
            config=config,
            project_root=project,
        )


def test_runtime_identity_comparison_fails_closed(monkeypatch):
    expected = {
        "sid": "S-1-test",
        "login_session_id": 1,
        "interactive_desktop": True,
    }
    actual = {
        "sid": "S-1-other",
        "login_session_id": 1,
        "interactive_desktop": True,
    }
    monkeypatch.setattr(
        "upload_search_materials.runtime_identity.current_runtime_identity",
        lambda: actual,
    )

    assert not same_local_resource_identity(expected, actual)
    with pytest.raises(
        LocalResourceIdentityMismatch,
        match="LOCAL_RESOURCE_IDENTITY_MISMATCH",
    ):
        require_local_resource_identity(expected)
