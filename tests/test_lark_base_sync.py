import json
from pathlib import Path

from upload_search_materials.lark_base_sync import (
    LarkCliResult,
    build_upload_log_rows,
    inspect_lark_base_config,
    inspect_product_metadata_snapshot,
    refresh_product_metadata_snapshot,
    sync_product_metadata,
    sync_product_metadata_from_snapshot,
    write_successful_upload_log,
)
from upload_search_materials.models import ProductRecord
from upload_search_materials.runtime_config import (
    DEFAULT_LARK_PRODUCT_BASE_URL,
    DEFAULT_LARK_PRODUCT_TABLE_ID,
    DEFAULT_LARK_UPLOAD_LOG_BASE_URL,
    DEFAULT_LARK_UPLOAD_LOG_TABLE_ID,
    LarkBaseConfig,
    normalize_lark_base_config,
)


def _product_raw(product_id: str, owner: str = "") -> dict[str, str]:
    return {
        "商品ID": product_id,
        "商品名称（查找引用）": "本地商品",
        "货号（查找引用）": "LOCAL-SKU",
        "产品等级": "A",
        "链接": "https://example.test/item",
        "运营": owner,
        "组别": "测试组",
        "品类-公司维度划分": "测试品类",
    }


def _write_products(path: Path) -> None:
    path.write_text(
        "\ufeff商品ID,商品名称（查找引用）,货号（查找引用）,产品等级,链接,运营,组别,品类-公司维度划分\n"
        "1001,本地商品,LOCAL-SKU,A,https://example.test/item,旧负责人,测试组,测试品类\n"
        "1002,另一个商品,SKU-2,A,https://example.test/item2,负责人二,测试组,测试品类\n",
        encoding="utf-8",
    )


def test_sync_product_metadata_overrides_owner_from_lark(tmp_path):
    product = ProductRecord(
        "1001",
        sku="LOCAL-SKU",
        title="本地商品",
        owner="旧负责人",
        raw=_product_raw("1001", "旧负责人"),
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
                            "商品ID": "1001",
                            "负责人": "新负责人",
                            "货号": "BASE-SKU",
                            "商品名称": "Base 商品",
                        },
                    }
                ],
                "has_more": False,
            },
        )

    evidence = tmp_path / "lark-product-sync.json"
    result = sync_product_metadata(
        [product],
        LarkBaseConfig(
            enabled=True,
            product_base_token="base-token",
            product_table_id="产品数据表",
        ),
        evidence_path=evidence,
        runner=runner,
    )

    assert result.status == "completed"
    assert result.matched_count == 1
    assert result.updated_owner_count == 1
    assert result.records[0].owner == "新负责人"
    assert result.records[0].raw["运营"] == "新负责人"
    assert json.loads(evidence.read_text(encoding="utf-8"))["status"] == "completed"


def test_sync_product_metadata_does_not_report_empty_table_as_completed(tmp_path):
    product = ProductRecord(
        "1001",
        sku="LOCAL-SKU",
        title="本地商品",
        owner="本地负责人",
        raw=_product_raw("1001", "本地负责人"),
    )

    result = sync_product_metadata(
        [product],
        LarkBaseConfig(
            enabled=True,
            product_base_token="base-token",
            product_table_id="产品数据表",
        ),
        evidence_path=tmp_path / "lark-product-sync.json",
        runner=lambda _args, _timeout: LarkCliResult(
            ok=True,
            payload={"items": [], "has_more": False},
        ),
    )

    assert result.status == "skipped"
    assert result.reason_code == "LARK_PRODUCT_TABLE_EMPTY_OR_INVISIBLE"
    assert result.records[0].owner == "本地负责人"
    evidence = json.loads(
        (tmp_path / "lark-product-sync.json").read_text(encoding="utf-8")
    )
    assert evidence["status"] == "skipped"
    assert evidence["fetched_count"] == 0


