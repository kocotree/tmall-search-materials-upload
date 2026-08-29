import json
from pathlib import Path

from upload_search_materials.lark_base_sync import (
    IncrementalUploadLogWriter,
    LarkCliResult,
    build_upload_log_rows,
    default_lark_cli_runner,
    inspect_lark_base_config,
    inspect_product_metadata_snapshot,
    inspect_upload_history_snapshot,
    read_upload_history_fingerprints,
    refresh_product_metadata_snapshot,
    refresh_upload_history_snapshot,
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


def _read_json_argument(args) -> tuple[dict, Path]:
    argument = args[args.index("--json") + 1]
    assert argument.startswith("@")
    payload_path = Path(argument[1:])
    assert payload_path.is_file()
    return json.loads(payload_path.read_text(encoding="utf-8")), payload_path


def test_default_lark_cli_runner_uses_relative_json_file_argument(
    tmp_path,
    monkeypatch,
):
    payload_dir = tmp_path / "含 空格的任务目录"
    payload_dir.mkdir()
    payload_path = payload_dir / ".lark-upload-log-test.json"
    payload_path.write_text('{"create_records":[]}', encoding="utf-8")
    observed = {}

    monkeypatch.setattr(
        "upload_search_materials.lark_base_sync.shutil.which",
        lambda name: "C:\\tools\\lark-cli.cmd" if name == "lark-cli.cmd" else None,
    )

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs.get("cwd")
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "{}", "stderr": ""},
        )()

    monkeypatch.setattr(
        "upload_search_materials.lark_base_sync.subprocess.run",
        fake_run,
    )

    result = default_lark_cli_runner(
        [
            "base",
            "+record-batch-create",
            "--json",
            f"@{payload_path}",
            "--as",
            "user",
        ],
        30,
    )

    assert result.ok is True
    assert observed["cwd"] == str(payload_dir)
    assert observed["command"] == [
        "C:\\tools\\lark-cli.cmd",
        "base",
        "+record-batch-create",
        "--json",
        "@.lark-upload-log-test.json",
        "--as",
        "user",
    ]


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
                            "产品等级": "S级",
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
    assert document["records"][0]["grade"] == "S级"
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
    assert synced.records[0].grade == "S级"
    assert synced.records[0].raw["产品等级"] == "S级"
    assert synced.matched_count == 1
    assert synced.snapshot_updated_at == result.updated_at


def test_runtime_product_snapshot_supports_columnar_record_pages(tmp_path):
    snapshot = tmp_path / "runtime" / "lark" / "product-owner-snapshot.json"
    config = LarkBaseConfig(
        enabled=True,
        product_base_token="base-token",
        product_table_id="产品数据表",
    )
    calls = []

    def runner(args, _timeout):
        calls.append(list(args))
        offset = int(args[args.index("--offset") + 1])
        if offset == 0:
            return LarkCliResult(
                ok=True,
                payload={
                    "ok": True,
                    "data": {
                        "fields": [
                            "商品ID",
                            "运营",
                            "货号（查找引用）",
                            "商品名称（查找引用）",
                            "产品等级",
                        ],
                        "data": [
                            ["1001", "蟹黄", ["SKU-1"], ["商品一"], "S级"],
                            ["1002", ["桃酥"], ["SKU-2"], ["商品二"], "A级"],
                        ],
                        "record_id_list": ["rec1", "rec2"],
                        "has_more": True,
                    },
                },
            )
        assert offset == 2
        return LarkCliResult(
            ok=True,
            payload={
                "ok": True,
                "data": {
                    "fields": [
                        "商品ID",
                        "运营",
                        "货号（查找引用）",
                        "商品名称（查找引用）",
                        "产品等级",
                    ],
                    "data": [["1003", "虾米", ["SKU-3"], ["商品三"], "B级"]],
                    "record_id_list": ["rec3"],
                    "has_more": False,
                },
            },
        )

    result = refresh_product_metadata_snapshot(
        config,
        snapshot,
        runner=runner,
    )

    assert result.status == "completed"
    assert result.fetched_count == 3
    assert result.metadata_count == 3
    assert result.owner_count == 3
    assert len(calls) == 2
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    assert [item["product_id"] for item in document["records"]] == [
        "1001",
        "1002",
        "1003",
    ]
    assert document["records"][1]["owner"] == "桃酥"
    assert [item["grade"] for item in document["records"]] == ["S级", "A级", "B级"]


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
            base_token = args[args.index("--base-token") + 1]
            return LarkCliResult(
                ok=True,
                payload={
                    "items": [
                        {
                            "field_id": "f1",
                            "name": (
                                "原图 SHA-256"
                                if base_token == "upload-base"
                                else "商品ID"
                            ),
                        }
                    ]
                },
            )
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


