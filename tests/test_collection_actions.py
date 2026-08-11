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

    def hover(self, **_kwargs):
        self.hovered = True

    def click(self, **_kwargs):
        self.clicked = True


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

    def locator(self, selector):
        if selector == ".promotion-row":
            return LocatorList(self.rows)
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
    assert page.context.pages == [page]
    assert page.keyboard.keys == ["Escape"]
    assert human_checks == ["random_action_after_open"]


def test_random_action_opens_current_empty_slot_without_searching(monkeypatch):
    row = Row("200")
    page = Page([row])
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [1])
    opened = []
    monkeypatch.setattr(
        collection_actions,
        "_open_slot_publish_form",
        lambda target, product_id, position, **kwargs: opened.append(
            (target, product_id, position, kwargs["row"])
        ),
    )

    result = collection_actions.perform_random_collection_action(
        page,
        rows_selector=".promotion-row",
        rng=FixedRandom(),
    )

    assert result["action"] == "open_empty_image_text"
    assert result["page_state_restored"] is True
    assert opened == [(page, "200", 1, row)]


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


def test_random_page_interval_is_limited_to_one_or_two_pages():
    class UpperRandom(FixedRandom):
        def randint(self, _minimum, maximum):
            return maximum

    assert collection_actions.random_page_interval(FixedRandom()) == 1
    assert collection_actions.random_page_interval(UpperRandom()) == 2
