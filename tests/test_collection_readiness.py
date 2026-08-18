import hashlib
import json
from pathlib import Path

import pytest
import yaml

from upload_search_materials.collection_readiness import (
    DEFAULT_CANDIDATE_SELECTORS,
    SelectorBootstrapError,
    build_collection_readiness,
    create_selector_candidate,
    ensure_production_selector_profile,
    merge_default_safe_popup_selectors,
    project_environment_status,
    promote_selector_candidate,
    validate_selector_candidate,
)
from upload_search_materials.browser.material_page import (
    prepare_high_value_validation_page,
)
from upload_search_materials.runtime_config import load_runtime_config


def test_default_popup_selectors_cover_current_first_install_dialogs():
    progress = DEFAULT_CANDIDATE_SELECTORS["safe_popup_progress"]
    priority = DEFAULT_CANDIDATE_SELECTORS["safe_popup_close_priority"]
    fallback = DEFAULT_CANDIDATE_SELECTORS["safe_popup_close"]

    assert 'class*="GuideModal_dialog"' in progress
    assert ':text-is("下一步")' in progress
    assert 'a.next-dialog-close[aria-label="关闭"]' in priority
    assert '[aria-label="close"]' in priority
    assert "AiImageGenerationOfflinePushModal_closeIcon" in priority
    assert 'button:has-text("以后再看")' in fallback


def test_current_popup_defaults_are_added_to_an_existing_profile():
    merged = merge_default_safe_popup_selectors(
        {
            "promotion_rows": ".rows",
            "safe_popup_close": "#legacy-close",
        }
    )

    assert merged["promotion_rows"] == ".rows"
    assert 'button:has-text("以后再看")' in merged["safe_popup_close"]
    assert merged["safe_popup_close"].endswith(", #legacy-close")
    assert 'class*="GuideModal_dialog"' in merged["safe_popup_progress"]


def test_environment_status_accepts_windows_virtual_environment(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: test\ndescription: test\n---\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='test'\nversion='0'\n", encoding="utf-8"
    )
    runtime_root = tmp_path / "user-data" / "runtime"
    python = runtime_root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "src" / "upload_search_materials").mkdir(parents=True)
    launcher = tmp_path / "scripts" / "run-plugin.py"
    launcher.parent.mkdir()
    launcher.touch()
    lock = tmp_path / "uv.lock"
    lock.touch()
    (runtime_root / "environment-fingerprint.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "python": str(python.resolve()),
                "dependency_mode": "no-install-project",
                "launch_mode": "current-plugin-source",
            }
        ),
        encoding="utf-8",
    )
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(tmp_path / "user-data")},
        start=tmp_path,
    )

    status = project_environment_status(runtime)

    assert status["ready"] is True
    assert status["reason_code"] == "READY"
    assert Path(status["evidence"]["python"]).parts[-3:] == (
        ".venv",
        "Scripts",
        "python.exe",
    )


class Locator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        if self.selector == self.page.store_selector:
            return 1
        if self.selector == self.page.human_selector:
            return 0
        return 1

    def inner_text(self):
        if self.selector in {
            self.page.selectors["promotion_first_page"],
        }:
            return "1"
        if self.selector == self.page.selectors["promotion_current_page"]:
            return str(self.page.current_page)
        if self.selector == self.page.selectors["promotion_terminal_page"]:
            return str(self.page.terminal_page)
        return self.page.store

    def all_inner_texts(self):
        if self.selector == self.page.selectors["promotion_rows"]:
            return ["商品 商品ID 100"]
        return []

    def get_attribute(self, _name):
        return None

    def is_enabled(self):
        if self.selector == self.page.selectors["promotion_next_page"]:
            return self.page.current_page < self.page.terminal_page
        return True

    def is_visible(self):
        return False


class Page:
    url = (
        "https://myseller.taobao.com/home.htm/"
        "material-center/material-management"
    )

    def __init__(
        self,
        selectors,
        store="测试店铺",
        *,
        current_page=1,
        terminal_page=1,
    ):
        self.store = store
        self.selectors = selectors
        self.current_page = current_page
        self.terminal_page = terminal_page
        self.store_selector = selectors["store_name"]
        self.human_selector = selectors["human_check"]

    def locator(self, selector):
        return Locator(self, selector)