def test_lark_readiness_does_not_treat_columnar_fields_as_records():
    def runner(args, _timeout):
        if "+field-list" in args:
            base_token = args[args.index("--base-token") + 1]
            return LarkCliResult(
                ok=True,
                payload={
                    "items": [
                        {
                            "field_id": "f1",
                            "name": (
                                "原图 SHA-256"
                                if base_token == "upload-base"
                                else "商品ID"
                            ),
                        }
                    ]
                },
            )
        if "+record-list" in args:
            return LarkCliResult(
                ok=True,
                payload={
                    "ok": True,
                    "data": {
                        "fields": ["商品ID", "运营"],
                        "data": [],
                        "record_id_list": [],
                        "has_more": False,
                    },
                },
            )
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
    assert result["product_sync"]["record_probe_count"] == 0


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
                            "original_source_path": str(image),
                            "original_sha256": "a" * 64,
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
            "上传时间": "2026-08-26 17:30:00",
            "上传图片文件名": "素材图.jpg",
            "原图 SHA-256": "a" * 64,
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
                            "original_source_path": str(image),
                            "original_sha256": "a" * 64,
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

    payload_paths = []

    def runner(args, _timeout):
        assert "+record-batch-create" in args
        submitted, payload_path = _read_json_argument(args)
        payload_paths.append(payload_path)
        assert submitted["create_records"][0]["上传负责人"] == "上传人"
        assert submitted["create_records"][0]["原图 SHA-256"] == "a" * 64
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
    assert payload_paths and all(not path.exists() for path in payload_paths)

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


def test_upload_log_retry_appends_only_new_successful_tasks(tmp_path, monkeypatch):
    evidence = tmp_path / "lark-upload-log.json"
    submitted_batches = []

    def fake_rows(**kwargs):
        return [
            {"商品 ID": str(record["task_id"]), "上传时间": "固定时间"}
            for record in kwargs["task_records"]
            if record
            and record.get("status") in {"submitted", "under_review", "success"}
            and record.get("remote_material_id")
        ]

    def runner(args, _timeout):
        assert "+record-batch-create" in args
        payload, _payload_path = _read_json_argument(args)
        submitted_batches.append(payload["create_records"])
        return LarkCliResult(
            ok=True,
            payload={
                "record_id_list": [
                    f"rec-{index}"
                    for index in range(len(payload["create_records"]))
                ]
            },
        )

    monkeypatch.setattr(
        "upload_search_materials.lark_base_sync.build_upload_log_rows",
        fake_rows,
    )
    config = LarkBaseConfig(
        enabled=True,
        upload_log_base_token="base-token",
        upload_log_table_id="上传记录表",
    )
    first = {
        "task_id": "MAT-1",
        "status": "submitted",
        "remote_material_id": "remote-1",
    }
    second = {
        "task_id": "MAT-2",
        "status": "success",
        "remote_material_id": "remote-2",
    }

    initial = write_successful_upload_log(
        run_dir=tmp_path,
        session_inputs_dir=tmp_path,
        task_records=[first],
        config=config,
        evidence_path=evidence,
        runner=runner,
    )
    resumed = write_successful_upload_log(
        run_dir=tmp_path,
        session_inputs_dir=tmp_path,
        task_records=[{**first, "status": "under_review"}, second],
        config=config,
        evidence_path=evidence,
        runner=runner,
    )

    assert initial.created_count == 1
    assert resumed.created_count == 1
    assert submitted_batches == [
        [{"商品 ID": "MAT-1", "上传时间": "固定时间"}],
        [{"商品 ID": "MAT-2", "上传时间": "固定时间"}],
    ]
    persisted = json.loads(evidence.read_text(encoding="utf-8"))
    assert persisted["created_count"] == 2
    assert len(persisted["recorded_upload_keys"]) == 2


def test_incremental_upload_log_writer_does_not_wait_before_next_slot(
    tmp_path,
    monkeypatch,
):
    import threading

    started = threading.Event()
    release = threading.Event()
    submitted_batches = []

    def fake_rows(**kwargs):
        return [
            {"商品 ID": str(record["task_id"]), "上传时间": "固定时间"}
            for record in kwargs["task_records"]
            if record
        ]

    def runner(args, _timeout):
        payload, _payload_path = _read_json_argument(args)
        submitted_batches.append(payload["create_records"])
        started.set()
        assert release.wait(timeout=5)
        return LarkCliResult(
            ok=True,
            payload={"record_id_list": ["recorded"]},
        )

    monkeypatch.setattr(
        "upload_search_materials.lark_base_sync.build_upload_log_rows",
        fake_rows,
    )
    writer = IncrementalUploadLogWriter(
        run_dir=tmp_path,
        session_inputs_dir=tmp_path,
        config=LarkBaseConfig(
            enabled=True,
            upload_log_base_token="base-token",
            upload_log_table_id="上传记录表",
        ),
        evidence_path=tmp_path / "lark-upload-log.json",
        runner=runner,
    )

    assert writer.submit({
        "task_id": "slot-1",
        "status": "submitted",
        "remote_material_id": "remote-1",
    }) is True
    assert started.wait(timeout=2)
    assert writer.submit({
        "task_id": "slot-2",
        "status": "under_review",
        "remote_material_id": "remote-2",
    }) is True
    release.set()
    results = writer.close()

    assert [result.status for result in results] == ["completed", "completed"]
    assert submitted_batches == [
        [{"商品 ID": "slot-1", "上传时间": "固定时间"}],
        [{"商品 ID": "slot-2", "上传时间": "固定时间"}],
    ]