def test_runtime_product_snapshot_downloads_then_drives_local_owner_sync(tmp_path):
    snapshot = tmp_path / "runtime" / "lark" / "product-owner-snapshot.json"
    config = LarkBaseConfig(
        enabled=True,
        product_base_token="base-token",
        product_table_id="产品数据表",
    )

    result = refresh_product_metadata_snapshot(
        config,
        snapshot,
        runner=lambda args, _timeout: LarkCliResult(
            ok=True,
            payload={
                "items": [
                    {
                        "fields": {
                            "商品ID": "1001",
                            "运营": "蟹黄",
                            "货号": "BASE-SKU",
                            "商品名称": "飞书商品",
                        }
                    }
                ],
                "has_more": False,
            },
        ),
    )

    assert result.status == "completed"
    assert result.metadata_count == 1
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    assert document["records"][0]["owner"] == "蟹黄"
    assert "base_token" not in document
    assert inspect_product_metadata_snapshot(snapshot)["status"] == "available"

    product = ProductRecord(
        "1001",
        sku="LOCAL-SKU",
        title="本地商品",
        owner="旧负责人",
        raw=_product_raw("1001", "旧负责人"),
    )
    synced = sync_product_metadata_from_snapshot([product], snapshot)

    assert synced.status == "completed"
    assert synced.records[0].owner == "蟹黄"
    assert synced.matched_count == 1
    assert synced.snapshot_updated_at == result.updated_at


def test_empty_product_snapshot_refresh_preserves_previous_success(tmp_path):
    snapshot = tmp_path / "runtime" / "lark" / "product-owner-snapshot.json"
    config = LarkBaseConfig(
        enabled=True,
        product_base_token="base-token",
        product_table_id="产品数据表",
    )
    refresh_product_metadata_snapshot(
        config,
        snapshot,
        runner=lambda _args, _timeout: LarkCliResult(
            ok=True,
            payload={
                "items": [{"fields": {"商品ID": "1001", "运营": "蟹黄"}}],
                "has_more": False,
            },
        ),
    )
    previous = snapshot.read_bytes()

    result = refresh_product_metadata_snapshot(
        config,
        snapshot,
        runner=lambda _args, _timeout: LarkCliResult(
            ok=True,
            payload={"items": [], "has_more": False},
        ),
    )

    assert result.status == "unavailable"
    assert result.reason_code == "LARK_PRODUCT_TABLE_EMPTY_OR_INVISIBLE"
    assert result.preserved_previous is True
    assert snapshot.read_bytes() == previous


def test_lark_readiness_requires_product_records_but_allows_empty_upload_log():
    calls = []

    def runner(args, _timeout):
        calls.append(list(args))
        if "+field-list" in args:
            return LarkCliResult(ok=True, payload={"items": [{"field_id": "f1"}]})
        if "+record-list" in args:
            return LarkCliResult(ok=True, payload={"items": [], "has_more": False})
        raise AssertionError(args)

    result = inspect_lark_base_config(
        LarkBaseConfig(
            enabled=True,
            product_base_token="product-base",
            product_table_id="产品数据表",
            upload_log_base_token="upload-base",
            upload_log_table_id="搜推素材上传记录",
        ),
        runner=runner,
    )

    assert result["product_sync"]["status"] == "unavailable"
    assert (
        result["product_sync"]["reason_code"]
        == "LARK_PRODUCT_TABLE_EMPTY_OR_INVISIBLE"
    )
    assert result["upload_log"]["status"] == "available"
    assert sum("+record-list" in call for call in calls) == 1


def test_build_upload_log_rows_keeps_only_successful_uploads(tmp_path):
    run_dir = tmp_path / "run"
    inputs_dir = tmp_path / "inputs"
    run_dir.mkdir()
    inputs_dir.mkdir()
    image = tmp_path / "素材图.jpg"
    image.write_bytes(b"image")
    _write_products(inputs_dir / "products.csv")
    (run_dir / "material-items.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "MAT-1",
                    "product_id": "1001",
                    "material_type": "image_text",
                    "slot_index": 1,
                    "assets": [
                        {
                            "product_id": "1001",
                            "source_path": str(image),
                            "asset_type": "image",
                            "sha256": "abc",
                            "validation_status": "valid",
                        }
                    ],
                    "title": "上传标题",
                },
                {
                    "task_id": "MAT-2",
                    "product_id": "1002",
                    "material_type": "image_text",
                    "slot_index": 2,
                    "assets": [],
                    "title": "失败标题",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "approval-manifest.json").write_text(
        json.dumps({"entries": [{"task_id": "MAT-1", "product_id": "1001"}]}),
        encoding="utf-8",
    )

    rows = build_upload_log_rows(
        run_dir=run_dir,
        session_inputs_dir=inputs_dir,
        task_records=[
            {
                "task_id": "MAT-1",
                "status": "under_review",
                "remote_material_id": "remote-1",
                "updated_at": "2026-08-26T09:30:00+00:00",
            },
            {
                "task_id": "MAT-2",
                "status": "failed",
                "remote_material_id": "",
            },
        ],
        confirmed_by="上传人",
    )

    assert rows == [
        {
            "上传负责人": "上传人",
            "商品 ID": "1001",
            "货号": "LOCAL-SKU",
            "商品名称": "本地商品",
            "上传时间": "2026-08-26 09:30:00",
            "上传图片文件名": "素材图.jpg",
            "标题": "上传标题",
        }
    ]


