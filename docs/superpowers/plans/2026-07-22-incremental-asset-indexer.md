# 分片增量素材索引器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `upload-search-materials` 增加只读、可分片、中断可恢复、可增量刷新的 NAS 图片索引命令，并输出安全的商品匹配候选。

**Architecture:** 使用 `asset_index_store.py` 隔离 SQLite schema 与事务，使用 `asset_matching.py` 隔离确定性路径匹配，使用 `asset_index.py` 编排分片扫描、候选深化和输出。CLI 只负责参数、退出码和用户可见摘要；现有素材源与生产上传路径不改变。

**Tech Stack:** Python 3.11、标准库 `sqlite3`/`os.scandir`/`csv`/`json`/`hashlib`、Pillow、pytest、uv。

## Global Constraints

- 原始 NAS 文件只读；不得创建、修改、删除、裁剪或转码源文件。
- 只处理 JPG、JPEG、PNG 和 WebP；视频与飞书接入不在本计划范围。
- 快速索引只读取路径、大小、mtime 和扩展名；只有产生商品匹配的图片才读取内容并计算 SHA-256、宽高和可读性。
- 商品 ID 路径组件精确命中优先，SKU 精确命中次之，商品名称仅产生 `needs_manual_confirmation`。
- 所有素材授权固定从 `unknown` 开始；索引器不得生成批准清单或执行 dry-run、上传、发布。
- SQLite schema version 固定为 `1`；输出 CSV/JSON 使用 UTF-8。
- `--resume` 与 `--refresh` 互斥；已有索引且无模式参数时拒绝覆盖。
- 不跟随符号链接或 Windows reparse point；所有文件必须保持在声明根目录内。
- 用户已有未提交的 `test_plan.md` 修改必须保留并在文档任务中追加，不得覆盖。

---

## 文件结构

- Create: `upload-search-materials/src/upload_search_materials/asset_index_store.py` — SQLite schema、索引身份、分片/文件/匹配事务与查询。
- Create: `upload-search-materials/src/upload_search_materials/asset_matching.py` — 商品目录、文本规范化与固定优先级路径匹配。
- Create: `upload-search-materials/src/upload_search_materials/asset_index.py` — 根目录解析、分片遍历、快速索引、候选深化、刷新和输出。
- Modify: `upload-search-materials/src/upload_search_materials/cli.py` — `index-assets` 子命令和退出码。
- Create: `upload-search-materials/tests/test_asset_index_store.py`。
- Create: `upload-search-materials/tests/test_asset_matching.py`。
- Create: `upload-search-materials/tests/test_asset_index.py`。
- Modify: `upload-search-materials/tests/test_orchestrator.py`。
- Modify: `upload-search-materials/SKILL.md`。
- Modify: `upload-search-materials/references/operations-guide.md`。
- Modify: `test_plan.md`。

---

### Task 1: SQLite 索引身份与事务存储

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/asset_index_store.py`
- Create: `upload-search-materials/tests/test_asset_index_store.py`

**Interfaces:**
- Produces `SCHEMA_VERSION = 1`。
- Produces `IndexIdentity(products_path: str, products_sha256: str, roots_json: str, partition_depth: int, checkpoint_size: int)`。
- Produces `IndexedFile(source_system: str, relative_path: str, absolute_path: str, extension: str, size_bytes: int, mtime_ns: int, candidate_directory: str)`。
- Produces `AssetIndexStore.create(path: Path, identity: IndexIdentity) -> AssetIndexStore`。
- Produces `AssetIndexStore.open(path: Path, identity: IndexIdentity) -> AssetIndexStore`，身份不一致抛出 `IndexIdentityError`。
- Produces `upsert_root(source_system: str, root_path: str) -> int`。
- Produces `upsert_partition(root_id: int, relative_path: str) -> str`。
- Produces `checkpoint_files(partition_id: str, scan_id: str, rows: Sequence[IndexedFile]) -> tuple[int, ...]`。
- Produces `update_file_inspection(file_id: int, *, sha256: str, width: int | None, height: int | None, validation_status: str, reason_codes: Sequence[str]) -> None`。
- Produces `replace_file_matches(file_id: int, matches: Sequence[PathMatchRecord]) -> None`，其中 `PathMatchRecord(product_id, sku, product_title, match_type, match_status, reason_codes)` 在本模块定义，避免存储层导入匹配层。
- Produces `complete_partition`、`fail_partition`、`mark_partition_missing_files_inactive` 和只读统计查询。

- [ ] **Step 1: 写存储 RED 测试**

```python
def test_store_creates_versioned_schema_and_rejects_identity_change(tmp_path):
    path = tmp_path / "asset-index.sqlite3"
    identity = IndexIdentity("products.csv", "a" * 64, "[]", 2, 1000)
    with AssetIndexStore.create(path, identity) as store:
        assert store.schema_version() == 1
    with pytest.raises(IndexIdentityError, match="identity"):
        AssetIndexStore.open(
            path,
            IndexIdentity("other.csv", "b" * 64, "[]", 2, 1000),
        )


