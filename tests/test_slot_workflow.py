import json

import pytest

from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.slot_workflow import (
    mark_manual_override,
    read_current_slot_plan,
    save_current_slot_plan,
    two_step_workflow_page,
    workflow_page,
)


def _board():
    outputs = [
        {
            "asset_id": f"asset-{index}",
            "product_id": "P1",
            "target_ratio": "3:4",
        }
        for index in range(4)
    ]
    return {
        "image_review_revision": 1,
        "policy_sha256": "policy",
        "slot_image_min": 3,
        "slot_image_max": 9,
        "products": [{"product_id": "P1", "outputs": outputs}],
    }


def _assignment():
    return {
        "slot_id": "P1-slot-1",
        "product_id": "P1",
        "target_ratio": "3:4",
        "asset_ids": ["asset-0", "asset-1", "asset-2"],
        "theme": "户外佩戴",
        "quantity_reason": "三张已经完整表达",
    }


def test_current_slot_plan_has_one_revisioned_draft_and_manual_audit(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    created = save_current_slot_plan(
        store,
        session.session_id,
        _board(),
        [_assignment()],
        decision_source="agent_assisted",
        context_revision=0,
        request_id="20260728-request",
        response_sha256="a" * 64,
    )
    edited = _assignment()
    edited["asset_ids"] = ["asset-1", "asset-2", "asset-3"]
    updated = mark_manual_override(
        store,
        session.session_id,
        _board(),
        [edited],
        context_revision=0,
        expected_plan_revision=created["plan_revision"],
    )

    assert updated["decision_source"] == "manual_override"
    assert updated["agent_request_id"] == "20260728-request"
    assert updated["plan_revision"] == 2
    assert len(updated["audit"]) == 2
    assert read_current_slot_plan(store, session.session_id) == updated


def test_current_slot_plan_rejects_exact_revision_conflict(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    save_current_slot_plan(
        store,
        session.session_id,
        _board(),
        [_assignment()],
        decision_source="rules",
        context_revision=0,
    )
    with pytest.raises(
        InteractionConflict, match="CURRENT_SLOT_PLAN_REVISION_STALE"
    ):
        mark_manual_override(
            store,
            session.session_id,
            _board(),
            [_assignment()],
            context_revision=0,
            expected_plan_revision=0,
        )


@pytest.mark.parametrize(
    "legacy_source",
    ["rules", "agent_assisted_with_rules_fallback"],
)
def test_historical_rule_draft_is_readable_and_edit_becomes_manual(
    tmp_path,
    legacy_source,
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    legacy = save_current_slot_plan(
        store,
        session.session_id,
        _board(),
        [_assignment()],
        decision_source=legacy_source,
        context_revision=0,
    )

    read_back = read_current_slot_plan(store, session.session_id)
    assert read_back["decision_source"] == legacy_source
    edited = mark_manual_override(
        store,
        session.session_id,
        _board(),
        [_assignment()],
        context_revision=0,
        expected_plan_revision=legacy["plan_revision"],
    )
    assert edited["decision_source"] == "manual"
    assert json.loads(
        (
            store._stage_path(session.session_id, "slots_copy")
            / "current-slot-plan.json"
        ).read_text(encoding="utf-8")
    )["decision_source"] == "manual"


@pytest.mark.parametrize(
    ("state", "page"),
    [
        ("analysing", "compose"),
        ("plan_review", "compose"),
        ("plan_confirmed", "process"),
        ("processing", "process"),
        ("outputs_ready", "copy"),
        ("copy_generating", "copy"),
        ("copy_review", "copy"),
        ("completed", "copy"),
    ],
)
def test_workflow_state_maps_to_three_step_page(state, page):
    assert workflow_page(state) == page


@pytest.mark.parametrize(
    ("state", "page"),
    [
        ("analysing", "process"),
        ("plan_review", "process"),
        ("plan_confirmed", "process"),
        ("processing", "process"),
        ("outputs_ready", "copy"),
        ("copy_generating", "copy"),
        ("copy_review", "copy"),
        ("completed", "copy"),
    ],
)
def test_historical_workflow_state_maps_to_two_step_page(state, page):
    assert two_step_workflow_page(state) == page
