from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def test_skill_requires_ui_before_requesting_stage_one_business_inputs():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "不得在配置页启动前通过聊天索取店铺名、图片源名称或图片根目录" in skill
    assert "即使尚未配置店铺或图片源，交互页面也必须正常打开" in skill
    assert "只从当前会话经过校验的 setup `input.json`/`handoff.json` 读取这些值" in skill


def test_operations_guide_keeps_environment_and_setup_inputs_separate():
    guide = (SKILL_ROOT / "references" / "operations-guide.md").read_text(
        encoding="utf-8"
    )

    assert "环境检查是启动配置页前唯一允许的阻断" in guide
    assert "不得要求用户在聊天中提供店铺名、月份或图片根目录" in guide
    assert "缺少这些业务值不得阻止页面启动" in guide
