import json

import pytest

from upload_search_materials.deterministic_slot_planning import (
    STRATEGY_SHA256,
    balanced_slot_sizes,
    build_deterministic_slot_plan,
    choose_slot_ratio,
    stable_source_interleave,
)


def _asset(
    asset_id: str,
    *,
    order: int,
    folder: str,
    native_34: bool = True,
    native_11: bool = False,
    compression_34: bool = False,
):
    return {
        "asset_id": asset_id,
        "product_id": "P1",
        "source_sha256": asset_id.lower() * 64,
        "selection_order": order,
        "candidate_directory": folder,
        "ratio_options": {
            "3:4": {
                "feasible": True,
                "native_ratio": native_34,
                "recommended_resolution_pass": True,
                "retained_fraction": 0.9,
                "requires_compression": compression_34,
            },
            "1:1": {
                "feasible": True,
                "native_ratio": native_11,
                "recommended_resolution_pass": True,
                "retained_fraction": 0.7,
                "requires_compression": False,
            },
        },
    }


@pytest.mark.parametrize(
    ("count", "missing", "expected"),
    [
        (0, 10, []),
        (1, 10, []),
        (2, 10, []),
        (3, 10, [3]),
        (4, 10, [4]),
        (8, 10, [4, 4]),
        (9, 1, [9]),
        (9, 2, [5, 4]),
        (9, 3, [3, 3, 3]),
        (10, 10, [4, 3, 3]),
        (30, 2, [9, 9]),
    ],
)
def test_balanced_slot_sizes(count, missing, expected):
    assert balanced_slot_sizes(count, missing) == expected


def test_source_interleave_is_stable_and_deduplicates_sha():
    assets = [
        _asset("A", order=1, folder="one"),
        _asset("B", order=2, folder="one"),
        _asset("C", order=3, folder="two"),
        _asset("D", order=4, folder="two"),
        {**_asset("X", order=5, folder="three"), "source_sha256": "a" * 64},
    ]
    assert [
        item["asset_id"] for item in stable_source_interleave(reversed(assets))
    ] == ["A", "C", "B", "D"]


def test_ratio_uses_lexicographic_score_and_prefers_34_on_exact_tie():
    values = [
        _asset(
            letter,
            order=index,
            folder="one",
            native_34=False,
            native_11=False,
        )
        for index, letter in enumerate("ABC", start=1)
    ]
    ratio, _ = choose_slot_ratio(values)
    assert ratio == "3:4"


def test_plan_is_byte_stable_and_records_strategy_identity():
    assets = [
        _asset(letter, order=index, folder=str(index % 3))
        for index, letter in enumerate("ABCDEFGHI", start=1)
    ]
    board = {
        "asset_matching_revision": 4,
        "image_review_revision": 2,
        "products": [{"product_id": "P1", "assets": assets}],
    }
    first = build_deterministic_slot_plan(
        board, missing_slots_by_product={"P1": 3}
    )
    second = build_deterministic_slot_plan(
        board, missing_slots_by_product={"P1": 3}
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["strategy_sha256"] == STRATEGY_SHA256
    assert [len(item["asset_ids"]) for item in first["assignments"]] == [3, 3, 3]
    assert all(item["plan_source"] == "deterministic" for item in first["assignments"])


def test_ratio_score_penalizes_compression_after_native_and_retained_scores():
    values = [
        _asset(
            letter,
            order=index,
            folder="one",
            native_34=False,
            native_11=False,
            compression_34=True,
        )
        for index, letter in enumerate("ABC", start=1)
    ]
    for item in values:
        item["ratio_options"]["3:4"]["retained_fraction"] = 0.7
        item["ratio_options"]["1:1"]["retained_fraction"] = 0.7

    ratio, explanation = choose_slot_ratio(values)

    assert ratio == "1:1"
    assert explanation["compression_count"] == 0


def test_capacity_overflow_is_retained_in_unused_pool():
    assets = [
        _asset(str(index), order=index, folder=str(index % 2))
        for index in range(1, 21)
    ]
    plan = build_deterministic_slot_plan(
        {"products": [{"product_id": "P1", "assets": assets}]},
        missing_slots_by_product={"P1": 1},
    )

    assert [len(item["asset_ids"]) for item in plan["assignments"]] == [9]
    assert len(plan["unused_assets"]) == 11
    assert {
        item["reason_code"] for item in plan["unused_assets"]
    } == {"SLOT_CAPACITY_EXCEEDED"}


def test_incompatible_assets_return_to_unused_pool_when_no_complete_group():
    assets = [
        _asset(letter, order=index, folder="one")
        for index, letter in enumerate("AB", start=1)
    ]
    for item in assets:
        item["ratio_options"] = {}
    plan = build_deterministic_slot_plan(
        {"products": [{"product_id": "P1", "assets": assets}]},
        missing_slots_by_product={"P1": 3},
    )

    assert plan["assignments"] == []
    assert len(plan["unused_assets"]) == 2
