import json
import hashlib
from pathlib import Path

from PIL import Image
import pytest

from upload_search_materials.image_compliance import default_image_policy
from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.slot_planning import (
    build_slot_board_data,
    materialize_confirmed_slot_plan,
    normalize_product_assets,
    prepare_slot_board_session,
    slot_context_is_stale,
    validate_slot_assignments,
)
from upload_search_materials.io_tables import sha256_file


def _decision(
    asset_id,
    root: Path,
    *,
    ratio="3:4",
    product_id="P1",
    valid_identity=True,
):
    output_path = root / f"{asset_id}.jpg"
    dimensions = (900, 1200) if ratio == "3:4" else (900, 900)
    Image.effect_noise(dimensions, 100).convert("RGB").save(
        output_path,
        format="JPEG",
        quality=95,
    )
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        "asset_id": asset_id,
        "product_id": product_id,
        "action": "crop",
        "target_ratio": ratio,
        "output": {
            "output_path": str(output_path),
            "output_sha256": digest if valid_identity else "0" * 64,
            "output_width": dimensions[0],
            "output_height": dimensions[1],
            "output_size_bytes": output_path.stat().st_size,
            "target_ratio": ratio,
            "kind": "crop",
        },
    }


def test_slot_board_only_exposes_confirmed_outputs_with_valid_identity(tmp_path):
    data = build_slot_board_data(
        [
            _decision("A", tmp_path),
            _decision("B", tmp_path),
            _decision("C", tmp_path),
            _decision("D", tmp_path, valid_identity=False),
            {"asset_id": "E", "product_id": "P1", "action": "excluded"},
        ],
        image_review_revision=3,
        policy=default_image_policy(),
    )
    assert [item["asset_id"] for item in data["products"][0]["outputs"]] == [
        "A", "B", "C"
    ]
    assert data["blocked_outputs"] == [{
        "asset_id": "D",
        "reason_code": "OUTPUT_IDENTITY_MISMATCH",
    }]


def test_slot_assignment_requires_three_to_nine_same_ratio_images(tmp_path):
    data = build_slot_board_data(
        [
            _decision("A", tmp_path),
            _decision("B", tmp_path),
            _decision("C", tmp_path),
            _decision("D", tmp_path, ratio="1:1"),
        ],
        image_review_revision=2,
        policy=default_image_policy(),
    )
    valid = validate_slot_assignments(
        [{
            "slot_id": "slot-1",
            "product_id": "P1",
            "asset_ids": ["A", "B", "C"],
        }],
        data,
    )
    assert valid[0]["target_ratio"] == "3:4"
    assert valid[0]["image_review_revision"] == 2
    with pytest.raises(ValueError, match="IMAGE_COUNT_INVALID"):
        validate_slot_assignments(
            [{
                "slot_id": "slot-1",
                "product_id": "P1",
                "asset_ids": ["A", "B"],
            }],
            data,
        )
    with pytest.raises(ValueError, match="MIXED_ASPECT_RATIO"):
        validate_slot_assignments(
            [{
                "slot_id": "slot-1",
                "product_id": "P1",
                "asset_ids": ["A", "B", "D"],
            }],
            data,
        )


def test_slot_assignment_rejects_stale_unconfirmed_and_cross_product_assets(tmp_path):
    data = build_slot_board_data(
        [
            _decision("A", tmp_path),
            _decision("B", tmp_path),
            _decision("C", tmp_path, product_id="P2"),
            _decision("D", tmp_path),
        ],
        image_review_revision=2,
        policy=default_image_policy(),
    )
    with pytest.raises(ValueError, match="another product"):
        validate_slot_assignments(
            [{
                "slot_id": "slot-1",
                "product_id": "P1",
                "asset_ids": ["A", "B", "C"],
            }],
            data,
        )
    with pytest.raises(ValueError, match="unconfirmed or stale"):
        validate_slot_assignments(
            [{
                "slot_id": "slot-1",
                "product_id": "P1",
                "asset_ids": ["A", "B", "UNKNOWN"],
            }],
            data,
        )


def test_ratio_candidates_are_normalized_with_original_metadata():
    assets = normalize_product_assets([
        {
            "asset_id": "A",
            "product_id": "P1",
            "source_sha256": "a" * 64,
            "width": 1000,
            "height": 1500,
            "size_bytes": 1_258_291,
            "format": "JPEG",
            "target_ratio": ratio,
            "crop_box": {"normalized": [0, 0, 1, 1]},
            "native_ratio": False,
        }
        for ratio in ("1:1", "3:4")
    ])
    assert len(assets) == 1
    assert assets[0]["original_ratio"] == "2:3"
    assert assets[0]["size_bytes"] == 1_258_291
    assert set(assets[0]["ratio_options"]) == {"1:1", "3:4"}


