import csv
from dataclasses import replace
import json
from pathlib import Path

import yaml
import pytest

from upload_search_materials.browser.config import load_selector_profile
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.io_tables import PRODUCT_REQUIRED_COLUMNS
from upload_search_materials.lark_base_sync import (
    LarkCliResult,
    product_metadata_snapshot_path,
    refresh_product_metadata_snapshot,
)
from upload_search_materials.runtime_config import LarkBaseConfig, load_runtime_config
from upload_search_materials.setup_collection import (
    process_setup_collection,
    refresh_completeness_product_metadata,
)
from upload_search_materials.supplement_collection import (
    CheckpointIdentityError,
    write_backend_status,
)


class Locator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        if self.selector == "[data-store-name]" and self.page.store:
            return 1
        if self.selector == "[data-human-check]":
            return int(self.page.human_checks_remaining > 0)
        return 0

    def inner_text(self):
        return self.page.store

    def is_visible(self):
        return (
            self.selector == "[data-human-check]"
            and self.page.human_checks_remaining > 0
        )


class Page:
    url = (
        "https://myseller.taobao.com/home.htm/"
        "material-center/material-management"
    )

    def __init__(self, store="测试店铺"):
        self.store = store
        self.human_checks_remaining = 0

    def locator(self, selector):
        return Locator(self, selector)

    def wait_for_timeout(self, _milliseconds):
        self.human_checks_remaining = max(
            0, self.human_checks_remaining - 1
        )


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
                "promotion_current_page": "[aria-current=page]",
                "promotion_first_page": "[data-page='1']",
                "promotion_terminal_page": "[data-page-last=true]",
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


def emit_verified_single_page(kwargs, rows):
    identity = "a" * 64
    kwargs["on_pagination_event"](
        {
            "event_type": "origin",
            "reason_code": "PAGINATION_ORIGIN_VERIFIED",
            "current_page": 1,
            "terminal_page": 1,
            "next_enabled": False,
            "ordered_product_id_hash": identity,
            "product_count": len(rows),
        }
    )
    kwargs["on_page"](1, rows)
    kwargs["on_pagination_event"](
        {
            "event_type": "terminal",
            "reason_code": "PAGINATION_TERMINAL_VERIFIED",
            "current_page": 1,
            "terminal_page": 1,
            "next_enabled": False,
            "ordered_product_id_hash": identity,
            "product_count": len(rows),
        }
    )


def test_setup_processor_collects_with_maintained_scanner_and_is_idempotent(
    tmp_path, monkeypatch
):
    store, session, handoff, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    runtime = replace(
        runtime,
        user_data_root=tmp_path / "user-data",
        lark_base=LarkBaseConfig(
            enabled=True,
            product_base_token="base-token",
            product_table_id="产品数据表",
        ),
    )
    refresh_product_metadata_snapshot(
        runtime.lark_base,
        product_metadata_snapshot_path(runtime.user_data_root),
        runner=lambda _args, _timeout: LarkCliResult(
            ok=True,
            payload={
                "items": [
                    {
                        "fields": {
                            "商品ID": "886506466908",
                            "运营": "飞书负责人",
                        }
                    }
                ],
                "has_more": False,
            },
        ),
    )
    calls = []

    def scan(page, selector_values, **kwargs):
        calls.append(kwargs["filter_selector_key"])
        rows = [collected_row()]
        emit_verified_single_page(kwargs, rows)
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
    attempt_id = first["result"]["attempt_id"]
    attempt = (
        session.path
        / "collected"
        / "promotion"
        / "attempts"
        / f"a-{attempt_id[:12]}"
    )
    assert (attempt / "promotion-material-status.csv").is_file()
    assert (attempt / "promotion-material-status.checkpoint.json").is_file()
    assert (attempt / "result.json").is_file()
    assert (
        session.path
        / "collected"
        / "promotion"
        / "current"
        / "publication.json"
    ).is_file()
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
    assert matrix["products"][0]["owner"] == "飞书负责人"
    assert matrix["lark_product_sync"]["status"] == "completed"
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