def test_upload_log_failure_does_not_mark_rows_as_recorded(tmp_path, monkeypatch):
    evidence = tmp_path / "lark-upload-log.json"
    submitted_batches = []

    def fake_rows(**kwargs):
        return [
            {"商品 ID": str(record["task_id"]), "上传时间": "固定时间"}
            for record in kwargs["task_records"]
            if record
        ]

    def runner(args, _timeout):
        payload, _payload_path = _read_json_argument(args)
        submitted_batches.append(payload["create_records"])
        if len(submitted_batches) == 1:
            return LarkCliResult(
                ok=False,
                reason_code="LARK_COMMAND_FAILED",
                message="invalid JSON",
            )
        return LarkCliResult(
            ok=True,
            payload={
                "record_id_list": [
                    f"rec-{index}"
                    for index in range(len(payload["create_records"]))
                ]
            },
        )

    monkeypatch.setattr(
        "upload_search_materials.lark_base_sync.build_upload_log_rows",
        fake_rows,
    )
    config = LarkBaseConfig(
        enabled=True,
        upload_log_base_token="base-token",
        upload_log_table_id="上传记录表",
    )
    first = {
        "task_id": "MAT-1",
        "status": "submitted",
        "remote_material_id": "remote-1",
    }
    second = {
        "task_id": "MAT-2",
        "status": "submitted",
        "remote_material_id": "remote-2",
    }

    failed = write_successful_upload_log(
        run_dir=tmp_path,
        session_inputs_dir=tmp_path,
        task_records=[first],
        config=config,
        evidence_path=evidence,
        runner=runner,
    )
    reconciled = write_successful_upload_log(
        run_dir=tmp_path,
        session_inputs_dir=tmp_path,
        task_records=[first, second],
        config=config,
        evidence_path=evidence,
        runner=runner,
    )

    assert failed.status == "failed"
    assert reconciled.status == "completed"
    assert reconciled.attempted_count == 2
    assert reconciled.created_count == 2
    assert submitted_batches == [
        [{"商品 ID": "MAT-1", "上传时间": "固定时间"}],
        [
            {"商品 ID": "MAT-1", "上传时间": "固定时间"},
            {"商品 ID": "MAT-2", "上传时间": "固定时间"},
        ],
    ]


def test_runtime_upload_history_snapshot_downloads_only_valid_fingerprints(
    tmp_path,
):
    snapshot = tmp_path / "runtime" / "lark" / "upload-history-snapshot.json"
    first = "a" * 64
    second = "B" * 64

    def runner(args, _timeout):
        if "+field-list" in args:
            return LarkCliResult(
                ok=True,
                payload={"items": [{"name": "原图 SHA-256"}]},
            )
        assert "+record-list" in args
        return LarkCliResult(
            ok=True,
            payload={
                "items": [
                    {"fields": {"原图 SHA-256": f"{first}；{second}"}},
                    {"fields": {"原图 SHA-256": "not-a-fingerprint"}},
                ],
                "has_more": False,
            },
        )

    result = refresh_upload_history_snapshot(
        LarkBaseConfig(
            enabled=True,
            upload_log_base_token="base-token",
            upload_log_table_id="上传记录表",
        ),
        snapshot,
        runner=runner,
    )

    assert result.status == "completed"
    assert result.fingerprint_count == 2
    assert read_upload_history_fingerprints(snapshot) == frozenset(
        {first, second.casefold()}
    )
    inspected = inspect_upload_history_snapshot(snapshot)
    assert inspected["status"] == "available"
    assert inspected["fingerprint_count"] == 2


def test_empty_upload_history_is_a_valid_snapshot(tmp_path):
    snapshot = tmp_path / "runtime" / "lark" / "upload-history-snapshot.json"

    def runner(args, _timeout):
        if "+field-list" in args:
            return LarkCliResult(
                ok=True,
                payload={"items": [{"name": "原图 SHA-256"}]},
            )
        assert "+record-list" in args
        return LarkCliResult(ok=True, payload={"items": [], "has_more": False})

    result = refresh_upload_history_snapshot(
        LarkBaseConfig(
            enabled=True,
            upload_log_base_token="base-token",
            upload_log_table_id="上传记录表",
        ),
        snapshot,
        runner=runner,
    )

    assert result.status == "completed"
    assert result.fingerprint_count == 0
    assert inspect_upload_history_snapshot(snapshot)["status"] == "available"
    assert read_upload_history_fingerprints(snapshot) == frozenset()


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
