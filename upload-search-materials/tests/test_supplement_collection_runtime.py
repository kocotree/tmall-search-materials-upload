import json

import pytest

from upload_search_materials.supplement_collection import (
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


def test_legacy_checkpoint_is_bound_to_current_attempt_on_resume(
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
    rows = collect_supplement_material_status(
        object(),
        {},
        scan_mode="high-value",
        output=output,
        checkpoint=checkpoint,
        collected_at="2026-07-29T10:01:00+08:00",
        checkpoint_context=context,
    )

    final = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert [item["商品ID"] for item in rows] == ["100", "200"]
    assert final["attempt_id"] == "attempt-current"
    assert final["last_completed_page"] == 2
    assert final["row_count"] == 2
