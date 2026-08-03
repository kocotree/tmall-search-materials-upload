from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from upload_search_materials.cli import build_parser
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.product_selection_handoff import (
    ProductSelectionProcessingError,
    process_product_selection_handoff,
)


FIELDS = [
    "folder_id",
    "source_system",
    "absolute_path",
    "relative_path",
    "folder_name",
    "product_id",
    "sku",
    "product_title",
    "match_type",
    "match_status",
]


def _write_shared_index(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "folder-candidates.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "folder_id": "folder-1",
                    "source_system": "source-a",
                    "absolute_path": "X:/materials/product-one",
                    "relative_path": "materials/product-one",
                    "folder_name": "product-one",
                    "product_id": "1",
                    "sku": "SKU-1",
                    "product_title": "Product One",
                    "match_type": "exact_product_id",
                    "match_status": "matched",
                },
                {
                    "folder_id": "folder-unselected",
                    "source_system": "source-a",
                    "absolute_path": "X:/materials/product-two",
                    "relative_path": "materials/product-two",
                    "folder_name": "product-two",
                    "product_id": "2",
                    "sku": "SKU-2",
                    "product_title": "Product Two",
                    "match_type": "exact_product_id",
                    "match_status": "matched",
                },
            ]
        )
    (root / "folder-scan-summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "complete": True,
                "candidate_rows": 2,
            }
        ),
        encoding="utf-8",
    )


def _submitted_selection(tmp_path: Path) -> tuple[SessionStore, str, dict]:
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    setup = store.save_input(session.session_id, "setup", {"store": "shop"})
    store.write_result(
        session.session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup complete",
    )
    handoff = store.save_input(
        session.session_id,
        "completeness",
        {"selected_product_ids": ["1"]},
    )
    return store, session.session_id, handoff


def test_one_processor_prepares_folder_review_and_advances_stage(tmp_path):
    store, session_id, handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "shared-folder-index"
    _write_shared_index(index_root)

    result = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
    )

    assert result == {
        "status": "completed",
        "idempotent": False,
        "session_id": session_id,
        "completeness_revision": handoff["revision"],
        "requested_products": 1,
        "matched_products": 1,
        "candidate_rows": 1,
        "unmatched_products": 0,
        "next_stage": "asset_matching",
        "folder_review": str(
            store._stage_path(session_id, "asset_matching")
            / "folder-review.json"
        ),
    }
    state = store.load_session(session_id)
    assert state["current_stage"] == "asset_matching"
    assert state["stages"]["completeness"]["status"] == "completed"
    assert state["stages"]["asset_matching"]["status"] == "needs_user_input"
    context = store.read_optional_stage_document(
        session_id, "asset_matching", "review-context"
    )
    assert context["data"]["workflow_step"] == "folder_review"
    assert [
        item["folder_id"] for item in context["data"]["folder_candidates"]
    ] == ["folder-1"]
    assert context["data"]["product_selection_identity"] == {
        "revision": handoff["revision"],
        "input_sha256": handoff["input_sha256"],
        "selected_product_ids": ["1"],
        "shared_candidates_sha256": context["data"][
            "product_selection_identity"
        ]["shared_candidates_sha256"],
    }
    assert not (
        store._stage_path(session_id, "asset_matching")
        / "folder-index.sqlite3"
    ).exists()

    repeated = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
    )
    assert repeated["status"] == "completed"
    assert repeated["idempotent"] is True


def test_failure_writes_codex_diagnostic_and_same_entry_can_resume(tmp_path):
    store, session_id, handoff = _submitted_selection(tmp_path)
    index_root = tmp_path / "missing-folder-index"

    with pytest.raises(ProductSelectionProcessingError) as raised:
        process_product_selection_handoff(
            store,
            session_id,
            folder_index_root=index_root,
            claimant_id="test-codex",
        )

    assert raised.value.reason_code == "FOLDER_INDEX_NOT_READY"
    diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert diagnostic["phase"] == "snapshot_candidates"
    assert diagnostic["revision"] == handoff["revision"]
    assert diagnostic["input_sha256"] == handoff["input_sha256"]
    assert diagnostic["codex_recovery"]["retry_same_handoff"] is True
    assert diagnostic["codex_recovery"]["command"][1] == (
        "process-product-selection"
    )
    failed = store.read_optional_stage_document(
        session_id, "completeness", "result"
    )
    assert failed["status"] == "blocked"
    assert failed["blocking_reasons"] == ["FOLDER_INDEX_NOT_READY"]
    assert store.load_session(session_id)["processing_claim"] is None

    _write_shared_index(index_root)
    resumed = process_product_selection_handoff(
        store,
        session_id,
        folder_index_root=index_root,
        claimant_id="test-codex",
    )
    assert resumed["status"] == "completed"
    assert store.load_session(session_id)["current_stage"] == "asset_matching"
    resolved_diagnostic = json.loads(
        raised.value.diagnostic_path.read_text(encoding="utf-8")
    )
    assert resolved_diagnostic["status"] == "resolved"
    assert resolved_diagnostic["resolved_at"]


def test_cli_exposes_single_product_selection_entry():
    args = build_parser().parse_args(
        [
            "process-product-selection",
            "--runs-root",
            "runs",
            "--session",
            "session-1",
        ]
    )
    assert args.command == "process-product-selection"
    assert args.claimant_id == "codex-agent"
