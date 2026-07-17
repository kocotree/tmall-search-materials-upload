from upload_search_materials.copywriting import CopyResult
from upload_search_materials.models import AssetRecord, MaterialStatus, ProductRecord, ProductStatus
from upload_search_materials.tasks import build_material_items, build_product_tasks


def copy_result(title="KK树便携水杯"):
    return CopyResult(
        title=title,
        description="便携水杯设计，满足日常携带和饮水使用需求。",
        source_fields={"品类": "水杯", "卖点1": "便携"},
        raw_output={"title": title},
        generated_at="2026-07-17T10:00:00+08:00",
        status="valid",
        reason_codes=[],
    )


def asset(name, kind="image"):
    return AssetRecord(
        product_id="123",
        source_path=name,
        asset_type=kind,
        license_status="confirmed",
        sha256=(name * 64)[:64],
        validation_status="valid",
    )


def test_material_item_ids_include_run_product_type_and_slot():
    items = build_material_items(
        run_id="RUN-1",
        product_id="123",
        target_slot_indexes=[2, 3],
        asset_groups=[
            ("video", [asset("v", "video")]),
            ("image_text", [asset("a"), asset("b"), asset("c")]),
        ],
        copy_results=[copy_result("视频标题"), copy_result("图文标题")],
    )

    assert [item.slot_index for item in items] == [2, 3]
    assert items[0].task_id != items[1].task_id
    assert all(item.status == MaterialStatus.READY_FOR_REVIEW for item in items)
    assert all(item.content_hash for item in items)


def test_invalid_copy_keeps_material_item_in_manual_review():
    invalid_copy = copy_result()
    object.__setattr__(invalid_copy, "status", "needs_manual_review")
    object.__setattr__(invalid_copy, "reason_codes", ["UNSUPPORTED_CLAIM"])

    items = build_material_items(
        run_id="RUN-1",
        product_id="123",
        target_slot_indexes=[1],
        asset_groups=[("image_text", [asset("a"), asset("b"), asset("c")])],
        copy_results=[invalid_copy],
    )

    assert items[0].status == MaterialStatus.NEEDS_MANUAL_REVIEW
    assert items[0].reason_codes == ["UNSUPPORTED_CLAIM"]


def test_product_task_is_ready_only_when_all_material_items_are_ready():
    product = ProductRecord(product_id="123", title="水杯", grade="A级", category="水杯")
    ready_items = build_material_items(
        run_id="RUN-1",
        product_id="123",
        target_slot_indexes=[1],
        asset_groups=[("image_text", [asset("a"), asset("b"), asset("c")])],
        copy_results=[copy_result()],
    )

    tasks = build_product_tasks(
        run_id="RUN-1",
        products=[product],
        decisions_by_product={"123": ("eligible", [])},
        desired_slots_by_product={"123": 3},
        material_items_by_product={"123": ready_items},
    )

    assert tasks[0].status == ProductStatus.READY_FOR_REVIEW
    assert tasks[0].desired_slots == 3


def test_excluded_product_gets_product_task_but_no_upload_state():
    product = ProductRecord(product_id="123", title="积分水杯", grade="A级", category="水杯")

    tasks = build_product_tasks(
        run_id="RUN-1",
        products=[product],
        decisions_by_product={"123": ("excluded", ["EXCLUDE_POINTS"])},
        desired_slots_by_product={},
        material_items_by_product={},
    )

    assert tasks[0].status == ProductStatus.EXCLUDED
    assert tasks[0].reason_codes == ["EXCLUDE_POINTS"]
