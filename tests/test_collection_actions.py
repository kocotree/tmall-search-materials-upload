from upload_search_materials.browser import collection_actions


class FixedRandom:
    def choice(self, values):
        return values[0]

    def randint(self, minimum, _maximum):
        return minimum


class Slot:
    def __init__(self):
        self.hovered = False
        self.clicked = False
        self.hover_options = {}
        self.click_options = {}

    def hover(self, **kwargs):
        self.hovered = True
        self.hover_options = kwargs

    def click(self, **kwargs):
        self.clicked = True
        self.click_options = kwargs


class Slots:
    def __init__(self, count):
        self.values = [Slot() for _ in range(count)]

    def count(self):
        return len(self.values)

    def nth(self, index):
        return self.values[index]


class Row:
    def __init__(self, product_id):
        self.product_id = product_id

    def inner_text(self):
        return f"商品 商品ID {self.product_id}"

    def is_visible(self):
        return True


class LocatorList:
    def __init__(self, values=()):
        self.values = list(values)

    def count(self):
        return len(self.values)

    def nth(self, index):
        return self.values[index]


class CloseButton:
    def __init__(self, page):
        self.page = page
        self.clicked = False

    def is_visible(self):
        return True

    def click(self, **_kwargs):
        self.clicked = True
        self.page.frames = []


class Backdrop:
    def __init__(self, page):
        self.page = page
        self.clicked_positions = []

    def is_visible(self):
        return True

    def click(self, **kwargs):
        self.clicked_positions.append(kwargs["position"])
        self.page.frames = []


class ActionOverlayClose:
    def __init__(self, overlay):
        self.overlay = overlay
        self.clicked = False

    def is_visible(self):
        return self.overlay.is_visible()

    def click(self, **_kwargs):
        self.clicked = True
        self.overlay.page.action_overlay = None


class ActionOverlay:
    def __init__(self, page):
        self.page = page
        self.close_button = ActionOverlayClose(self)

    def is_visible(self):
        return self.page.action_overlay is self

    def locator(self, selector):
        if selector == '[aria-label*="关闭"]':
            return LocatorList([self.close_button])
        return LocatorList()


class Frame:
    def __init__(self, url, close_button=None, backdrop=None):
        self.url = url
        self.close_button = close_button
        self.backdrop = backdrop

    def locator(self, selector):
        if self.backdrop is not None and selector == ".ant-modal-wrap":
            return LocatorList([self.backdrop])
        if self.close_button is not None and selector == '[aria-label*="关闭"]':
            return LocatorList([self.close_button])
        return LocatorList()


class Keyboard:
    def __init__(self):
        self.keys = []

    def press(self, key):
        self.keys.append(key)


class Context:
    def __init__(self, page):
        self.pages = [page]


class Page:
    def __init__(self, rows):
        self.rows = rows
        self.url = "https://example.test/current-page"
        self.frames = []
        self.keyboard = Keyboard()
        self.context = Context(self)
        self.waited = []
        self.parent_backdrop = None
        self.action_overlay = None

    def locator(self, selector):
        if selector == ".promotion-row":
            return LocatorList(self.rows)
        if selector == collection_actions.ACTION_OVERLAY_SELECTOR:
            return LocatorList(
                [self.action_overlay] if self.action_overlay is not None else []
            )
        if (
            selector == ".next-overlay-wrapper.opened > .next-overlay-backdrop"
            and self.parent_backdrop is not None
        ):
            return LocatorList([self.parent_backdrop])
        return LocatorList()

    def wait_for_timeout(self, milliseconds):
        self.waited.append(milliseconds)


def common_stubs(monkeypatch, slots, empty_positions):
    monkeypatch.setattr(collection_actions, "_slot_cells", lambda _row: slots)
    monkeypatch.setattr(
        collection_actions,
        "_empty_slot_positions",
        lambda _row: list(empty_positions),
    )


def test_random_action_views_filled_slot_on_current_page(monkeypatch):
    page = Page([Row("100")])
    slots = Slots(2)
    common_stubs(monkeypatch, slots, [2])
    human_checks = []

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=FixedRandom(),
        wait_for_human_check=lambda _page, location: human_checks.append(location),
    )

    assert result == {
        "status": "completed",
        "action": "view_filled_slot",
        "product_id": "100",
        "slot_position": 1,
        "read_only": True,
        "source": "current_page",
        "page_state_restored": True,
    }
    assert slots.values[0].hovered is True
    assert slots.values[0].clicked is True
    assert slots.values[0].hover_options == {"force": True, "timeout": 3_000}
    assert slots.values[0].click_options == {"force": True, "timeout": 3_000}
    assert page.context.pages == [page]
    assert page.keyboard.keys == ["Escape"]
    assert human_checks == ["random_action_after_open"]


def test_random_action_only_chooses_from_filled_slots(monkeypatch):
    class ChooseLastRandom(FixedRandom):
        def choice(self, values):
            return values[-1]

    row = Row("150")
    page = Page([row])
    slots = Slots(6)
    common_stubs(monkeypatch, slots, [2, 6])

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=ChooseLastRandom(),
    )

    assert result["action"] == "view_filled_slot"
    assert result["slot_position"] == 5
    assert slots.values[4].clicked is True
    assert slots.values[5].clicked is False