def test_setup_processor_reuses_verified_collection_after_unchanged_back_submit(
    tmp_path, monkeypatch
):
    store, session, first_handoff, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    calls = []

    def scan(page, selector_values, **kwargs):
        calls.append(kwargs["filter_selector_key"])
        rows = [collected_row()]
        emit_verified_single_page(kwargs, rows)
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
    state = store.load_session(session.session_id)
    store.reopen_previous_stage(
        session.session_id,
        "completeness",
        expected_revision=state["stages"]["completeness"]["revision"],
    )
    second_handoff = store.save_input(
        session.session_id,
        "setup",
        {
            "store": "测试店铺",
            "products_csv": str(tmp_path / "products.csv"),
            "rules_csv": str(tmp_path / "rules.csv"),
        },
    )

    second = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page(),
    )

    assert second["status"] == "completed", second
    assert second["reused"] is True
    assert calls == ["high_value_filter"]
    assert second_handoff["revision"] == first_handoff["revision"] + 1
    assert second["result"]["revision"] == second_handoff["revision"]
    assert second["result"]["attempt_id"] != first["result"]["attempt_id"]
    checkpoint = json.loads(
        (
            session.path
            / "collected"
            / "promotion"
            / "promotion-material-status.checkpoint.json"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["revision"] == second_handoff["revision"]
    assert checkpoint["input_sha256"] == second_handoff["input_sha256"]
    assert checkpoint["attempt_id"] == second["result"]["attempt_id"]
    assert checkpoint["reused_from_attempt_id"] == first["result"]["attempt_id"]


def test_setup_processor_starts_isolated_collection_after_changed_back_submit(
    tmp_path, monkeypatch
):
    store, session, _, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    calls = []

    def scan(page, selector_values, **kwargs):
        calls.append(kwargs["filter_selector_key"])
        rows = [collected_row()]
        emit_verified_single_page(kwargs, rows)
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
    state = store.load_session(session.session_id)
    store.reopen_previous_stage(
        session.session_id,
        "completeness",
        expected_revision=state["stages"]["completeness"]["revision"],
    )
    store.save_input(
        session.session_id,
        "setup",
        {
            "store": "变更店铺",
            "products_csv": str(tmp_path / "products.csv"),
            "rules_csv": str(tmp_path / "rules.csv"),
        },
    )

    second = process_setup_collection(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        page=Page("变更店铺"),
    )

    assert second["status"] == "completed", second
    assert second["reused"] is False
    assert calls == ["high_value_filter", "high_value_filter"]
    assert second["result"]["attempt_id"] != first["result"]["attempt_id"]


def test_refresh_completeness_product_metadata_reuses_collection_snapshot(tmp_path):
    store, session, _, runtime, _ = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    inputs = session.path / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    write_product_table(
        inputs / "products.csv", [product_row("886506466908")]
    )
    (inputs / "scan-summary.json").write_text(
        json.dumps({"row_count": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    collected = (
        session.path
        / "collected"
        / "promotion"
        / "current"
        / "promotion-material-status.csv"
    )
    collected.parent.mkdir(parents=True, exist_ok=True)
    write_backend_status(collected, [collected_row()])
    matrix_path = (
        session.path / "02-completeness" / "completeness-matrix.json"
    )
    existing_matrix = {
        "products": [],
        "summary": {},
        "pagination": {"terminal_page": 29},
        "product_row_anomalies": {"blocked_row_count": 0},
    }
    matrix_path.write_text(
        json.dumps(existing_matrix, ensure_ascii=False),
        encoding="utf-8",
    )
    state = store.load_session(session.session_id)
    state["current_stage"] = "completeness"
    state["stages"]["completeness"]["status"] = "needs_user_input"
    store._write_session_state(session.session_id, state)
    store.write_review_context(
        session.session_id,
        "completeness",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "stage_id": "completeness",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "已采集 1 个搜推高价值商品",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "选择商品",
            "created_at": "2026-08-26T10:00:00+08:00",
            "data": existing_matrix,
        },
    )
    runtime = replace(
        runtime,
        user_data_root=tmp_path / "user-data",
        lark_base=LarkBaseConfig(
            enabled=True,
            product_base_token="base-token",
            product_table_id="产品数据表",
        ),
    )

    def runner(args, _timeout):
        assert "+record-list" in args
        return LarkCliResult(
            ok=True,
            payload={
                "items": [
                    {
                        "record_id": "rec1",
                        "fields": {
                            "商品ID": "886506466908",
                            "负责人": "飞书负责人",
                            "产品等级": "S级",
                        },
                    }
                ],
                "has_more": False,
            },
        )

    refresh_product_metadata_snapshot(
        runtime.lark_base,
        product_metadata_snapshot_path(runtime.user_data_root),
        runner=runner,
    )

    result = refresh_completeness_product_metadata(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
    )

    assert result["status"] == "refreshed"
    refreshed = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert refreshed["products"][0]["owner"] == "飞书负责人"
    assert refreshed["products"][0]["product_grade"] == "S级"
    assert refreshed["pagination"] == {"terminal_page": 29}
    assert refreshed["product_row_anomalies"] == {"blocked_row_count": 0}
    assert "base_token" not in refreshed["lark_product_sync"]
    context = store.read_optional_stage_document(
        session.session_id, "completeness", "review-context"
    )
    assert context["data"]["products"][0]["owner"] == "飞书负责人"
    assert context["data"]["products"][0]["product_grade"] == "S级"

    second = refresh_completeness_product_metadata(
        runs_root=session.path.parent,
        session_id=session.session_id,
        runtime=runtime,
    )
    assert second["status"] == "unchanged"


def test_setup_processor_checkpoints_human_check_and_auto_resumes(
    tmp_path, monkeypatch
):
    _, session, _, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )
    page = Page()
    phases = []

    def scan(_page, _selector_values, **kwargs):
        rows = [collected_row()]
        identity = "b" * 64
        kwargs["on_pagination_event"](
            {
                "event_type": "origin",
                "reason_code": "PAGINATION_ORIGIN_VERIFIED",
                "current_page": 1,
                "terminal_page": 1,
                "next_enabled": False,
                "ordered_product_id_hash": identity,
                "product_count": 1,
            }
        )
        kwargs["on_page"](1, rows)
        page.human_checks_remaining = 2
        kwargs["human_check_waiter"](1, "after_checkpoint")
        kwargs["on_pagination_event"](
            {
                "event_type": "terminal",
                "reason_code": "PAGINATION_TERMINAL_VERIFIED",
                "current_page": 1,
                "terminal_page": 1,
                "next_enabled": False,
                "ordered_product_id_hash": identity,
                "product_count": 1,
            }
        )
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
        page=page,
        progress_callback=lambda phase, **_kwargs: phases.append(phase),
    )

    attempt_id = result["result"]["attempt_id"]
    checkpoint = json.loads(
        (
            session.path
            / "collected"
            / "promotion"
            / "attempts"
            / f"a-{attempt_id[:12]}"
            / "human-checkpoint.json"
        ).read_text(encoding="utf-8")
    )
    assert result["status"] == "completed"
    assert checkpoint["status"] == "resolved"
    assert checkpoint["last_completed_page"] == 1
    assert [event["event_type"] for event in checkpoint["events"]] == [
        "detected",
        "resolved",
    ]
    assert "waiting_human_check" in phases
    assert "collecting_page" in phases


def test_setup_processor_pauses_for_login_and_resumes_same_session(
    tmp_path, monkeypatch
):
    _, session, _, runtime, selectors = prepare_session(
        tmp_path, [product_row("886506466908")]
    )

    def scan(page, selector_values, **kwargs):
        rows = [collected_row()]
        emit_verified_single_page(kwargs, rows)
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
        emit_verified_single_page(kwargs, rows)
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


def test_setup_processor_archives_checkpoint_from_prior_attempt(
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
    profile = load_selector_profile(
        selectors,
        purpose="high_value_collection",
        production=True,
    )
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "needs_manual_review",
                "scan_mode": "high-value",
                "last_completed_page": 0,
                "session_id": session.session_id,
                "revision": handoff["revision"],
                "input_sha256": handoff["input_sha256"],
                "selector_profile_sha256": profile.sha256,
                "attempt_id": "attempt-old",
                "target_store": "测试店铺",
            }
        ),
        encoding="utf-8",
    )

    def scan(*args, **kwargs):
        rows = [collected_row()]
        emit_verified_single_page(kwargs, rows)
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

    archived = (
        checkpoint.parent
        / "attempts"
        / "a-attempt-old"
        / checkpoint.name
    )
    assert result["status"] == "completed"
    assert archived.is_file()
    assert json.loads(archived.read_text(encoding="utf-8"))[
        "attempt_id"
    ] == "attempt-old"
    current_attempt_id = result["result"]["attempt_id"]
    assert current_attempt_id != "attempt-old"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))[
        "attempt_id"
    ] == current_attempt_id


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