def test_checkpoint_upserts_without_duplicate_files(tmp_path):
    with make_store(tmp_path) as store:
        root_id = store.upsert_root("model_nas", "C:/assets")
        partition_id = store.upsert_partition(root_id, "2026/帽子")
        row = IndexedFile("model_nas", "2026/帽子/a.jpg", "C:/assets/2026/帽子/a.jpg", ".jpg", 10, 20, "2026/帽子")
        store.checkpoint_files(partition_id, "scan-1", [row])
        store.checkpoint_files(partition_id, "scan-1", [row])
        assert store.file_count(active_only=True) == 1
```

同时添加以下独立测试并使用实际 SQLite 查询断言：

- `test_failed_partition_keeps_existing_files_active`
- `test_successful_refresh_marks_only_unseen_partition_files_inactive`
- `test_changed_size_or_mtime_clears_deep_metadata_and_matches`
- `test_match_replacement_is_transactional`

- [ ] **Step 2: 验证 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_index_store.py
```

Expected: collection fails with `ModuleNotFoundError: upload_search_materials.asset_index_store`。

- [ ] **Step 3: 实现最小 SQLite schema 与事务**

创建表：

```sql
CREATE TABLE scan_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE roots (
  root_id INTEGER PRIMARY KEY,
  source_system TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  completed_at TEXT
);
CREATE TABLE partitions (
  partition_id TEXT PRIMARY KEY,
  root_id INTEGER NOT NULL REFERENCES roots(root_id),
  relative_path TEXT NOT NULL,
  status TEXT NOT NULL,
  processed_count INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  checkpoint_at TEXT,
  UNIQUE(root_id, relative_path)
);
CREATE TABLE files (
  file_id INTEGER PRIMARY KEY,
  source_system TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  absolute_path TEXT NOT NULL,
  extension TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  candidate_directory TEXT NOT NULL,
  active INTEGER NOT NULL,
  seen_scan_id TEXT NOT NULL,
  sha256 TEXT NOT NULL DEFAULT '',
  width INTEGER,
  height INTEGER,
  validation_status TEXT NOT NULL DEFAULT 'not_inspected',
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(source_system, relative_path)
);
CREATE TABLE matches (
  file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
  product_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  product_title TEXT NOT NULL,
  match_type TEXT NOT NULL,
  match_status TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  PRIMARY KEY(file_id, product_id, match_type)
);
```

`checkpoint_files` 必须使用单个事务；相同来源和相对路径执行 upsert。大小或 mtime 变化时把深度元数据恢复为未检查并删除旧 matches。只有 `complete_partition` 后才能对该分片调用 inactive 标记。

- [ ] **Step 4: 验证 GREEN 与回归**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_index_store.py tests\test_state_store.py
```

Expected: all pass。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add upload-search-materials/src/upload_search_materials/asset_index_store.py upload-search-materials/tests/test_asset_index_store.py
git commit -m "feat: add durable asset index store"
```

---

### Task 2: 确定性商品路径匹配

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/asset_matching.py`
- Create: `upload-search-materials/tests/test_asset_matching.py`

**Interfaces:**
- Consumes `ProductRecord` 和 `ProductValidationReport` from `io_tables.py`。
- Produces `PathMatch(product_id, sku, product_title, match_type, match_status, reason_codes)`。
- Produces `ProductPathMatcher.from_products(records, validation_report) -> ProductPathMatcher`。
- Produces `ProductPathMatcher.match(relative_path: Path) -> tuple[PathMatch, ...]`。
- Produces `normalize_match_text(value: str) -> str`。

- [ ] **Step 1: 写匹配 RED 测试**

```python
def test_product_id_component_wins_over_sku_and_name(product_records):
    matcher = ProductPathMatcher.from_products(product_records, clean_report(product_records))
    matches = matcher.match(Path("2026/1009488728622/KQ26021/花仙子翻翻帽/a.jpg"))
    assert [(m.product_id, m.match_type) for m in matches] == [
        ("1009488728622", "exact_product_id")
    ]


