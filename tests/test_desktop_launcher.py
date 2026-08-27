from contextlib import contextmanager
from pathlib import Path

import pytest

from upload_search_materials.desktop_launcher import (
    DesktopLauncherError,
    ensure_login_browser,
    inspect_login_browser,
    launch_desktop_workbench,
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


def test_desktop_launcher_allows_configured_user_data_runs_root(tmp_path):
    project = _project(tmp_path)
    user_runs = tmp_path.parent / "user-state" / "runs"

    result = validate_desktop_launch(
        runs_root=user_runs,
        session_id=None,
        port_start=8765,
        port_end=8795,
        config=None,
        project_root=project,
        allowed_runs_root=user_runs,
    )

    assert result["runs_root"] == user_runs.resolve()


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


def test_desktop_workbench_requests_managed_desktop_service(monkeypatch, tmp_path):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )
    launch = {
        "project_root": tmp_path,
        "runs_root": runtime.runs_root,
        "session_id": "20260811_120000",
        "port_start": 8765,
        "port_end": 8795,
        "config": None,
    }
    calls = []
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.validate_desktop_launch",
        lambda **_kwargs: launch,
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.load_runtime_config",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_login_browser",
        lambda _runtime: {"connected": True},
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.start_service",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"healthy": True},
    )

    result = launch_desktop_workbench()

    assert result["healthy"] is True
    assert result["login_browser"] == {"connected": True}
    assert calls[0][1]["managed_desktop"] is True
    assert calls[0][1]["project_root"] == tmp_path
    assert calls[0][1]["workspace_root"] == tmp_path


def test_desktop_workbench_defaults_to_runtime_runs_root(monkeypatch, tmp_path):
    project = _project(tmp_path)
    runtime_runs = tmp_path.parent / "user-state" / "runs"
    runtime = RuntimeConfig(
        workspace_root=project,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=runtime_runs,
    )
    calls = []
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.load_runtime_config",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_login_browser",
        lambda _runtime: {"connected": True},
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.start_service",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"healthy": True},
    )

    launch_desktop_workbench(project_root=project)

    assert calls[0][0][0] == runtime_runs.resolve()


def test_desktop_workbench_uses_explicit_plugin_root_environment(
    monkeypatch, tmp_path
):
    project = _project(tmp_path)
    runtime = RuntimeConfig(
        workspace_root=project,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=project.parent / "runs",
    )
    calls = []
    monkeypatch.setenv("TMALL_PLUGIN_ROOT", str(project))
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.load_runtime_config",
        lambda *_args, **kwargs: calls.append(kwargs) or runtime,
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_login_browser",
        lambda _runtime: {"connected": True},
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.start_service",
        lambda *_args, **_kwargs: {"healthy": True},
    )

    launch_desktop_workbench()

    assert calls[0]["start"] == project.resolve()


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
            raise AssertionError("authenticated status must stay in background")

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


def test_login_gate_detects_authenticated_store_inside_child_frame(
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

    class Frame:
        def locator(self, selector):
            if "captcha" in selector:
                return Locator([])
            return Locator([(True, "iframe 测试店铺")])

    class Page:
        url = "https://example.test/material-center"
        frames = [Frame()]

        def bring_to_front(self):
            raise AssertionError("authenticated status must stay in background")

        def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return Locator([])

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
    assert result["observed_store"] == "iframe 测试店铺"


def test_login_gate_blocks_visible_textless_human_check_inside_frame(
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
        def __init__(self, visible):
            self.visible = visible

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return self.visible

    class Frame:
        def locator(self, selector):
            return Locator("captcha" in selector)

    class Page:
        url = "https://example.test/material-center"
        frames = [Frame()]

        def __init__(self):
            self.focused = False

        def bring_to_front(self):
            self.focused = True

        def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return Locator(False)

    page = Page()

    @contextmanager
    def open_page(*_args, **_kwargs):
        yield page

    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_login_browser",
        lambda _runtime: {"connected": True, "status": "connected"},
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.open_cdp_page", open_page
    )

    result = inspect_login_browser(runtime)

    assert result["ready"] is False
    assert result["login_state"] == "human_check"
    assert result["reason_code"] == "HUMAN_CHECK"
    assert page.focused is True


@pytest.mark.parametrize(
    ("url", "login_state", "reason_code", "expected_focus"),
    (
        (
            "https://login.taobao.com/member/login.jhtml",
            "interaction_required",
            "LOGIN_INTERACTION_REQUIRED",
            True,
        ),
        (
            "https://myseller.taobao.com/material-center/index",
            "store_unrecognized",
            "STORE_IDENTITY_NOT_FOUND",
            False,
        ),
        (
            "https://myseller.taobao.com/workbench",
            "opening_material_center",
            "MATERIAL_CENTER_OPENING",
            False,
        ),
    ),
)
def test_login_gate_distinguishes_page_and_login_states(
    monkeypatch,
    tmp_path,
    url,
    login_state,
    reason_code,
    expected_focus,
):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )

    class Locator:
        def count(self):
            return 0

    class Page:
        def __init__(self):
            self.url = url
            self.focused = False

        def bring_to_front(self):
            self.focused = True

        def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return Locator()

    page = Page()

    @contextmanager
    def open_page(*_args, **_kwargs):
        yield page

    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.ensure_login_browser",
        lambda _runtime: {"connected": True, "status": "connected"},
    )
    monkeypatch.setattr(
        "upload_search_materials.desktop_launcher.open_cdp_page", open_page
    )

    result = inspect_login_browser(runtime)

    assert result["ready"] is False
    assert result["login_state"] == login_state
    assert result["reason_code"] == reason_code
    assert page.focused is expected_focus
