import json
from pathlib import Path
from urllib.request import ProxyHandler

import pytest

import upload_search_materials.browser.session as session_module
from upload_search_materials.browser.session import (
    CdpStatus,
    CdpUnavailable,
    ensure_cdp_browser,
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
        "promotion_current_page": "#current",
        "promotion_first_page": "#first",
        "promotion_terminal_page": "#terminal",
        "promotion_next_page": "#next",
    }


def test_cdp_health_check_bypasses_system_proxy_for_loopback():
    assert not any(
        isinstance(handler, ProxyHandler)
        for handler in session_module._LOOPBACK_OPENER.handlers
    )


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
    monkeypatch.setattr(
        session_module,
        "discover_browser_executable",
        lambda: None,
    )

    with pytest.raises(CdpUnavailable, match="BROWSER_EXECUTABLE_REQUIRED"):
        launch_cdp_browser(
            executable=None,
            profile_dir=tmp_path / "profile",
            cdp_url="http://127.0.0.1:9222",
            material_center_url="https://example.test/materials",
        )


def test_discovery_finds_standard_windows_browser_location(
    monkeypatch, tmp_path
):
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"fake")
    monkeypatch.setattr(session_module.shutil, "which", lambda _: None)

    assert session_module.discover_browser_executable(
        {"PROGRAMFILES": str(tmp_path)}
    ) == chrome


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
    assert calls["kwargs"]["start_new_session"] is (session_module.os.name != "nt")
    assert result["reused"] is False
    assert (tmp_path / "profile" / ".tmall-cdp-service.json").is_file()


def test_ensure_connected_endpoint_with_no_pages_opens_material_target(
    monkeypatch, tmp_path
):
    endpoint = "http://127.0.0.1:9222"
    material_url = "https://example.test/materials"
    opened = []

    monkeypatch.setattr(
        session_module,
        "launch_cdp_browser",
        lambda **_kwargs: {
            "status": "connected",
            "reused": True,
            "endpoint": endpoint,
            "pages": [],
        },
    )
    monkeypatch.setattr(
        session_module,
        "_open_cdp_target",
        lambda cdp_url, target_url, **_kwargs: opened.append(
            (cdp_url, target_url)
        ),
    )
    monkeypatch.setattr(
        session_module,
        "inspect_cdp_endpoint",
        lambda *_args, **_kwargs: CdpStatus(
            True,
            endpoint,
            ({"id": "page-1", "title": "素材中心", "url": material_url},),
        ),
    )

    result = ensure_cdp_browser(
        executable=None,
        profile_dir=tmp_path / "profile",
        cdp_url=endpoint,
        material_center_url=material_url,
        startup_timeout_seconds=1,
    )

    assert opened == [(endpoint, material_url)]
    assert result["status"] == "connected"
    assert result["pages"] == [
        {"id": "page-1", "title": "素材中心", "url": material_url}
    ]


def test_open_cdp_target_uses_local_put_with_encoded_url(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "id": "page-1",
                    "title": "素材中心",
                    "type": "page",
                    "url": "https://example.test/materials?a=1&b=2",
                }
            ).encode("utf-8")

    class Opener:
        def open(self, request, *, timeout):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(session_module, "_LOOPBACK_OPENER", Opener())

    result = session_module._open_cdp_target(
        "http://127.0.0.1:9222",
        "https://example.test/materials?a=1&b=2",
    )

    assert captured["method"] == "PUT"
    assert captured["url"].startswith("http://127.0.0.1:9222/json/new?")
    assert "%3F" in captured["url"] and "%26" in captured["url"]
    assert result["id"] == "page-1"
