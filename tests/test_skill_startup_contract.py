from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def test_skill_requires_ui_before_requesting_stage_one_business_inputs():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "不得在配置页启动前通过聊天索取店铺名、图片源名称或图片根目录" in skill
    assert "即使尚未配置店铺或图片源，交互页面也必须正常打开" in skill
    assert "只从当前会话经过校验的 setup `input.json`/`handoff.json` 读取这些值" in skill


def test_every_user_interaction_reminder_includes_exact_workbench_url():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    entry = (
        SKILL_ROOT / "skills" / "upload-search-materials" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "每次在聊天中提醒用户执行任何交互操作前" in skill
    assert "ui-status --runs-root <精确 runs-root> --session <精确 session-id>" in skill
    assert "同一条提醒中提供可点击的工作台链接" in skill
    assert "Before every user-interaction reminder" in entry
    assert "Never hardcode a port" in entry


def test_first_shared_snapshot_does_not_require_second_verbal_authorization():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    entry = (
        SKILL_ROOT / "skills" / "upload-search-materials" / "SKILL.md"
    ).read_text(encoding="utf-8")
    index_skill = (
        SKILL_ROOT / "skills" / "maintain-team-folder-index" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "自动发布缺失的第一份有效快照，无需再次索取口头授权" in skill
    assert "without asking for a second verbal authorization" in entry
    assert "without requesting a second verbal authorization" in index_skill


def test_normal_workbench_flow_does_not_request_duplicate_host_approval():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    entry = (
        SKILL_ROOT / "skills" / "upload-search-materials" / "SKILL.md"
    ).read_text(encoding="utf-8")
    root_agent = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )
    entry_agent = (
        SKILL_ROOT / "skills" / "upload-search-materials" / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert "正常业务流与宿主审批边界" in skill
    assert "不得为它们生成命令审批卡" in skill
    assert "读取目录元数据并生成文件夹候选" in skill
    assert "不得再追加聊天确认或命令确认" in skill
    assert "Only unexpected paths that require new technical authority" in entry
    assert "without chat or command approval prompts" in root_agent
    assert "without chat or command approval prompts" in entry_agent


def test_operations_guide_keeps_environment_and_setup_inputs_separate():
    guide = (SKILL_ROOT / "references" / "operations-guide.md").read_text(
        encoding="utf-8"
    )

    assert "环境检查是启动配置页前唯一允许的阻断" in guide
    assert "不得要求用户在聊天中提供店铺名、月份或图片根目录" in guide
    assert "缺少这些业务值不得阻止页面启动" in guide
    assert "不得在每个任务或阶段再次请求批准" in guide
