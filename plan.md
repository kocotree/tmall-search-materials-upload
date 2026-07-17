# 天猫搜推素材全自动上传 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前只能处理 CSV 草案的 `upload-search-materials` skill 完成到可导出、解析、筛选、补采、生成文案、审批、上传、回查和断点恢复的端到端实现。

**Architecture:** 使用 Python 领域模块处理确定性的表格、规则、素材、任务、审批和状态逻辑；使用 Playwright Page Object 隔离天猫后台页面变化。各阶段通过 `run_id` 目录中的版本化 JSON/CSV 文件衔接，默认 `dry-run`，只有通过不可变审批清单校验后才能进入 `publish`。

**Tech Stack:** Python 3.11+、pytest、openpyxl、Pillow、Playwright for Python、标准库 `dataclasses/json/hashlib/sqlite3/pathlib`、Jinja2。

## Global Constraints

- 默认模式必须是 `dry-run`；没有有效 `approval-manifest.json` 时禁止发布。
- 不存储密码、Cookie、Token、短信验证码或二维码登录数据；仅复用用户已经登录的浏览器会话。
- 不调用未公开的天猫后台内部 API，不绕过验证码、短信、扫码、风控或平台权限。
- 目标店铺不一致、商品不一致、素材归属不确定、选择器失效或发布结果不确定时必须停止相应任务或批次。
- 排除规则必须先于月度筛选、Playwright 补采和素材匹配执行，并保留全部命中原因。
- 排除 `清仓`、`UVNO`（忽略大小写）、`好物体验`、`会员日`、`积分`。
- 商品 ID 全程按字符串处理；每个输入商品必须产生 eligible、excluded 或 blocked 审计结果，禁止用 `continue` 静默丢弃。
- 图片/视频原文件只读；任何裁剪或转码结果写入独立派生目录并重新计算 SHA-256。
- 发布前必须重新计算媒体、标题、描述和动作哈希；与批准清单不一致时批准立即失效。
- 发布按钮只能点击一次；点击后超时进入 `publish_uncertain`，必须先远端回查，禁止盲目重试。
- 当前没有 NAS/飞书/光合路径、视频完整规格和生产页面稳定选择器；缺少对应配置时必须返回明确阻断状态。
- 当前工作区不是 Git 仓库；每个任务末尾的提交步骤仅在执行时已经进入 Git 工作树后运行，否则记录测试证据并跳过提交，不在本计划内初始化仓库。

---

## 目标文件结构

```text
upload-search-materials/
  SKILL.md                         # 用户入口、工作流、安全门禁和执行命令
  pyproject.toml                   # Python 依赖、pytest 和 CLI 配置
  config/
    selectors.example.yaml        # 选择器键示例；生产值由运行时文件提供
    media-policy.example.yaml      # 图片/视频规格示例与必填字段
  src/upload_search_materials/
    __init__.py
    models.py                      # 批次、商品、坑位、素材、审批状态模型
    io_tables.py                   # CSV/XLSX 解析、表头映射和来源哈希
    eligibility.py                 # 排除和月度准入规则
    material_state.py              # 基础素材、搜推数据、页面补采结果合并
    assets.py                      # 本地/NAS/清单型来源适配与素材校验
    copywriting.py                 # AI 请求、响应导入和文案规则校验
    tasks.py                       # product_task/material_item 生成与状态转换
    approval.py                    # HTML 审核页、审批清单和内容哈希
    state_store.py                 # SQLite 落盘、幂等和断点恢复
    reporting.py                   # 中文汇总与审计文件
    cli.py                         # dry-run、approve、publish、resume 命令
    browser/
      config.py                    # 选择器配置加载与完整性校验
      session.py                   # 登录态、店铺身份和人工验证检测
      export_page.py               # 两份 XLSX 导出
      material_page.py             # 3/9 坑、空坑和审核状态补采
      upload_page.py               # 上传、发布一次和证据捕获
      verifier.py                  # 发布后远端回查
  tests/
    fixtures/                      # 脱敏 CSV/XLSX、HTML 页面和小媒体文件
    test_io_tables.py
    test_eligibility.py
    test_material_state.py
    test_assets.py
    test_copywriting.py
    test_tasks.py
    test_approval.py
    test_state_store.py
    test_browser_pages.py
    test_orchestrator.py
  scripts/
    validate_product_data.py       # 兼容入口，转调新 CLI
    build_upload_tasks.py          # 兼容入口，转调新 CLI
  references/
    business-rules.md
    data-schema.md
    error-handling.md
    upload-ui-workflow.md
```

## Task 1: 建立可测试的包、依赖和核心状态模型

**Files:**
- Create: `upload-search-materials/pyproject.toml`
- Create: `upload-search-materials/src/upload_search_materials/__init__.py`
- Create: `upload-search-materials/src/upload_search_materials/models.py`
- Test: `upload-search-materials/tests/test_models.py`

**Interfaces:**
- Produces: `ProductStatus`、`MaterialStatus`、`BatchMode` 枚举；`SourceFile`、`ProductRecord`、`AssetRecord`、`ProductTask`、`MaterialItem` 数据类；`make_run_id()`。
- Consumes: 无。

- [ ] **Step 1: 写失败测试，锁定状态枚举和确定性 ID**

```python
from upload_search_materials.models import MaterialStatus, make_task_id


def test_task_id_is_deterministic():
    left = make_task_id("run-1", "123", "image_text", 1)
    right = make_task_id("run-1", "123", "image_text", 1)
    assert left == right
    assert left.startswith("MAT-")


def test_publish_uncertain_is_a_material_status():
    assert MaterialStatus.PUBLISH_UNCERTAIN.value == "publish_uncertain"
```

- [ ] **Step 2: 运行测试并确认因包不存在而失败**

