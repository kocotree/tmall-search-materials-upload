import json
import hashlib

import pytest

from upload_search_materials.supplement_collection import (
    CheckpointIdentityError,
    collect_supplement_material_status,
)


def row(product_id: str) -> dict[str, str]:
    return {
        "商品ID": product_id,
        "目标容量": "9",
        "目标坑位": "9",
        "现有素材数": "0",
        "缺失数量": "9",
    }


def test_stale_claim_is_rejected_before_checkpoint_write(
    tmp_path, monkeypatch
):
    output = tmp_path / "status.csv"
    checkpoint = tmp_path / "checkpoint.json"

    def scan(_page, _selectors, **kwargs):
        kwargs["on_page"](1, [row("100")])
        return [row("100")]

    monkeypatch.setattr(
        "upload_search_materials.supplement_collection."
        "scan_recommended_material_status",
        scan,
    )

    def reject(_page, _rows):
        raise RuntimeError("PROCESSING_CLAIM_STALE")

    with pytest.raises(RuntimeError, match="PROCESSING_CLAIM_STALE"):
        collect_supplement_material_status(
            object(),
            {},
            scan_mode="high-value",
            output=output,
            checkpoint=checkpoint,
            collected_at="2026-07-29T10:00:00+08:00",
            checkpoint_context={
                "session_id": "session",
                "revision": 1,
                "input_sha256": "a" * 64,
                "attempt_id": "attempt-new",
            },
            before_checkpoint=reject,
        )

    assert not output.exists()
    assert not checkpoint.exists()


def test_legacy_checkpoint_without_pagination_evidence_is_rejected(
    tmp_path, monkeypatch
):
    output = tmp_path / "status.csv"
    checkpoint = tmp_path / "checkpoint.json"
    context = {
        "session_id": "session",
        "revision": 1,
        "input_sha256": "a" * 64,
        "attempt_id": "attempt-current",
    }
    output.write_text(
        "\ufeff商品ID,目标容量,目标坑位,现有素材数,缺失数量\n"
        "100,9,9,0,9\n",
        encoding="utf-8",
    )
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "in_progress",
                "scan_mode": "high-value",
                "last_completed_page": 1,
                "row_count": 1,
                "session_id": "session",
                "revision": 1,
                "input_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    def scan(_page, _selectors, **kwargs):
        assert kwargs["skip_completed_pages"] == 1
        values = [*kwargs["initial_rows"], row("200")]
        kwargs["on_page"](2, values)
        return values

    monkeypatch.setattr(
        "upload_search_materials.supplement_collection."
        "scan_recommended_material_status",
        scan,
    )
    with pytest.raises(
        CheckpointIdentityError, match="PAGINATION_EVIDENCE_LEGACY"
    ):
        collect_supplement_material_status(
            object(),
            {},
            scan_mode="high-value",
            output=output,
            checkpoint=checkpoint,
            collected_at="2026-07-29T10:01:00+08:00",
            checkpoint_context=context,
        )

    final = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert final["attempt_id"] == "attempt-current"
    assert final["identity_upgraded_at"]


def test_quarantined_false_success_preserves_original_files(tmp_path):
    output = tmp_path / "status.csv"
    checkpoint = tmp_path / "checkpoint.json"
    output.write_text("商品ID\n100\n", encoding="utf-8")
    checkpoint.write_text(
        json.dumps({"status": "complete"}),
        encoding="utf-8",
    )
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (output, checkpoint)
    }

    with pytest.raises(
        CheckpointIdentityError,
        match="PAGINATION_HISTORICAL_OUTPUT_QUARANTINED",
    ):
        collect_supplement_material_status(
            object(),
            {},
            scan_mode="high-value",
            output=output,
            checkpoint=checkpoint,
            collected_at="2026-07-30T10:00:00+08:00",
            checkpoint_context={
                "session_id": "20260730_032916",
            },
        )

    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (output, checkpoint)
    }
    assert after == before
