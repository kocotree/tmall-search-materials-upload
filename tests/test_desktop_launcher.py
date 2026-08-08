from contextlib import contextmanager
from pathlib import Path

import pytest

from upload_search_materials.desktop_launcher import (
    DesktopLauncherError,
    ensure_login_browser,
    inspect_login_browser,
    validate_desktop_launch,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig
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


def test_desktop_launcher_allows_new_session_in_desktop_identity(tmp_path):
    project = _project(tmp_path)

    result = validate_desktop_launch(
        runs_root=project.parent / "runs",
        session_id=None,
        port_start=8765,
        port_end=8795,
        config=None,
        project_root=project,
    )

    assert result["session_id"] is None


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


def test_runtime_identity_ignores_mutable_remote_drive_list():
    expected = {
        "sid": "S-1-test",
        "login_session_id": 1,
        "interactive_desktop": True,
        "remote_drive_letters": ["Y:", "Z:"],
    }
    actual = {
        "sid": "S-1-test",
        "login_session_id": 1,
        "interactive_desktop": True,
        "remote_drive_letters": [],
    }

    assert same_local_resource_identity(expected, actual)


def test_login_browser_is_started_before_workbench_ui(monkeypatch, tmp_path):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
        browser_profile_dir=tmp_path / "profile",
        material_center_url="https://example.test/material-center",
    )
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        return {
            "status": "connected",
            "reused": False,
            "endpoint": kwargs["cdp_url"],
            "pages": [{"url": kwargs["material_center_url"]}],
        }

    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_cdp_browser",
        ensure,
    )

    result = ensure_login_browser(runtime)

    assert result["connected"] is True
    assert result["page_count"] == 1
    assert calls[0]["material_center_url"] == runtime.material_center_url


def test_login_gate_detects_authenticated_store_without_target_store(
    monkeypatch, tmp_path
):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )

    class Locator:
        def __init__(self, values):
            self.values = values

        def count(self):
            return len(self.values)

        def nth(self, index):
            return Locator([self.values[index]])

        def is_visible(self):
            return bool(self.values and self.values[0][0])

        def inner_text(self):
            return self.values[0][1]

    class Page:
        url = "https://example.test/material-center"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, selector):
            if "captcha" in selector:
                return Locator([])
            return Locator([(True, "测试店铺")])

    @contextmanager
    def open_page(*_args, **_kwargs):
        yield Page()

    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_login_browser",
        lambda _runtime: {"connected": True, "status": "connected"},
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.open_cdp_page", open_page
    )

    result = inspect_login_browser(runtime)

    assert result["ready"] is True
    assert result["login_state"] == "authenticated"
    assert result["observed_store"] == "测试店铺"
