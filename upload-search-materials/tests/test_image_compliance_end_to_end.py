import hashlib
import json
from pathlib import Path

from PIL import Image

from upload_search_materials.image_review import prepare_image_review_session
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.web import create_app
from upload_search_materials.slot_planning import prepare_slot_board_session


def _fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return (
        stat.st_size,
        stat.st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_stage_three_to_stage_five_dry_run_preserves_sources(tmp_path):
    runs = tmp_path / "runs"
    store = SessionStore(runs)
    session = store.create_session()
    source_root = tmp_path / "shared-source"
    source_root.mkdir()
    sources = []
    candidates = []
    selections = []
    for index in range(6):
        source = source_root / f"source-{index}.jpg"
        Image.effect_noise(
            (1200 + index * 8, 1600 + index * 8),
            100,
        ).convert("RGB").save(source, quality=95)
        asset_id = f"asset-{index}"
        sources.append(source)
        candidates.append({
            "asset_id": asset_id,
            "product_id": "P1",
            "product_title": "隔离测试商品",
            "source_system": "test",
            "source_path": str(source),
        })
        selections.append({
            "asset_id": asset_id,
            "product_id": "P1",
            "decision": "selected",
            "selection_order": index + 1,
        })
    source_fingerprints = {
        source.name: _fingerprint(source) for source in sources
    }

    matching_handoff = store.save_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(source_root)],
            "asset_decisions": selections,
        },
    )
    store.write_result(
        session.session_id,
        "asset_matching",
        matching_handoff["revision"],
        matching_handoff["input_sha256"],
        status="completed",
        summary="six images selected",
        data={"asset_candidates": candidates},
    )
    policy_path = (
        Path(__file__).parents[1] / "config" / "media-policy.example.yaml"
    )
    review_context = prepare_image_review_session(
        store,
        session.session_id,
        policy_path=policy_path,
    )
    assert review_context["data"]["reviewable_count"] == 6

    decisions = []
    for index, asset in enumerate(review_context["data"]["assets"]):
        ratio = "1:1" if index < 3 else "3:4"
        decisions.append({
            "asset_id": asset["asset_id"],
            "action": "crop",
            "target_ratio": ratio,
            "crop_box": asset["crop_options"][ratio]["normalized"],
        })
    client = create_app(runs, enforce_stage_order=True).test_client()
    review_submit = client.post(
        f"/api/sessions/{session.session_id}/stages/image_review/submit",
        json={
            "revision": 1,
            "values": {"decisions": decisions, "user_notes": ""},
        },
    )
    assert review_submit.status_code == 202, review_submit.json
    review_input = store.read_optional_stage_document(
        session.session_id, "image_review", "input"
    )
    assert all(
        "output" not in decision
        for decision in review_input["values"]["decisions"]
    )
    assert not (
        store._stage_path(session.session_id, "image_review") / "derived"
    ).exists()

    review_handoff = store.read_optional_stage_document(
        session.session_id, "image_review", "handoff"
    )
    store.write_result(
        session.session_id,
        "image_review",
        review_handoff["revision"],
        review_handoff["input_sha256"],
        status="completed",
        summary="six derivatives confirmed",
    )
    board = prepare_slot_board_session(store, session.session_id)
    assert board["blocking_reasons"] == []
    slot_assignments = [
        {
            "slot_id": "slot-square",
            "product_id": "P1",
            "target_ratio": "1:1",
            "asset_ids": ["asset-0", "asset-1", "asset-2"],
        },
        {
            "slot_id": "slot-portrait",
            "product_id": "P1",
            "target_ratio": "3:4",
            "asset_ids": ["asset-3", "asset-4", "asset-5"],
        },
    ]
    process_response = client.post(
        f"/api/sessions/{session.session_id}/stages/slots_copy/process-plan",
        json={"slot_assignments": slot_assignments},
    )
    assert process_response.status_code == 200, process_response.json
    slot_submit = client.post(
        f"/api/sessions/{session.session_id}/stages/slots_copy/submit",
        json={
            "revision": 1,
            "values": {
                "slot_assignments": slot_assignments,
                "copy_edits": [
                    {
                        "slot_id": "slot-square",
                        "product_id": "P1",
                        "title": "square dry-run",
                        "description": "square description",
                        "confirmed": True,
                    },
                    {
                        "slot_id": "slot-portrait",
                        "product_id": "P1",
                        "title": "portrait dry-run",
                        "description": "portrait description",
                        "confirmed": True,
                    },
                ],
                "user_notes": "",
            },
        },
    )
    assert slot_submit.status_code == 202
    slot_input = store.read_optional_stage_document(
        session.session_id, "slots_copy", "input"
    )
    assert [
        assignment["target_ratio"]
        for assignment in slot_input["values"]["slot_assignments"]
    ] == ["1:1", "3:4"]
    processed = json.loads(
        (
            store._stage_path(session.session_id, "slots_copy")
            / "processed-outputs.json"
        ).read_text(encoding="utf-8")
    )
    derived_root = (
        store._stage_path(session.session_id, "slots_copy") / "derived"
    )
    assert processed["workflow_state"] == "outputs_ready"
    assert all(
        Path(output["output_path"]).is_relative_to(derived_root)
        and Path(output["output_path"]).is_file()
        for slot in processed["slots"]
        for output in slot["outputs"]
    )
    assert all(
        source_fingerprints[source.name] == _fingerprint(source)
        for source in sources
    )
    assert not any(source_root.glob("derived*"))
    json.dumps(slot_input, ensure_ascii=False)
