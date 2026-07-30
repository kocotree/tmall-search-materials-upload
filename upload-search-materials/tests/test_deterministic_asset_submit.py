import json
from pathlib import Path

from PIL import Image
import pytest

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.web import create_app
from upload_search_materials.final_material_handoff import (
    process_final_material_handoff,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


@pytest.fixture
def client(tmp_path):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path,
    )
    return create_app(
        tmp_path, runtime_config=runtime, enforce_stage_order=False
    ).test_client()


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json["session_id"]


def test_selected_assets_submit_creates_one_final_handoff_then_codex_plans(
    client, session_id, tmp_path
):
    sources = tmp_path / "sources"
    sources.mkdir()
    candidates = []
    for index in range(9):
        source = sources / f"{index}.jpg"
        Image.effect_noise((1440, 1920), 100).convert("RGB").save(
            source, quality=94
        )
        candidates.append(
            {
                "asset_id": f"A{index}",
                "product_id": "P1",
                "product_title": "测试商品",
                "source_path": str(source),
                "source_system": f"folder-{index % 3}",
                "candidate_directory": f"folder-{index % 3}",
                "match_type": "exact_product_name",
                "validation_status": "valid",
                "preflight": {"selectable": True, "status": "direct"},
                "source_inspection": {
                    "size_bytes": source.stat().st_size,
                    "width": 1440,
                    "height": 1920,
                },
            }
        )

    first = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(sources)],
                "source_types": ["image"],
            }
        },
    )
    store = SessionStore(tmp_path)
    store.write_result(
        session_id,
        "asset_matching",
        first.json["revision"],
        first.json["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data={
            "requirements": [
                {"product_id": "P1", "missing_materials": 3}
            ],
            "asset_candidates": candidates,
        },
    )

    final_payload = {
        "request_id": "final-material-submit-1",
        "values": {
                "image_roots": [str(sources)],
                "source_types": ["image"],
                "asset_decisions": [
                    {
                        "asset_id": item["asset_id"],
                        "decision": "selected",
                    }
                    for item in candidates
                ],
            },
    }
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json=final_payload,
    )

    assert submitted.status_code == 202
    state = store.load_session(session_id)
    assert state["stages"]["asset_matching"]["status"] == "ready_for_agent"
    assert state["stages"]["image_review"]["revision"] == 0
    assert state["current_stage"] == "asset_matching"
    assert submitted.json["next_stage"] is None
    assert submitted.json["handoff_kind"] == "final_material_selection"
    plan_path = (
        store._stage_path(session_id, "slots_copy")
        / "current-slot-plan.json"
    )
    assert not plan_path.exists()
    handoff = store.read_optional_stage_document(
        session_id, "asset_matching", "handoff"
    )
    assert handoff["handoff_kind"] == "final_material_selection"
    retried = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json=final_payload,
    )
    assert retried.status_code == 202
    assert retried.json["revision"] == submitted.json["revision"]
    assert retried.json["input_sha256"] == submitted.json["input_sha256"]

    processed = process_final_material_handoff(store, session_id)

    assert processed["status"] == "completed"
    state = store.load_session(session_id)
    assert state["stages"]["asset_matching"]["status"] == "completed"
    assert state["current_stage"] == "slots_copy"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["decision_source"] == "deterministic"
    assert [len(item["asset_ids"]) for item in plan["slot_assignments"]] == [
        3,
        3,
        3,
    ]
    assert (
        store._stage_path(session_id, "asset_matching")
        / "selected-asset-preflight.json"
    ).is_file()

    slot_stage = store._stage_path(session_id, "slots_copy")
    for name in (
        "processed-outputs.json",
        "slot-plan.snapshot.json",
        "confirmed-copy-drafts.json",
    ):
        (slot_stage / name).write_text(
            '{"schema_version":1}', encoding="utf-8"
        )
    manual = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan",
        json={
            "plan_revision": plan["plan_revision"],
            "slot_assignments": list(reversed(plan["slot_assignments"])),
        },
    )
    assert manual.status_code == 200
    manual_revision = manual.json["current_slot_plan"]["plan_revision"]
    assert not (slot_stage / "processed-outputs.json").exists()
    assert (
        slot_stage
        / "invalidated"
        / f"manual-r{manual_revision:04d}"
        / "confirmed-copy-drafts.json"
    ).is_file()
    stale = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan/replan",
        json={"plan_revision": manual_revision - 1},
    )
    assert stale.status_code == 409
    replanned = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan/replan",
        json={"plan_revision": manual_revision},
    )
    assert replanned.status_code == 200
    assert (
        replanned.json["current_slot_plan"]["decision_source"]
        == "deterministic"
    )


