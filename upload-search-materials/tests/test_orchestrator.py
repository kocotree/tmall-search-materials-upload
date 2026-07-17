import csv
import json
from contextlib import contextmanager
from pathlib import Path

from openpyxl import Workbook
from PIL import Image

import pytest

from upload_search_materials.cli import (
    BrowserSessionRequired,
    main,
    page_context,
    partition_persisted_items,
)
from upload_search_materials.reporting import material_item_from_dict
from upload_search_materials.state_store import StateStore


PRODUCT_HEADERS = [
    "商品ID",
    "商品名称（查找引用）",
    "货号（查找引用）",
    "产品等级",
    "链接",
    "运营",
    "组别",
    "品类-公司维度划分",
]


class BrowserSentinel:
    def __init__(self):
        self.publish_page_open_count = 0


class CliFakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        if self.selector in self.page.counts:
            return self.page.counts[self.selector]
        return 1 if self.selector in self.page.texts else 0

    def inner_text(self):
        return self.page.texts.get(self.selector, "")

    def all_inner_texts(self):
        return list(self.page.lists.get(self.selector, []))

    def is_visible(self):
        return self.selector in self.page.visible

    def fill(self, value):
        self.page.filled.append((self.selector, value))

    def press(self, key):
        self.page.pressed.append((self.selector, key))

    def click(self):
        self.page.clicked.append(self.selector)


class CliFakeDownload:
    def __init__(self, source, suggested_filename):
        self.source = Path(source)
        self.suggested_filename = suggested_filename

    def save_as(self, destination):
        Path(destination).write_bytes(self.source.read_bytes())


class CliFakeDownloadInfo:
    def __init__(self, value):
        self.value = value


class CliFakePage:
    def __init__(self, downloads=None):
        self.downloads = iter(downloads or [])
        self.texts = {"#store": "KK Tree"}
        self.lists = {}
        self.counts = {}
        self.visible = set()
        self.filled = []
        self.pressed = []
        self.clicked = []

    def locator(self, selector):
        return CliFakeLocator(self, selector)

    @contextmanager
    def expect_download(self):
        yield CliFakeDownloadInfo(next(self.downloads))


def write_sources(tmp_path):
    products = tmp_path / "products.csv"
    with products.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PRODUCT_HEADERS)
        writer.writeheader()
        writer.writerow(
            {
                "商品ID": "123",
                "商品名称（查找引用）": "普通水杯",
                "货号（查找引用）": "SKU-1",
                "产品等级": "A级",
                "链接": "https://example.invalid/123",
                "运营": "小王",
                "组别": "一组",
                "品类-公司维度划分": "水杯",
            }
        )
    rules = tmp_path / "rules.csv"
    with rules.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["月份", "品类", "要推等级"])
        writer.writeheader()
        writer.writerow({"月份": "7月", "品类": "水杯", "要推等级": "A级"})
    basic = tmp_path / "basic.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["商品ID", "商品标题", "商品白底图", "短标题"])
    sheet.append([123, "普通水杯", "已完成", "水杯"])
    workbook.save(basic)
    search = tmp_path / "search.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["商品id", "商品名称", "素材id", "素材类型"])
    sheet.append([123, "普通水杯", "", ""])
    workbook.save(search)
    return products, rules, basic, search


def test_dry_run_never_opens_publish_page(tmp_path):
    products, rules, basic, search = write_sources(tmp_path)
    output = tmp_path / "run"
    browser = BrowserSentinel()

    exit_code = main(
        [
            "run",
            "--mode", "dry-run",
            "--month", "7",
            "--store", "KK Tree",
            "--products", str(products),
            "--rules", str(rules),
            "--basic", str(basic),
            "--search", str(search),
            "--output", str(output),
            "--started-at", "2026-07-17T10:00:00+08:00",
        ],
        page=browser,
    )

    assert exit_code == 0
    assert browser.publish_page_open_count == 0
    assert (output / "review.html").exists()
    assert (output / "eligibility.csv").exists()
    assert (output / "product-tasks.json").exists()
    assert (output / "material-items.json").exists()
    assert (output / "supplement-candidates.csv").exists()
    assert (output / "run.sqlite3").exists()