def test_name_containment_is_manual_candidate(product_records):
    matcher = ProductPathMatcher.from_products(product_records, clean_report(product_records))
    matches = matcher.match(Path("2026/达人 - 花仙子翻翻帽-椰蓉/a.jpg"))
    assert matches[0].match_type == "name_candidate"
    assert matches[0].match_status == "needs_manual_confirmation"


def test_invalid_product_rows_never_match(product_records):
    report = ProductValidationReport(1, {2: ["DUPLICATE_PRODUCT_ID"]})
    matcher = ProductPathMatcher.from_products(product_records[:1], report)
    assert matcher.match(Path("1009488728622/a.jpg")) == ()
```

补充：SKU 忽略大小写精确组件命中、短于 4 字符名称不候选、品牌前缀/空白/连接符规范化、多商品名称候选稳定排序、文件名本身不参与名称候选。

- [ ] **Step 2: 验证 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_matching.py
```

Expected: collection fails because `asset_matching` does not exist。

- [ ] **Step 3: 实现固定优先级匹配**

```python
def normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"^kk树", "", normalized)
    return re.sub(r"[\s\-—_+·]+", "", normalized)
```

先按每个目录组件的原始规范化值查商品 ID，再查 SKU；只要有更高优先级命中就不返回较低优先级。名称匹配仅遍历目录组件，不使用文件名；按 `(product_id, match_type)` 稳定去重和排序。

- [ ] **Step 4: 验证 GREEN 与商品表回归**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_matching.py tests\test_io_tables.py tests\test_eligibility.py
```

Expected: all pass。

- [ ] **Step 5: 提交 Task 2**

```powershell
git add upload-search-materials/src/upload_search_materials/asset_matching.py upload-search-materials/tests/test_asset_matching.py
git commit -m "feat: match indexed paths to product candidates"
```

---

### Task 3: 分片扫描、候选深化与恢复/刷新

**Files:**
- Create: `upload-search-materials/src/upload_search_materials/asset_index.py`
- Create: `upload-search-materials/tests/test_asset_index.py`

**Interfaces:**
- Consumes `AssetIndexStore` and `ProductPathMatcher`。
- Produces `NamedRoot.parse(value: str) -> NamedRoot`。
- Produces `IndexOptions(partition_depth: int = 2, checkpoint_size: int = 1000)`。
- Produces `IncrementalAssetIndexer(store, matcher, roots, options).run(mode: Literal["new", "resume", "refresh"]) -> ScanOutcome`。
- Produces `ScanOutcome(complete: bool, partial_failure: bool, discovered: int, indexed: int, matched: int, failed: int, elapsed_seconds: float)`。

- [ ] **Step 1: 写扫描 RED 测试**

```python
def test_fast_scan_indexes_images_without_opening_unmatched_content(tmp_path, product_csv):
    root = tmp_path / "assets"
    image = root / "unmatched" / "broken.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"not-an-image")
    outcome = run_index(product_csv, root, tmp_path / "out", mode="new")
    assert outcome.indexed == 1
    row = query_file(tmp_path / "out", "unmatched/broken.jpg")
    assert row["validation_status"] == "not_inspected"
    assert row["sha256"] == ""


def test_only_matched_image_is_hashed_and_inspected(tmp_path, product_csv):
    matched = make_png(tmp_path / "assets" / "1009488728622" / "a.png")
    unmatched = make_png(tmp_path / "assets" / "other" / "b.png")
    before = snapshot([matched, unmatched])
    run_index(product_csv, tmp_path / "assets", tmp_path / "out", mode="new")
    assert query_file(tmp_path / "out", "1009488728622/a.png")["sha256"]
    assert query_file(tmp_path / "out", "other/b.png")["sha256"] == ""
    assert snapshot([matched, unmatched]) == before
```

再增加并逐个观察 RED：

- `test_partition_ids_are_stable_at_configured_depth`
- `test_video_and_symlink_are_skipped`
- `test_keyboard_interrupt_leaves_checkpoint_resumable_without_duplicates`
- `test_resume_skips_completed_partitions_and_retries_failed_partition`
- `test_refresh_adds_new_file_and_invalidates_changed_deep_metadata`
- `test_refresh_marks_deleted_file_inactive_only_after_partition_success`
- `test_failed_refresh_partition_keeps_old_files_active`
- `test_resolved_path_outside_root_is_recorded_and_not_indexed`

- [ ] **Step 2: 验证 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_index.py
```

