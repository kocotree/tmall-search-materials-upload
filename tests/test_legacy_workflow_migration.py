import json

import pytest

from upload_search_materials.interaction.session import (
    CURRENT_WORKFLOW_PROFILE,
    LEGACY_WORKFLOW_MIGRATION_ID,
    SessionStore,
)
from upload_search_materials.interaction.web import create_app
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig
from upload_search_materials.slot_workflow import two_step_workflow_page


def _make_legacy_session(tmp_path, *, image_status, with_slot_context):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    session_id = session.session_id
    state_path = store._session_path(session_id) / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("workflow_profile")
    state["current_stage"] = "image_review"
    state["stages"]["asset_matching"]["revision"] = 7
    state["stages"]["asset_matching"]["status"] = "completed"
    state["stages"]["image_review"]["revision"] = 9
    state["stages"]["image_review"]["status"] = image_status
    store._write_json_atomic(state_path, state)

    legacy_data = {
        "schema_version": 1,
        "record_type": "image_review_data",
        "asset_matching_revision": 7,
        "policy_sha256": "a" * 64,
        "selected_count": 3,
        "reviewable_count": 3,
        "blocked_count": 0,
        "duplicate_count": 0,
        "assets": [
            {
                "asset_id": f"asset-{index}",
                "product_id": "P1",
                "status": "reviewable",
                "source_sha256": f"{index + 1:064x}",
            }
            for index in range(3)
        ],
    }
    image_context_path = (
        store._stage_path(session_id, "image_review") / "review-context.json"
    )
    store._write_json_atomic(
        image_context_path,
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "image_review",
            "revision": 9,
            "status": "needs_user_input",
            "data": legacy_data,
        },
    )
    if with_slot_context:
        store._write_json_atomic(
            store._stage_path(session_id, "slots_copy")
            / "review-context.json",
            {
                "schema_version": 1,
                "session_id": session_id,
                "stage_id": "slots_copy",
                "revision": 4,
                "status": "needs_user_input",
                "data": {"products": [], "rule_drafts": []},
            },
        )
    return store, session_id, image_context_path


def test_completed_historical_image_review_maps_to_two_page_slots(tmp_path):
    store, session_id, image_context_path = _make_legacy_session(
        tmp_path,
        image_status="completed",
        with_slot_context=True,
    )
    original_image_context = image_context_path.read_bytes()

    migrated = store.load_session(session_id)

    assert migrated["workflow_profile"] == CURRENT_WORKFLOW_PROFILE
    assert migrated["current_stage"] == "slots_copy"
    assert migrated["workflow_migration"] == {
        "migration_id": LEGACY_WORKFLOW_MIGRATION_ID,
        "original_current_stage": "image_review",
        "mapped_current_stage": "slots_copy",
        "legacy_image_review_status": "completed",
        "selected_asset_preflight_migrated": True,
        "legacy_files_preserved": True,
    }
    assert image_context_path.read_bytes() == original_image_context

    preflight = json.loads(
        (
            store._stage_path(session_id, "asset_matching")
            / "selected-asset-preflight.json"
        ).read_text(encoding="utf-8")
    )
    assert preflight["stage_id"] == "asset_matching"
    assert preflight["revision"] == 7
    assert preflight["data"]["assets"][0]["asset_id"] == "asset-0"
    assert preflight["migration"]["source_stage_id"] == "image_review"

    slot_context = json.loads(
        (
            store._stage_path(session_id, "slots_copy")
            / "review-context.json"
        ).read_text(encoding="utf-8")
    )
    assert slot_context["data"]["two_page_workflow"] is True
    assert slot_context["migration"]["legacy_page_count"] == 3

    state_path = store._session_path(session_id) / "session.json"
    first_migration = state_path.read_bytes()
    assert store.load_session(session_id) == migrated
    assert state_path.read_bytes() == first_migration


def test_incomplete_historical_image_review_returns_to_asset_selection(tmp_path):
    store, session_id, image_context_path = _make_legacy_session(
        tmp_path,
        image_status="needs_user_input",
        with_slot_context=False,
    )

    migrated = store.load_session(session_id)

    assert migrated["current_stage"] == "asset_matching"
    assert (
        migrated["stages"]["asset_matching"]["status"]
        == "needs_user_input"
    )
    assert image_context_path.is_file()
    assert (
        store._stage_path(session_id, "asset_matching")
        / "selected-asset-preflight.json"
    ).is_file()
    events = [
        json.loads(line)
        for line in (
            store._session_path(session_id) / "events.ndjson"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "workflow_migrated"
    assert events[-1]["mapped_current_stage"] == "asset_matching"


def test_migrated_browser_hides_legacy_stage_and_reports_two_page_mode(tmp_path):
    store, session_id, _ = _make_legacy_session(
        tmp_path,
        image_status="completed",
        with_slot_context=True,
    )
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=store.runs_root,
    )
    client = create_app(
        store.runs_root,
        runtime_config=runtime,
        enforce_stage_order=False,
    ).test_client()

    page = client.get(f"/?session_id={session_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-stage-id="image_review"' not in html
    assert html.count('data-stage-id="') == 7

    plan = client.get(
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    assert plan.status_code == 200
    assert plan.json["ai_default"] is False
    assert plan.json["three_step_ui"] is False
    assert plan.json["workflow_page"] == "process"


@pytest.mark.parametrize(
    ("workflow_state", "expected_page"),
    [
        ("plan_review", "process"),
        ("plan_confirmed", "process"),
        ("outputs_ready", "copy"),
        ("copy_review", "copy"),
        ("completed", "copy"),
    ],
)
def test_historical_durable_state_recovers_to_two_page_workflow(
    workflow_state, expected_page
):
    assert two_step_workflow_page(workflow_state) == expected_page
