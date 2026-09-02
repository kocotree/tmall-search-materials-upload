from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from upload_search_materials.browser.qianniu_upload import (
    QianniuProductUploadSession,
    QianniuUploadError,
    _click_material_confirm_when_unblocked,
    _uploaded_card_for_unique_name,
    _wait_for_material_confirm_ready,
    _wait_for_upload_completion,
)


def test_uploaded_card_zero_match_is_not_found():
    with pytest.raises(QianniuUploadError) as exc_info:
        _uploaded_card_for_unique_name("seed.jpg", [])

    assert exc_info.value.reason_code == "QIANNIU_MATERIAL_CARD_NOT_FOUND"
    assert exc_info.value.detail == "seed.jpg:matches=0"


def test_uploaded_card_multiple_matches_are_ambiguous():
    with pytest.raises(QianniuUploadError) as exc_info:
        _uploaded_card_for_unique_name("seed.jpg", [object(), object()])

    assert exc_info.value.reason_code == "QIANNIU_MATERIAL_IDENTITY_AMBIGUOUS"
    assert exc_info.value.detail == "seed.jpg:matches=2"


class _RaisingLocator:
    def __init__(self, error):
        self.error = error

    def wait_for(self, **_kwargs):
        raise self.error


class _SelectorFrame:
    def __init__(self, error):
        self.error = error

    def get_by_text(self, _text, *, exact=False):
        assert exact is False
        return _RaisingLocator(self.error)


class _RecordingLocator:
    def __init__(self):
        self.timeout = None

    def wait_for(self, *, state, timeout):
        assert state == "visible"
        self.timeout = timeout
        raise PlaywrightError("Frame was detached")


class _RecordingSelectorFrame:
    def __init__(self):
        self.locator = _RecordingLocator()

    def get_by_text(self, _text, *, exact=False):
        assert exact is False
        return self.locator


def test_upload_completion_accepts_selector_frame_replacement():
    _wait_for_upload_completion(
        object(),
        _SelectorFrame(PlaywrightError("Frame was detached")),
        1,
    )


def test_upload_completion_wraps_unexpected_playwright_error():
    with pytest.raises(
        QianniuUploadError,
        match="QIANNIU_LOCAL_UPLOAD_RUNTIME_FAILED",
    ):
        _wait_for_upload_completion(
            object(),
            _SelectorFrame(PlaywrightError("Browser process exited")),
            1,
        )


def test_upload_completion_accepts_a_longer_copy_timeout():
    selector = _RecordingSelectorFrame()

    _wait_for_upload_completion(
        object(),
        selector,
        1,
        timeout_ms=120_000,
    )

    assert selector.locator.timeout == 120_000