def test_candidate_is_non_production_until_all_current_dom_fields_validate(
    tmp_path,
):
    path = tmp_path / "selectors.local.yaml"
    created = create_selector_candidate(
        path,
        material_center_url=Page.url,
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert created["production"] is False
    assert document["production"] is False

    validation = validate_selector_candidate(
        path,
        Page(document),
        expected_store="测试店铺",
    )
    promoted = promote_selector_candidate(path, validation)
    final = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert validation["ready"] is True
    assert promoted["production"] is True
    assert final["production"] is True
    assert final["current_dom_validation"]["observed_store"] == "测试店铺"


def test_first_install_bootstraps_and_installs_validated_profile(
    tmp_path, monkeypatch
):
    user_data = tmp_path / "user-data"
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)},
        start=tmp_path,
    )
    observed = {}

    monkeypatch.setattr(
        "upload_search_materials.collection_readiness."
        "prepare_high_value_validation_page",
        lambda *_args, **_kwargs: None,
    )

    def validate(path, _page, *, expected_store):
        candidate = yaml.safe_load(path.read_text(encoding="utf-8"))
        observed["production_during_validation"] = candidate["production"]
        candidate_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "ready": True,
            "reason_code": "READY",
            "candidate_sha256": candidate_sha256,
            "field_results": {"promotion_rows": {"ready": True}},
            "page_evidence": {
                "page_identity": "material_center",
                "observed_store": expected_store,
                "store_match": True,
                "pagination_state": {"verified": True},
            },
            "validated_at": "2026-08-18T08:00:00+00:00",
        }

    monkeypatch.setattr(
        "upload_search_materials.collection_readiness."
        "validate_selector_candidate",
        validate,
    )

    installed_runtime, profile, validation = (
        ensure_production_selector_profile(
            runtime,
            object(),
            expected_store="测试店铺",
        )
    )

    assert observed["production_during_validation"] is False
    assert validation["ready"] is True
    assert profile.path == installed_runtime.selectors_file
    assert installed_runtime.selectors_file == (
        user_data / "config" / "selectors.local.yaml"
    ).resolve()
    assert yaml.safe_load(
        installed_runtime.selectors_file.read_text(encoding="utf-8")
    )["production"] is True
    runtime_document = json.loads(
        (user_data / "config" / "runtime.json").read_text(encoding="utf-8")
    )
    assert runtime_document["selectors_file"] == str(
        installed_runtime.selectors_file
    )


def test_first_install_keeps_failed_candidate_non_production(
    tmp_path, monkeypatch
):
    user_data = tmp_path / "user-data"
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)},
        start=tmp_path,
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_readiness."
        "prepare_high_value_validation_page",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_readiness."
        "validate_selector_candidate",
        lambda path, _page, **_kwargs: {
            "ready": False,
            "reason_code": "SELECTOR_FIELD_INVALID:promotion_rows",
            "candidate_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "field_results": {"promotion_rows": {"ready": False}},
            "page_evidence": {
                "reason_code": "SELECTOR_FIELD_INVALID:promotion_rows"
            },
            "validated_at": "2026-08-18T08:00:00+00:00",
        },
    )

    with pytest.raises(
        SelectorBootstrapError,
        match="SELECTOR_FIELD_INVALID:promotion_rows",
    ):
        ensure_production_selector_profile(
            runtime,
            object(),
            expected_store="测试店铺",
        )

    candidate = user_data / "config" / "selectors.local.yaml"
    assert yaml.safe_load(candidate.read_text(encoding="utf-8"))[
        "production"
    ] is False
    assert not (user_data / "config" / "runtime.json").exists()