def test_write_successful_upload_log_uses_batch_create(tmp_path):
    run_dir = tmp_path / "run"
    inputs_dir = tmp_path / "inputs"
    run_dir.mkdir()
    inputs_dir.mkdir()
    image = tmp_path / "素材图.jpg"
    image.write_bytes(b"image")
    _write_products(inputs_dir / "products.csv")
    (run_dir / "material-items.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "MAT-1",
                    "product_id": "1001",
                    "material_type": "image_text",
                    "slot_index": 1,
                    "assets": [
                        {
                            "product_id": "1001",
                            "source_path": str(image),
                            "asset_type": "image",
                            "sha256": "abc",
                            "validation_status": "valid",
                        }
                    ],
                    "title": "上传标题",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "approval-manifest.json").write_text(
        json.dumps({"entries": [{"task_id": "MAT-1", "product_id": "1001"}]}),
        encoding="utf-8",
    )

    def runner(args, _timeout):
        assert "+record-batch-create" in args
        submitted = json.loads(args[args.index("--json") + 1])
        assert submitted["create_records"][0]["上传负责人"] == "上传人"
        return LarkCliResult(
            ok=True,
            payload={"record_id_list": ["rec1"]},
        )

    evidence = tmp_path / "lark-upload-log.json"
    result = write_successful_upload_log(
        run_dir=run_dir,
        session_inputs_dir=inputs_dir,
        task_records=[
            {
                "task_id": "MAT-1",
                "status": "success",
                "remote_material_id": "remote-1",
                "updated_at": "2026-08-26T09:30:00+00:00",
            }
        ],
        config=LarkBaseConfig(
            enabled=True,
            upload_log_base_token="base-token",
            upload_log_table_id="搜推素材上传记录",
        ),
        confirmed_by="上传人",
        evidence_path=evidence,
        runner=runner,
    )

    assert result.status == "completed"
    assert result.created_count == 1

    def should_not_write(_args, _timeout):
        raise AssertionError("duplicate upload logs must not be appended")

    duplicate = write_successful_upload_log(
        run_dir=run_dir,
        session_inputs_dir=inputs_dir,
        task_records=[
            {
                "task_id": "MAT-1",
                "status": "success",
                "remote_material_id": "remote-1",
                "updated_at": "2026-08-26T09:30:00+00:00",
            }
        ],
        config=LarkBaseConfig(
            enabled=True,
            upload_log_base_token="base-token",
            upload_log_table_id="搜推素材上传记录",
        ),
        confirmed_by="上传人",
        evidence_path=evidence,
        runner=should_not_write,
    )

    assert duplicate.reason_code == "LARK_UPLOAD_LOG_ALREADY_RECORDED"


def test_lark_base_config_respects_explicit_disable():
    config = normalize_lark_base_config(
        {
            "enabled": False,
            "product_base_url": "https://example.feishu.cn/wiki/base",
            "product_table_id": "产品数据表",
        },
        {},
    )

    assert config.enabled is False


def test_lark_base_config_uses_team_defaults_for_a_new_user():
    config = normalize_lark_base_config({}, {})

    assert config.enabled is False
    assert config.product_base_url == DEFAULT_LARK_PRODUCT_BASE_URL
    assert config.product_table_id == DEFAULT_LARK_PRODUCT_TABLE_ID
    assert config.upload_log_base_url == DEFAULT_LARK_UPLOAD_LOG_BASE_URL
    assert config.upload_log_table_id == DEFAULT_LARK_UPLOAD_LOG_TABLE_ID
