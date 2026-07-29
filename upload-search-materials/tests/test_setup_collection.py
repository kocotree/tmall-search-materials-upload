import csv
import json
from pathlib import Path

import yaml
import pytest

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.io_tables import PRODUCT_REQUIRED_COLUMNS
from upload_search_materials.runtime_config import load_runtime_config
from upload_search_materials.setup_collection import process_setup_collection
from upload_search_materials.supplement_collection import (
    CheckpointIdentityError,
)


class Locator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 1 if self.selector == "[data-store-name]" and self.page.store else 0

    def inner_text(self):
        return self.page.store

    def is_visible(self):
        return False


class Page:
    url = (
        "https://myseller.taobao.com/home.htm/"
        "material-center/material-management"
    )

    def __init__(self, store="测试店铺"):
        self.store = store

    def locator(self, selector):
        return Locator(self, selector)


def write_product_table(path: Path, rows: list[dict[str, str]]) -> None:
    headers = sorted(PRODUCT_REQUIRED_COLUMNS)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def product_row(product_id: str, title: str = "分龄成长太阳镜") -> dict[str, str]:
    return {
        "商品ID": product_id,
        "商品名称（查找引用）": title,
        "货号（查找引用）": "KQ-TEST",
        "产品等级": "A",
        "链接": "https://item.taobao.com/item.htm?id=" + product_id,
        "运营": "测试",
        "组别": "测试组",
        "品类-公司维度划分": "太阳镜",
    }


def write_profile(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "profile_name": "test-store",
                "profile_version": "2026.07.29",
                "production": True,
                "supported_purposes": ["high_value_collection"],
                "store_name": "[data-store-name]",
                "human_check": "[data-human-check]",
                "promotion_tab": '[role="tab"]:has-text("搜推素材")',
                "high_value_filter": '[role="checkbox"]:has-text("搜推高价值")',
                "promotion_rows": "tbody tr",
                "promotion_next_page": 'button:has-text("下一页")',
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def prepare_session(tmp_path: Path, rows: list[dict[str, str]]):
    products = tmp_path / "products.csv"
    rules = tmp_path / "rules.csv"
    selectors = tmp_path / "selectors.local.yaml"
    write_product_table(products, rows)
    rules.write_text("month,rule\n7,include\n", encoding="utf-8")
    write_profile(selectors)
    runs_root = tmp_path / "runs"
    store = SessionStore(runs_root)
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "setup",
        {
            "store": "测试店铺",
            "products_csv": str(products),
            "rules_csv": str(rules),
        },
    )
    runtime = load_runtime_config(environ={}, start=tmp_path)
    return store, session, handoff, runtime, selectors


def collected_row(product_id="886506466908"):
    return {
        "商品ID": product_id,
        "目标容量": "9",
        "目标坑位": "9",
        "现有素材数": "2",
        "缺失数量": "7",
        "空坑位": "",
        "精确坑位状态": "not_collected",
        "远端素材ID": "",
        "素材状态": "",
        "审核状态": "",
        "状态完整": "false",
        "审核状态完整": "false",
        "状态": "needs_supplement",
        "原因码": "",
        "采集时间": "2026-07-29T10:00:00+08:00",
        "证据": "product=886506466908;target=9;current=2",
    }


def test_setup_processor_collects_with_maintained_scanner_and_is_idempotent(
    tmp_path, monkeypatch
):
    store, session, handoff, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    calls = []

    def scan(page, selector_values, **kwargs):
        calls.append(kwargs["filter_selector_key"])
        rows = [collected_row()]
        kwargs["on_page"](1, rows)
        return rows

    monkeypatch.setattr(
        "upload_search_materials.supplement_collection."
        "scan_recommended_material_status",
        scan,
    )

    first = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(),
    )
    second = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(),
    )

    assert first["status"] == "completed"
    assert second["reused"] is True
    assert calls == ["high_value_filter"]
    assert store.load_session(session.session_id)["current_stage"] == "completeness"
    matrix = json.loads(
        (
            session.path / "02-completeness" / "completeness-matrix.json"
        ).read_text(encoding="utf-8")
    )
    assert matrix["products"][0]["promotion"]["missing_count"] == 7
    assert matrix["products"][0]["promotion"]["empty_slot_indexes"] is None
    page_evidence = json.loads(
        (
            session.path
            / "collected"
            / "promotion"
            / "store-page-evidence.json"
        ).read_text(encoding="utf-8")
    )
    assert set(page_evidence["field_results"].values()) == {
        "observed_during_collection"
    }