Run: `cd upload-search-materials; python -m pytest tests/test_models.py -v`

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'upload_search_materials'`。

- [ ] **Step 3: 创建项目配置和最小核心模型**

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "upload-search-materials"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "openpyxl>=3.1,<4",
  "Pillow>=10,<12",
  "playwright>=1.45,<2",
  "Jinja2>=3.1,<4",
  "PyYAML>=6,<7",
]

[project.optional-dependencies]
test = ["pytest>=8,<9"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```python
from dataclasses import dataclass, field
from enum import Enum
import hashlib


class BatchMode(str, Enum):
    DRY_RUN = "dry-run"
    PUBLISH = "publish"


class ProductStatus(str, Enum):
    DISCOVERED = "discovered"
    EXCLUDED = "excluded"
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    UPLOADING = "uploading"
    PARTIALLY_COMPLETED = "partially_completed"
    COMPLETED = "completed"
    FAILED = "failed"


class MaterialStatus(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    SUCCESS = "success"
    FAILED = "failed"
    PUBLISH_UNCERTAIN = "publish_uncertain"


def make_task_id(run_id: str, product_id: str, material_type: str, slot_index: int) -> str:
    raw = f"{run_id}|{product_id}|{material_type}|{slot_index}".encode("utf-8")
    return "MAT-" + hashlib.sha256(raw).hexdigest()[:16]
```

数据类必须使用字符串商品 ID，并为 `reason_codes` 使用 `list[str]`，为媒体清单使用 `list[AssetRecord]`；不得把浏览器定位器放进领域模型。

- [ ] **Step 4: 安装开发依赖并运行测试**

Run: `cd upload-search-materials; python -m pip install -e ".[test]"; python -m pytest tests/test_models.py -v`

Expected: 2 passed。

- [ ] **Step 5: 提交该独立交付物（仅 Git 工作树）**

```powershell
git add upload-search-materials/pyproject.toml upload-search-materials/src upload-search-materials/tests/test_models.py
git commit -m "feat: add material workflow domain models"
```

## Task 2: 实现真实 CSV/XLSX 解析和阻断级校验

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/io_tables.py`
- Create: `upload-search-materials/tests/test_io_tables.py`
- Create: `upload-search-materials/tests/fixtures/basic-materials.xlsx`
- Create: `upload-search-materials/tests/fixtures/search-materials.xlsx`
- Modify: `upload-search-materials/scripts/validate_product_data.py`

**Interfaces:**
- Consumes: Task 1 的 `SourceFile`、`ProductRecord`。
- Produces: `read_product_csv(path) -> list[ProductRecord]`、`read_basic_materials_xlsx(path) -> list[dict]`、`read_search_materials_xlsx(path) -> list[dict]`、`sha256_file(path) -> str`、`SchemaError`。

- [ ] **Step 1: 用 openpyxl 创建最小脱敏夹具并写失败测试**

```python
from pathlib import Path
import pytest
from upload_search_materials.io_tables import SchemaError, read_basic_materials_xlsx


def test_xlsx_keeps_product_id_as_text(tmp_path: Path):
    from openpyxl import Workbook
    path = tmp_path / "basic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["商品ID", "商品标题", "白底图", "短标题"])
    ws.append([1234567890123, "KK树儿童水杯", "已完成", "儿童水杯"])
    wb.save(path)
    rows = read_basic_materials_xlsx(path)
    assert rows[0]["商品ID"] == "1234567890123"


def test_missing_required_xlsx_header_blocks_batch(tmp_path: Path):
    from openpyxl import Workbook
    path = tmp_path / "bad.xlsx"
    wb = Workbook()
    wb.active.append(["错误字段"])
    wb.save(path)
    with pytest.raises(SchemaError, match="商品ID"):
        read_basic_materials_xlsx(path)
```

- [ ] **Step 2: 运行测试并确认 XLSX 解析器尚不存在**

Run: `cd upload-search-materials; python -m pytest tests/test_io_tables.py -v`

Expected: FAIL，包含 `cannot import name 'read_basic_materials_xlsx'`。

- [ ] **Step 3: 实现统一表头解析和文件哈希**

```python
from pathlib import Path
import hashlib
from openpyxl import load_workbook


class SchemaError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_xlsx(path: Path, required: set[str]) -> list[dict[str, str]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    iterator = sheet.iter_rows(values_only=True)
    headers = [str(value).strip() if value is not None else "" for value in next(iterator)]
    missing = sorted(required - set(headers))
    if missing:
        raise SchemaError(f"缺少必需表头: {', '.join(missing)}")
    return [
        {headers[index]: "" if value is None else str(value).strip() for index, value in enumerate(row)}
        for row in iterator
        if any(value not in (None, "") for value in row)
    ]


def read_basic_materials_xlsx(path: Path) -> list[dict[str, str]]:
    return _read_xlsx(path, {"商品ID", "商品标题", "白底图", "短标题"})


def read_search_materials_xlsx(path: Path) -> list[dict[str, str]]:
    return _read_xlsx(path, {"商品ID", "素材类型"})
```

CSV 解析器必须使用 `utf-8-sig`、检测重复表头、把缺失必需表头列为批次阻断；缺 ID、非法 ID 和重复 ID 必须成为单商品阻断，不能继续报告 `blocking: false`。

- [ ] **Step 4: 将旧校验脚本改为新模块的兼容入口**

`validate_product_data.py` 只保留参数解析和 JSON 输出，调用 `read_product_csv()` 与 `validate_product_records()`；退出码规则固定为：成功 `0`、存在单商品阻断 `1`、批次 schema 阻断 `2`。

- [ ] **Step 5: 运行单元测试和两份真实 XLSX 只读集成测试**

Run: `cd upload-search-materials; python -m pytest tests/test_io_tables.py -v`

Expected: 全部 PASS。

Run: `cd upload-search-materials; python -m upload_search_materials.cli inspect-xlsx --basic "../docs/基础素材.xlsx" --search "../docs/搜推素材数据经营数据_20260717_f7d80a06ea261ece5e44632121e19d40.xlsx" --dry-run`

Expected: 输出两份文件的行数、唯一商品数和 SHA-256，退出码 0，不创建上传任务、不访问浏览器。

- [ ] **Step 6: 提交该独立交付物（仅 Git 工作树）**

```powershell
git add upload-search-materials/src/upload_search_materials/io_tables.py upload-search-materials/tests upload-search-materials/scripts/validate_product_data.py
git commit -m "feat: parse and validate tmall csv and xlsx sources"
```

## Task 3: 实现完整排除规则和月度准入审计

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/eligibility.py`
- Create: `upload-search-materials/tests/test_eligibility.py`
- Modify: `upload-search-materials/references/business-rules.md`
- Modify: `upload-search-materials/scripts/build_upload_tasks.py`

**Interfaces:**
- Consumes: `ProductRecord`、基础素材标题、搜推素材标题、月度规则。
- Produces: `EligibilityDecision(product_id, status, reason_codes, evidence)`；`evaluate_product(...)`；`evaluate_all(...)`。

- [ ] **Step 1: 写失败测试覆盖五类排除和多原因保留**

```python
import pytest
from upload_search_materials.eligibility import evaluate_exclusions


@pytest.mark.parametrize(("grade", "title", "code"), [
    ("清仓", "普通标题", "EXCLUDE_CLEARANCE"),
    ("A级", "uvno 夏季商品", "EXCLUDE_UVNO"),
    ("A级", "好物体验专享", "EXCLUDE_GOOD_EXPERIENCE"),
    ("A级", "会员日新品", "EXCLUDE_MEMBER_DAY"),
    ("A级", "积分兑换商品", "EXCLUDE_POINTS"),
])
def test_each_exclusion_rule(grade, title, code):
    decision = evaluate_exclusions(grade, [title])
    assert decision.status == "excluded"
    assert code in decision.reason_codes


def test_multiple_reasons_are_kept_in_one_record():
    decision = evaluate_exclusions("A级", ["UVNO会员日积分商品"])
    assert decision.reason_codes == ["EXCLUDE_UVNO", "EXCLUDE_MEMBER_DAY", "EXCLUDE_POINTS"]
```

- [ ] **Step 2: 运行测试并确认排除引擎尚不存在**

Run: `cd upload-search-materials; python -m pytest tests/test_eligibility.py -v`

Expected: FAIL，包含 `ModuleNotFoundError` 或缺少函数。

- [ ] **Step 3: 实现顺序稳定、可审计的排除判断**

```python
RULES = (
    ("EXCLUDE_UVNO", lambda grade, text: "uvno" in text.casefold()),
    ("EXCLUDE_GOOD_EXPERIENCE", lambda grade, text: "好物体验" in text),
    ("EXCLUDE_MEMBER_DAY", lambda grade, text: "会员日" in text),
    ("EXCLUDE_POINTS", lambda grade, text: "积分" in text),
)


def evaluate_exclusions(grade: str, titles: list[str]):
    text = "\n".join(value.strip() for value in titles if value)
    reasons = ["EXCLUDE_CLEARANCE"] if grade.strip() == "清仓" else []
    reasons.extend(code for code, predicate in RULES if predicate(grade, text))
    return EligibilityDecision(
        status="excluded" if reasons else "eligible_candidate",
        reason_codes=reasons,
        evidence={"grade": grade, "titles": titles},
    )
```

`evaluate_all()` 必须为输入的每一行写一条决策；重复商品 ID 合并证据但进入 `blocked / DUPLICATE_PRODUCT_ID`，缺 ID 进入 `blocked / MISSING_PRODUCT_ID`。月度规则只作用于未排除商品。

- [ ] **Step 4: 修改兼容任务脚本，禁止静默 continue**

输出增加 `eligibility_status`、`reason_codes`、`evidence_json`；被排除商品也写入审计 CSV，但不得生成 `material_item`。

- [ ] **Step 5: 运行测试并核对真实 7 月数据**

Run: `cd upload-search-materials; python -m pytest tests/test_eligibility.py -v`

Expected: 全部 PASS。

Run: `cd upload-search-materials; python -m upload_search_materials.cli select --products "../docs/天猫商品信息表_产品数据表_数据总表.csv" --rules "../docs/天猫商品信息表_每月推品规则（合并）_Grid View.csv" --basic "../docs/基础素材.xlsx" --search "../docs/搜推素材数据经营数据_20260717_f7d80a06ea261ece5e44632121e19d40.xlsx" --month 7 --output runs/test-selection --dry-run`

Expected: 所有输入行均出现在 eligibility 审计中；五类排除商品不进入待补采列表。

- [ ] **Step 6: 提交该独立交付物（仅 Git 工作树）**

```powershell
git add upload-search-materials/src/upload_search_materials/eligibility.py upload-search-materials/tests/test_eligibility.py upload-search-materials/references/business-rules.md upload-search-materials/scripts/build_upload_tasks.py
git commit -m "feat: audit all eligibility and exclusion decisions"
```

## Task 4: 合并后台素材状态并计算 3/9 坑缺口

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/material_state.py`
- Create: `upload-search-materials/tests/test_material_state.py`

**Interfaces:**
- Consumes: 两份 XLSX 标准化记录和 `backend-material-status.csv`。
- Produces: `MaterialSnapshot`、`MaterialGap`；`merge_material_state()`；`calculate_gap(desired_slots, items)`；`products_requiring_supplement()`。

- [ ] **Step 1: 写失败测试覆盖 3 坑、9 坑、审核中和空坑**

```python
from upload_search_materials.material_state import calculate_gap


def test_three_slot_gap_with_one_video_and_one_image():
    gap = calculate_gap(3, [
        {"type": "video", "status": "approved"},
        {"type": "image_text", "status": "under_review"},
    ])
    assert gap.missing_slots == 1
    assert gap.needs_page_supplement is False


def test_unknown_slot_requirement_requires_browser_supplement():
    gap = calculate_gap(None, [])
    assert gap.needs_page_supplement is True
    assert gap.missing_slots is None
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_material_state.py -v`

Expected: FAIL，缺少 `material_state` 模块。

- [ ] **Step 3: 实现状态合并和候选补采集合**

```python
def calculate_gap(desired_slots: int | None, items: list[dict]) -> MaterialGap:
    if desired_slots not in (3, 9):
        return MaterialGap(None, True, "SLOT_REQUIREMENT_UNKNOWN")
    occupied = sum(1 for item in items if item["status"] not in {"rejected", "deleted"})
    return MaterialGap(max(desired_slots - occupied, 0), False, "")


def products_requiring_supplement(snapshots):
    return [
        snapshot.product_id for snapshot in snapshots
        if snapshot.desired_slots not in (3, 9)
        or snapshot.empty_slot_indexes is None
        or snapshot.review_states_complete is False
    ]
```

合并键只允许使用商品 ID 和远端素材 ID；不能用商品名称作为唯一键。无法确定的信息保留 `None`，不得转换成 0 或空列表。

- [ ] **Step 4: 运行测试并提交**

Run: `cd upload-search-materials; python -m pytest tests/test_material_state.py -v`

Expected: 全部 PASS。

```powershell
git add upload-search-materials/src/upload_search_materials/material_state.py upload-search-materials/tests/test_material_state.py
git commit -m "feat: calculate material slots and supplement candidates"
```

## Task 5: 实现素材来源适配、指纹和图片/视频校验

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/assets.py`
- Create: `upload-search-materials/config/media-policy.example.yaml`
- Create: `upload-search-materials/tests/test_assets.py`
- Modify: `upload-search-materials/references/asset-requirements.md`

**Interfaces:**
- Consumes: 本地/NAS 根目录、外部来源清单 CSV、媒体策略 YAML。
- Produces: `AssetSource` 协议、`DirectoryAssetSource`、`ManifestAssetSource`、`inspect_asset()`、`validate_asset_group()`。

- [ ] **Step 1: 写失败测试覆盖零字节、重复指纹、比例混用和许可未知**

```python
from PIL import Image
from upload_search_materials.assets import inspect_asset, validate_asset_group


def test_mixed_image_ratios_are_blocked(tmp_path):
    paths = []
    for name, size in [("a.png", (300, 400)), ("b.png", (400, 400))]:
        path = tmp_path / name
        Image.new("RGB", size).save(path)
        paths.append(path)
    records = [inspect_asset(path, "123", "confirmed") for path in paths]
    result = validate_asset_group(records, material_type="image_text")
    assert "MIXED_ASPECT_RATIO" in result.reason_codes


def test_unknown_license_blocks_asset(tmp_path):
    path = tmp_path / "a.png"
    Image.new("RGB", (400, 400)).save(path)
    record = inspect_asset(path, "123", "unknown")
    assert record.validation_status == "blocked"
    assert "LICENSE_UNKNOWN" in record.reason_codes
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_assets.py -v`

Expected: FAIL，缺少素材模块。

- [ ] **Step 3: 实现只读检查和策略阻断**

```python
def inspect_asset(path, product_id: str, license_status: str) -> AssetRecord:
    path = Path(path).resolve()
    size_bytes = path.stat().st_size
    reasons = []
    if size_bytes == 0:
        reasons.append("ZERO_BYTE_ASSET")
    if license_status != "confirmed":
        reasons.append("LICENSE_UNKNOWN")
    width = height = None
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and size_bytes:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    return AssetRecord(
        product_id=product_id,
        source_path=str(path),
        sha256=sha256_file(path),
        width=width,
        height=height,
        license_status=license_status,
        validation_status="blocked" if reasons else "valid",
        reason_codes=reasons,
    )
```

`media-policy.example.yaml` 必须列出图片允许格式、3:4/1:1 比例容差、3–9 张数量规则，以及视频格式、编码、时长、分辨率、比例、大小、封面、水印和授权字段。视频字段任何一项在运行时策略中缺失时返回 `VIDEO_POLICY_INCOMPLETE`。

- [ ] **Step 4: 运行测试并提交**

Run: `cd upload-search-materials; python -m pytest tests/test_assets.py -v`

Expected: 全部 PASS。

```powershell
git add upload-search-materials/src/upload_search_materials/assets.py upload-search-materials/config/media-policy.example.yaml upload-search-materials/tests/test_assets.py upload-search-materials/references/asset-requirements.md
git commit -m "feat: validate licensed image and video assets"
```

## Task 6: 实现可追溯 AI 文案请求和确定性规则校验

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/copywriting.py`
- Create: `upload-search-materials/tests/test_copywriting.py`
- Create: `upload-search-materials/config/copy-policy.example.yaml`

**Interfaces:**
- Consumes: 已确认商品字段和运行时注入的 `CopyProvider.generate(request)`。
- Produces: `CopyRequest`、`CopyResult`、`CopyProvider` 协议、`generate_and_validate_copy()`。

- [ ] **Step 1: 写失败测试，禁止模型编造无依据属性**

```python
from upload_search_materials.copywriting import generate_and_validate_copy


class FakeProvider:
    def generate(self, request):
        return {"title": "KK树女童防晒水杯", "description": "适合女童，UPF50+。"}


def test_unsupported_claim_is_manual_review():
    result = generate_and_validate_copy(
        source={"品牌": "KK树", "品类": "水杯", "卖点": "便携"},
        provider=FakeProvider(),
        prohibited_terms=["UPF50+"],
    )
    assert result.status == "needs_manual_review"
    assert "UNSUPPORTED_CLAIM" in result.reason_codes
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_copywriting.py -v`

Expected: FAIL，缺少文案模块。

- [ ] **Step 3: 实现 provider 接口、来源白名单和校验**

```python
class CopyProvider(Protocol):
    def generate(self, request: CopyRequest) -> dict[str, str]: ...


ALLOWED_SOURCE_FIELDS = {"商品标题", "品类", "卖点1", "卖点2", "短标题", "利益点", "品牌"}


def generate_and_validate_copy(source, provider, prohibited_terms):
    safe_source = {key: value for key, value in source.items() if key in ALLOWED_SOURCE_FIELDS and value}
    output = provider.generate(CopyRequest(source_fields=safe_source))
    reasons = validate_copy(output, safe_source, prohibited_terms)
    return CopyResult(
        title=output["title"],
        description=output["description"],
        source_fields=safe_source,
        generated_at=utc_now_iso(),
        status="needs_manual_review" if reasons else "valid",
        reason_codes=reasons,
    )
```

规则固定为：标题不超过 30 字符并优先 8–20 个汉字；描述 10–1000 字符；年龄、性别、材质、认证、功效和防晒等级只能在来源字段明确出现时使用。Provider 的凭据只从进程环境或 Codex 当前会话获得，不写入文件。

- [ ] **Step 4: 运行测试并提交**

Run: `cd upload-search-materials; python -m pytest tests/test_copywriting.py -v`

Expected: 全部 PASS。

```powershell
git add upload-search-materials/src/upload_search_materials/copywriting.py upload-search-materials/config/copy-policy.example.yaml upload-search-materials/tests/test_copywriting.py
git commit -m "feat: generate and validate traceable product copy"
```

## Task 7: 生成两级任务、中文审核页和不可变审批清单

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/tasks.py`
- Create: `upload-search-materials/src/upload_search_materials/approval.py`
- Create: `upload-search-materials/tests/test_tasks.py`
- Create: `upload-search-materials/tests/test_approval.py`
- Create: `upload-search-materials/assets/review-template.html.j2`

**Interfaces:**
- Consumes: eligibility、MaterialGap、AssetRecord、CopyResult。
- Produces: `build_product_tasks()`、`build_material_items()`、`render_review_html()`、`create_manifest()`、`verify_manifest()`。

- [ ] **Step 1: 写失败测试覆盖两级 ID 和审批后变更失效**

```python
from upload_search_materials.approval import create_manifest, verify_manifest


def test_changed_copy_invalidates_approval(tmp_path):
    item = sample_material_item(title="原标题")
    manifest = create_manifest("KK Tree", [item], "operator", "2026-07-17T10:00:00+08:00")
    item.title = "审批后标题"
    result = verify_manifest(manifest, [item], expected_store="KK Tree")
    assert result.valid is False
    assert result.reason == "APPROVED_CONTENT_CHANGED"


def test_manifest_store_must_match():
    item = sample_material_item()
    manifest = create_manifest("KK Tree", [item], "operator", "2026-07-17T10:00:00+08:00")
    assert verify_manifest(manifest, [item], expected_store="Other Store").reason == "STORE_IDENTITY_MISMATCH"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_tasks.py tests/test_approval.py -v`

Expected: FAIL，缺少任务和审批模块。

- [ ] **Step 3: 实现规范化内容哈希和 manifest 校验**

```python
def item_payload(item) -> dict:
    return {
        "task_id": item.task_id,
        "product_id": item.product_id,
        "material_type": item.material_type,
        "slot_index": item.slot_index,
        "asset_sha256": sorted(asset.sha256 for asset in item.assets),
        "title": item.title,
        "description": item.description,
        "action": "publish",
    }


def canonical_hash(value: dict | list) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_manifest(store, items, confirmed_by, confirmed_at):
    entries = [item_payload(item) for item in sorted(items, key=lambda value: value.task_id)]
    return {
        "schema_version": 1,
        "store": store,
        "entries": entries,
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
        "manifest_sha256": canonical_hash(entries),
    }
```

批准粒度固定为 `material_item`。某个 item 内容变化只撤销该 item 的批准；目标店铺、清单 schema 或批次级输入文件哈希变化则阻断整个批次。

- [ ] **Step 4: 生成中文审核页并运行测试**

审核页按负责人、品类、风险分组，展示商品 ID、目标坑位、缩略图、标题、描述、来源、授权状态、阻断原因和 task ID；页面只负责展示，不直接执行发布。

Run: `cd upload-search-materials; python -m pytest tests/test_tasks.py tests/test_approval.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交该独立交付物（仅 Git 工作树）**

```powershell
git add upload-search-materials/src/upload_search_materials/tasks.py upload-search-materials/src/upload_search_materials/approval.py upload-search-materials/tests/test_tasks.py upload-search-materials/tests/test_approval.py upload-search-materials/assets/review-template.html.j2
git commit -m "feat: add review and immutable approval manifests"
```

## Task 8: 实现 Playwright 配置、会话门禁、导出和页面补采

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/browser/config.py`
- Create: `upload-search-materials/src/upload_search_materials/browser/session.py`
- Create: `upload-search-materials/src/upload_search_materials/browser/export_page.py`
- Create: `upload-search-materials/src/upload_search_materials/browser/material_page.py`
- Create: `upload-search-materials/config/selectors.example.yaml`
- Create: `upload-search-materials/tests/fixtures/material-page.html`
- Create: `upload-search-materials/tests/test_browser_pages.py`

**Interfaces:**
- Consumes: Playwright `Page`、目标店铺、选择器配置、补采商品 ID 列表。
- Produces: `SelectorConfig`、`assert_store_identity()`、`detect_human_check()`、`export_reports()`、`supplement_material_status()`。

- [ ] **Step 1: 写失败测试覆盖错误店铺、选择器缺失和空结果**

```python
import pytest
from upload_search_materials.browser.config import SelectorConfigError, load_selectors
from upload_search_materials.browser.session import StoreIdentityError, assert_store_identity


def test_missing_required_selector_fails_configuration(tmp_path):
    path = tmp_path / "selectors.yaml"
    path.write_text("store_name: '#store'\n", encoding="utf-8")
    with pytest.raises(SelectorConfigError, match="publish_button"):
        load_selectors(path)


def test_wrong_store_stops_batch(fake_page):
    fake_page.set_text("#store", "Other Store")
    with pytest.raises(StoreIdentityError):
        assert_store_identity(fake_page, "#store", "KK Tree")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_browser_pages.py -v`

Expected: FAIL，缺少 browser 模块。

- [ ] **Step 3: 实现选择器完整性和会话安全门禁**

```python
REQUIRED_SELECTORS = {
    "store_name", "human_check", "export_basic", "export_search",
    "product_search", "product_id", "desired_slots", "material_rows",
    "empty_slots", "review_status", "image_text_action", "video_action",
    "file_input", "title_input", "description_input", "publish_button",
    "success_signal", "remote_material_id",
}


def load_selectors(path: Path) -> dict[str, str]:
    values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = sorted(key for key in REQUIRED_SELECTORS if not values.get(key))
    if missing:
        raise SelectorConfigError("缺少选择器: " + ", ".join(missing))
    return values
```

选择器优先级固定为 `data-testid`、稳定 `data-*`、固定字段属性、ARIA role/label；禁止使用屏幕坐标。`selectors.example.yaml` 使用本地脱敏 HTML 夹具中的真实测试值，生产运行必须通过 `--selectors <runtime-file>` 明确传入生产值。

- [ ] **Step 4: 实现下载文件指纹和仅候选商品补采**

`export_reports()` 使用 `page.expect_download()` 分别保存两个带 `run_id` 的文件，记录原始文件名、下载时间和 SHA-256，不覆盖旧批次。`supplement_material_status()` 只接收 Task 4 产生的候选 ID；如果 `material_rows` 或坑位字段选择器失效，返回 `SELECTOR_INVALID`，不得写成 0 坑。

- [ ] **Step 5: 运行脱敏 HTML 测试并提交**

Run: `cd upload-search-materials; python -m pytest tests/test_browser_pages.py -v`

Expected: 全部 PASS，不访问天猫网络。

```powershell
git add upload-search-materials/src/upload_search_materials/browser upload-search-materials/config/selectors.example.yaml upload-search-materials/tests/fixtures/material-page.html upload-search-materials/tests/test_browser_pages.py
git commit -m "feat: export and supplement tmall material state"
```

## Task 9: 实现发布一次、远端回查和 publish_uncertain 处理

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/browser/upload_page.py`
- Create: `upload-search-materials/src/upload_search_materials/browser/verifier.py`
- Extend: `upload-search-materials/tests/test_browser_pages.py`
- Modify: `upload-search-materials/references/error-handling.md`

**Interfaces:**
- Consumes: 已验证 manifest、`MaterialItem`、Playwright Page、选择器配置。
- Produces: `upload_approved_item()`、`verify_remote_item()`、`UploadOutcome`。

- [ ] **Step 1: 写失败测试锁定“只点一次”和超时回查行为**

```python
def test_publish_timeout_does_not_click_twice(fake_upload_page, approved_item):
    fake_upload_page.publish_result = "timeout"
    outcome = upload_approved_item(fake_upload_page, approved_item)
    assert fake_upload_page.publish_click_count == 1
    assert outcome.status == "publish_uncertain"
    assert outcome.retry_allowed is False


def test_remote_match_resolves_uncertain(fake_upload_page, approved_item):
    fake_upload_page.remote_rows = [{"product_id": approved_item.product_id,
                                     "fingerprint": approved_item.content_hash,
                                     "remote_material_id": "RM-1",
                                     "status": "under_review"}]
    outcome = verify_remote_item(fake_upload_page, approved_item)
    assert outcome.status == "under_review"
    assert outcome.remote_material_id == "RM-1"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_browser_pages.py -v`

Expected: FAIL，缺少上传和回查函数。

- [ ] **Step 3: 实现发布前门禁和单次点击**

```python
def upload_approved_item(page, item, manifest, selectors) -> UploadOutcome:
    approval = verify_manifest(manifest, [item], expected_store=read_store(page, selectors))
    if not approval.valid:
        return UploadOutcome("blocked", approval.reason, retry_allowed=False)
    detect_human_check(page, selectors)
    assert_product_identity(page, selectors, item.product_id)
    assert_remote_slot_unchanged(page, selectors, item)
    fill_approved_content(page, selectors, item)
    validate_before_publish(page, selectors, item)
    try:
        page.locator(selectors["publish_button"]).click(timeout=15_000)
        return observe_publish_result(page, selectors, item)
    except PlaywrightTimeoutError:
        return UploadOutcome("publish_uncertain", "PUBLISH_UNCERTAIN", retry_allowed=False)
```

发布前网络错误最多重试两次；发布按钮点击后任何异常都不得再次调用 `upload_approved_item()`。远端“确认不存在”必须同时满足：正确店铺、精确商品 ID、目标坑位、批准内容指纹和提交时间窗口均无匹配记录；否则保持 `publish_uncertain` 并转人工。

- [ ] **Step 4: 运行测试并提交**

Run: `cd upload-search-materials; python -m pytest tests/test_browser_pages.py -v`

Expected: 全部 PASS。

```powershell
git add upload-search-materials/src/upload_search_materials/browser/upload_page.py upload-search-materials/src/upload_search_materials/browser/verifier.py upload-search-materials/tests/test_browser_pages.py upload-search-materials/references/error-handling.md
git commit -m "feat: publish approved items with remote verification"
```

## Task 10: 实现状态落盘、断点恢复和幂等执行

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/state_store.py`
- Create: `upload-search-materials/tests/test_state_store.py`

**Interfaces:**
- Consumes: 批次、商品、坑位、动作结果和远端证据。
- Produces: `StateStore`、`record_transition()`、`recoverable_items()`、`has_remote_evidence()`。

- [ ] **Step 1: 写失败测试覆盖重启恢复和不重复上传**

```python
from upload_search_materials.state_store import StateStore


def test_resume_skips_item_with_remote_evidence(tmp_path):
    store = StateStore(tmp_path / "run.sqlite3")
    store.save_item("MAT-1", "submitted", remote_material_id="RM-1", evidence="list-row.png")
    store.close()
    reopened = StateStore(tmp_path / "run.sqlite3")
    assert "MAT-1" not in reopened.recoverable_items(["MAT-1", "MAT-2"])
    assert reopened.recoverable_items(["MAT-1", "MAT-2"]) == ["MAT-2"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd upload-search-materials; python -m pytest tests/test_state_store.py -v`

Expected: FAIL，缺少状态存储模块。

- [ ] **Step 3: 实现 SQLite 事务和合法状态转换**

```python
ALLOWED_TRANSITIONS = {
    "ready_for_review": {"approved", "needs_manual_review"},
    "approved": {"uploading", "ready_for_review", "blocked"},
    "uploading": {"submitted", "failed", "publish_uncertain"},
    "publish_uncertain": {"submitted", "under_review", "success", "failed"},
    "submitted": {"under_review", "success", "failed"},
    "under_review": {"success", "failed"},
}


def record_transition(self, task_id, old_status, new_status, reason, evidence):
    if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        raise InvalidTransition(f"{old_status} -> {new_status}")
    with self.connection:
        self.connection.execute(
            "INSERT INTO transitions(task_id, old_status, new_status, reason, evidence, created_at) VALUES(?,?,?,?,?,?)",
            (task_id, old_status, new_status, reason, evidence, utc_now_iso()),
        )
        self.connection.execute("UPDATE material_items SET status=? WHERE task_id=?", (new_status, task_id))
```

每完成导出、解析、筛选、补采、审批、上传或回查动作都必须事务落盘。恢复时先重新核对目标店铺和 manifest，再只执行没有远端证据、状态允许恢复的 item。

- [ ] **Step 4: 运行测试并提交**

Run: `cd upload-search-materials; python -m pytest tests/test_state_store.py -v`

Expected: 全部 PASS。

```powershell
git add upload-search-materials/src/upload_search_materials/state_store.py upload-search-materials/tests/test_state_store.py
git commit -m "feat: persist workflow state for safe resume"
```

## Task 11: 编排完整 CLI、输出报告并更新 skill 用户流程

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/cli.py`
- Create: `upload-search-materials/src/upload_search_materials/reporting.py`
- Create: `upload-search-materials/tests/test_orchestrator.py`
- Modify: `upload-search-materials/SKILL.md`
- Modify: `upload-search-materials/agents/openai.yaml`
- Modify: `upload-search-materials/references/data-schema.md`
- Modify: `upload-search-materials/references/upload-ui-workflow.md`
- Modify: `upload-search-materials/assets/task-report-template.csv`

**Interfaces:**
- Consumes: Tasks 1–10 的全部稳定接口。
- Produces: `tmall-materials run`、`approve`、`publish`、`resume`、`report` 命令和批次输出目录。

- [ ] **Step 1: 写失败的端到端 dry-run 测试**

```python
def test_dry_run_never_opens_publish_page(tmp_path, fixture_sources, fake_browser):
    exit_code = main([
        "run", "--mode", "dry-run", "--month", "7", "--store", "KK Tree",
        "--products", fixture_sources.products,
        "--rules", fixture_sources.rules,
        "--basic", fixture_sources.basic,
        "--search", fixture_sources.search,
        "--output", str(tmp_path / "run"),
    ], browser=fake_browser)
    assert exit_code == 0
    assert fake_browser.publish_page_open_count == 0
    assert (tmp_path / "run" / "review.html").exists()
    assert (tmp_path / "run" / "eligibility.csv").exists()
```

- [ ] **Step 2: 运行测试并确认 CLI 尚未存在**

Run: `cd upload-search-materials; python -m pytest tests/test_orchestrator.py -v`

Expected: FAIL，缺少 `cli.main`。

- [ ] **Step 3: 实现显式模式和门禁顺序**

```python
def run_batch(config, services):
    run = services.state.create_run(config)
    sources = services.tables.load_and_hash(config)
    decisions = services.eligibility.evaluate_all(sources, config.month)
    candidates = [decision for decision in decisions if decision.status == "eligible"]
    snapshots = services.materials.merge(sources, candidates)
    supplements = services.materials.products_requiring_supplement(snapshots)
    if supplements and services.browser is None:
        services.state.block_products(supplements, "BROWSER_SUPPLEMENT_REQUIRED")
    assets = services.assets.collect_and_validate(candidates, config.asset_sources)
    copy = services.copy.generate_and_validate(candidates)
    tasks = services.tasks.build(candidates, snapshots, assets, copy)
    services.approval.render_review(tasks, run.output_dir / "review.html")
    services.reporting.write_all(run, decisions, tasks)
    return run
```

`publish` 命令必须要求 `--manifest`、`--selectors`、`--store`，并在打开上传页之前验证清单哈希、有效期和店铺。`run` 默认 dry-run；不能通过环境变量静默切换为 publish。

- [ ] **Step 4: 更新 SKILL.md 为推荐用户流程**

文档必须明确五个用户动作：提供月份/店铺/素材配置；处理登录或风控；查看中文审核页；一次批准不可变清单；查看上传和回查报告。删除“优先 API”这种可能误导当前实现的描述，明确正式上传使用 Playwright，官方 API 仅在未来确认存在素材接口、权限和幂等语义时重新评估。

- [ ] **Step 5: 运行完整测试并提交**

Run: `cd upload-search-materials; python -m pytest -v`

Expected: 全部 PASS。

```powershell
git add upload-search-materials/src/upload_search_materials/cli.py upload-search-materials/src/upload_search_materials/reporting.py upload-search-materials/tests/test_orchestrator.py upload-search-materials/SKILL.md upload-search-materials/agents/openai.yaml upload-search-materials/references upload-search-materials/assets/task-report-template.csv
git commit -m "feat: orchestrate reviewed tmall material batches"
```

## Task 12: 完成只读真实数据验收、skill 压力测试和生产小批量门禁

**Files:**
- Create: `upload-search-materials/tests/test_real_exports_readonly.py`
- Create: `upload-search-materials/tests/pressure-scenarios.md`
- Create: `upload-search-materials/references/production-acceptance.md`
- Modify: `upload-search-materials/SKILL.md`

**Interfaces:**
- Consumes: 完整 CLI、当前 `docs` 真实导出、用户授权的测试店铺会话。
- Produces: 只读验收报告、六个压力场景结果、生产小批量验收记录。

- [ ] **Step 1: 写真实导出的只读集成测试**

```python
from pathlib import Path
from upload_search_materials.io_tables import read_basic_materials_xlsx, read_search_materials_xlsx


DOCS = Path(__file__).parents[2] / "docs"


def test_current_exports_parse_without_writes():
    basic = read_basic_materials_xlsx(DOCS / "基础素材.xlsx")
    search = read_search_materials_xlsx(
        DOCS / "搜推素材数据经营数据_20260717_f7d80a06ea261ece5e44632121e19d40.xlsx"
    )
    assert len(basic) > 0
    assert len(search) > 0
    assert all(isinstance(row["商品ID"], str) for row in basic if row["商品ID"])
```

- [ ] **Step 2: 运行全部自动化测试和 skill 结构校验**

Run: `cd upload-search-materials; python -m pytest -v`

Expected: 全部 PASS。

Run: `python C:\Users\Admin\.codex\skills\.system\skill-creator\scripts\quick_validate.py upload-search-materials`

Expected: `Skill is valid!`。

- [ ] **Step 3: 复测六个压力场景**

`pressure-scenarios.md` 必须逐项记录输入、预期状态、实际状态和审计证据：紧急跳过批准、错误店铺、批准后素材变化、发布后超时、页面元素消失、同时命中会员日和积分。通过标准分别是拒绝发布、整批停止、受影响 item 退回审核、进入 `publish_uncertain` 并回查、返回 `SELECTOR_INVALID`、保留两个排除原因。

- [ ] **Step 4: 执行 dry-run 验收，不登录和不发布**

Run: `cd upload-search-materials; python -m upload_search_materials.cli run --mode dry-run --month 7 --store "KK Tree" --products "../docs/天猫商品信息表_产品数据表_数据总表.csv" --rules "../docs/天猫商品信息表_每月推品规则（合并）_Grid View.csv" --basic "../docs/基础素材.xlsx" --search "../docs/搜推素材数据经营数据_20260717_f7d80a06ea261ece5e44632121e19d40.xlsx" --output runs/acceptance-dry-run`

Expected: 生成来源哈希、完整 eligibility 审计、补采候选、阻断清单、两级任务和中文审核页；没有 Playwright 发布动作。

- [ ] **Step 5: 在用户明确授权后执行生产小批量验收**

先由用户登录正确店铺并处理所有人工验证，只选择 1–3 个低风险商品生成审批清单。执行：

先在当前 PowerShell 会话设置运行时文件路径：

Run: `$env:TMALL_SELECTOR_CONFIG = "D:\tmall-runtime\selectors.production.yaml"`

其中 `D:\tmall-runtime\selectors.production.yaml` 必须由用户基于当前已登录生产页面确认并保存在版本库之外；文件不存在时验收停止。

Run: `cd upload-search-materials; python -m upload_search_materials.cli publish --store "KK Tree" --run-dir runs/production-pilot --manifest runs/production-pilot/approval-manifest.json --selectors "$env:TMALL_SELECTOR_CONFIG"`

Expected: 每个批准 item 最多点击一次发布；记录远端素材 ID、审核状态和页面证据。出现错误店铺、验证码、选择器失效、素材变化或结果未知时立即停止，不扩大商品范围。

- [ ] **Step 6: 标记生产可用性的严格条件**

只有以下证据全部存在时，才在 `SKILL.md` 中标记生产可用：全部单元/集成测试通过；两份真实 XLSX 只读测试通过；六个压力场景通过；生产小批量的远端回查成功；断点恢复没有重复上传；用户确认生产选择器、NAS/飞书/光合配置和视频规格有效。完成代码不等于完成生产后台验收。

- [ ] **Step 7: 提交最终验收文档（仅 Git 工作树）**

```powershell
git add upload-search-materials/tests/test_real_exports_readonly.py upload-search-materials/tests/pressure-scenarios.md upload-search-materials/references/production-acceptance.md upload-search-materials/SKILL.md
git commit -m "test: verify end-to-end material workflow safety"
```

## 实施顺序与检查点

1. Tasks 1–4 完成后，系统应能可靠解析真实数据、排除不维护商品并计算需要页面补采的候选集合；此时仍不能上传。
2. Tasks 5–7 完成后，系统应能形成经过素材和文案校验的两级任务、中文审核页和不可变批准清单；此时仍不能声称生产可用。
3. Tasks 8–10 完成后，系统具备固定 Playwright 流程、发布后回查和断点恢复；先只用脱敏 HTML 和模拟页面测试。
4. Tasks 11–12 完成后，先运行真实数据 dry-run，再由用户单独授权 1–3 个商品做生产小批量验收。

## 当前仍需用户提供的运行时数据

- 目标天猫店铺的准确可见名称，以及生产小批量使用的商品范围。
- NAS 模特场景图根目录及“商品 ID/货号到目录”的映射规则。
- 飞书和光合素材的可读取入口或导出清单，以及每个素材的授权状态字段。
- 天猫后台接受的视频容器、编码、时长、尺寸、比例、大小、封面和水印规则。
- 通过一次人工页面分析得到的生产选择器配置；选择器不得写死在业务逻辑中。
- AI 文案禁用词、广告法审核策略，以及允许使用的年龄、性别、材质、认证和功效来源字段。
- 批准人身份记录方式和批准清单有效期。

这些配置缺失不妨碍 Tasks 1–11 的代码和脱敏测试，但对应商品必须保持 blocked 或 needs_manual_review，不能进入生产发布。
