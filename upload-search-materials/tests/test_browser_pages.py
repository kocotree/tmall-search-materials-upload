from contextlib import contextmanager
import hashlib
from pathlib import Path

import pytest

from upload_search_materials.browser.config import SelectorConfigError, load_selectors
from upload_search_materials.browser.export_page import export_reports
from upload_search_materials.browser.material_page import (
    SelectorInvalidError,
    supplement_material_status,
)
from upload_search_materials.browser.session import (
    HumanCheckRequired,
    StoreIdentityError,
    assert_store_identity,
    detect_human_check,
)
from upload_search_materials.browser.upload_page import upload_approved_item
from upload_search_materials.browser.verifier import verify_remote_item
from upload_search_materials.approval import create_manifest
from upload_search_materials.models import AssetRecord, MaterialItem, MaterialStatus
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


REQUIRED_SELECTOR_VALUES = {
    "store_name": "#store",
    "human_check": "#human-check",
    "export_basic": "#export-basic",
    "export_search": "#export-search",
    "product_search": "#product-search",
    "product_id": "#product-id",
    "desired_slots": "#desired-slots",
    "material_table": "#material-table",
    "material_rows": ".material-row",
    "empty_slots": ".empty-slot",
    "review_status": ".review-status",
    "image_text_action": "#image-text-action",
    "video_action": "#video-action",
    "file_input": "#file-input",
    "title_input": "#title-input",
    "description_input": "#description-input",
    "publish_button": "#publish",
    "success_signal": "#success",
    "remote_material_id": "#remote-id",
    "remote_material_table": "#remote-table",
    "remote_material_fingerprint": "#remote-fingerprint",
    "remote_material_status": "#remote-status",
    "remote_material_slot": "#remote-slot",
    "remote_material_time": "#remote-time",
}


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def inner_text(self):
        return self.page.texts.get(self.selector, "")

    def all_inner_texts(self):
        return list(self.page.lists.get(self.selector, []))

    def count(self):
        if self.selector in self.page.counts:
            return self.page.counts[self.selector]
        return 1 if self.selector in self.page.texts else 0

    def is_visible(self):
        return self.selector in self.page.visible

    def fill(self, value):
        self.page.filled.append((self.selector, value))

    def set_input_files(self, values):
        self.page.input_files.append((self.selector, list(values)))

    def press(self, key):
        self.page.pressed.append((self.selector, key))

    def click(self):
        self.page.clicked.append(self.selector)
        error = self.page.click_errors.get(self.selector)
        if error:
            raise error


class FakePage:
    def __init__(self):
        self.texts = {}
        self.lists = {}
        self.counts = {}
        self.visible = set()
        self.filled = []
        self.pressed = []
        self.clicked = []
        self.input_files = []
        self.click_errors = {}

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeDownload:
    def __init__(self, source, suggested_filename):
        self.source = Path(source)
        self.suggested_filename = suggested_filename

    def save_as(self, destination):
        Path(destination).write_bytes(self.source.read_bytes())


class FakeDownloadInfo:
    def __init__(self, download):
        self.value = download


class FakeExportPage(FakePage):
    def __init__(self, downloads):
        super().__init__()
        self.downloads = iter(downloads)

    @contextmanager
    def expect_download(self):
        yield FakeDownloadInfo(next(self.downloads))


def write_selectors(path, values):
    import yaml

    path.write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")


def test_missing_required_selector_fails_configuration(tmp_path):
    path = tmp_path / "selectors.yaml"
    values = dict(REQUIRED_SELECTOR_VALUES)
    del values["publish_button"]
    write_selectors(path, values)

    with pytest.raises(SelectorConfigError, match="publish_button"):
        load_selectors(path)


def test_wrong_store_stops_batch():
    page = FakePage()
    page.texts["#store"] = "Other Store"

    with pytest.raises(StoreIdentityError):
        assert_store_identity(page, "#store", "KK Tree")


def test_human_check_stops_batch():
    page = FakePage()
    page.visible.add("#human-check")

    with pytest.raises(HumanCheckRequired):
        detect_human_check(page, "#human-check")


def test_missing_material_table_is_not_interpreted_as_zero():
    page = FakePage()
    page.texts["#product-id"] = "123"
    page.texts["#desired-slots"] = "3坑"
    selectors = dict(REQUIRED_SELECTOR_VALUES)

    rows = supplement_material_status(
        page,
        selectors,
        ["123"],
        collected_at="2026-07-17T10:00:00+08:00",
    )

    assert rows[0]["状态"] == "needs_manual_review"
    assert rows[0]["原因码"] == "SELECTOR_INVALID"
    assert rows[0]["现有素材数"] == ""
    assert rows[0]["空坑位"] == ""
    assert "failed_field=material_table" in rows[0]["证据"]


def test_material_status_supplement_reads_exact_product():
    page = FakePage()
    page.texts.update({"#product-id": "123", "#desired-slots": "3坑", "#material-table": "table"})
    page.visible.add("#material-table")
    page.counts[".material-row"] = 2
    page.lists[".empty-slot"] = ["坑位3"]
    page.lists[".review-status"] = ["审核通过", "审核中"]

    rows = supplement_material_status(
        page,
        REQUIRED_SELECTOR_VALUES,
        ["123"],
        collected_at="2026-07-17T10:00:00+08:00",
    )

    assert rows == [
        {
            "商品ID": "123",
            "目标坑位": "3",
            "现有素材数": "2",
            "空坑位": "3",
            "审核状态": "审核通过;审核中",
            "审核状态完整": "true",
            "状态": "ready_for_review",
            "原因码": "",
            "采集时间": "2026-07-17T10:00:00+08:00",
            "证据": "product=123;rows=2;selector_version=runtime",
        }
    ]


