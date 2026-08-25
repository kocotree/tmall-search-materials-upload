import csv
import json
from pathlib import Path
import sqlite3

import pytest

from upload_search_materials.runtime_config import stable_image_source_id
from upload_search_materials.team_folder_index import (
    TeamFolderIndexError,
    ensure_missing_snapshots,
    materialize_task_folder_candidates,
    publish_snapshot,
    snapshot_status,
    sync_snapshots,
    validate_snapshot,
)

def make_database(path: Path, rows: list[tuple[str, str, str]]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE folders (
          folder_id TEXT PRIMARY KEY,
          source_system TEXT NOT NULL,
          relative_path TEXT NOT NULL,
          absolute_path TEXT NOT NULL,
          folder_name TEXT NOT NULL,
          parent_relative_path TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          last_seen_scan_id TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO metadata VALUES ('last_summary', ?)",
        (json.dumps({"complete": True}),),
    )
    for folder_id, source_id, relative_path in rows:
        connection.execute(
            "INSERT INTO folders VALUES (?, ?, ?, ?, ?, ?, 1, 'scan')",
            (
                folder_id,
                source_id,
                relative_path,
                f"/machine-specific/{relative_path}",
                Path(relative_path).name,
                Path(relative_path).parent.as_posix(),
            ),
        )
    connection.commit()
    connection.close()


def make_products(path: Path) -> None:
    headers = [
        "商品ID",
        "商品名称（查找引用）",
        "货号（查找引用）",
        "产品等级",
        "链接",
        "运营",
        "组别",
        "品类-公司维度划分",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerow(
            {
                "商品ID": "1001",
                "商品名称（查找引用）": "测试商品",
                "货号（查找引用）": "SKU1",
                "产品等级": "A",
                "链接": "https://example.test/1001",
                "运营": "测试",
                "组别": "测试",
                "品类-公司维度划分": "测试",
            }
        )


def test_ensure_missing_snapshots_keeps_valid_existing_snapshot(
    tmp_path, monkeypatch
):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    products = tmp_path / "products.csv"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])
    make_products(products)
    published = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
    )
    monkeypatch.setattr(
        "upload_search_materials.team_folder_index.build_folder_index",
        lambda **_kwargs: pytest.fail("valid snapshots must not be rebuilt"),
    )

    result = ensure_missing_snapshots(
        shared_root=shared,
        local_root=tmp_path / "local",
        products_path=products,
        image_sources=({
            "source_id": "source-a",
            "path": str(tmp_path / "media"),
            "canonical_unc": r"\\nas\media\source-a",
        },),
    )

    assert result["created"] == []
    assert result["existing_source_ids"] == ["source-a"]
    status = snapshot_status(shared_root=shared, local_root=tmp_path / "local")
    assert status["sources"][0]["snapshot_id"] == published["snapshot_id"]


def test_old_snapshot_with_same_path_identity_is_ignored_for_new_source_id(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    local = tmp_path / "local"
    products = tmp_path / "products.csv"
    legacy_source_id = "source-legacy-model"
    local_path = tmp_path / "视觉部" / "1-模特图"
    (local_path / "season" / "SKU1").mkdir(parents=True)
    source_id = stable_image_source_id(local_path)
    make_database(
        database,
        [("folder-1", legacy_source_id, "season/SKU1")],
    )
    make_products(products)
    published = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id=legacy_source_id,
    )
    binding = {
        "source_id": source_id,
        "label": "公司模特图",
        "path": str(local_path),
    }

    ensured = ensure_missing_snapshots(
        shared_root=shared,
        local_root=local,
        products_path=products,
        image_sources=(binding,),
    )

    assert ensured["existing_source_ids"] == []
    assert ensured["created"][0]["source_id"] == source_id
    assert (
        shared
        / "sources"
        / legacy_source_id
        / "snapshots"
        / published["snapshot_id"]
    ).is_dir()
    assert (
        shared
        / "sources"
        / source_id
        / "snapshots"
        / ensured["created"][0]["snapshot_id"]
    ).is_dir()

    synced = sync_snapshots(
        shared_root=shared,
        local_root=local,
        image_sources=(binding,),
        source_ids=(source_id,),
    )
    assert synced["complete"] is True
    assert synced["folder_rows"] >= 1
    assert synced["sources"][0]["source_id"] == source_id
    assert synced["skipped_sources"] == []
    assert synced["superseded_sources"] == []

    output_path = tmp_path / "run" / "folder-candidates.csv"
    materialized = materialize_task_folder_candidates(
        local_root=local,
        products_path=products,
        image_sources=(binding,),
        selected_product_ids=("1001",),
        output_path=output_path,
        source_ids=(source_id,),
    )
    assert materialized["candidate_rows"] == 1
    with output_path.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["source_system"] == source_id