def test_run_defaults_to_dry_run(tmp_path):
    products, rules, basic, search = write_sources(tmp_path)
    output = tmp_path / "run"

    exit_code = main(
        [
            "run",
            "--month", "7",
            "--store", "KK Tree",
            "--products", str(products),
            "--rules", str(rules),
            "--basic", str(basic),
            "--search", str(search),
            "--output", str(output),
            "--started-at", "2026-07-17T10:00:00+08:00",
        ]
    )

    metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert metadata["mode"] == "dry-run"


def test_approve_creates_manifest_only_for_selected_ready_items(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"store": "KK Tree", "run_id": "RUN-1"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "material-items.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "MAT-1",
                    "product_id": "123",
                    "material_type": "image_text",
                    "slot_index": 1,
                    "status": "ready_for_review",
                    "assets": [
                        {
                            "product_id": "123",
                            "source_path": "a.png",
                            "sha256": "a" * 64,
                            "license_status": "confirmed",
                            "validation_status": "valid",
                        }
                    ],
                    "title": "KK树便携水杯",
                    "description": "便携水杯设计，满足日常携带和饮水使用需求。",
                    "content_hash": "hash-1",
                    "reason_codes": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "approve",
            "--run-dir", str(run_dir),
            "--task-id", "MAT-1",
            "--confirmed-by", "operator",
            "--confirmed-at", "2026-07-17T10:00:00+08:00",
            "--valid-until", "2026-07-18T10:00:00+08:00",
        ]
    )
    manifest = json.loads((run_dir / "approval-manifest.json").read_text(encoding="utf-8"))
    persisted_items = json.loads((run_dir / "material-items.json").read_text(encoding="utf-8"))
    state = StateStore(run_dir / "run.sqlite3")

    assert exit_code == 0
    assert manifest["entries"][0]["task_id"] == "MAT-1"
    assert len(manifest["manifest_sha256"]) == 64
    assert persisted_items[0]["status"] == "approved"
    assert state.item_status("MAT-1") == "approved"
    state.close()


def test_publish_requires_manifest_before_browser_use(tmp_path):
    browser = BrowserSentinel()

    exit_code = main(
        [
            "publish",
            "--run-dir", str(tmp_path),
            "--store", "KK Tree",
            "--selectors", str(tmp_path / "selectors.yaml"),
        ],
        page=browser,
    )

    assert exit_code == 2
    assert browser.publish_page_open_count == 0


def test_report_summarizes_existing_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "RUN-1", "store": "KK Tree", "mode": "dry-run"}),
        encoding="utf-8",
    )
    (run_dir / "product-tasks.json").write_text(
        json.dumps([{"status": "blocked"}, {"status": "ready_for_review"}]),
        encoding="utf-8",
    )
    (run_dir / "material-items.json").write_text("[]", encoding="utf-8")

    exit_code = main(["report", "--run-dir", str(run_dir)])
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "blocked: 1" in summary
    assert "ready_for_review: 1" in summary