Expected: collection fails because `asset_index` does not exist。

- [ ] **Step 3: 实现只读分片遍历**

`NamedRoot.parse` 只在第一个 `=` 分割来源和路径；来源不能为空且只能包含字母、数字、下划线和短横线。遍历使用 `os.scandir`，对目录项调用 `is_symlink()` 并检查 Windows reparse attribute，任何链接均跳过。支持扩展名集合直接复用 `assets.IMAGE_EXTENSIONS`。

分片 ID 使用：

```python
hashlib.sha256(f"{source_system}\0{relative_partition.as_posix()}".encode("utf-8")).hexdigest()[:24]
```

每个分片扫描时，先设置 `in_progress`；每 `checkpoint_size` 个图片调用一次 `checkpoint_files`。只有枚举和候选深化都成功后设为 `completed`；`OSError` 设为 `failed` 并继续其他分片；`KeyboardInterrupt` 保留已提交检查点并向上抛出。

- [ ] **Step 4: 实现候选深化与模式门禁**

对 `matcher.match(relative_path)` 非空的文件调用现有 `inspect_asset(path, product_id, "unknown", source_system=..., sku=...)`。把 SHA-256、宽高、状态与原因写回 files，并替换 matches。名称候选固定 `needs_manual_confirmation`；ID/SKU 固定 `matched_unlicensed`。

`new` 只允许不存在数据库；`resume` 校验身份并跳过 completed；`refresh` 给全部成功枚举文件写入新 scan ID，分片完成后才把未见旧文件标为 inactive。

- [ ] **Step 5: 验证 GREEN 与素材回归**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_index.py tests\test_asset_index_store.py tests\test_asset_matching.py tests\test_assets.py
```

Expected: all pass；测试前后源文件 snapshot 相同。

- [ ] **Step 6: 提交 Task 3**

```powershell
git add upload-search-materials/src/upload_search_materials/asset_index.py upload-search-materials/tests/test_asset_index.py
git commit -m "feat: scan asset roots incrementally"
```

---

### Task 4: 候选输出与 `index-assets` CLI

**Files:**
- Modify: `upload-search-materials/src/upload_search_materials/asset_index.py`
- Modify: `upload-search-materials/src/upload_search_materials/cli.py`
- Modify: `upload-search-materials/tests/test_asset_index.py`
- Modify: `upload-search-materials/tests/test_orchestrator.py`

**Interfaces:**
- Produces `write_match_candidates(store, path: Path) -> int`。
- Produces `write_scan_summary(store, outcome, path: Path, mode: str, products_sha256: str) -> dict`。
- Produces CLI handler `_index_assets(args) -> int`。

- [ ] **Step 1: 写输出和 CLI RED 测试**

```python
def test_candidate_csv_is_grouped_utf8_and_never_confirms_license(indexed_store, tmp_path):
    count = write_match_candidates(indexed_store, tmp_path / "match-candidates.csv")
    rows = list(csv.DictReader((tmp_path / "match-candidates.csv").open(encoding="utf-8-sig")))
    assert count == 1
    assert rows[0]["license_status"] == "unknown"
    assert rows[0]["match_status"] == "needs_manual_confirmation"
    assert rows[0]["image_count"] == "2"


def test_index_assets_cli_writes_all_three_artifacts(tmp_path, product_csv, capsys):
    code = main([
        "index-assets", "--products", str(product_csv),
        "--root", f"model_nas={tmp_path / 'assets'}",
        "--output", str(tmp_path / "out"),
    ])
    assert code == 0
    assert (tmp_path / "out" / "asset-index.sqlite3").is_file()
    assert (tmp_path / "out" / "match-candidates.csv").is_file()
    assert (tmp_path / "out" / "scan-summary.json").is_file()
```

增加 CLI 断言：重复来源、缺根目录、schema 错误、已有 DB 无模式、resume/refresh 同时出现返回 `2`；部分分片失败返回 `1`；`--help` 显示所有参数。

- [ ] **Step 2: 验证 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_index.py tests\test_orchestrator.py -k "index_assets or candidate_csv or scan_summary"
```