def test_ensure_missing_snapshots_builds_and_publishes_first_snapshot(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    products = tmp_path / "products.csv"
    make_products(products)
    media = tmp_path / "media"
    (media / "season" / "SKU1").mkdir(parents=True)

    result = ensure_missing_snapshots(
        shared_root=shared,
        local_root=tmp_path / "local",
        products_path=products,
        image_sources=({
            "source_id": "source-a",
            "path": str(media),
        },),
    )

    assert len(result["created"]) == 1
    assert result["created"][0]["source_id"] == "source-a"
    assert result["existing_source_ids"] == []
    status = snapshot_status(shared_root=shared, local_root=tmp_path / "local")
    assert status["using"] == "shared"
    assert status["sources"][0]["status"] == "valid"
    snapshot_path = (
        shared
        / "sources"
        / "source-a"
        / "snapshots"
        / result["created"][0]["snapshot_id"]
    )
    assert "canonical_source" not in validate_snapshot(snapshot_path)


def test_publish_creates_portable_immutable_snapshot(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])

    first = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
        publisher="machine-a",
    )
    second = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
        publisher="machine-b",
    )

    assert first["snapshot_id"] != second["snapshot_id"]
    snapshots = shared / "sources" / "source-a" / "snapshots"
    assert (snapshots / first["snapshot_id"]).is_dir()
    assert (snapshots / second["snapshot_id"]).is_dir()
    payload = (snapshots / first["snapshot_id"] / "folders.csv").read_text(
        encoding="utf-8-sig"
    )
    assert "/machine-specific" not in payload
    assert "absolute_path" not in payload
    pointer = json.loads(
        (shared / "sources" / "source-a" / "current.json").read_text()
    )
    assert pointer["snapshot_id"] == second["snapshot_id"]


def test_status_falls_back_to_newest_valid_immutable_snapshot(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])
    first = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
    )
    second = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
    )
    latest_csv = (
        shared
        / "sources"
        / "source-a"
        / "snapshots"
        / second["snapshot_id"]
        / "folders.csv"
    )
    latest_csv.write_text("tampered", encoding="utf-8")

    status = snapshot_status(shared_root=shared, local_root=tmp_path / "local")

    assert status["sources"][0]["status"] == "valid"
    assert status["sources"][0]["snapshot_id"] == first["snapshot_id"]


def test_sync_caches_snapshot_and_task_materialization_rehydrates_paths(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    local = tmp_path / "local"
    products = tmp_path / "products.csv"
    local_media = tmp_path / "mounted-media"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])
    make_products(products)
    published = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
    )

    summary = sync_snapshots(
        shared_root=shared,
        local_root=local,
        image_sources=(
            {
                "source_id": "source-a",
                "path": str(local_media),
            },
        ),
    )

    assert summary["complete"] is True
    assert summary["folder_rows"] == 1
    assert summary["sources"][0]["origin"] == "shared"
    assert not (local / "folder-candidates.csv").exists()
    task_candidates = tmp_path / "run" / "folder-candidates.csv"
    task_summary = materialize_task_folder_candidates(
        local_root=local,
        products_path=products,
        image_sources=(
            {
                "source_id": "source-a",
                "path": str(local_media),
            },
        ),
        selected_product_ids=("1001",),
        output_path=task_candidates,
    )
    assert task_summary["candidate_rows"] == 1
    assert task_summary["matcher_version"] >= 1
    with task_candidates.open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        row = next(csv.DictReader(stream))
    assert row["product_id"] == "1001"
    assert row["absolute_path"] == str(local_media / "season" / "SKU1")
    cached = (
        local
        / "team-cache"
        / "sources"
        / "source-a"
        / "snapshots"
        / published["snapshot_id"]
    )
    assert validate_snapshot(cached)["snapshot_id"] == published["snapshot_id"]

    before = sorted((shared / "sources" / "source-a" / "snapshots").iterdir())
    offline_summary = sync_snapshots(
        shared_root=tmp_path / "unmounted",
        local_root=local,
        image_sources=(
            {
                "source_id": "source-a",
                "path": str(local_media),
            },
        ),
    )
    after = sorted((shared / "sources" / "source-a" / "snapshots").iterdir())
    assert offline_summary["sources"][0]["origin"] == "local_cache"
    assert before == after


def test_requested_sync_blocks_when_local_binding_is_missing(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    products = tmp_path / "products.csv"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])
    make_products(products)
    publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
    )

    with pytest.raises(TeamFolderIndexError, match="TEAM_INDEX_LOCAL_BINDING_MISSING"):
        sync_snapshots(
            shared_root=shared,
            local_root=tmp_path / "local",
            image_sources=(),
            source_ids=("source-a",),
        )


def test_publish_manifest_omits_machine_path_identity(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    shared = tmp_path / "shared"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])

    result = publish_snapshot(
        database_path=database,
        shared_root=shared,
        source_id="source-a",
    )
    snapshot_path = (
        shared
        / "sources"
        / "source-a"
        / "snapshots"
        / result["snapshot_id"]
    )

    assert "canonical_source" not in validate_snapshot(snapshot_path)


def test_publish_rejects_partial_local_refresh(tmp_path):
    database = tmp_path / "folder-index.sqlite3"
    make_database(database, [("folder-1", "source-a", "season/SKU1")])
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE metadata SET value=? WHERE key='last_summary'",
        (json.dumps({"complete": False, "errors": ["NAS timeout"]}),),
    )
    connection.commit()
    connection.close()

    with pytest.raises(TeamFolderIndexError, match="LOCAL_SOURCE_INCOMPLETE"):
        publish_snapshot(
            database_path=database,
            shared_root=tmp_path / "shared",
            source_id="source-a",
        )