def test_random_action_settles_popups_before_action_and_restore(monkeypatch):
    page = Page([Row("175")])
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [])
    settled = []
    monkeypatch.setattr(
        collection_actions,
        "_settle_safe_popups",
        lambda target, selectors, *, delay_ms: settled.append(
            (target, dict(selectors), delay_ms)
        ),
    )

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        popup_selectors={"safe_popup_close": "#safe-close"},
        rng=FixedRandom(),
    )

    assert result["status"] == "completed"
    assert settled == [
        (page, {"safe_popup_close": "#safe-close"}, 250),
        (page, {"safe_popup_close": "#safe-close"}, 250),
    ]


def test_random_action_closes_the_new_filled_slot_dialog(monkeypatch):
    page = Page([Row("180")])
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [])
    opened = {}

    def open_dialog(**_kwargs):
        overlay = ActionOverlay(page)
        page.action_overlay = overlay
        opened["overlay"] = overlay

    slots.values[0].click = open_dialog

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=FixedRandom(),
    )

    assert result["status"] == "completed"
    assert result["page_state_restored"] is True
    assert opened["overlay"].close_button.clicked is True
    assert page.action_overlay is None


def test_random_action_skips_page_with_only_empty_slots(monkeypatch):
    row = Row("200")
    page = Page([row])
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [1])

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=FixedRandom(),
    )

    assert result == {
        "status": "skipped",
        "reason_code": "RANDOM_ACTION_NO_FILLED_SLOT",
    }
    assert slots.values[0].hovered is False
    assert slots.values[0].clicked is False


def test_random_action_safely_skips_without_current_page_rows():
    result = collection_actions.perform_random_collection_action(
        Page([]),
        rows_selector=".promotion-row",
    )

    assert result["reason_code"] == "RANDOM_ACTION_NO_CURRENT_ROW"


def test_random_action_reports_failed_page_restoration(monkeypatch):
    page = Page([Row("300")])
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [])

    def leave_current_page(**_kwargs):
        page.rows = [Row("999")]

    slots.values[0].click = leave_current_page

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=FixedRandom(),
    )

    assert result["reason_code"] == "RANDOM_ACTION_PAGE_RESTORE_FAILED"
    assert result["page_state_restored"] is False
    assert result["restore_diagnostics"]["product_order_matches"] is False
    assert "product_order_matches=false" in result["detail"]


def test_random_action_allows_slow_page_hydration_before_restoration(
    monkeypatch,
):
    class SlowlyRestoredPage(Page):
        def __init__(self, rows):
            super().__init__(rows)
            self.expected_rows = list(rows)
            self.restore_pending = False
            self.restore_polls = 0

        def wait_for_timeout(self, milliseconds):
            super().wait_for_timeout(milliseconds)
            if not self.restore_pending or milliseconds != 250:
                return
            self.restore_polls += 1
            if self.restore_polls >= 24:
                self.rows = list(self.expected_rows)

    page = SlowlyRestoredPage([Row("700")])
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [])

    def temporarily_unload_rows(**_kwargs):
        page.rows = []
        page.restore_pending = True

    slots.values[0].click = temporarily_unload_rows

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=FixedRandom(),
    )

    assert result["page_state_restored"] is True
    assert page.restore_polls >= 26


def test_publish_form_exit_finds_close_control_outside_publish_frame():
    page = Page([Row("400")])
    close_button = CloseButton(page)
    publish_frame = Frame(
        f"https://example.test{collection_actions.PUBLISH_FRAME_FRAGMENT}"
    )
    shell_frame = Frame("https://example.test/shell", close_button)
    page.frames = [publish_frame, shell_frame]

    collection_actions._close_opened_action(page, publish_frame)

    assert close_button.clicked is True
    assert collection_actions._publish_frame_visible(page) is False


def test_publish_form_exit_clicks_backdrop_outside_form_first():
    page = Page([Row("500")])
    backdrop = Backdrop(page)
    publish_frame = Frame(
        f"https://example.test{collection_actions.PUBLISH_FRAME_FRAGMENT}",
        backdrop=backdrop,
    )
    page.frames = [publish_frame]

    collection_actions._close_opened_action(page, publish_frame)

    assert backdrop.clicked_positions == [{"x": 8, "y": 8}]
    assert page.keyboard.keys == []
    assert collection_actions._publish_frame_visible(page) is False


def test_publish_form_exit_clicks_parent_next_drawer_backdrop():
    page = Page([Row("600")])
    page.parent_backdrop = Backdrop(page)
    publish_frame = Frame(
        f"https://example.test{collection_actions.PUBLISH_FRAME_FRAGMENT}"
    )
    page.frames = [publish_frame]

    collection_actions._close_opened_action(page, publish_frame)

    assert page.parent_backdrop.clicked_positions == [{"x": 8, "y": 8}]
    assert page.keyboard.keys == []
    assert collection_actions._publish_frame_visible(page) is False


def test_publish_form_exit_clicks_parent_backdrop_before_frame_is_discovered():
    page = Page([Row("650")])
    page.parent_backdrop = Backdrop(page)

    collection_actions._close_opened_action(page, None)

    assert page.parent_backdrop.clicked_positions == [{"x": 8, "y": 8}]
    assert page.keyboard.keys == []


def test_random_page_interval_is_limited_to_one_or_two_pages():
    class UpperRandom(FixedRandom):
        def randint(self, _minimum, maximum):
            return maximum

    assert collection_actions.random_page_interval(FixedRandom()) == 1
    assert collection_actions.random_page_interval(UpperRandom()) == 2