def test_run_builds_reviewable_items_when_all_inputs_are_resolved(tmp_path):
    products, rules, basic, search = write_sources(tmp_path)
    backend = tmp_path / "backend.csv"
    with backend.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["商品ID", "目标坑位", "空坑位", "审核状态完整", "采集时间", "证据"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "商品ID": "123",
                "目标坑位": "3",
                "空坑位": "1;2;3",
                "审核状态完整": "true",
                "采集时间": "2026-07-17T09:00:00+08:00",
                "证据": "fixture",
            }
        )
    asset_dir = tmp_path / "assets" / "123"
    asset_dir.mkdir(parents=True)
    for index in range(9):
        Image.new("RGB", (400, 400), color=(index, index, index)).save(asset_dir / f"{index}.png")
    copy_responses = tmp_path / "copy-responses.json"
    copy_responses.write_text(
        json.dumps(
            {
                f"123:{slot}": {
                    "title": f"KK树便携水杯{slot}",
                    "description": "便携水杯设计，满足日常携带和饮水使用需求。",
                }
                for slot in (1, 2, 3)
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "resolved-run"

    exit_code = main(
        [
            "run",
            "--month", "7",
            "--store", "KK Tree",
            "--products", str(products),
            "--rules", str(rules),
            "--basic", str(basic),
            "--search", str(search),
            "--backend-status", str(backend),
            "--asset-root", str(tmp_path / "assets"),
            "--license-status", "confirmed",
            "--copy-responses", str(copy_responses),
            "--output", str(output),
            "--started-at", "2026-07-17T10:00:00+08:00",
        ]
    )
    items = json.loads((output / "material-items.json").read_text(encoding="utf-8"))
    product_tasks = json.loads((output / "product-tasks.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert len(items) == 3
    assert all(item["status"] == "ready_for_review" for item in items)
    assert product_tasks[0]["status"] == "ready_for_review"


def test_page_context_connects_to_runtime_cdp_without_storing_session():
    sentinel = object()
    calls = []

    @contextmanager
    def factory(url):
        calls.append(url)
        yield sentinel

    with page_context(None, "http://127.0.0.1:9222", factory) as resolved:
        assert resolved is sentinel

    assert calls == ["http://127.0.0.1:9222"]


def test_page_context_requires_injected_page_or_cdp_url():
    with pytest.raises(BrowserSessionRequired):
        with page_context(None, None, None):
            pass


def test_export_command_writes_two_hashed_source_records(tmp_path):
    basic = tmp_path / "basic-source.xlsx"
    search = tmp_path / "search-source.xlsx"
    basic.write_bytes(b"basic")
    search.write_bytes(b"search")
    page = CliFakePage(
        [CliFakeDownload(basic, "基础素材.xlsx"), CliFakeDownload(search, "搜推素材.xlsx")]
    )
    selectors = Path(__file__).parents[1] / "config" / "selectors.example.yaml"
    output = tmp_path / "exports"

    exit_code = main(
        [
            "export",
            "--store", "KK Tree",
            "--selectors", str(selectors),
            "--output", str(output),
            "--run-id", "RUN-1",
            "--downloaded-at", "2026-07-17T10:00:00+08:00",
        ],
        page=page,
    )
    records = json.loads((output / "source-files.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert len(records) == 2
    assert all(len(record["sha256"]) == 64 for record in records)


def test_supplement_command_writes_backend_status_csv(tmp_path):
    candidates = tmp_path / "candidates.csv"
    with candidates.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["商品ID", "reason"])
        writer.writeheader()
        writer.writerow({"商品ID": "123", "reason": "BROWSER_SUPPLEMENT_REQUIRED"})
    page = CliFakePage()
    page.texts.update({"#product-id": "123", "#desired-slots": "3坑", "#material-table": "table"})
    page.visible.add("#material-table")
    page.counts[".material-row"] = 2
    page.lists[".empty-slot"] = ["坑位3"]
    page.lists[".review-status"] = ["审核通过", "审核中"]
    selectors = Path(__file__).parents[1] / "config" / "selectors.example.yaml"
    output = tmp_path / "backend.csv"

    exit_code = main(
        [
            "supplement",
            "--store", "KK Tree",
            "--selectors", str(selectors),
            "--candidates", str(candidates),
            "--output", str(output),
            "--collected-at", "2026-07-17T10:00:00+08:00",
        ],
        page=page,
    )
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert exit_code == 0
    assert rows[0]["商品ID"] == "123"
    assert rows[0]["空坑位"] == "3"


def test_resume_partitions_uncertain_item_into_verification_only(tmp_path):
    item = material_item_from_dict(
        {
            "task_id": "MAT-1",
            "product_id": "123",
            "material_type": "image_text",
            "slot_index": 1,
            "status": "approved",
            "assets": [],
            "title": "标题",
            "description": "描述内容满足长度要求。",
        }
    )
    state = StateStore(tmp_path / "run.sqlite3")
    state.save_item("MAT-1", "publish_uncertain", evidence="timeout", attempt_count=1)

    upload_items, verification_items = partition_persisted_items(state, [item], resume=True)

    assert upload_items == []
    assert verification_items == [item]
    state.close()


def test_repeated_publish_does_not_requeue_submitted_item(tmp_path):
    item = material_item_from_dict(
        {
            "task_id": "MAT-1",
            "product_id": "123",
            "material_type": "image_text",
            "slot_index": 1,
            "status": "approved",
            "assets": [],
            "title": "标题",
            "description": "描述内容满足长度要求。",
        }
    )
    state = StateStore(tmp_path / "run.sqlite3")
    state.save_item("MAT-1", "submitted", remote_material_id="RM-1", evidence="remote row")

    upload_items, verification_items = partition_persisted_items(state, [item], resume=False)

    assert upload_items == []
    assert verification_items == []
    state.close()
