from upload_search_materials.models import (
    AssetRecord,
    MaterialItem,
    MaterialStatus,
    ProductRecord,
    make_run_id,
    make_task_id,
)


def test_task_id_is_deterministic():
    left = make_task_id("run-1", "123", "image_text", 1)
    right = make_task_id("run-1", "123", "image_text", 1)

    assert left == right
    assert left.startswith("MAT-")


def test_publish_uncertain_is_a_material_status():
    assert MaterialStatus.PUBLISH_UNCERTAIN.value == "publish_uncertain"


def test_product_id_is_normalized_to_text():
    record = ProductRecord(product_id=1234567890123)

    assert record.product_id == "1234567890123"


def test_material_asset_lists_are_not_shared():
    left = MaterialItem(
        task_id="MAT-1",
        product_id="1",
        material_type="image_text",
        slot_index=1,
    )
    right = MaterialItem(
        task_id="MAT-2",
        product_id="2",
        material_type="video",
        slot_index=1,
    )

    left.assets.append(AssetRecord(product_id="1", source_path="a.png"))

    assert right.assets == []


def test_run_id_is_deterministic_for_batch_identity():
    run_id = make_run_id("2026-07-17T10:00:00+08:00", "KK Tree", 7)

    assert run_id == make_run_id("2026-07-17T10:00:00+08:00", "KK Tree", 7)
    assert run_id.startswith("RUN-20260717-")
