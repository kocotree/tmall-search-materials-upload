from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest
import yaml

from upload_search_materials.image_compliance import (
    MIB,
    default_image_policy,
)
from upload_search_materials.image_review import (
    build_image_review_data,
    materialize_review_decisions,
    migrate_legacy_suitability_to_selected_preflight,
    normalize_review_decisions,
    prepare_image_review_session,
    review_context_is_stale,
)
from upload_search_materials.interaction.session import SessionStore


def _candidate(source: Path, *, asset_id: str = "asset-a") -> dict:
    return {
        "asset_id": asset_id,
        "product_id": "P1",
        "product_title": "测试商品",
        "source_system": "test",
        "folder_id": "folder-1",
        "folder_path": str(source.parent),
        "candidate_directory": str(source.parent),
        "source_path": str(source),
    }


def _permissive_policy(**overrides) -> dict:
    return {
        **default_image_policy(),
        "min_size_kb": 0.001,
        **overrides,
    }


def test_build_review_reinspects_selected_assets_and_computes_both_boxes(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (3000, 4000), "red").save(source)
    data = build_image_review_data(
        [_candidate(source)],
        [{
            "asset_id": "asset-a",
            "product_id": "P1",
            "decision": "selected",
            "selection_order": 1,
        }],
        policy=_permissive_policy(),
        policy_sha256="policy",
        asset_matching_revision=4,
    )
    assert data["selected_count"] == data["reviewable_count"] == 1
    assert set(data["assets"][0]["crop_options"]) == {"3:4", "1:1"}
    assert data["assets"][0]["asset_matching_revision"] == 4
    assert data["assets"][0]["folder_id"] == "folder-1"
    assert data["assets"][0]["folder_path"] == str(tmp_path)
    assert data["assets"][0]["selection_order"] == 1
    assert len(data["assets"][0]["source_sha256"]) == 64
    assert data["capabilities"] == {
        "manual_crop": True,
        "ai_crop": False,
        "image_compression": True,
        "compression_provider": "pillow",
        "compression_provider_version": "1",
    }


def test_build_review_reads_and_decodes_source_once_for_both_ratios(
    tmp_path, monkeypatch
):
    import upload_search_materials.image_compliance as compliance

    source = tmp_path / "single-decode.jpg"
    Image.effect_noise((1800, 2400), 100).convert("RGB").save(
        source, quality=94
    )
    real_open = compliance.Image.open
    open_count = 0

    def counted_open(*args, **kwargs):
        nonlocal open_count
        open_count += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(compliance.Image, "open", counted_open)
    data = build_image_review_data(
        [_candidate(source)],
        [{
            "asset_id": "asset-a",
            "product_id": "P1",
            "decision": "selected",
            "selection_order": 1,
        }],
        policy=_permissive_policy(),
        policy_sha256="policy",
        asset_matching_revision=4,
    )

    asset = data["assets"][0]
    assert open_count == 1
    assert set(asset["crop_size_probes"]) == {"3:4", "1:1"}
    assert set(asset["preflight_performance"]["ratio_encode_ms"]) == {
        "3:4",
        "1:1",
    }
    assert asset["preflight_performance"]["read_ms"] >= 0
    assert asset["preflight_performance"]["decode_ms"] >= 0


def test_review_keeps_unreadable_asset_as_auditable_blocked_row(tmp_path):
    data = build_image_review_data(
        [_candidate(tmp_path / "missing.jpg")],
        [{"asset_id": "asset-a", "decision": "selected"}],
        policy=default_image_policy(),
        policy_sha256="policy",
        asset_matching_revision=1,
    )
    assert data["blocked_count"] == 1
    assert data["assets"][0]["status"] == "blocked"
    assert "SOURCE_UNREADABLE" in data["assets"][0]["reason_codes"]


def test_review_blocks_crop_that_cannot_reach_two_hundred_kib(tmp_path):
    source = tmp_path / "flat.jpg"
    Image.new("RGB", (1440, 1920), "white").save(source, quality=95)
    source.write_bytes(source.read_bytes() + (b"\0" * 300_000))

    data = build_image_review_data(
        [_candidate(source)],
        [{"asset_id": "asset-a", "decision": "selected"}],
        policy=default_image_policy(),
        policy_sha256="policy",
        asset_matching_revision=2,
    )

    asset = data["assets"][0]
    assert asset["status"] == "blocked"
    assert asset["crop_options"] == {}
    assert "OUTPUT_SIZE_BELOW_MINIMUM" in asset["reason_codes"]
    assert all(
        probe["output_size_bytes"] < 204_800
        and probe["minimum_size_bytes"] == 204_800
        for probe in asset["crop_size_probes"].values()
    )


def test_review_blocks_source_that_changed_after_selection(tmp_path):
    source = tmp_path / "changed.jpg"
    Image.effect_noise((1440, 1920), 100).convert("RGB").save(
        source, quality=94
    )
    candidate = _candidate(source)
    candidate["sha256"] = "0" * 64

    data = build_image_review_data(
        [candidate],
        [{
            "asset_id": "asset-a",
            "decision": "selected",
            "sha256": "0" * 64,
        }],
        policy=default_image_policy(),
        policy_sha256="policy",
        asset_matching_revision=2,
    )

    asset = data["assets"][0]
    assert asset["status"] == "blocked"
    assert asset["crop_options"] == {}
    assert "SOURCE_SHA_CHANGED" in asset["reason_codes"]



