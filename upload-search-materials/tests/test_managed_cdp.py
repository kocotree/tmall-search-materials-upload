import json
from pathlib import Path

import pytest

import upload_search_materials.browser.session as session_module
from upload_search_materials.browser.session import (
    CdpStatus,
    CdpUnavailable,
    launch_cdp_browser,
    validate_collection_page,
)


class Locator:
    def __init__(self, *, text="", count=1, visible=False):
        self._text = text
        self._count = count
        self._visible = visible

    def count(self):
        return self._count

    def inner_text(self):
        return self._text

    def is_visible(self):
        return self._visible


class Page:
    def __init__(self, url, store="kocotree旗舰店"):
        self.url = url
        self.store = store

    def locator(self, selector):
        if selector == "[data-store]":
            return Locator(text=self.store)
        return Locator(count=0, visible=False)


def selectors():
    return {
        "store_name": "[data-store]",
        "human_check": "[data-human-check]",
        "promotion_tab": "#promotion",
        "high_value_filter": "#high-value",
        "promotion_rows": "tbody tr",
        "promotion_next_page": "#next",
    }


def test_collection_page_evidence_contains_no_authentication_material():
    page = Page(
        "https://myseller.taobao.com/home.htm/"
        "material-center/material-management"
    )

    evidence = validate_collection_page(
        page,
        selectors(),
        expected_store="kocotree旗舰店",
        profile_name="current",
        profile_version="1",
        profile_sha256="a" * 64,
    )

    assert evidence["store_match"] is True
    assert evidence["page_identity"] == "material_center"
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    for forbidden in ("cookie", "password", "token", "sms", "二维码"):
        assert forbidden not in serialized


def test_launch_reuses_connected_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        session_module,
        "inspect_cdp_endpoint",
        lambda value: CdpStatus(True, value, ({"url": "https://example.test"},)),
    )

    result = launch_cdp_browser(
        executable=None,
        profile_dir=tmp_path / "profile",
        cdp_url="http://127.0.0.1:9222",
        material_center_url="https://example.test/materials",
    )

    assert result["reused"] is True


def test_launch_requires_configured_or_discoverable_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(
        session_module,
        "inspect_cdp_endpoint",
        lambda value: CdpStatus(False, value),
    )
    monkeypatch.setattr(session_module.shutil, "which", lambda _: None)

    with pytest.raises(CdpUnavailable, match="BROWSER_EXECUTABLE_REQUIRED"):
        launch_cdp_browser(
            executable=None,
            profile_dir=tmp_path / "profile",
            cdp_url="http://127.0.0.1:9222",
            material_center_url="https://example.test/materials",
        )


def test_launch_passes_material_center_url_to_visible_browser(
    monkeypatch, tmp_path
):
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"fake")
    calls = {}

    class Process:
        pid = 123

    monkeypatch.setattr(
        session_module,
        "inspect_cdp_endpoint",
        lambda value: CdpStatus(False, value),
    )

    def fake_popen(arguments, **kwargs):
        calls["arguments"] = arguments
        calls["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(session_module.subprocess, "Popen", fake_popen)

    result = launch_cdp_browser(
        executable=executable,
        profile_dir=tmp_path / "profile",
        cdp_url="http://127.0.0.1:9222",
        material_center_url="https://example.test/materials",
    )

    assert calls["arguments"][-1] == "https://example.test/materials"
    assert result["reused"] is False
    assert (tmp_path / "profile" / ".tmall-cdp-service.json").is_file()