def test_setup_processor_pauses_for_login_and_resumes_same_session(
    tmp_path, monkeypatch
):
    _, session, _, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )

    def scan(page, selector_values, **kwargs):
        rows = [collected_row()]
        kwargs["on_page"](1, rows)
        return rows

    monkeypatch.setattr(
        "upload_search_materials.supplement_collection."
        "scan_recommended_material_status",
        scan,
    )
    waiting = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(store=""),
    )
    resumed = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(),
    )

    assert waiting["reason_code"] == "LOGIN_INTERACTION_REQUIRED"
    assert resumed["status"] == "completed"


def test_setup_processor_reports_mixed_product_row_anomalies(
    tmp_path, monkeypatch
):
    _, session, _, runtime, selectors = prepare_session(
        tmp_path,
        [
            product_row("886506466908"),
            product_row(""),
            product_row("duplicate"),
        ],
    )

    def scan(page, selector_values, **kwargs):
        rows = [collected_row()]
        kwargs["on_page"](1, rows)
        return rows

    monkeypatch.setattr(
        "upload_search_materials.supplement_collection."
        "scan_recommended_material_status",
        scan,
    )
    result = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(),
    )

    anomalies = result["completeness"]["product_row_anomalies"]
    assert anomalies["valid_row_count"] == 1
    assert anomalies["blocked_row_count"] == 2
    assert anomalies["reason_codes_by_row"] == {
        "3": ["MISSING_PRODUCT_ID"],
        "4": ["INVALID_PRODUCT_ID"],
    }


def test_setup_processor_rejects_checkpoint_from_other_selector_profile(
    tmp_path, monkeypatch
):
    _, session, handoff, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    checkpoint = (
        session.path
        / "collected"
        / "promotion"
        / "promotion-material-status.checkpoint.json"
    )
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "in_progress",
                "scan_mode": "high-value",
                "session_id": session.session_id,
                "revision": handoff["revision"],
                "input_sha256": handoff["input_sha256"],
                "selector_profile_sha256": "0" * 64,
                "target_store": "测试店铺",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def scan(*args, **kwargs):
        calls.append(True)
        return [collected_row()]

    monkeypatch.setattr(
        "upload_search_materials.supplement_collection."
        "scan_recommended_material_status",
        scan,
    )
    result = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(),
    )

    assert result["status"] == "blocked"
    assert result["result"]["blocking_reasons"][0] == (
        "CHECKPOINT_IDENTITY_MISMATCH"
    )
    assert calls == []


def test_expired_claim_recovery_validates_checkpoint_before_reclaim(
    tmp_path,
):
    store, session, handoff, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="old-agent",
    )
    old_claim = store.processing_claim(session.session_id, "setup")
    state = store.load_session(session.session_id)
    state["processing_claim"]["lease_expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    store._write_json_atomic(session.path / "session.json", state)
    checkpoint = (
        session.path
        / "collected"
        / "promotion"
        / "promotion-material-status.checkpoint.json"
    )
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "in_progress",
                "scan_mode": "high-value",
                "session_id": session.session_id,
                "revision": handoff["revision"],
                "input_sha256": "wrong-input",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        CheckpointIdentityError,
        match="CHECKPOINT_IDENTITY_MISMATCH:input_sha256",
    ):
        process_setup_collection(
            runs_root=session.path.parent,
            session_id=session.session_id,
            runtime=runtime,
            selectors_path=selectors,
            page=Page(),
            claimant_id="replacement-agent",
        )

    state_after = store.load_session(session.session_id)
    assert state_after["processing_claim"]["claim_id"] == (
        old_claim["claim_id"]
    )
