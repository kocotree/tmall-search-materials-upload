import json
import hashlib
from pathlib import Path

from PIL import Image
import pytest

from upload_search_materials.image_compliance import default_image_policy
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.slot_planning import (
    build_slot_board_data,
    prepare_slot_board_session,
    slot_context_is_stale,
    validate_slot_assignments,
)


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


def test_prepare_slot_board_binds_current_review_revision(tmp_path):
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
    context = prepare_slot_board_session(store, session.session_id)
    assert context["data"]["image_review_revision"] == 1
    assert store.load_session(session.session_id)["stages"]["slots_copy"][
        "status"
    ] == "needs_user_input"
    assert not slot_context_is_stale(
        context,
        current_image_review_revision=1,
    )
    assert slot_context_is_stale(
        context,
        current_image_review_revision=2,
    )
