import json

from upload_search_materials.approval import (
    create_manifest,
    render_review_html,
    verify_manifest,
)
from upload_search_materials.models import AssetRecord, MaterialItem, MaterialStatus


def sample_material_item(title="原标题"):
    asset = AssetRecord(
        product_id="123",
        source_path="a.png",
        asset_type="image",
        license_status="confirmed",
        sha256="a" * 64,
        validation_status="valid",
    )
    return MaterialItem(
        task_id="MAT-1",
        product_id="123",
        material_type="image_text",
        slot_index=1,
        status=MaterialStatus.READY_FOR_REVIEW,
        assets=[asset],
        title=title,
        description="原描述内容足够长，可以进入审核页面。",
        content_hash="original-hash",
    )


def test_changed_copy_invalidates_approval():
    item = sample_material_item(title="原标题")
    manifest = create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-17T10:00:00+08:00",
        valid_until="2026-07-18T10:00:00+08:00",
    )
    item.title = "审批后标题"

    result = verify_manifest(
        manifest,
        [item],
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert result.valid is False
    assert result.reason == "APPROVED_CONTENT_CHANGED"


def test_manifest_store_must_match():
    item = sample_material_item()
    manifest = create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-17T10:00:00+08:00",
        valid_until="2026-07-18T10:00:00+08:00",
    )

    result = verify_manifest(
        manifest,
        [item],
        expected_store="Other Store",
        now="2026-07-17T11:00:00+08:00",
    )

    assert result.reason == "STORE_IDENTITY_MISMATCH"


def test_manifest_entry_tampering_is_detected():
    item = sample_material_item()
    manifest = create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-17T10:00:00+08:00",
        valid_until="2026-07-18T10:00:00+08:00",
    )
    manifest["entries"][0]["title"] = "被篡改"

    result = verify_manifest(
        manifest,
        [item],
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert result.reason == "MANIFEST_HASH_INVALID"


def test_manifest_expiry_tampering_is_detected():
    item = sample_material_item()
    manifest = create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-17T10:00:00+08:00",
        valid_until="2026-07-18T10:00:00+08:00",
        run_id="RUN-1",
        source_sha256={"products": "a" * 64},
    )
    manifest["valid_until"] = "2099-07-18T10:00:00+08:00"

    result = verify_manifest(
        manifest,
        [item],
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
        expected_run_id="RUN-1",
        expected_source_sha256={"products": "a" * 64},
    )

    assert result.reason == "MANIFEST_HASH_INVALID"


def test_expired_manifest_is_rejected():
    item = sample_material_item()
    manifest = create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-17T10:00:00+08:00",
        valid_until="2026-07-17T10:30:00+08:00",
    )

    result = verify_manifest(
        manifest,
        [item],
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert result.reason == "APPROVAL_EXPIRED"


def test_future_dated_approval_is_rejected():
    item = sample_material_item()
    manifest = create_manifest(
        "KK Tree",
        [item],
        "operator",
        "2026-07-18T10:00:00+08:00",
        valid_until="2026-07-19T10:00:00+08:00",
    )

    result = verify_manifest(
        manifest,
        [item],
        expected_store="KK Tree",
        now="2026-07-17T11:00:00+08:00",
    )

    assert result.reason == "APPROVAL_NOT_YET_VALID"


def test_review_html_escapes_copy_and_shows_task_id(tmp_path):
    item = sample_material_item(title="<script>alert(1)</script>")
    output = tmp_path / "review.html"

    render_review_html([item], output, store="KK Tree")
    html = output.read_text(encoding="utf-8")

    assert "MAT-1" in html
    assert "KK Tree" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert json.dumps("123", ensure_ascii=False).strip('"') in html
