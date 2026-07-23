"""Immutable definitions for the ten-stage material-upload interaction flow."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDefinition:
    """A user-visible field that a stage renderer can turn into a control."""

    name: str
    label: str
    component: str
    required: bool = False
    disabled: bool = False
    help_text: str = ""
    exclusive_with: tuple[str, ...] = ()
    required_before_stage: str | None = None
    enabled_when: str | None = None


@dataclass(frozen=True)
class ExactlyOneConstraint:
    """Require exactly one field in a group before entering a later stage."""

    field_names: tuple[str, ...]
    required_before_stage: str


@dataclass(frozen=True)
class StageDefinition:
    """One ordered stage of the interaction workflow."""

    id: str
    title: str
    description: str
    component: str
    fields: tuple[FieldDefinition, ...]
    previous_stage: str | None = None
    read_only: bool = False
    exactly_one_constraints: tuple[ExactlyOneConstraint, ...] = ()


def _field(
    name: str,
    label: str,
    component: str,
    *,
    required: bool = False,
    disabled: bool = False,
    help_text: str = "",
    exclusive_with: tuple[str, ...] = (),
    required_before_stage: str | None = None,
    enabled_when: str | None = None,
) -> FieldDefinition:
    return FieldDefinition(
        name=name,
        label=label,
        component=component,
        required=required,
        disabled=disabled,
        help_text=help_text,
        exclusive_with=exclusive_with,
        required_before_stage=required_before_stage,
        enabled_when=enabled_when,
    )


STAGES: tuple[StageDefinition, ...] = (
    StageDefinition(
        id="setup",
        title="任务配置",
        description="设置目标店铺、月份、商品范围以及数据和素材输入路径。",
        component="setup_form",
        fields=(
            _field("store", "目标店铺", "text", required=True),
            _field("month", "目标月份", "month", required=True),
            _field("product_scope", "商品范围", "textarea", required=True),
            _field("products_csv", "商品表", "path", required=True),
            _field("rules_csv", "规则表", "path", required=True),
            _field("basic_xlsx", "基础素材表", "path", required=True),
            _field("search_xlsx", "推广素材表（原搜推素材）", "path", required=True),
            _field(
                "asset_root",
                "图片素材根目录",
                "path",
                exclusive_with=("asset_manifest",),
            ),
            _field(
                "asset_manifest",
                "图片素材清单",
                "path",
                exclusive_with=("asset_root",),
            ),
            _field("runs_root", "运行目录", "path", required=True),
        ),
        exactly_one_constraints=(
            ExactlyOneConstraint(
                field_names=("asset_root", "asset_manifest"),
                required_before_stage="asset_matching",
            ),
        ),
    ),
    StageDefinition(
        id="completeness",
        title="完整度巡检",
        description="确认基础与推广素材矩阵，记录误判覆盖原因和补充说明。",
        component="inspection_matrix",
        previous_stage="setup",
        fields=(
            _field("confirmed_product_ids", "已确认商品 ID", "multi_select", required=True),
            _field("overrides", "误判覆盖原因", "table"),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="scope",
        title="维护范围确认",
        description="逐个决定本次维护或排除，并记录覆盖自动判断的原因。",
        component="product_scope_table",
        previous_stage="completeness",
        fields=(
            _field("decisions", "维护或排除决定及原因", "table", required=True),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="asset_matching",
        title="素材匹配",
        description="确认图片来源、显式别名、授权状态和采用或排除决定。",
        component="asset_match_gallery",
        previous_stage="scope",
        fields=(
            _field("image_roots", "图片源路径", "path_list", required=True),
            _field("source_types", "来源类型", "multi_select", required=True),
            _field("aliases", "显式别名映射", "table"),
            _field("license_decisions", "授权决定", "table", required=True),
            _field("asset_decisions", "素材采用或排除决定", "table", required=True),
            _field(
                "include_video",
                "包含视频",
                "checkbox",
                disabled=True,
                help_text="本轮测试延期",
            ),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="image_review",
        title="图片适用性与裁剪",
        description="记录图片策略、比例容差和直接使用、裁剪或人工处理决定。",
        component="image_review",
        previous_stage="asset_matching",
        fields=(
            _field("policy_path", "图片策略文件", "path", required=True),
            _field("ratio_tolerance", "比例容差", "number", required=True),
            _field("decisions", "使用、裁剪或人工处理决定", "table", required=True),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="slots_copy",
        title="坑位编排与文案",
        description="分配坑位素材及素材组顺序，并编辑标题、描述和备注。",
        component="slots_copy_editor",
        previous_stage="image_review",
        fields=(
            _field("slot_assignments", "坑位素材分配与顺序", "table", required=True),
            _field("copy_edits", "标题、描述与人工备注", "table", required=True),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="dry_run",
        title="全量 dry-run 审查",
        description="确认 dry-run 结果或返回修改，并说明非阻塞警告的处理。",
        component="dry_run_review",
        previous_stage="slots_copy",
        fields=(
            _field("decision", "返回修改或确认结果", "radio", required=True),
            _field("warning_notes", "非阻塞警告处理说明", "textarea"),
        ),
    ),
    StageDefinition(
        id="approval",
        title="精确任务授权",
        description="选择精确任务 ID，并记录批准人、时间、有效期和确认。",
        component="approval_table",
        previous_stage="dry_run",
        fields=(
            _field("task_ids", "精确任务 ID", "multi_select", required=True),
            _field("confirmed_by", "批准人", "text", required=True),
            _field("confirmed_at", "批准时间", "datetime", required=True),
            _field("valid_until", "有效期", "datetime", required=True),
            _field("acknowledgement", "确认授权", "checkbox", required=True),
        ),
    ),
    StageDefinition(
        id="production_confirmation",
        title="生产确认",
        description="核对店铺、商品、坑位、任务和批准清单哈希并生成交接。",
        component="production_confirmation",
        previous_stage="approval",
        fields=(
            _field("store", "目标店铺", "text", required=True),
            _field("product_ids", "商品 ID", "multi_select", required=True),
            _field("task_ids", "精确任务 ID", "multi_select", required=True),
            _field("slot_ids", "目标坑位", "multi_select", required=True),
            _field("max_products", "最大商品数", "number", required=True),
            _field("approval_manifest_sha256", "批准清单哈希", "text", required=True),
            _field("final_confirmation", "最终确认", "checkbox", required=True),
            _field("notes", "备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="results",
        title="结果与恢复",
        description="展示结果；仅在结果报告需要用户处理的异常时启用恢复输入。",
        component="result_timeline",
        previous_stage="production_confirmation",
        read_only=True,
        fields=(
            _field(
                "recovery_action",
                "恢复请求",
                "select",
                disabled=True,
                enabled_when="result.exception_requires_user_action",
            ),
            _field(
                "manual_notes",
                "人工处理说明",
                "textarea",
                disabled=True,
                enabled_when="result.exception_requires_user_action",
            ),
            _field(
                "allow_retry_after_remote_absence",
                "确认远端不存在后允许重试",
                "checkbox",
                disabled=True,
                enabled_when="result.exception_requires_user_action",
            ),
        ),
    ),
)

_STAGES_BY_ID = {stage.id: stage for stage in STAGES}


def get_stage(stage_id: str) -> StageDefinition:
    """Return a stage definition by its stable workflow identifier."""

    return _STAGES_BY_ID[stage_id]