def test_export_reports_preserves_both_downloads_and_hashes(tmp_path):
    source_basic = tmp_path / "source-basic.xlsx"
    source_search = tmp_path / "source-search.xlsx"
    source_basic.write_bytes(b"basic")
    source_search.write_bytes(b"search")
    page = FakeExportPage(
        [
            FakeDownload(source_basic, "基础素材.xlsx"),
            FakeDownload(source_search, "搜推素材.xlsx"),
        ]
    )

    records = export_reports(
        page,
        REQUIRED_SELECTOR_VALUES,
        tmp_path / "downloads",
        run_id="RUN-1",
        downloaded_at="2026-07-17T10:00:00+08:00",
    )

    assert len(records) == 2
    assert all(Path(record.path).exists() for record in records)
    assert all(len(record.sha256) == 64 for record in records)
    assert page.clicked == ["#export-basic", "#export-search"]


def approved_item(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"image")
    asset = AssetRecord(
        product_id="123",
        source_path=str(image),
        asset_type="image",
        license_status="confirmed",
        sha256=hashlib.sha256(b"image").hexdigest(),
        validation_status="valid",
    )
    return MaterialItem(
        task_id="MAT-1",
        product_id="123",
        material_type="image_text",
        slot_index=1,
        status=MaterialStatus.APPROVED,
        assets=[asset],
        title="KK树便携水杯",
        description="便携水杯设计，满足日常携带和饮水使用需求。",
        content_hash="fingerprint-1",
    )


def approval_manifest(item):
    return create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-17T10:00:00+08:00",
        valid_until="2026-07-18T10:00:00+08:00",
    )


def configured_upload_page():
    page = FakePage()
    page.texts.update(
        {
            "#store": "KK Tree",
            "#product-id": "123",
            "#desired-slots": "3坑",
            "#material-table": "table",
        }
    )
    page.visible.add("#material-table")
    page.lists[".empty-slot"] = ["坑位1", "坑位2", "坑位3"]
    return page


def test_publish_timeout_does_not_click_twice(tmp_path):
    page = configured_upload_page()
    page.click_errors["#publish"] = PlaywrightTimeoutError("timeout")
    item = approved_item(tmp_path)

    outcome = upload_approved_item(
        page,
        item,
        approval_manifest(item),
        REQUIRED_SELECTOR_VALUES,
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert page.clicked.count("#publish") == 1
    assert outcome.status == "publish_uncertain"
    assert outcome.retry_allowed is False


def test_before_publish_checkpoint_runs_before_the_single_click(tmp_path):
    page = configured_upload_page()
    page.visible.add("#success")
    page.texts["#remote-id"] = "RM-1"
    item = approved_item(tmp_path)
    checkpoints = []

    def checkpoint():
        assert "#publish" not in page.clicked
        checkpoints.append("uploading-persisted")

    outcome = upload_approved_item(
        page,
        item,
        approval_manifest(item),
        REQUIRED_SELECTOR_VALUES,
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
        before_publish=checkpoint,
    )

    assert checkpoints == ["uploading-persisted"]
    assert page.clicked.count("#publish") == 1
    assert outcome.status == "submitted"


def test_changed_approved_content_stops_before_publish(tmp_path):
    page = configured_upload_page()
    item = approved_item(tmp_path)
    manifest = approval_manifest(item)
    item.title = "审批后修改"

    outcome = upload_approved_item(
        page,
        item,
        manifest,
        REQUIRED_SELECTOR_VALUES,
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "APPROVED_CONTENT_CHANGED"
    assert "#publish" not in page.clicked


def test_replaced_asset_bytes_stop_before_publish(tmp_path):
    page = configured_upload_page()
    item = approved_item(tmp_path)
    manifest = approval_manifest(item)
    Path(item.assets[0].source_path).write_bytes(b"replaced after approval")

    outcome = upload_approved_item(
        page,
        item,
        manifest,
        REQUIRED_SELECTOR_VALUES,
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "APPROVED_CONTENT_CHANGED"
    assert "#publish" not in page.clicked


def test_successful_publish_records_remote_id(tmp_path):
    page = configured_upload_page()
    page.visible.add("#success")
    page.texts["#remote-id"] = "RM-1"
    item = approved_item(tmp_path)

    outcome = upload_approved_item(
        page,
        item,
        approval_manifest(item),
        REQUIRED_SELECTOR_VALUES,
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert outcome.status == "submitted"
    assert outcome.remote_material_id == "RM-1"
    assert page.input_files == [("#file-input", [item.assets[0].source_path])]


def test_remote_match_resolves_uncertain(tmp_path):
    page = configured_upload_page()
    page.visible.add("#remote-table")
    page.texts.update(
        {
            "#remote-id": "RM-1",
            "#remote-fingerprint": "fingerprint-1",
            "#remote-status": "审核中",
            "#remote-slot": "1",
            "#remote-time": "2026-07-17T11:00:05+08:00",
        }
    )
    item = approved_item(tmp_path)

    outcome = verify_remote_item(
        page,
        item,
        REQUIRED_SELECTOR_VALUES,
        expected_store="KK Tree",
        submitted_at="2026-07-17T11:00:00+08:00",
    )

    assert outcome.status == "under_review"
    assert outcome.remote_material_id == "RM-1"
    assert outcome.retry_allowed is False