def test_material_confirm_waits_until_selected_cards_are_committed(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_upload as module

    waits = []
    enabled_states = iter([False, False, True])
    confirm = SimpleNamespace(is_enabled=lambda: next(enabled_states))
    page = SimpleNamespace(wait_for_timeout=lambda delay: waits.append(delay))
    selector = SimpleNamespace(locator=lambda _selector: object())
    monkeypatch.setattr(
        module,
        "_read_selection_count",
        lambda _selector: (1, "确定"),
    )
    monkeypatch.setattr(module, "_first_visible", lambda _locator: confirm)

    result = _wait_for_material_confirm_ready(
        page,
        selector,
        1,
        attempts=5,
        delay_ms=300,
    )

    assert result is confirm
    assert waits == [300, 300]


def test_material_confirm_reports_not_ready_after_wait_exhaustion(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_upload as module

    waits = []
    confirm = SimpleNamespace(is_enabled=lambda: False)
    page = SimpleNamespace(wait_for_timeout=lambda delay: waits.append(delay))
    selector = SimpleNamespace(locator=lambda _selector: object())
    monkeypatch.setattr(
        module,
        "_read_selection_count",
        lambda _selector: (1, "确定"),
    )
    monkeypatch.setattr(module, "_first_visible", lambda _locator: confirm)

    with pytest.raises(QianniuUploadError) as exc_info:
        _wait_for_material_confirm_ready(
            page,
            selector,
            1,
            attempts=3,
            delay_ms=300,
        )

    assert exc_info.value.reason_code == "QIANNIU_MATERIAL_CONFIRM_NOT_READY"
    assert waits == [300, 300]


def test_material_confirm_waits_for_middleware_overlay_before_click(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_upload as module

    waits = []
    clicks = []
    overlay_states = iter([True, True, False])
    page = SimpleNamespace(wait_for_timeout=lambda delay: waits.append(delay))
    confirm = SimpleNamespace(
        click=lambda **kwargs: clicks.append(kwargs["timeout"])
    )
    monkeypatch.setattr(
        module,
        "_material_confirm_overlay_visible",
        lambda *_args: next(overlay_states),
    )

    _click_material_confirm_when_unblocked(
        page,
        object(),
        confirm,
        attempts=5,
        delay_ms=300,
    )

    assert waits == [300, 300]
    assert clicks == [module.MATERIAL_CONFIRM_CLICK_TIMEOUT_MS]


def test_material_confirm_click_timeout_becomes_retryable_upload_error(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_upload as module

    overlay_states = iter([False, True])
    page = SimpleNamespace(wait_for_timeout=lambda _delay: None)

    def timeout_click(**_kwargs):
        raise PlaywrightTimeoutError("overlay intercepts pointer events")

    confirm = SimpleNamespace(click=timeout_click)
    monkeypatch.setattr(
        module,
        "_material_confirm_overlay_visible",
        lambda *_args: next(overlay_states),
    )

    with pytest.raises(QianniuUploadError) as exc_info:
        _click_material_confirm_when_unblocked(
            page,
            object(),
            confirm,
            attempts=5,
            delay_ms=300,
        )

    assert exc_info.value.reason_code == "QIANNIU_MATERIAL_CONFIRM_NOT_READY"
    assert "middleware_overlay_visible=true" in exc_info.value.detail


def test_product_upload_session_reuses_filtered_product_row(monkeypatch):
    import upload_search_materials.browser.qianniu_upload as module

    calls = []
    row = SimpleNamespace(is_visible=lambda: True)
    item = SimpleNamespace(product_id="123")
    monkeypatch.setattr(
        module,
        "_recommend_list_is_current",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        module,
        "_ensure_recommend_list",
        lambda *_args: calls.append("ensure"),
    )
    monkeypatch.setattr(
        module,
        "_find_product_row_with_recovery",
        lambda *_args: calls.append("search") or row,
    )
    monkeypatch.setattr(
        module,
        "_approved_upload_paths",
        lambda _item: ["approved.jpg"],
    )
    monkeypatch.setattr(
        module,
        "_prepare_qianniu_upload_from_row",
        lambda _page, _item, *, row, paths: (
            calls.append(("prepare", row, tuple(paths))) or {"OLD-1"}
        ),
    )

    session = QianniuProductUploadSession(
        object(),
        "123",
        material_center_url="https://example.invalid/material-center",
    )

    assert session.prepare(item) == {"OLD-1"}
    assert session.prepare(item) == {"OLD-1"}
    assert calls == [
        "ensure",
        "search",
        ("prepare", row, ("approved.jpg",)),
        ("prepare", row, ("approved.jpg",)),
    ]


def test_product_upload_session_waits_for_remote_id_without_refresh(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_upload as module

    waits = []
    page = SimpleNamespace(wait_for_timeout=lambda delay: waits.append(delay))
    row = SimpleNamespace(is_visible=lambda: True)
    item = SimpleNamespace(product_id="123")
    remote_id_snapshots = iter(
        [
            ({"OLD-1"}, {"OLD-1": (1, "old")}),
            (
                {"OLD-1", "NEW-1"},
                {"OLD-1": (2, "old"), "NEW-1": (1, "审核中")},
            ),
        ]
    )
    monkeypatch.setattr(
        module,
        "_recommend_list_is_current",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        module,
        "_wait_for_publish_form_to_close",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        module,
        "_remote_ids_from_row",
        lambda _row: next(remote_id_snapshots),
    )
    monkeypatch.setattr(
        module,
        "_observe_new_remote_item_from_row",
        lambda *_args, **_kwargs: "observed",
    )
    monkeypatch.setattr(
        module,
        "_find_product_row_with_recovery",
        lambda *_args: pytest.fail("same product must not be searched again"),
    )
    session = QianniuProductUploadSession(
        page,
        "123",
        material_center_url="https://example.invalid/material-center",
    )
    session._row = row

    observed = session.observe_new_remote_item(
        item,
        before_remote_ids={"OLD-1"},
    )

    assert observed == "observed"
    assert waits == [module.REMOTE_ID_OBSERVE_DELAY_MS]