@pytest.mark.parametrize(("image_count", "shortage"), [(1, 2), (2, 1)])
def test_one_or_two_images_are_rejected_with_exact_shortage(
    client, session_id, tmp_path, image_count, shortage
):
    sources = tmp_path / "short"
    sources.mkdir()
    candidates = []
    for index in range(image_count):
        source = sources / f"{index}.jpg"
        Image.effect_noise((1440, 1920), 100).convert("RGB").save(
            source, quality=94
        )
        candidates.append(
            {
                "asset_id": f"A{index}",
                "product_id": "P1",
                "source_path": str(source),
                "source_system": "folder",
                "match_type": "exact_product_name",
                "validation_status": "valid",
                "preflight": {"selectable": True, "status": "direct"},
                "source_inspection": {
                    "size_bytes": source.stat().st_size,
                    "width": 1440,
                    "height": 1920,
                },
            }
        )
    first = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(sources)],
                "source_types": ["image"],
            }
        },
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "asset_matching",
        first.json["revision"],
        first.json["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data={
            "requirements": [
                {"product_id": "P1", "missing_materials": 1}
            ],
            "asset_candidates": candidates,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(sources)],
                "source_types": ["image"],
                "asset_decisions": [
                    {"asset_id": item["asset_id"], "decision": "selected"}
                    for item in candidates
                ],
            }
        },
    )

    assert response.status_code == 422
    assert (
        f"还差 {shortage} 张"
        in response.json["field_errors"]["asset_decisions"]
    )


def test_three_images_create_one_complete_slot(client, session_id, tmp_path):
    sources = tmp_path / "three"
    sources.mkdir()
    candidates = []
    for index in range(3):
        source = sources / f"{index}.jpg"
        Image.effect_noise((1440, 1920), 100).convert("RGB").save(
            source, quality=94
        )
        candidates.append(
            {
                "asset_id": f"A{index}",
                "product_id": "P1",
                "source_path": str(source),
                "source_system": "folder",
                "match_type": "exact_product_name",
                "validation_status": "valid",
                "preflight": {"selectable": True, "status": "direct"},
                "source_inspection": {
                    "size_bytes": source.stat().st_size,
                    "width": 1440,
                    "height": 1920,
                },
            }
        )
    first = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(sources)],
                "source_types": ["image"],
            }
        },
    )
    store = SessionStore(tmp_path)
    store.write_result(
        session_id,
        "asset_matching",
        first.json["revision"],
        first.json["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data={
            "requirements": [
                {"product_id": "P1", "missing_materials": 3}
            ],
            "asset_candidates": candidates,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(sources)],
                "source_types": ["image"],
                "asset_decisions": [
                    {"asset_id": item["asset_id"], "decision": "selected"}
                    for item in candidates
                ],
            }
        },
    )

    assert response.status_code == 202
    assert not (
        store._stage_path(session_id, "slots_copy")
        / "current-slot-plan.json"
    ).exists()
    process_final_material_handoff(store, session_id)
    plan = json.loads(
        (
            store._stage_path(session_id, "slots_copy")
            / "current-slot-plan.json"
        ).read_text(encoding="utf-8")
    )
    assert [len(item["asset_ids"]) for item in plan["slot_assignments"]] == [3]
