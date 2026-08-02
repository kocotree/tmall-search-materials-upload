"""Human, deterministic-rule, and Codex-assisted decision boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DECISION_MODES = frozenset(
    {"manual", "rules", "deterministic", "agent_assisted", "manual_only"}
)
DECISION_REASON_CODES = frozenset(
    {
        "AGENT_THUMBNAIL_UNAVAILABLE",
        "AGENT_IMAGE_BUDGET_EXCEEDED",
        "AGENT_CANDIDATE_IDENTITY_INCOMPLETE",
        "AGENT_REQUEST_STALE",
        "AGENT_REQUEST_CANCELLED",
        "AGENT_REQUEST_SUPERSEDED",
        "AGENT_RESPONSE_INVALID",
        "AGENT_SLOT_PLAN_HARD_RULE_REJECTED",
        "DECISION_MODE_NOT_ALLOWED",
        "DECISION_REVISION_STALE",
    }
)


@dataclass(frozen=True)
class DecisionBoundary:
    stage_id: str
    decision_id: str
    allowed_modes: tuple[str, ...]
    default_mode: str
    input_summary: str
    output_summary: str
    user_confirmation: str
    agent_allowed: tuple[str, ...]
    agent_forbidden: tuple[str, ...]
    fallback: str
    continue_when: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DECISION_BOUNDARIES: tuple[DecisionBoundary, ...] = (
    DecisionBoundary(
        "setup",
        "task_configuration",
        ("manual_only",),
        "manual_only",
        "店铺、月份、图片源和自动发现文件",
        "经用户确认的任务配置",
        "用户确认店铺身份和配置",
        ("探测默认值", "校验路径", "启动配置页"),
        ("代替用户确认店铺", "跳过配置页"),
        "保留草稿并等待用户修正",
        "配置提交且店铺身份已确认",
    ),
    DecisionBoundary(
        "completeness",
        "product_selection",
        ("rules", "manual"),
        "rules",
        "搜推高价值采集结果和硬编码排除词",
        "进入素材匹配的商品清单",
        "用户确认或调整商品清单",
        ("采集商品", "应用排除规则", "准备选择页"),
        ("自动确认用户未选择的商品",),
        "保留规则结果并允许人工调整",
        "商品清单已提交",
    ),
    DecisionBoundary(
        "asset_matching",
        "asset_selection",
        ("manual", "rules"),
        "manual",
        "商品、文件夹候选和图片候选",
        "已确认文件夹与已采用图片",
        "用户采用或排除文件夹和图片",
        ("检索文件夹", "按比例抽样候选", "执行重复检测"),
        ("自行采用未展示图片", "把名称近似当成确认归属"),
        "保留当前选择并允许换一批",
        "每个商品的选择通过硬规则并提交",
    ),
    DecisionBoundary(
        "image_review",
        "suitability_review",
        ("rules", "manual"),
        "rules",
        "已选图片、尺寸、比例、体积和双比例裁剪框",
        "适用性候选与排除决定",
        "用户确认排除项和必要的人工裁剪框",
        ("执行合规检测", "计算裁剪候选", "标注压缩需要"),
        ("提前确定坑位比例", "生成正式派生图片"),
        "保留合规结果并允许人工覆盖",
        "适用性决定提交",
    ),
    DecisionBoundary(
        "slots_copy",
        "slot_plan",
        ("deterministic", "manual", "agent_assisted"),
        "deterministic",
        "已选图片预检、后台缺失坑位、选择顺序和来源文件夹",
        "统一比例、3–9张有序图片的坑位草稿",
        "用户检查或人工调整并确认计划后才处理图片",
        ("运行确定性编排器", "解释数量和比例依据", "保留人工草稿"),
        ("创建新坑位AI请求", "自动确认计划", "自动处理图片", "进入dry-run"),
        "保留当前草稿或空状态并允许人工编排",
        "用户确认计划、输出通过校验且文案确认",
    ),
    DecisionBoundary(
        "dry_run",
        "dry_run_review",
        ("rules", "manual_only"),
        "rules",
        "全部拟执行任务和校验结果",
        "可审查的dry-run与警告清单",
        "用户确认dry-run或返回修改",
        ("生成dry-run", "解释阻断和警告"),
        ("代替用户接受警告", "创建生产批准"),
        "返回前序阶段修改",
        "用户确认dry-run",
    ),
    DecisionBoundary(
        "approval",
        "exact_authorization",
        ("manual_only",),
        "manual_only",
        "精确任务、商品、坑位和清单哈希",
        "带批准人的精确任务授权",
        "用户显式选择任务并确认授权",
        ("校验清单", "展示差异"),
        ("扩大批准范围", "复用一般性确认"),
        "等待用户重新授权",
        "精确授权有效且未过期",
    ),
    DecisionBoundary(
        "production_confirmation",
        "production_write",
        ("manual_only",),
        "manual_only",
        "店铺、商品、坑位、任务和批准哈希",
        "一次受限生产执行交接",
        "用户最终确认生产写入",
        ("核对身份", "执行已批准任务", "记录结果"),
        ("更换店铺", "增加任务", "跳过最终确认"),
        "停止生产并保留可恢复状态",
        "最终确认与批准清单完全一致",
    ),
    DecisionBoundary(
        "results",
        "result_recovery",
        ("rules", "manual"),
        "rules",
        "生产结果、远端回执和异常原因",
        "结果报告或受控恢复决定",
        "仅异常要求人工处理时由用户选择恢复动作",
        ("汇总结果", "验证幂等身份", "提出恢复选项"),
        ("在远端状态未知时盲目重试",),
        "保持阻断并要求核对远端状态",
        "结果已记录或恢复决定已确认",
    ),
)

_BOUNDARIES = {
    (item.stage_id, item.decision_id): item for item in DECISION_BOUNDARIES
}


def get_decision_boundary(stage_id: str, decision_id: str) -> DecisionBoundary:
    try:
        return _BOUNDARIES[(stage_id, decision_id)]
    except KeyError:
        raise KeyError(f"{stage_id}:{decision_id}") from None


def stage_decision_boundaries(stage_id: str) -> tuple[DecisionBoundary, ...]:
    return tuple(
        item for item in DECISION_BOUNDARIES if item.stage_id == stage_id
    )
