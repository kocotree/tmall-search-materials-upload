from __future__ import annotations

import hashlib
import pytest
import sqlite3

from upload_search_materials.asset_index_store import (
    AssetIndexStore,
    IndexedFile,
    IndexIdentity,
    IndexIdentityError,
    PathMatchRecord,
)


IDENTITY = IndexIdentity("products.csv", "a" * 64, "[]", 2, 1000)


def make_store(tmp_path):
    return AssetIndexStore.create(tmp_path / "asset-index.sqlite3", IDENTITY)


def add_file(store, *, size=10, mtime=20, scan_id="scan-1"):
    root_id = store.upsert_root("model_nas", "C:/assets")
    partition_id = store.upsert_partition(root_id, "2026/hats")
    file_id = store.checkpoint_files(
        partition_id,
        scan_id,
        [IndexedFile("model_nas", "2026/hats/a.jpg", "C:/assets/2026/hats/a.jpg", ".jpg", size, mtime, "2026/hats")],
    )[0]
    return partition_id, file_id


def match(product_id="P-1", match_type="exact_product_id"):
    return PathMatchRecord(product_id, "SKU-1", "Hat", match_type, "confirmed", ("PATH",))


def test_store_creates_versioned_schema_and_rejects_identity_change(tmp_path):
    path = tmp_path / "asset-index.sqlite3"
    with AssetIndexStore.create(path, IDENTITY) as store:
        assert store.schema_version() == 1

    with pytest.raises(IndexIdentityError, match="identity"):
        AssetIndexStore.open(path, IndexIdentity("other.csv", "b" * 64, "[]", 2, 1000))


def test_open_rejects_persisted_schema_version_other_than_one(tmp_path):
    path = tmp_path / "asset-index.sqlite3"
    with AssetIndexStore.create(path, IDENTITY):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE scan_meta SET value='2' WHERE key='schema_version'")

    with pytest.raises(IndexIdentityError, match="schema"):
        AssetIndexStore.open(path, IDENTITY)


def test_scan_started_marker_persists_across_reopen(tmp_path):
    path = tmp_path / "asset-index.sqlite3"
    store = AssetIndexStore.create(path, IDENTITY)

    assert store.scan_started() is False
    store.mark_scan_started()
    assert store.scan_started() is True
    store.close()

    with AssetIndexStore.open(path, IDENTITY) as reopened:
        assert reopened.scan_started() is True


def test_begin_scan_persists_id_and_original_mode_and_conditionally_clears(tmp_path):
    path = tmp_path / "asset-index.sqlite3"
    store = AssetIndexStore.create(path, IDENTITY)

    assert store.active_scan() is None
    assert store.begin_scan("new", "scan-1") is True
    assert store.begin_scan("refresh", "scan-2") is False
    store.close()

    with AssetIndexStore.open(path, IDENTITY) as reopened:
        assert reopened.active_scan() == ("scan-1", "new")
        assert reopened.clear_active_scan("scan-2") is False
        assert reopened.active_scan() == ("scan-1", "new")
        assert reopened.clear_active_scan("scan-1") is True
        assert reopened.active_scan() is None


def test_begin_scan_sqlite_failure_rolls_back_new_started_marker(tmp_path):
    store = make_store(tmp_path)
    store._connection.execute(
        "CREATE TRIGGER reject_active_scan BEFORE INSERT ON scan_meta "
        "WHEN NEW.key='active_scan_id' BEGIN SELECT RAISE(ABORT, 'forced active failure'); END"
    )
    store._connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced active failure"):
        store.begin_scan("new", "scan-1")

    assert store.scan_started() is False
    assert store.active_scan() is None
    store._connection.execute("DROP TRIGGER reject_active_scan")
    store._connection.commit()
    assert store.begin_scan("new", "scan-2") is True
    assert store.active_scan() == ("scan-2", "new")
    store.close()


def test_checkpoint_upserts_one_file_with_stable_id(tmp_path):
    with make_store(tmp_path) as store:
        _, first_id = add_file(store)
        _, second_id = add_file(store)

        assert second_id == first_id
        assert store.file_count(active_only=True) == 1


def test_root_and_partition_upserts_are_idempotent_and_partition_id_is_stable(tmp_path):
    with make_store(tmp_path) as store:
        root_id = store.upsert_root("model_nas", "C:/assets")
        expected = hashlib.sha256(b"model_nas\x00" + b"2026/hats").hexdigest()[:24]

        assert store.upsert_root("model_nas", "C:/assets") == root_id
        assert store.upsert_partition(root_id, "2026/hats") == expected
        assert store.upsert_partition(root_id, "2026/hats") == expected


