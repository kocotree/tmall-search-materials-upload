import pytest

from upload_search_materials.browser.qianniu_copy import (
    COPY_FORM_SAFE_POPUP_SELECTORS,
    COPY_LATE_POPUP_QUIET_CHECKS,
    COPY_LIST_SAFE_POPUP_SELECTORS,
    COPY_POPUP_SETTLE_DELAY_MS,
    QianniuCopyError,
    _click_visible_image_text_action,
    _ensure_recommend_list,
    _find_product_row_with_recovery,
    _generate_copy,
    _open_recommend_list,
    _read_ai_result_panel,
    _reset_material_selector_to_all_images,
    _settle_copy_popups,
    _wait_for_product_scope,
    parse_qianniu_ai_copy,
)


def test_parse_qianniu_ai_copy_extracts_visible_result():
    title, description = parse_qianniu_ai_copy(
        "\n".join(
            [
                "AI生成标题和正文，点击填充文案直接使用：",
                "重新生成",
                "填充文案",
                "标题：KK树儿童防滑溯溪鞋",
                (
                    "正文：轻盈透气，穿脱方便，专为戏水设计！"
                    "户外探险、赶海玩水，一双搞定。"
                ),
                "确认",
                "取消",
            ]
        )
    )

    assert title == "KK树儿童防滑溯溪鞋"
    assert description.endswith("一双搞定。")


def test_parse_qianniu_ai_copy_rejects_generating_placeholder():
    with pytest.raises(
        QianniuCopyError, match="QIANNIU_COPY_RESULT_INVALID"
    ):
        parse_qianniu_ai_copy(
            "AI生成标题和正文，点击填充文案直接使用：\n重新生成\n生成中…"
        )


def test_parse_qianniu_ai_copy_keeps_multiline_description():
    title, description = parse_qianniu_ai_copy(
        "\n".join(
            [
                "标题：KK树儿童沙滩鞋",
                "正文：第一行卖点。",
                "第二行使用场景。",
                "填充文案",
            ]
        )
    )

    assert title == "KK树儿童沙滩鞋"
    assert description == "第一行卖点。\n第二行使用场景。"


def test_parse_qianniu_ai_copy_excludes_concatenated_action_labels():
    title, description = parse_qianniu_ai_copy(
        "\n".join(
            [
                "标题：KK树儿童防晒连帽衫",
                "正文：轻盈透气，夏日出行舒适防晒。",
                "确认取消",
            ]
        )
    )

    assert title == "KK树儿童防晒连帽衫"
    assert description == "轻盈透气，夏日出行舒适防晒。"


def test_parse_qianniu_ai_copy_accepts_labels_on_separate_lines():
    title, description = parse_qianniu_ai_copy(
        "\n".join(
            [
                "重新生成",
                "填充文案",
                "标题",
                "KK树儿童轻便运动鞋",
                "描述",
                "轻盈鞋身，适合日常活动。",
                "确认取消",
            ]
        )
    )

    assert title == "KK树儿童轻便运动鞋"
    assert description == "轻盈鞋身，适合日常活动。"


class _FakeInput:
    def __init__(self, placeholder, visible=True):
        self.placeholder = placeholder
        self.visible = visible

    def get_attribute(self, name):
        return self.placeholder if name == "placeholder" else None

    def is_visible(self):
        return self.visible


class _FakeCollection:
    def __init__(self, items):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    def is_visible(self):
        return len(self.items) == 1 and self.items[0].is_visible()

    def click(self, **kwargs):
        assert len(self.items) == 1
        return self.items[0].click(**kwargs)


class _FakeScope:
    def __init__(self, url, placeholders, *, table_ready):
        self.url = url
        self.inputs = [_FakeInput(value) for value in placeholders]
        self.table_ready = table_ready

    def locator(self, selector):
        if selector == "input":
            return _FakeCollection(self.inputs)
        if selector == "tbody tr":
            return _FakeCollection([object()] if self.table_ready else [])
        return _FakeCollection([])


class _FakePage:
    def __init__(self, frames, on_wait=None):
        self.frames = frames
        self.on_wait = on_wait
        self.wait_count = 0

    def wait_for_timeout(self, _milliseconds):
        self.wait_count += 1
        if self.on_wait is not None:
            self.on_wait(self.wait_count)