Expected: FAIL because writers and subcommand do not exist。

- [ ] **Step 3: 实现稳定输出**

CSV 表头固定为：

```python
[
    "source_system", "candidate_directory", "product_id", "sku",
    "product_title", "match_type", "match_status", "image_count",
    "hashes_complete", "license_status", "reason_codes",
]
```

按 `(source_system, candidate_directory, product_id, match_type)` 排序。`license_status` 固定 `unknown`，原因码使用分号连接。摘要 JSON 使用 `schema_version: 1`，所有数量从 SQLite 查询，包含 `complete`、`errors`、`elapsed_seconds` 和精确恢复命令。

- [ ] **Step 4: 接入 argparse 与退出码**

在 `build_parser()` 添加 `index-assets`，参数为设计文档中的 `--products`、重复 `--root`、`--output`、`--partition-depth`、`--checkpoint-size`、`--resume`、`--refresh`。在 `main()` 的非浏览器分支调用 `_index_assets`；禁止进入 publish/browser 分支。

- [ ] **Step 5: 验证 GREEN 与 CLI 回归**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_asset_index.py tests\test_orchestrator.py tests\test_assets.py
.\.venv\Scripts\python.exe -m upload_search_materials.cli index-assets --help
```

Expected: all tests pass；帮助显示完整参数。

- [ ] **Step 6: 提交 Task 4**

```powershell
git add upload-search-materials/src/upload_search_materials/asset_index.py upload-search-materials/src/upload_search_materials/cli.py upload-search-materials/tests/test_asset_index.py upload-search-materials/tests/test_orchestrator.py
git commit -m "feat: expose incremental asset index CLI"
```

---

### Task 5: Skill 文档、验收与阶段 3 状态

**Files:**
- Modify: `upload-search-materials/SKILL.md`
- Modify: `upload-search-materials/references/operations-guide.md`
- Modify: `test_plan.md`

**Interfaces:**
- Documents exact first scan, resume and refresh commands。
- Records that index results remain unlicensed candidates and do not authorize upload。
- Records automated evidence without claiming the real 413,309-file scan completed before it actually runs。

- [ ] **Step 1: 更新 Skill 工作流和操作指南**

在素材来源步骤加入：先运行 `index-assets`，检查 `scan-summary.json` 与 `match-candidates.csv`，人工确认后才生成 `confirmed-assets.csv`。加入三条可复制命令：new、`--resume`、`--refresh`。明确视频延期、原图只读、名称候选不能自动确认。

- [ ] **Step 2: 更新 test_plan.md**

保留当前三源预检、413,309 数量和阻断证据；只把“缺少索引器”改为“索引器实现完成，待真实隔离任务运行”。目标月份、店铺、坑位和授权仍是后续完整性审查输入，不得标记阶段 3 全部通过。

- [ ] **Step 3: 运行小型功能验收**

在 `test_evidence/03-assets-video/indexer-acceptance/<timestamp>/` 创建临时商品表和三处分片图片副本，依次运行 new、制造中断后的 `--resume`、新增/修改/删除后的 `--refresh`。核对三个输出文件、数量、UTF-8、SHA-256 深化和源文件前后 snapshot。

- [ ] **Step 4: 运行最终验证**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests\ui_state.test.cjs
$env:UV_CACHE_DIR='..\test_evidence\uv-cache'; uv lock --check
$env:PYTHONUTF8='1'; .\.venv\Scripts\python.exe "$env:USERPROFILE\.codex\skills\.system\skill-creator\scripts\quick_validate.py" .
git diff --check
```

Expected: Python 和 Node 全部通过；锁文件无变化；输出 `Skill is valid!`；无 whitespace error。

- [ ] **Step 5: 提交 Task 5**

```powershell
git add upload-search-materials/SKILL.md upload-search-materials/references/operations-guide.md test_plan.md
git commit -m "docs: document incremental asset indexing"
```

---

## 完成门禁

- 每个 Task 必须先看到预期 RED，再写生产代码并得到 GREEN。
- 每个 Task 提交后必须由独立 reviewer 同时给出 spec compliance 与 code quality 结论；Critical/Important 必须修复并复审。
- 最后执行从本计划基线到 HEAD 的整分支复审。
- 本计划完成只代表索引器实现与小型验收完成；真实三处 NAS 全量索引、页面人工确认、`confirmed-assets.csv`、dry-run 和生产上传仍是后续阶段。
