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
        return self.page.store

    def is_visible(self):
        return False


class Page:
    url = (
        "https://myseller.taobao.com/home.htm/"
        "material-center/material-management"
    )

    def __init__(self, selectors, store="测试店铺"):
        self.store = store
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