def test_normalize_decisions_requires_every_reviewable_asset(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (1000, 1500), "blue").save(source)
    data = build_image_review_data(
        [_candidate(source)],
        [{"asset_id": "asset-a", "decision": "selected"}],
        policy=_permissive_policy(),
        policy_sha256="policy",
        asset_matching_revision=1,
    )
    with pytest.raises(ValueError, match="missing decisions"):
        normalize_review_decisions([], data)
    decisions = normalize_review_decisions(
        [{
            "asset_id": "asset-a",
            "action": "crop",
            "target_ratio": "1:1",
            "crop_box": data["assets"][0]["crop_options"]["1:1"]["normalized"],
        }],
        data,
    )
    assert decisions[0]["crop_box"]["pixel"]["width"] == 1000


def test_materialize_crop_writes_only_to_derived_root(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), "green").save(source)
    original = source.read_bytes()
    data = build_image_review_data(
        [_candidate(source)],
        [{"asset_id": "asset-a", "decision": "selected"}],
        policy=_permissive_policy(),
        policy_sha256="policy",
        asset_matching_revision=1,
    )
    decisions = normalize_review_decisions(
        [{
            "asset_id": "asset-a",
            "action": "crop",
            "target_ratio": "3:4",
            "crop_box": data["assets"][0]["crop_options"]["3:4"]["normalized"],
        }],
        data,
    )
    output = materialize_review_decisions(
        decisions,
        data,
        derived_root=tmp_path / "run" / "04-image-review" / "derived",
    )[0]["output"]
    assert Path(output["output_path"]).is_file()
    assert output["kind"] == "crop"
    assert source.read_bytes() == original


def test_prepare_session_snapshots_policy_and_binds_current_revision(tmp_path):
    runs = tmp_path / "runs"
    store = SessionStore(runs)
    session = store.create_session()
    source = tmp_path / "source.jpg"
    Image.new("RGB", (1440, 1920), "white").save(source)
    handoff = store.save_input(
        session.session_id,
        "asset_matching",
        {
            "asset_decisions": [{
                "asset_id": "asset-a",
                "product_id": "P1",
                "decision": "selected",
                "selection_order": 1,
            }],
        },
    )
    store.write_result(
        session.session_id,
        "asset_matching",
        handoff["revision"],
        handoff["input_sha256"],
        status="completed",
        summary="done",
        data={"asset_candidates": [_candidate(source)]},
    )
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        yaml.safe_dump({"image": default_image_policy()}, sort_keys=False),
        encoding="utf-8",
    )
    context = prepare_image_review_session(
        store,
        session.session_id,
        policy_path=policy_path,
    )
    stage_path = store._stage_path(session.session_id, "image_review")
    assert (stage_path / "policy.snapshot.json").is_file()
    assert context["data"]["asset_matching_revision"] == 1
    assert store.load_session(session.session_id)["stages"]["image_review"][
        "status"
    ] == "needs_user_input"
    assert not review_context_is_stale(
        context,
        current_asset_matching_revision=1,
    )
    assert review_context_is_stale(
        context,
        current_asset_matching_revision=2,
    )

    legacy_state = store.load_session(session.session_id)
    legacy_state.pop("workflow_profile", None)
    legacy_state["current_stage"] = "image_review"
    store._write_session_state(session.session_id, legacy_state)
    store.write_review_context(
        session.session_id,
        "slots_copy",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "stage_id": "slots_copy",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "legacy three-page workflow",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "legacy",
            "data": {"workflow_state": "plan_review"},
        },
    )
    report = migrate_legacy_suitability_to_selected_preflight(
        store, session.session_id
    )
    migrated_state = store.load_session(session.session_id)
    assert report["current_stage"] == "asset_matching"
    assert migrated_state["current_stage"] == "asset_matching"
    assert migrated_state["workflow_profile"] == "deterministic-manual-v1"
    assert migrated_state["stages"]["asset_matching"]["status"] == "needs_user_input"
    assert (stage_path / "suitability.snapshot.json").is_file()
    assert Path(report["selected_asset_preflight"]).is_file()
    migrated_slots = store.read_optional_stage_document(
        session.session_id, "slots_copy", "review-context"
    )
    assert migrated_slots["data"]["two_page_workflow"] is True


def test_oversize_source_is_blocked_before_manual_crop(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (1440, 1920), "white").save(source)
    data = build_image_review_data(
        [_candidate(source)],
        [{"asset_id": "asset-a", "decision": "selected"}],
        policy=_permissive_policy(max_size_mb=0.01),
        policy_sha256="policy",
        asset_matching_revision=1,
    )
    assert data["assets"][0]["preflight"]["size_bytes"] > 0
    assert data["assets"][0]["preflight"]["status"] == "unusable"
    assert data["assets"][0]["preflight"]["selectable"] is False
    assert "IMAGE_SIZE_EXCEEDED" in data["assets"][0]["reason_codes"]
