"""Immutable definitions for the nine-stage material-upload interaction flow."""

from dataclasses import dataclass


INTERACTION_POLICIES = frozenset(
    {"frontend_required", "frontend_preferred", "chat_fallback"}
)
FALLBACK_REASON_CODES = frozenset(
    {
        "UI_START_FAILED",
        "UI_UNREACHABLE",
        "BROWSER_OPEN_FAILED",
        "SYSTEM_PERMISSION_REQUIRED",
        "LOGIN_INTERACTION_REQUIRED",
        "SCHEMA_GAP",
    }
)
UI_FALLBACK_REASONS = (
    "UI_START_FAILED",
    "UI_UNREACHABLE",
    "BROWSER_OPEN_FAILED",
)


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
    interaction_policy: str = "frontend_required"
    fallback_reason_codes: tuple[str, ...] = ()


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
    visible: bool = True
    exactly_one_constraints: tuple[ExactlyOneConstraint, ...] = ()
    interaction_policy: str = "frontend_required"


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
    interaction_policy: str = "frontend_required",
    fallback_reason_codes: tuple[str, ...] = (),
) -> FieldDefinition:
    if interaction_policy not in INTERACTION_POLICIES:
        raise ValueError(f"unsupported interaction policy: {interaction_policy}")
    unknown_reasons = set(fallback_reason_codes) - FALLBACK_REASON_CODES
    if unknown_reasons:
        raise ValueError(f"unsupported fallback reason codes: {sorted(unknown_reasons)}")
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
        interaction_policy=interaction_policy,
        fallback_reason_codes=fallback_reason_codes,
    )


STAGES: tuple[StageDefinition, ...] = (
    StageDefinition(
        id="setup",
        title="任务配置",
        description="确认团队索引和本次图片源。",
        component="setup_form",
        interaction_policy="frontend_preferred",
        fields=(
            _field(
                "store",
                "目标店铺",
                "text",
                required=True,
                help_text="由系统从已登录页面自动识别，不需要用户填写",
                interaction_policy="frontend_preferred",
                fallback_reason_codes=UI_FALLBACK_REASONS,
            ),
            _field("products_csv", "商品表", "auto_path", required=True),
            _field("rules_csv", "规则表", "auto_path", required=True),
            _field("folder_index_root", "共享文件夹索引", "auto_path"),
            _field(
                "team_folder_index_root",
                "团队索引文件夹",
                "path",
                required=True,
                interaction_policy="frontend_preferred",
                fallback_reason_codes=UI_FALLBACK_REASONS,
            ),
            _field(
                "image_source_labels",
                "图片源名称",
                "auto_path_list",
                required=True,
                interaction_policy="frontend_preferred",
                fallback_reason_codes=UI_FALLBACK_REASONS,
            ),
            _field(
                "image_roots",
                "图片源根目录",
                "auto_path_list",
                required=True,
                interaction_policy="frontend_preferred",
                fallback_reason_codes=UI_FALLBACK_REASONS,
            ),
            _field(
                "asset_manifest",
                "人工图片素材清单",
                "path",
                help_text="可选；仅用于导入人工逐文件清单",
            ),
            _field(
                "historical_basic_xlsx",
                "历史基础素材表",
                "path",
                help_text="可选；仅用于恢复历史任务或离线测试",
            ),
            _field(
                "historical_promotion_csv",
                "历史推广素材状态",
                "path",
                help_text="可选；仅用于恢复历史任务或离线测试",
            ),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="completeness",
        title="完整度巡检",
        description="选择需要补充素材的商品。",
        component="inspection_matrix",
        previous_stage="setup",
        fields=(
            _field("selected_product_ids", "选择进入下一阶段的商品 ID", "multi_select", required=True),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="asset_matching",
        title="素材匹配",
        description="确认候选文件夹并选择本次使用的素材。",
        component="asset_match_gallery",
        previous_stage="completeness",
        fields=(
            _field(
                "image_roots",
                "图片源路径",
                "auto_path_list",
                required=True,
                help_text="沿用任务配置；确认文件夹后由 Agent 在读取图片前检查可访问性",
            ),
            _field(
                "source_types",
                "来源类型",
                "hidden_json_list",
            ),
            _field(
                "folder_decisions",
                "候选文件夹归属决定",
                "hidden_json_list",
            ),
            _field(
                "license_decisions",
                "授权决定",
                "hidden_json_list",
            ),
            _field(
                "asset_decisions",
                "素材采用或排除决定",
                "hidden_json_list",
            ),
            _field(
                "removed_product_ids",
                "本次任务已去掉的商品 ID",
                "hidden_json_list",
            ),
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
        title="图片适用性检测",
        description="检查图片是否适合使用。",
        component="image_review",
        previous_stage="asset_matching",
        visible=False,
        fields=(
            _field(
                "decisions",
                "图片审查决定",
                "table",
                required=True,
                help_text="由可视化审查组件自动写入，不需要手工编辑 JSON",
            ),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="slots_copy",
        title="坑位编排、图片处理与文案",
        description="完成坑位编排、图片处理和文案确认。",
        component="slots_copy_editor",
        previous_stage="asset_matching",
        fields=(
            _field("slot_assignments", "坑位素材分配与顺序", "table", required=True),
            _field("copy_edits", "标题、描述与人工备注", "table", required=True),
            _field("user_notes", "用户备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="dry_run",
        title="阻塞项处理",
        description="自动 dry-run 仅在发现阻塞项时停留于此；处理后重新提交第四阶段。",
        component="dry_run_review",
        previous_stage="slots_copy",
        visible=False,
        fields=(
            _field("decision", "返回修改或确认结果", "radio", required=True),
            _field("warning_notes", "非阻塞警告处理说明", "textarea"),
        ),
    ),
    StageDefinition(
        id="approval",
        title="上传任务确认",
        description="确认需要上传的任务；提交后开始上传。",
        component="approval_table",
        interaction_policy="frontend_preferred",
        previous_stage="dry_run",
        fields=(
            _field("task_ids", "待授权上传任务", "multi_select", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
        ),
    ),
    StageDefinition(
        id="production_confirmation",
        title="生产确认",
        description="查看历史生产确认信息。",
        component="production_confirmation",
        interaction_policy="frontend_preferred",
        previous_stage="approval",
        visible=False,
        fields=(
            _field("store", "目标店铺", "text", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("product_ids", "商品 ID", "multi_select", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("task_ids", "精确任务 ID", "multi_select", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("slot_ids", "目标坑位", "multi_select", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("max_products", "最大商品数", "number", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("approval_manifest_sha256", "批准清单哈希", "text", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("final_confirmation", "最终确认", "checkbox", required=True, interaction_policy="frontend_preferred", fallback_reason_codes=UI_FALLBACK_REASONS),
            _field("notes", "备注", "textarea"),
        ),
    ),
    StageDefinition(
        id="results",
        title="结果",
        description="查看本次上传结果。",
        component="upload_results",
        previous_stage="approval",
        read_only=True,
        fields=(),
    ),
)

_STAGES_BY_ID = {stage.id: stage for stage in STAGES}


def get_stage(stage_id: str) -> StageDefinition:
    """Return a stage definition by its stable workflow identifier."""

    return _STAGES_BY_ID[stage_id]
