from pathlib import Path

import yaml

from upload_search_materials.collection_readiness import (
    build_collection_readiness,
    create_selector_candidate,
    promote_selector_candidate,
    validate_selector_candidate,
)
from upload_search_materials.runtime_config import load_runtime_config


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
    (workspace / "docs").mkdir()
    project = workspace / "upload-search-materials"
    (project / "config").mkdir(parents=True)
    runtime = load_runtime_config(environ={}, start=workspace)
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
