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


class AuxiliaryPage:
    def __init__(self):
        self.closed = False
        self.waited = []

    def wait_for_timeout(self, milliseconds):
        self.waited.append(milliseconds)

    def close(self, **_kwargs):
        self.closed = True


class Context:
    def __init__(self, auxiliary):
        self.auxiliary = auxiliary

    def new_page(self):
        return self.auxiliary


class Page:
    def __init__(self, auxiliary):
        self.context = Context(auxiliary)


def common_stubs(monkeypatch, slots, empty_positions):
    monkeypatch.setattr(collection_actions, "_open_recommend_list", lambda *_args: None)
    monkeypatch.setattr(collection_actions, "_find_product_row", lambda *_args: object())
    monkeypatch.setattr(collection_actions, "_slot_cells", lambda _row: slots)
    monkeypatch.setattr(
        collection_actions,
        "_empty_slot_positions",
        lambda _row: list(empty_positions),
    )


def test_random_action_views_filled_slot_in_disposable_tab(monkeypatch):
    auxiliary = AuxiliaryPage()
    slots = Slots(2)
    common_stubs(monkeypatch, slots, [2])
    human_checks = []

    result = collection_actions.perform_random_collection_action(
        Page(auxiliary),
        ["100"],
        material_center_url="https://example.test/material-center",
        rng=FixedRandom(),
        wait_for_human_check=lambda _page, location: human_checks.append(location),
    )

    assert result == {
        "status": "completed",
        "action": "view_filled_slot",
        "product_id": "100",
        "slot_position": 1,
        "read_only": True,
    }
    assert slots.values[0].hovered is True
    assert slots.values[0].clicked is True
    assert auxiliary.closed is True
    assert human_checks == ["random_action_open", "random_action_after_open"]


def test_random_action_opens_empty_image_text_without_confirming(monkeypatch):
    auxiliary = AuxiliaryPage()
    slots = Slots(1)
    common_stubs(monkeypatch, slots, [1])
    opened = []
    monkeypatch.setattr(
        collection_actions,
        "_open_slot_publish_form",
        lambda page, product_id, position, **_kwargs: opened.append(
            (page, product_id, position)
        ),
    )

    result = collection_actions.perform_random_collection_action(
        Page(auxiliary),
        ["200"],
        material_center_url="https://example.test/material-center",
        rng=FixedRandom(),
    )

    assert result["action"] == "open_empty_image_text"
    assert opened == [(auxiliary, "200", 1)]
    assert auxiliary.closed is True


def test_random_action_safely_skips_without_browser_context():
    result = collection_actions.perform_random_collection_action(
        object(),
        ["100"],
        material_center_url="https://example.test/material-center",
    )

    assert result["reason_code"] == "RANDOM_ACTION_CONTEXT_UNAVAILABLE"


def test_random_page_interval_is_limited_to_one_or_two_pages():
    class UpperRandom(FixedRandom):
        def randint(self, _minimum, maximum):
            return maximum

    assert collection_actions.random_page_interval(FixedRandom()) == 1
    assert collection_actions.random_page_interval(UpperRandom()) == 2