def test_copy_popup_settlement_reuses_collection_safe_controls(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    calls = []
    monkeypatch.setattr(
        copy_module,
        "_settle_safe_popups",
        lambda page, selectors, **kwargs: calls.append(
            (page, dict(selectors), kwargs)
        )
        or 2,
    )
    page = _FakePage([])

    closed = _settle_copy_popups(page, watch_for_late_popup=True)

    assert closed == 2
    assert calls == [
        (
            page,
            COPY_LIST_SAFE_POPUP_SELECTORS,
            {
                "delay_ms": COPY_POPUP_SETTLE_DELAY_MS,
                "quiet_checks_required": COPY_LATE_POPUP_QUIET_CHECKS,
            },
        )
    ]
    assert "GuideModal_dialog" in calls[0][1]["safe_popup_progress"]


def test_copy_form_popup_controls_cannot_close_active_publish_form():
    priority = COPY_FORM_SAFE_POPUP_SELECTORS[
        "safe_popup_close_priority"
    ]
    fallback = COPY_FORM_SAFE_POPUP_SELECTORS["safe_popup_close"]

    assert "GuideModal_dialog" in priority
    assert "AiImageGenerationOfflinePushModal" in priority
    assert ".next-overlay-wrapper" not in priority
    assert "button.ant-modal-close" not in priority
    assert '.ant-modal-wrap.ant-modal-centered [aria-label="close"]' not in (
        priority
    )
    assert 'button:has-text("关闭")' not in fallback
    assert '[aria-label*="关闭"]' not in fallback


def test_copy_popup_settlement_blocks_when_safe_control_remains(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_copy as copy_module

    class VisibleControl:
        def is_visible(self):
            return True

    class PopupPage(_FakePage):
        def locator(self, selector):
            if "GuideModal_dialog" in selector:
                return _FakeCollection([VisibleControl()])
            return _FakeCollection([])

    monkeypatch.setattr(
        copy_module,
        "_settle_safe_popups",
        lambda *_args, **_kwargs: 0,
    )

    with pytest.raises(
        QianniuCopyError,
        match="QIANNIU_COPY_POPUP_BLOCKED",
    ):
        _settle_copy_popups(PopupPage([]))


def test_recommend_navigation_waits_for_late_safe_popup(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    calls = []

    class NavigationPage:
        def goto(self, url, **kwargs):
            calls.append(("goto", url, kwargs))

    monkeypatch.setattr(
        copy_module,
        "_settle_copy_popups",
        lambda page, **kwargs: calls.append(("settle", page, kwargs)),
    )
    page = NavigationPage()

    _open_recommend_list(page, "https://example.test/material-center")

    assert calls[0][0] == "goto"
    assert calls[1] == (
        "settle",
        page,
        {"watch_for_late_popup": True},
    )


class _FakeMenuAction:
    def __init__(self, *, stale=False):
        self.stale = stale
        self.clicked = False

    def is_visible(self):
        return True

    def evaluate(self, expression, *, timeout):
        assert expression == "element => element.click()"
        assert timeout == 1_500
        if self.stale:
            raise RuntimeError("portal node replaced")
        self.clicked = True


class _ReplacingMenuScope:
    url = "https://myseller.taobao.com/material-frame"

    def __init__(self):
        self.calls = 0
        self.fresh = _FakeMenuAction()

    def get_by_text(self, text, *, exact=False):
        assert text == "发图文"
        assert exact is True
        self.calls += 1
        action = _FakeMenuAction(stale=True) if self.calls == 1 else self.fresh
        return _FakeCollection([action])


def test_image_text_action_re_resolves_replaced_portal_item():
    scope = _ReplacingMenuScope()
    page = _FakePage([scope])

    clicked, observed = _click_visible_image_text_action(page)

    assert clicked is True
    assert observed is True
    assert scope.fresh.clicked is True
    assert page.wait_count == 1


class _FakeAssistantAction:
    def __init__(self):
        self.clicked = False

    def is_visible(self):
        return True

    def click(self, *, force=False):
        assert force is True
        self.clicked = True


class _FakeBody:
    def __init__(self, page):
        self.page = page

    def inner_text(self):
        if self.page.wait_count < 2:
            return "重新生成\n填充文案\n标题\n正文"
        return (
            "重新生成\n填充文案\n标题\nKK树儿童遮阳帽\n"
            "正文\n轻盈透气，适合夏日出行。\n确认\n取消"
        )


class _FakeCopyFrame:
    def __init__(self, page):
        self.page = page
        self.assistant = _FakeAssistantAction()

    def get_by_text(self, text, *, exact=False):
        assert exact is True
        if text == "AI生成文案":
            return _FakeCollection([self.assistant])
        if text == "填充文案":
            return _FakeCollection([])
        raise AssertionError(text)

    def locator(self, selector):
        assert selector == "body"
        return _FakeBody(self.page)


def test_generate_copy_waits_until_both_fields_are_committed():
    page = _FakePage([])
    frame = _FakeCopyFrame(page)

    title, description, _elapsed = _generate_copy(page, frame)

    assert frame.assistant.clicked is True
    assert page.wait_count == 2
    assert title == "KK树儿童遮阳帽"
    assert description == "轻盈透气，适合夏日出行。"


def test_generate_copy_settles_form_popups_before_ai_action(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    page = _FakePage([])
    frame = _FakeCopyFrame(page)
    calls = []
    monkeypatch.setattr(
        copy_module,
        "_settle_copy_popups",
        lambda target, **kwargs: calls.append((target, kwargs)),
    )

    _generate_copy(page, frame)

    assert calls == [(page, {"publish_form_open": True})]
    assert frame.assistant.clicked is True


class _FakeResultPanelAction:
    def is_visible(self):
        return True

    def evaluate(self, _expression, *, timeout):
        assert timeout == 1_000
        return {
            "text": "",
            "controls": [
                {
                    "hint": "请输入标题",
                    "context": "标题",
                    "value": "KK树儿童太阳镜",
                },
                {
                    "hint": "请输入正文",
                    "context": "正文",
                    "value": "轻盈镜架，日常遮阳佩戴舒适。",
                },
            ],
        }


class _FakeResultPanelFrame:
    def get_by_text(self, text, *, exact=False):
        assert text == "填充文案"
        assert exact is True
        return _FakeCollection([_FakeResultPanelAction()])


def test_read_ai_result_panel_prefers_labelled_control_values():
    assert _read_ai_result_panel(_FakeResultPanelFrame()) == (
        "KK树儿童太阳镜",
        "轻盈镜架，日常遮阳佩戴舒适。",
    )


def test_product_scope_ignores_help_search_and_uses_product_frame():
    help_scope = _FakeScope(
        "https://myseller.taobao.com/shell",
        ["如何设置电子发票"],
        table_ready=False,
    )
    product_scope = _FakeScope(
        "https://myseller.taobao.com/material-frame",
        ["商品名称/ID", "素材id"],
        table_ready=True,
    )

    scope, search = _wait_for_product_scope(
        _FakePage([help_scope, product_scope])
    )

    assert scope is product_scope
    assert search.placeholder == "商品名称/ID"


def test_product_scope_waits_for_spa_table_hydration(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    monkeypatch.setattr(copy_module, "PRODUCT_SCOPE_ATTEMPTS", 4)
    product_scope = _FakeScope(
        "https://myseller.taobao.com/material-center",
        ["如何设置电子发票", "商品名称/ID"],
        table_ready=False,
    )

    def hydrate(wait_count):
        if wait_count == 2:
            product_scope.table_ready = True

    page = _FakePage([product_scope], on_wait=hydrate)
    scope, search = _wait_for_product_scope(page)

    assert scope is product_scope
    assert search.placeholder == "商品名称/ID"
    assert page.wait_count == 2


def test_product_scope_keeps_waiting_past_previous_sixty_second_limit():
    product_scope = _FakeScope(
        "https://myseller.taobao.com/material-center",
        ["商品名称/ID"],
        table_ready=False,
    )

    def hydrate(wait_count):
        if wait_count == 250:
            product_scope.table_ready = True

    page = _FakePage([product_scope], on_wait=hydrate)
    scope, search = _wait_for_product_scope(page)

    assert scope is product_scope
    assert search.placeholder == "商品名称/ID"
    assert page.wait_count == 250


def test_copy_network_waits_cover_slow_navigation_upload_and_ai():
    import upload_search_materials.browser.qianniu_copy as copy_module

    assert copy_module.NAVIGATION_TIMEOUT_MS == 60_000
    assert (
        copy_module.PRODUCT_SCOPE_ATTEMPTS
        * copy_module.PRODUCT_SCOPE_DELAY_MS
    ) >= 90_000
    assert copy_module.COPY_UPLOAD_TIMEOUT_MS == 120_000
    assert copy_module.AI_COPY_TIMEOUT_MS == 180_000


def test_product_scope_timeout_refreshes_once_before_retrying(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    attempts = []
    refreshes = []

    def find(_page, _product_id):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise QianniuCopyError("QIANNIU_PRODUCT_SEARCH_NOT_FOUND")
        return "product-row"

    monkeypatch.setattr(copy_module, "_find_product_row", find)
    monkeypatch.setattr(
        copy_module,
        "_open_recommend_list",
        lambda page, url: refreshes.append((page, url)),
    )
    page = object()

    row = _find_product_row_with_recovery(
        page,
        "886506466908",
        "https://example.test/material-center",
    )

    assert row == "product-row"
    assert attempts == [1, 2]
    assert refreshes == [(page, "https://example.test/material-center")]


def test_recommend_list_reuses_page_returned_by_previous_slot(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    target = "https://example.test/material-center?tab=recommend"
    page = type(
        "Page",
        (),
        {
            "url": target,
            "frames": [type("Frame", (), {"url": target})()],
        },
    )()
    navigations = []
    monkeypatch.setattr(
        copy_module,
        "_open_recommend_list",
        lambda page, url: navigations.append((page, url)),
    )

    opened = _ensure_recommend_list(
        page,
        "https://example.test/material-center",
    )

    assert opened is False
    assert navigations == []


def test_recommend_list_does_not_reuse_an_unclosed_publish_form(monkeypatch):
    import upload_search_materials.browser.qianniu_copy as copy_module

    target = "https://example.test/material-center?tab=recommend"
    page = type(
        "Page",
        (),
        {
            "url": target,
            "frames": [
                type("Frame", (), {"url": target})(),
                type(
                    "Frame",
                    (),
                    {"url": "https://example.test/publish-feeds/imagePreview"},
                )(),
            ],
        },
    )()
    navigations = []
    monkeypatch.setattr(
        copy_module,
        "_open_recommend_list",
        lambda page, url: navigations.append((page, url)),
    )

    opened = _ensure_recommend_list(
        page,
        "https://example.test/material-center",
    )

    assert opened is True
    assert navigations == [(page, "https://example.test/material-center")]


def test_product_scope_reports_observed_help_input_on_timeout(
    monkeypatch,
):
    import upload_search_materials.browser.qianniu_copy as copy_module

    monkeypatch.setattr(copy_module, "PRODUCT_SCOPE_ATTEMPTS", 2)
    page = _FakePage(
        [
            _FakeScope(
                "https://myseller.taobao.com/shell",
                ["如何设置电子发票"],
                table_ready=False,
            )
        ]
    )

    with pytest.raises(
        QianniuCopyError,
        match="如何设置电子发票",
    ):
        _wait_for_product_scope(page)


class _FakeMaterialRoot:
    def __init__(self, selector, *, visible=True):
        self.selector = selector
        self.visible = visible
        self.clicked = False

    def is_visible(self):
        return self.visible

    def click(self, *, force=False):
        assert force is True
        self.clicked = True
        self.selector.root_loaded = True


class _FakeMaterialSelector:
    def __init__(self, visible_roots=1):
        self.root_loaded = False
        self.roots = [
            _FakeMaterialRoot(self, visible=index < visible_roots)
            for index in range(max(visible_roots, 1))
        ]

    def get_by_text(self, text, *, exact=False):
        assert text == "全部图片"
        assert exact is True
        return _FakeCollection(self.roots)

    def locator(self, selector):
        assert selector == '[class*="PicList_PicturesShow_main-show"]'
        return _FakeCollection([object()] if self.root_loaded else [])


def test_material_selector_resets_empty_remembered_folder_to_root():
    selector = _FakeMaterialSelector()

    cards = _reset_material_selector_to_all_images(
        _FakePage([]),
        selector,
    )

    assert selector.roots[0].clicked is True
    assert cards.count() == 1


def test_material_selector_waits_for_root_tree_hydration():
    selector = _FakeMaterialSelector(visible_roots=0)

    def hydrate(wait_count):
        if wait_count == 2:
            selector.roots[0].visible = True

    cards = _reset_material_selector_to_all_images(
        _FakePage([], on_wait=hydrate),
        selector,
    )

    assert selector.roots[0].clicked is True
    assert cards.count() == 1


def test_material_selector_requires_unique_visible_root():
    selector = _FakeMaterialSelector(visible_roots=2)

    with pytest.raises(
        QianniuCopyError,
        match="QIANNIU_MATERIAL_ROOT_NOT_FOUND",
    ):
        _reset_material_selector_to_all_images(
            _FakePage([]),
            selector,
        )