def test_candidate_human_check_is_one_parseable_playwright_css_selector(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    create_selector_candidate(path, material_center_url=Page.url)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert "text=/" not in document["human_check"]
    assert ':text-is("验证码")' in document["human_check"]


def test_candidate_names_the_first_failed_field_and_is_not_promoted(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    create_selector_candidate(path, material_center_url=Page.url)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    class MissingRowsPage(Page):
        def locator(self, selector):
            locator = super().locator(selector)
            if selector == document["promotion_rows"]:
                locator.count = lambda: 0
            return locator

    validation = validate_selector_candidate(
        path,
        MissingRowsPage(document),
        expected_store="测试店铺",
    )

    assert validation["ready"] is False
    assert validation["reason_code"] == "SELECTOR_FIELD_INVALID:promotion_rows"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["production"] is False


def test_candidate_rejects_ambiguous_current_page_control(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    create_selector_candidate(path, material_center_url=Page.url)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    class AmbiguousPage(Page):
        def locator(self, selector):
            locator = super().locator(selector)
            if selector == document["promotion_current_page"]:
                locator.count = lambda: 2
            return locator

    validation = validate_selector_candidate(
        path,
        AmbiguousPage(document),
        expected_store="测试店铺",
    )

    assert validation["ready"] is False
    assert (
        validation["field_results"]["promotion_current_page"]["count"]
        == 2
    )


def test_candidate_interprets_first_middle_final_and_single_page(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    create_selector_candidate(path, material_center_url=Page.url)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    for current, terminal in ((1, 3), (2, 3), (3, 3), (1, 1)):
        validation = validate_selector_candidate(
            path,
            Page(
                document,
                current_page=current,
                terminal_page=terminal,
            ),
            expected_store="测试店铺",
        )
        state = validation["page_evidence"]["pagination_state"]
        assert state["verified"] is True
        assert state["current_page"] == current
        assert state["terminal_page"] == terminal


def test_candidate_reports_login_and_wrong_store_as_user_actions(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    create_selector_candidate(path, material_center_url=Page.url)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    login = validate_selector_candidate(
        path,
        Page(document, store=""),
        expected_store="测试店铺",
    )
    wrong_store = validate_selector_candidate(
        path,
        Page(document, store="其他店铺"),
        expected_store="测试店铺",
    )

    assert login["reason_code"] == "LOGIN_INTERACTION_REQUIRED"
    assert wrong_store["reason_code"] == "STORE_IDENTITY_MISMATCH"
    assert login["repair_action"]
    assert wrong_store["repair_action"]


def test_readiness_keeps_checks_independent_without_nas_access(tmp_path, monkeypatch):
    workspace = tmp_path
    (workspace / "src" / "upload_search_materials" / "docs").mkdir(
        parents=True
    )
    (workspace / "config").mkdir()
    (workspace / "SKILL.md").write_text(
        "---\nname: test\ndescription: test\n---\n",
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        "[project]\nname='test'\n",
        encoding="utf-8",
    )
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(workspace / "user-data")},
        start=workspace,
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_readiness.inspect_cdp_endpoint",
        lambda *_args, **_kwargs: type(
            "Status",
            (),
            {
                "connected": False,
                "endpoint": "http://127.0.0.1:9222",
                "reason_code": "CDP_UNAVAILABLE",
                "next_action": "启动 Chrome",
                "pages": (),
            },
        )(),
    )

    readiness = build_collection_readiness(
        runtime,
        expected_store="测试店铺",
    )
    checks = {item["id"]: item for item in readiness["checks"]}

    assert readiness["ready"] is False
    assert checks["environment"]["reason_code"] == "ENVIRONMENT_NOT_PREPARED"
    assert (
        checks["selector_schema"]["reason_code"]
        == "SELECTOR_PROFILE_NOT_FOUND"
    )
    assert checks["cdp_connection"]["reason_code"] == "CDP_UNAVAILABLE"


def test_dom_failure_does_not_misreport_authenticated_material_page_as_login(
    tmp_path, monkeypatch
):
    runtime = load_runtime_config(environ={}, start=tmp_path)
    monkeypatch.setattr(
        "upload_search_materials.collection_readiness.inspect_cdp_endpoint",
        lambda *_args, **_kwargs: type(
            "Status",
            (),
            {
                "connected": True,
                "endpoint": "http://127.0.0.1:9222",
                "reason_code": "",
                "next_action": "",
                "pages": (),
            },
        )(),
    )

    readiness = build_collection_readiness(
        runtime,
        expected_store="测试店铺",
        dom_evidence={
            "page_identity": "material_center",
            "observed_store": "测试店铺",
            "store_match": True,
            "field_results": {
                "high_value_filter": {
                    "configured": True,
                    "count": 0,
                }
            },
            "pagination_state": {
                "verified": False,
                "detail": "PAGINATION_ORIGIN_UNVERIFIED",
            },
        },
    )
    checks = {item["id"]: item for item in readiness["checks"]}

    assert checks["login_and_human_check"]["ready"] is True
    assert "自动切换" in checks["selector_current_dom"]["next_action"]


def test_validation_navigation_selects_high_value_and_returns_to_page_one():
    selectors = {
        "promotion_tab": "#promotion",
        "high_value_filter": "#high-value",
        "promotion_rows": "#rows",
        "promotion_current_page": "#current",
        "promotion_first_page": "#first",
        "promotion_terminal_page": "#terminal",
        "promotion_next_page": "#next",
    }

    class ValidationLocator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector

        def count(self):
            if self.selector == "#high-value":
                return 1 if self.page.promotion_open else 0
            return 1

        def click(self, timeout=None):
            if self.selector == "#promotion":
                self.page.promotion_open = True
            elif self.selector == "#high-value":
                self.page.high_value_checked = True
            elif self.selector == "#first":
                self.page.current_page = 1

        def get_attribute(self, name):
            if self.selector == "#high-value" and name == "aria-checked":
                return "true" if self.page.high_value_checked else "false"
            return None

        def inner_text(self):
            if self.selector == "#current":
                return str(self.page.current_page)
            if self.selector == "#first":
                return "1"
            if self.selector == "#terminal":
                return "3"
            return ""

        def all_inner_texts(self):
            return (
                [f"商品 商品ID 10{self.page.current_page}"]
                if self.selector == "#rows"
                else []
            )

        def is_enabled(self):
            return self.selector != "#next" or self.page.current_page < 3

    class ValidationPage:
        promotion_open = False
        high_value_checked = False
        current_page = 3

        def locator(self, selector):
            return ValidationLocator(self, selector)

        def wait_for_timeout(self, _milliseconds):
            return None

    page = ValidationPage()
    observed = prepare_high_value_validation_page(
        page,
        selectors,
        settle_delay_ms=0,
        action_wait_ms=1,
    )

    assert page.promotion_open is True
    assert page.high_value_checked is True
    assert observed.current_page == 1
