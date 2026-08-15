import json

import pytest

from upload_search_materials.decision_modes import (
    DECISION_BOUNDARIES,
    DECISION_MODES,
    get_decision_boundary,
)
from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.interaction.stages import STAGES


def test_every_stage_has_a_complete_decision_boundary():
    stage_ids = {stage.id for stage in STAGES}
    covered = {item.stage_id for item in DECISION_BOUNDARIES}
    assert covered == stage_ids
    for item in DECISION_BOUNDARIES:
        assert item.default_mode in item.allowed_modes
        assert set(item.allowed_modes) <= DECISION_MODES
        assert item.user_confirmation
        assert item.agent_allowed
        assert item.agent_forbidden
        assert item.fallback
        assert item.continue_when


def test_new_session_uses_deterministic_default_slot_planning(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()

    value = store.read_decision_mode(
        session.session_id, "slots_copy", "slot_plan"
    )

    assert value["mode"] == "deterministic"
    assert value["selected_by"] == "skill_default"
    assert value["persisted"] is False
    recovery = store.recovery_instruction(
        session.session_id, "slots_copy"
    )
    assert "Do not wait for, claim, retry, or create" in recovery
    assert "declared agent-request endpoints" in recovery
    assert "current-slot-plan.json" not in recovery


def test_explicit_decision_mode_is_revision_bound_and_audited(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()

    written = store.write_decision_mode(
        session.session_id,
        "slots_copy",
        "slot_plan",
        "agent_assisted",
        selected_by="user",
        expected_revision=0,
    )

    assert written["mode"] == "agent_assisted"
    assert written["bound_revision"] == 0
    assert store.read_decision_mode(
        session.session_id, "slots_copy", "slot_plan"
    ) == written
    events = [
        json.loads(line)
        for line in (session.path / "events.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "decision_mode_selected"
    assert events[-1]["mode"] == "agent_assisted"


def test_decision_mode_rejects_disallowed_mode_and_stale_revision(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()

    with pytest.raises(InteractionConflict, match="NOT_ALLOWED"):
        store.write_decision_mode(
            session.session_id,
            "approval",
            "exact_authorization",
            "agent_assisted",
            selected_by="user",
            expected_revision=0,
        )
    with pytest.raises(InteractionConflict, match="REVISION_STALE"):
        store.write_decision_mode(
            session.session_id,
            "slots_copy",
            "slot_plan",
            "manual",
            selected_by="user",
            expected_revision=1,
        )


def test_unknown_decision_is_rejected():
    with pytest.raises(KeyError):
        get_decision_boundary("slots_copy", "unknown")