def test_missing_historical_metadata_is_backfilled_from_only_its_source(
    tmp_path,
):
    source = tmp_path / "historical.png"
    Image.new("RGB", (1000, 1500), "orange").save(source)
    assets = normalize_product_assets([
        {
            "asset_id": "A",
            "product_id": "P1",
            "source_path": str(source),
            "source_sha256": sha256_file(source),
            "target_ratio": "3:4",
            "crop_box": {"normalized": [0.0, 0.0, 1.0, 1.0]},
        }
    ])

    assert assets[0]["width"] == 1000
    assert assets[0]["height"] == 1500
    assert assets[0]["original_ratio"] == "2:3"
    assert assets[0]["size_bytes"] == source.stat().st_size
    assert assets[0]["size_display"].endswith("KB")
    assert assets[0]["format"] == "PNG"


def test_incomplete_draft_is_allowed_but_duplicate_cross_slot_asset_is_not(
    tmp_path,
):
    data = build_slot_board_data(
        [
            _decision("A", tmp_path),
            _decision("B", tmp_path),
            _decision("C", tmp_path),
        ],
        image_review_revision=2,
        policy=default_image_policy(),
    )
    incomplete = validate_slot_assignments(
        [{
            "slot_id": "slot-1",
            "product_id": "P1",
            "target_ratio": "3:4",
            "asset_ids": ["A"],
        }],
        data,
        allow_incomplete=True,
    )
    assert incomplete[0]["asset_ids"] == ["A"]
    with pytest.raises(ValueError, match="ASSET_ASSIGNED_TO_MULTIPLE_SLOTS"):
        validate_slot_assignments(
            [
                {
                    "slot_id": "slot-1",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["A"],
                },
                {
                    "slot_id": "slot-2",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["A"],
                },
            ],
            data,
            allow_incomplete=True,
        )


def test_legacy_stage_four_outputs_require_explicit_migration(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "image_review",
        {
            "decisions": [
                _decision("A", tmp_path),
                _decision("B", tmp_path),
                _decision("C", tmp_path),
            ]
        },
    )
    stage_path = store._stage_path(session.session_id, "image_review")
    (stage_path / "policy.snapshot.json").write_text(
        json.dumps(default_image_policy()),
        encoding="utf-8",
    )
    store.write_result(
        session.session_id,
        "image_review",
        handoff["revision"],
        handoff["input_sha256"],
        status="completed",
        summary="done",
    )
    with pytest.raises(
        InteractionConflict,
        match="legacy stage-four outputs require explicit migration",
    ):
        prepare_slot_board_session(store, session.session_id)


def test_post_plan_crop_change_reprocesses_only_changed_image_identity(tmp_path):
    policy = default_image_policy()
    outputs = []
    for index in range(3):
        source = tmp_path / f"source-{index}.jpg"
        Image.effect_noise((1600, 2000), 70 + index).convert("RGB").save(
            source, format="JPEG", quality=95
        )
        outputs.append(
            {
                "asset_id": f"A{index}",
                "product_id": "P1",
                "target_ratio": "3:4",
                "source_path": str(source),
                "source_sha256": sha256_file(source),
                "crop_box": {
                    "normalized": [0.03125, 0.0, 0.96875, 1.0]
                },
                "native_ratio": False,
                "requires_compression": False,
            }
        )
    board = {
        "image_review_revision": 2,
        "policy_sha256": "policy",
        "policy": policy,
        "slot_image_min": 3,
        "slot_image_max": 9,
        "products": [{"product_id": "P1", "outputs": outputs}],
    }
    assignments = [{
        "slot_id": "slot-1",
        "product_id": "P1",
        "target_ratio": "3:4",
        "asset_ids": ["A0", "A1", "A2"],
    }]
    first = materialize_confirmed_slot_plan(
        assignments, board, derived_root=tmp_path / "derived-1"
    )
    second = materialize_confirmed_slot_plan(
        assignments,
        board,
        derived_root=tmp_path / "derived-2",
        crop_parameters={
            "slot-1:A0": {
                "normalized_box": [0.0, 0.0, 0.9375, 1.0],
                "confirm_compression": True,
            }
        },
    )
    first_outputs = first["slots"][0]["outputs"]
    second_outputs = second["slots"][0]["outputs"]

    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["processing_sha256"] != second["processing_sha256"]
    assert first_outputs[0]["output_sha256"] != second_outputs[0]["output_sha256"]
    assert [item["output_sha256"] for item in first_outputs[1:]] == [
        item["output_sha256"] for item in second_outputs[1:]
    ]