def test_partitions_for_root_exposes_persisted_scan_progress(tmp_path):
    with make_store(tmp_path) as store:
        root_id = store.upsert_root("model_nas", "C:/assets")
        partition_id, _ = add_file(store, scan_id="scan-1")
        store.complete_partition(partition_id)

        assert store.partitions_for_root(root_id) == (
            {
                "partition_id": partition_id,
                "relative_path": "2026/hats",
                "status": "completed",
                "processed_count": 1,
                "current_scan_id": "scan-1",
                "completed_scan_id": "scan-1",
            },
        )


def test_changed_size_or_mtime_clears_deep_metadata_and_matches(tmp_path):
    with make_store(tmp_path) as store:
        partition_id, file_id = add_file(store)
        store.update_file_inspection(file_id, sha256="f" * 64, width=100, height=200, validation_status="valid", reason_codes=("OK",))
        store.replace_file_matches(file_id, [match()])

        changed_id = store.checkpoint_files(
            partition_id,
            "scan-2",
            [IndexedFile("model_nas", "2026/hats/a.jpg", "C:/assets/2026/hats/a.jpg", ".jpg", 11, 20, "2026/hats")],
        )[0]

        assert changed_id == file_id
        assert store.file_record(file_id) == {
            "sha256": "", "width": None, "height": None, "validation_status": "not_inspected", "reason_codes": ()
        }
        assert store.matches_for(file_id) == ()


def test_update_file_inspection_persists_metadata_and_reason_codes(tmp_path):
    with make_store(tmp_path) as store:
        _, file_id = add_file(store)
        store.update_file_inspection(file_id, sha256="f" * 64, width=100, height=None, validation_status="invalid", reason_codes=("CORRUPT", "TOO_SMALL"))

        assert store.file_record(file_id) == {
            "sha256": "f" * 64, "width": 100, "height": None, "validation_status": "invalid", "reason_codes": ("CORRUPT", "TOO_SMALL")
        }


def test_match_replacement_is_transactional_and_replaces_prior_matches(tmp_path):
    with make_store(tmp_path) as store:
        _, file_id = add_file(store)
        store.replace_file_matches(file_id, [match("OLD")])

        with pytest.raises(Exception):
            store.replace_file_matches(file_id, [match("NEW"), match("NEW")])
        assert [record["product_id"] for record in store.matches_for(file_id)] == ["OLD"]

        store.replace_file_matches(file_id, [match("NEW")])
        assert [record["product_id"] for record in store.matches_for(file_id)] == ["NEW"]


def test_failed_partition_keeps_existing_files_active(tmp_path):
    with make_store(tmp_path) as store:
        partition_id, _ = add_file(store)
        store.fail_partition(partition_id, "network unavailable")

        assert store.mark_partition_missing_files_inactive(partition_id, "scan-2") == 0
        assert store.file_count(active_only=True) == 1


def test_successful_refresh_marks_only_unseen_partition_files_inactive(tmp_path):
    with make_store(tmp_path) as store:
        partition_id, existing_id = add_file(store)
        store.checkpoint_files(
            partition_id,
            "scan-2",
            [IndexedFile("model_nas", "2026/hats/b.jpg", "C:/assets/2026/hats/b.jpg", ".jpg", 10, 20, "2026/hats")],
        )
        store.complete_partition(partition_id)

        assert store.mark_partition_missing_files_inactive(partition_id, "scan-2") == 1
        assert store.file_is_active(existing_id) is False
        assert store.file_count(active_only=True) == 1


def test_incomplete_new_scan_cannot_deactivate_files_from_a_completed_scan(tmp_path):
    with make_store(tmp_path) as store:
        partition_id, existing_id = add_file(store, scan_id="scan-1")
        store.complete_partition(partition_id)

        assert store.mark_partition_missing_files_inactive(partition_id, "scan-2") == 0
        assert store.file_is_active(existing_id) is True


def test_processed_count_is_reset_for_each_new_scan(tmp_path):
    with make_store(tmp_path) as store:
        partition_id, _ = add_file(store, scan_id="scan-1")
        store.checkpoint_files(
            partition_id,
            "scan-1",
            [IndexedFile("model_nas", "2026/hats/b.jpg", "C:/assets/2026/hats/b.jpg", ".jpg", 10, 20, "2026/hats")],
        )
        assert store.partition_record(partition_id)["processed_count"] == 2

        store.checkpoint_files(
            partition_id,
            "scan-2",
            [IndexedFile("model_nas", "2026/hats/c.jpg", "C:/assets/2026/hats/c.jpg", ".jpg", 10, 20, "2026/hats")],
        )
        assert store.partition_record(partition_id)["processed_count"] == 1
