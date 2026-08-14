from pathlib import Path

from upload_search_materials.decision_modes import DECISION_BOUNDARIES
from upload_search_materials.interaction.stages import STAGES


SKILL_ROOT = Path(__file__).parents[1]


def test_every_stage_has_a_documented_decision_boundary():
    boundary_doc = (
        SKILL_ROOT / "references" / "decision-boundaries.md"
    ).read_text(encoding="utf-8")
    registered_stages = {item.stage_id for item in DECISION_BOUNDARIES}

    assert registered_stages == {stage.id for stage in STAGES}
    for boundary in DECISION_BOUNDARIES:
        assert boundary.decision_id in boundary_doc
        assert boundary.default_mode in boundary.allowed_modes
        assert boundary.agent_allowed
        assert boundary.agent_forbidden
        assert boundary.fallback
        assert boundary.continue_when


def test_skill_documents_ai_as_advisory_and_production_as_manual_only():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    boundary_doc = (
        SKILL_ROOT / "references" / "decision-boundaries.md"
    ).read_text(encoding="utf-8")
    combined = skill + boundary_doc

    assert "decision-boundaries.md" in skill
    assert "AI 不参与选图、坑位数量、分组、顺序、比例、裁剪或压缩" in combined
    assert "不能触发 dry-run" in combined
    assert "`approval/exact_authorization`" in boundary_doc
    assert "`production_confirmation/production_write`" in boundary_doc
    assert boundary_doc.count("`manual_only`（默认）") >= 2
    assert "`production_confirmation/production_write` | 历史只读" in boundary_doc


def test_agent_request_recovery_is_bounded_and_has_safe_fallbacks():
    boundary_doc = (
        SKILL_ROOT / "references" / "decision-boundaries.md"
    ).read_text(encoding="utf-8")

    assert "wait-agent-request" in boundary_doc
    assert "--timeout 30" in boundary_doc
    assert "cancel-agent-request" in boundary_doc
    assert "重试 AI" in boundary_doc
    assert "人工编排" in boundary_doc
    assert "不得按目录新旧猜测任务" in boundary_doc


def test_slot_ui_exposes_ai_and_manual_only_with_processing_gate():
    javascript = (
        SKILL_ROOT
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "AI 编排坑位" in javascript
    assert "人工添加坑位" in javascript
    assert "添加候选图片" in javascript
    assert "确认坑位并进入图片裁剪" in javascript
    assert "/stages/slots_copy/use-rules" not in javascript
    assert "window.prompt(" not in javascript
