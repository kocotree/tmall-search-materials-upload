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
    assert "工作台后台按持久化 handoff" in skill
    assert "服务重启后扫描当前精确 session" in skill
    assert "Only unexpected paths that require new technical authority" in entry
    assert "single-session background dispatcher" in entry
    assert "without chat or command approval prompts" in root_agent
    assert "without chat or command approval prompts" in entry_agent


def test_normal_workbench_flow_forbids_source_rediscovery_until_diagnostic_error():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    entry = (
        SKILL_ROOT / "skills" / "upload-search-materials" / "SKILL.md"
    ).read_text(encoding="utf-8")
    contract = (
        SKILL_ROOT / "references" / "frontend-interaction-contract.md"
    ).read_text(encoding="utf-8")

    assert "正常流程禁止源码研究" in skill
    assert "持久化 `handoff_identity`" in skill
    assert "Do not search or read project source" in entry
    assert "不得用 CodeGraph、`rg`、`Get-Content`" in contract
    assert "agent-diagnostics/current.json" in contract


def test_normal_stages_use_workbench_dispatcher_without_agent_listener():
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    entry = (
        SKILL_ROOT / "skills" / "upload-search-materials" / "SKILL.md"
    ).read_text(encoding="utf-8")
    guide = (SKILL_ROOT / "references" / "operations-guide.md").read_text(
        encoding="utf-8"
    )

    assert "托管工作台为当前精确 session 启动单一后台调度器" in skill
    assert "服务启动时先扫描" in skill
    assert "不建立 `agent_wait`" in skill
    assert "不得要求用户在聊天输入“已提交”" in skill
    assert "Codex must not call `agent-wait`" in entry
    assert "Submission is atomically persisted" in entry
    assert "正常流程由托管工作台的单 session 后台调度器处理" in guide


def test_operations_guide_keeps_environment_and_setup_inputs_separate():
    guide = (SKILL_ROOT / "references" / "operations-guide.md").read_text(
        encoding="utf-8"
    )

    assert "环境检查是启动配置页前唯一允许的阻断" in guide
    assert "不得要求用户在聊天中提供店铺名、月份或图片根目录" in guide
    assert "缺少这些业务值不得阻止页面启动" in guide
    assert "不得在每个任务或阶段再次请求批准" in guide
