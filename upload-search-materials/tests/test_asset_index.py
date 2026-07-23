from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
from PIL import Image

import upload_search_materials.asset_index as asset_index_module
from upload_search_materials.asset_index_store import (
    AssetIndexStore,
    IndexedFile,
    IndexIdentity,
    PathMatchRecord,
)
from upload_search_materials.asset_index import (
    IncrementalAssetIndexer,
    IndexOptions,
    NamedRoot,
    ScanOutcome,
)
from upload_search_materials.asset_matching import PathMatch


class Matcher:
    def match(self, path: Path):
        if "matched" not in path.parts:
            return ()
        return (PathMatch("P-1", "SKU-1", "Hat", "exact_product_id", "matched_unlicensed", ()),)


def make_image(path: Path, size=(20, 10)):
    Image.new("RGB", size, color="white").save(path)


def make_indexer(tmp_path, root, *, depth=2, checkpoint_size=1):
    identity = IndexIdentity("products.csv", "a" * 64, "[]", depth, checkpoint_size)
    store = AssetIndexStore.create(
        tmp_path / "index.sqlite3", identity
    )
    return store, IncrementalAssetIndexer(store, Matcher(), (NamedRoot("model", root),), IndexOptions(depth, checkpoint_size))


def source_snapshot(root: Path):
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def file_ids(store):
    return [row[0] for row in store._connection.execute("SELECT file_id FROM files ORDER BY file_id")]


def test_named_root_splits_only_first_equals_and_validates_source():
    assert NamedRoot.parse("model=C:/a=b") == NamedRoot("model", Path("C:/a=b"))
    with pytest.raises(ValueError):
        NamedRoot.parse("bad source=/assets")


@pytest.mark.parametrize("value", ["model=", "model=   "])
def test_named_root_rejects_empty_root_path(value):
    with pytest.raises(ValueError, match="path"):
        NamedRoot.parse(value)


def test_unmatched_broken_image_is_fast_indexed_without_inspection(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "unmatched").mkdir(parents=True)
    (root / "unmatched" / "broken.jpg").write_bytes(b"not-an-image")
    store, indexer = make_indexer(tmp_path, root)
    monkeypatch.setattr("upload_search_materials.asset_index.inspect_asset", lambda *args, **kwargs: pytest.fail("must not inspect unmatched image"))

    outcome = indexer.run("new")

    assert outcome.indexed == 1
    assert store.file_count() == 1
    record = store.file_record(file_ids(store)[0])
    assert record["sha256"] == "" and record["validation_status"] == "not_inspected"
    store.close()


def test_only_matched_image_is_inspected_and_partition_key_is_stable(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "matched" / "P-1").mkdir(parents=True)
    (root / "other").mkdir()
    make_image(root / "matched" / "P-1" / "a.png")
    make_image(root / "other" / "b.png")
    calls = []
    from upload_search_materials.assets import inspect_asset as real_inspect
    monkeypatch.setattr("upload_search_materials.asset_index.inspect_asset", lambda *args, **kwargs: (calls.append(args[0]) or real_inspect(*args, **kwargs)))
    store, indexer = make_indexer(tmp_path, root, depth=1)

    outcome = indexer.run("new")

    assert outcome.indexed == 2 and outcome.matched == 1 and calls == [root / "matched" / "P-1" / "a.png"]
    records = [store.file_record(file_id) for file_id in file_ids(store)]
    assert sum(bool(record["sha256"]) for record in records) == 1
    expected = hashlib.sha256(b"model\0matched").hexdigest()[:24]
    assert indexer.partition_id_for("model", Path("matched")) == expected
    assert store.partition_record(expected)["status"] == "completed"
    store.close()


def test_non_images_and_symlinks_are_skipped(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "movie.mp4").write_bytes(b"video")
    make_image(root / "real.png")
    link = root / "linked.png"
    try:
        link.symlink_to(root / "real.png")
    except OSError as error:
        pytest.skip(f"symlink creation is not permitted: {error}")
    store, indexer = make_indexer(tmp_path, root)

    assert indexer.run("new").indexed == 1
    store.close()


def test_windows_reparse_points_are_skipped(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.png"
    make_image(target)
    real_scandir = os.scandir

    class ReparseEntry:
        name = "junction"
        path = str(root / "junction")

        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks=False):
            return SimpleNamespace(
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
                st_size=0,
                st_mtime_ns=0,
            )

        def is_dir(self, *, follow_symlinks=False):
            return True

    class Entries:
        def __enter__(self):
            return iter((ReparseEntry(),))

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        lambda path: Entries() if Path(path) == root.resolve() else real_scandir(path),
    )
    store, indexer = make_indexer(tmp_path, root)

    assert indexer.run("new").indexed == 0
    store.close()


def test_refresh_only_deactivates_missing_file_after_successful_partition(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    image = root / "old.png"
    make_image(image)
    store, indexer = make_indexer(tmp_path, root)
    indexer.run("new")
    image.unlink()

    outcome = indexer.run("refresh")

    assert outcome.complete and store.file_count(active_only=True) == 0
    store.close()


def test_new_indexer_refresh_deactivates_files_from_deleted_historical_partition(tmp_path):
    root = tmp_path / "root"
    partition = root / "gone"
    partition.mkdir(parents=True)
    image = partition / "old.png"
    make_image(image)
    store, indexer = make_indexer(tmp_path, root, depth=1)
    indexer.run("new")
    old_id = file_ids(store)[0]
    store.close()
    image.unlink()
    partition.rmdir()

    identity = IndexIdentity("products.csv", "a" * 64, "[]", 1, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    fresh_indexer = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(1, 1),
    )

    outcome = fresh_indexer.run("refresh")

    assert outcome.complete
    assert reopened.file_is_active(old_id) is False
    reopened.close()


def test_refresh_root_enumeration_failure_does_not_deactivate_historical_files(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "existing"
    partition.mkdir(parents=True)
    image = partition / "old.png"
    make_image(image)
    store, indexer = make_indexer(tmp_path, root, depth=1)
    indexer.run("new")
    old_id = file_ids(store)[0]
    real_scandir = os.scandir

    def fail_root(path):
        if Path(path) == root.resolve():
            raise OSError("root offline")
        return real_scandir(path)

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", fail_root)

    outcome = indexer.run("refresh")

    assert outcome.partial_failure
    assert store.file_is_active(old_id) is True
    store.close()


def test_refresh_intermediate_error_protects_all_historical_descendants(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    for relative_path in ("a/b/old.png", "a/c/old.png", "x/y/old.png"):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        make_image(path)
    store, indexer = make_indexer(tmp_path, root, depth=2)
    indexer.run("new")
    old_ids = file_ids(store)
    store.close()

    identity = IndexIdentity("products.csv", "a" * 64, "[]", 2, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    fresh_indexer = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(2, 1),
    )
    real_scandir = os.scandir

    def fail_intermediate(path):
        if Path(path) == (root / "a").resolve():
            raise OSError("intermediate offline")
        return real_scandir(path)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir", fail_intermediate
    )

    outcome = fresh_indexer.run("refresh")

    assert outcome.partial_failure
    assert all(reopened.file_is_active(file_id) for file_id in old_ids)
    for relative_partition in ("a/b", "a/c"):
        partition_id = fresh_indexer.partition_id_for(
            "model", Path(relative_partition)
        )
        assert reopened.partition_record(partition_id)["status"] == "failed"
    unrelated_id = fresh_indexer.partition_id_for("model", Path("x/y"))
    assert reopened.partition_record(unrelated_id)["status"] == "completed"
    reopened.close()


def test_empty_new_scan_is_persistently_rejected_for_fresh_indexer(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    store, indexer = make_indexer(tmp_path, root, depth=1)

    first = indexer.run("new")

    assert first.complete and first.discovered == 0
    store.close()

    identity = IndexIdentity("products.csv", "a" * 64, "[]", 1, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    fresh_indexer = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(1, 1),
    )

    with pytest.raises(ValueError, match="new mode"):
        fresh_indexer.run("new")
    assert fresh_indexer.run("resume").complete
    assert fresh_indexer.run("refresh").complete
    reopened.close()


def test_keyboard_interrupt_keeps_checkpoint_for_resume(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "matched").mkdir(parents=True)
    make_image(root / "matched" / "a.png")
    make_image(root / "matched" / "b.png")
    store, indexer = make_indexer(tmp_path, root, checkpoint_size=1)
    from upload_search_materials.assets import inspect_asset as real_inspect
    calls = 0
    def interrupt_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return real_inspect(*args, **kwargs)
    monkeypatch.setattr("upload_search_materials.asset_index.inspect_asset", interrupt_after_first)

    with pytest.raises(KeyboardInterrupt):
        indexer.run("new")
    assert store.file_count() == 2
    partition_id = store._connection.execute(
        "SELECT partition_id FROM partitions"
    ).fetchone()[0]
    assert store.partition_record(partition_id)["processed_count"] == 1
    store.close()

    monkeypatch.setattr("upload_search_materials.asset_index.inspect_asset", real_inspect)
    identity = IndexIdentity("products.csv", "a" * 64, "[]", 2, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    fresh_indexer = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(2, 1),
    )
    outcome = fresh_indexer.run("resume")

    assert outcome.complete
    assert outcome.indexed == 2
    assert reopened.partition_record(partition_id)["processed_count"] == 2
    assert reopened.file_count() == 2
    assert sum(len(reopened.matches_for(file_id)) for file_id in file_ids(reopened)) == 2
    reopened.close()


def test_new_resume_restarts_incomplete_partition_after_files_change_before_cursor(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    for name in ("a.png", "b.png", "c.png"):
        make_image(partition / name)
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    real_advance = store.advance_partition_progress
    checkpoint_calls = 0

    def interrupt_after_two_checkpoints(
        partition_id, scan_id, processed_count, **statistics
    ):
        nonlocal checkpoint_calls
        real_advance(
            partition_id,
            scan_id,
            processed_count,
            **statistics,
        )
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        store, "advance_partition_progress", interrupt_after_two_checkpoints
    )
    with pytest.raises(KeyboardInterrupt):
        indexer.run("new")
    monkeypatch.setattr(store, "advance_partition_progress", real_advance)

    (partition / "a.png").unlink()
    (partition / "b.png").rename(partition / "1-renamed.png")
    make_image(partition / "0-added.png")

    outcome = indexer.run("resume")

    active_paths = {
        row[0]
        for row in store._connection.execute(
            "SELECT relative_path FROM files WHERE active=1"
        )
    }
    assert outcome.complete
    assert active_paths == {
        "matched/0-added.png",
        "matched/1-renamed.png",
        "matched/c.png",
    }
    assert store._connection.execute(
        "SELECT COUNT(*) FROM files WHERE active=1 AND seen_scan_id=?",
        (store.partition_record(indexer.partition_id_for("model", Path("matched")))[
            "completed_scan_id"
        ],),
    ).fetchone()[0] == 3
    assert store._connection.execute(
        "SELECT COUNT(DISTINCT file_id) FROM matches "
        "JOIN files USING(file_id) WHERE files.active=1"
    ).fetchone()[0] == 3
    store.close()


def test_refresh_resume_restarts_incomplete_partition_after_files_change_before_cursor(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    for name in ("a.png", "b.png", "c.png"):
        make_image(partition / name)
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    assert indexer.run("new").complete
    real_advance = store.advance_partition_progress
    checkpoint_calls = 0

    def interrupt_after_two_checkpoints(
        partition_id, scan_id, processed_count, **statistics
    ):
        nonlocal checkpoint_calls
        real_advance(
            partition_id,
            scan_id,
            processed_count,
            **statistics,
        )
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        store, "advance_partition_progress", interrupt_after_two_checkpoints
    )
    with pytest.raises(KeyboardInterrupt):
        indexer.run("refresh")
    monkeypatch.setattr(store, "advance_partition_progress", real_advance)

    (partition / "a.png").unlink()
    (partition / "b.png").rename(partition / "1-renamed.png")
    make_image(partition / "0-added.png")

    outcome = indexer.run("resume")

    rows = store._connection.execute(
        "SELECT relative_path, active FROM files ORDER BY relative_path"
    ).fetchall()
    assert outcome.complete
    assert {row["relative_path"] for row in rows if row["active"]} == {
        "matched/0-added.png",
        "matched/1-renamed.png",
        "matched/c.png",
    }
    assert {
        row["relative_path"] for row in rows if not row["active"]
    } == {"matched/a.png", "matched/b.png"}
    assert store._connection.execute(
        "SELECT COUNT(DISTINCT file_id) FROM matches "
        "JOIN files USING(file_id) WHERE files.active=1"
    ).fetchone()[0] == 3
    store.close()


def test_refresh_resume_deactivates_deleted_file_after_finalization_interrupt(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    kept = partition / "kept.png"
    deleted = partition / "deleted.png"
    make_image(kept)
    make_image(deleted)
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    assert indexer.run("new").complete
    deleted_id = store._connection.execute(
        "SELECT file_id FROM files WHERE relative_path='matched/deleted.png'"
    ).fetchone()[0]
    deleted.unlink()
    real_finalize = store.finalize_partition

    def interrupt_before_finalization(partition_id, scan_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        store, "finalize_partition", interrupt_before_finalization
    )
    with pytest.raises(KeyboardInterrupt):
        indexer.run("refresh")

    partition_id = indexer.partition_id_for("model", Path("matched"))
    interrupted = store.partition_record(partition_id)
    assert interrupted["status"] == "in_progress"
    assert interrupted["completed_scan_id"] is None
    assert store.file_is_active(deleted_id) is True

    monkeypatch.setattr(store, "finalize_partition", real_finalize)
    outcome = indexer.run("resume")

    assert outcome.complete
    assert store.file_is_active(deleted_id) is False
    assert {
        row["relative_path"]
        for row in store._connection.execute(
            "SELECT relative_path FROM files WHERE active=1"
        )
    } == {"matched/kept.png"}
    store.close()


def test_new_discovery_interrupt_persists_active_scan_for_real_resume(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "matched").mkdir(parents=True)
    make_image(root / "matched" / "a.png")
    store, indexer = make_indexer(tmp_path, root, checkpoint_size=1)
    real_scandir = os.scandir
    calls = 0

    def interrupt_discovery_once(path):
        nonlocal calls
        if Path(path) == root.resolve():
            calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return real_scandir(path)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        interrupt_discovery_once,
    )
    with pytest.raises(KeyboardInterrupt):
        indexer.run("new")
    active_scan_id = store._connection.execute(
        "SELECT value FROM scan_meta WHERE key='active_scan_id'"
    ).fetchone()
    store.close()

    identity = IndexIdentity("products.csv", "a" * 64, "[]", 2, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    resumed = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(2, 1),
    ).run("resume")

    assert active_scan_id is not None
    assert resumed.complete and reopened.file_count() == 1
    assert reopened.active_scan_id() is None
    reopened.close()


def test_refresh_interrupt_with_mixed_partition_scan_ids_resumes_active_scan(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    for directory in ("a", "b"):
        (root / directory).mkdir(parents=True, exist_ok=True)
        make_image(root / directory / f"{directory}.png")
    store, indexer = make_indexer(tmp_path, root, depth=1, checkpoint_size=1)
    assert indexer.run("new").complete
    partition_ids = {
        row["relative_path"]: row["partition_id"]
        for row in store._connection.execute(
            "SELECT relative_path, partition_id FROM partitions"
        ).fetchall()
    }
    old_scan_id = store.partition_record(partition_ids["b"])["current_scan_id"]
    real_restart = store.restart_partition
    interrupted = False

    def interrupt_before_second_partition(partition_id, scan_id):
        nonlocal interrupted
        if partition_id == partition_ids["b"] and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return real_restart(partition_id, scan_id)

    monkeypatch.setattr(store, "restart_partition", interrupt_before_second_partition)
    with pytest.raises(KeyboardInterrupt):
        indexer.run("refresh")
    mixed_ids = {
        store.partition_record(partition_id)["current_scan_id"]
        for partition_id in partition_ids.values()
    }
    active_scan_id = store._connection.execute(
        "SELECT value FROM scan_meta WHERE key='active_scan_id'"
    ).fetchone()
    store.close()

    identity = IndexIdentity("products.csv", "a" * 64, "[]", 1, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    resumed = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(1, 1),
    ).run("resume")

    assert old_scan_id in mixed_ids and len(mixed_ids) == 2
    assert active_scan_id is not None
    assert resumed.complete
    assert reopened.active_scan_id() is None
    reopened.close()


def test_refresh_resume_deactivates_deleted_file_and_removes_candidate(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    images = {}
    for directory in ("a", "b"):
        image = root / directory / "matched" / f"{directory}.png"
        image.parent.mkdir(parents=True)
        make_image(image)
        images[directory] = image
    store, indexer = make_indexer(tmp_path, root, depth=1, checkpoint_size=1)
    assert indexer.run("new").complete
    partition_ids = {
        row["relative_path"]: row["partition_id"]
        for row in store._connection.execute(
            "SELECT relative_path, partition_id FROM partitions"
        ).fetchall()
    }
    file_ids_by_partition = {
        row["candidate_directory"]: row["file_id"]
        for row in store._connection.execute(
            "SELECT candidate_directory, file_id FROM files"
        ).fetchall()
    }
    images["b"].unlink()
    real_restart = store.restart_partition
    interrupted = False

    def interrupt_before_deleted_partition(partition_id, scan_id):
        nonlocal interrupted
        if partition_id == partition_ids["b"] and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return real_restart(partition_id, scan_id)

    monkeypatch.setattr(store, "restart_partition", interrupt_before_deleted_partition)
    with pytest.raises(KeyboardInterrupt):
        indexer.run("refresh")

    resumed = indexer.run("resume")
    output = tmp_path / "candidates.csv"
    asset_index_module.write_match_candidates(store, output)
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        candidates = list(csv.DictReader(stream))

    assert resumed.complete
    assert store.file_is_active(file_ids_by_partition["a"]) is True
    assert store.file_is_active(file_ids_by_partition["b"]) is False
    assert [row["candidate_directory"] for row in candidates] == ["a"]
    store.close()


def test_partial_failure_refresh_keeps_original_mode_for_resume_deactivation(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "b"
    image = partition / "matched" / "old.png"
    image.parent.mkdir(parents=True)
    make_image(image)
    store, indexer = make_indexer(tmp_path, root, depth=1, checkpoint_size=1)
    assert indexer.run("new").complete
    old_file_id = file_ids(store)[0]
    image.unlink()
    real_scandir = os.scandir

    def fail_partition(path):
        if Path(path) == partition.resolve():
            raise OSError("temporary refresh failure")
        return real_scandir(path)

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", fail_partition)
    failed = indexer.run("refresh")
    active_mode = store._connection.execute(
        "SELECT value FROM scan_meta WHERE key='active_scan_mode'"
    ).fetchone()
    store.close()

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", real_scandir)
    identity = IndexIdentity("products.csv", "a" * 64, "[]", 1, 1)
    reopened = AssetIndexStore.open(tmp_path / "index.sqlite3", identity)
    resumed = IncrementalAssetIndexer(
        reopened,
        Matcher(),
        (NamedRoot("model", root),),
        IndexOptions(1, 1),
    ).run("resume")

    assert failed.partial_failure
    assert active_mode is not None and active_mode[0] == "refresh"
    assert resumed.complete
    assert reopened.file_is_active(old_file_id) is False
    assert reopened.active_scan() is None
    reopened.close()


def test_resume_skips_completed_and_retries_failed_partition(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "completed").mkdir(parents=True)
    (root / "failed").mkdir()
    make_image(root / "completed" / "a.png")
    make_image(root / "failed" / "b.png")
    store, indexer = make_indexer(tmp_path, root, depth=1)
    real_scandir = os.scandir

    def fail_one(path):
        if Path(path) == (root / "failed").resolve():
            raise OSError("temporary failure")
        return real_scandir(path)

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", fail_one)
    first = indexer.run("new")
    assert first.partial_failure and first.indexed == 1

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", real_scandir)
    second = indexer.run("resume")

    assert second.complete and second.indexed == 2
    assert store.file_count() == 2
    store.close()


def test_refresh_adds_files_and_reinspects_changed_matched_file(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "matched").mkdir(parents=True)
    matched = root / "matched" / "a.png"
    make_image(matched, (20, 10))
    store, indexer = make_indexer(tmp_path, root, depth=1)
    indexer.run("new")
    matched_id = file_ids(store)[0]

    make_image(matched, (40, 20))
    os.utime(matched, ns=(matched.stat().st_atime_ns, matched.stat().st_mtime_ns + 1_000_000))
    make_image(root / "matched" / "added.png")
    from upload_search_materials.assets import inspect_asset as real_inspect
    calls = []
    monkeypatch.setattr(
        "upload_search_materials.asset_index.inspect_asset",
        lambda *args, **kwargs: (calls.append(args[0]) or real_inspect(*args, **kwargs)),
    )

    outcome = indexer.run("refresh")

    assert outcome.complete and store.file_count(active_only=True) == 2
    assert calls == [matched, root / "matched" / "added.png"]
    assert store.file_record(matched_id)["width"] == 40
    store.close()


def test_failed_refresh_keeps_missing_old_file_active(tmp_path, monkeypatch):
    root = tmp_path / "root"
    partition = root / "broken"
    partition.mkdir(parents=True)
    image = partition / "old.png"
    make_image(image)
    store, indexer = make_indexer(tmp_path, root, depth=1)
    indexer.run("new")
    old_id = file_ids(store)[0]
    image.unlink()
    real_scandir = os.scandir

    def fail_partition(path):
        if Path(path) == partition.resolve():
            raise OSError("offline")
        return real_scandir(path)

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", fail_partition)
    outcome = indexer.run("refresh")

    assert outcome.partial_failure
    assert store.file_is_active(old_id) is True
    store.close()


def test_resolved_path_escape_is_failed_and_not_indexed(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.png"
    make_image(outside)

    class EscapedEntry:
        name = "escape.png"
        path = str(outside)

        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks=False):
            return outside.stat()

        def is_dir(self, *, follow_symlinks=False):
            return False

        def is_file(self, *, follow_symlinks=False):
            return True

    class Entries:
        def __enter__(self):
            return iter((EscapedEntry(),))

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", lambda path: Entries())
    store, indexer = make_indexer(tmp_path, root)

    outcome = indexer.run("new")

    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )
    partition = store.partition_record(
        indexer.partition_id_for("model", Path("."))
    )
    assert outcome.complete and not outcome.partial_failure
    assert outcome.failed == 1 and store.file_count() == 0
    assert partition["status"] == "completed"
    assert partition["failed"] == 1
    assert summary["complete"] is True
    assert summary["partial_failure"] is False
    assert summary["errors"] == [
        {
            "source_system": "model",
            "relative_path": "escape.png",
            "partition": ".",
            "code": "PATH_OUTSIDE_ROOT",
            "detail": "resolved path escapes declared root",
        }
    ]
    store.close()


def test_file_stat_error_is_recorded_and_good_file_completes_partition(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    make_image(partition / "bad.png")
    make_image(partition / "good.png")
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    real_scandir = os.scandir

    class BadStatEntry:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name
            self.path = entry.path

        def is_symlink(self):
            return self._entry.is_symlink()

        def stat(self, *, follow_symlinks=False):
            raise OSError("stat denied")

        def is_dir(self, *, follow_symlinks=False):
            return self._entry.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks=False):
            return self._entry.is_file(follow_symlinks=follow_symlinks)

    class Entries:
        def __init__(self, entries):
            self._entries = entries

        def __enter__(self):
            return iter(self._entries)

        def __exit__(self, *args):
            return False

    def stat_failure_scandir(path):
        if Path(path) != partition.resolve():
            return real_scandir(path)
        with real_scandir(path) as scanner:
            entries = [
                BadStatEntry(entry) if entry.name == "bad.png" else entry
                for entry in scanner
            ]
        return Entries(entries)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        stat_failure_scandir,
    )

    outcome = indexer.run("new")
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )

    partition_record = store.partition_record(
        indexer.partition_id_for("model", Path("matched"))
    )
    assert outcome.complete and not outcome.partial_failure
    assert outcome.indexed == 1 and outcome.matched == 1 and outcome.failed == 1
    assert partition_record["status"] == "completed"
    assert partition_record["failed"] == 1
    assert summary["complete"] is True and summary["partial_failure"] is False
    assert any(
        error["relative_path"] == "matched/bad.png"
        and error["partition"] == "matched"
        and error["code"] == "FILE_STAT_ERROR"
        and error["detail"] == "stat denied"
        for error in summary["errors"]
    )
    store.close()


def test_file_inspection_error_is_recorded_and_good_file_still_matches(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    bad = partition / "bad.png"
    good = partition / "good.png"
    make_image(bad)
    make_image(good)
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    from upload_search_materials.assets import inspect_asset as real_inspect

    def fail_one_inspection(path, *args, **kwargs):
        if Path(path) == bad:
            raise OSError("image read denied")
        return real_inspect(path, *args, **kwargs)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.inspect_asset",
        fail_one_inspection,
    )

    outcome = indexer.run("new")
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )

    partition_record = store.partition_record(
        indexer.partition_id_for("model", Path("matched"))
    )
    assert outcome.complete and not outcome.partial_failure
    assert outcome.indexed == 2 and outcome.matched == 1 and outcome.failed == 1
    assert partition_record["status"] == "completed"
    assert partition_record["failed"] == 1
    assert store._connection.execute(
        "SELECT COUNT(DISTINCT file_id) FROM matches"
    ).fetchone()[0] == 1
    assert summary["complete"] is True and summary["partial_failure"] is False
    assert any(
        error["relative_path"] == "matched/bad.png"
        and error["partition"] == "matched"
        and error["code"] == "FILE_INSPECTION_ERROR"
        and error["detail"] == "image read denied"
        for error in summary["errors"]
    )
    store.close()


def test_non_oserror_file_resolve_failure_is_recorded_and_good_file_completes(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    bad = partition / "bad.png"
    good = partition / "good.png"
    make_image(bad)
    make_image(good)
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    real_resolve = Path.resolve

    def fail_one_resolve(path, *args, **kwargs):
        if path == bad:
            raise RuntimeError("symlink loop while resolving file")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_one_resolve)

    outcome = indexer.run("new")
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )

    partition_record = store.partition_record(
        indexer.partition_id_for("model", Path("matched"))
    )
    assert outcome.complete and not outcome.partial_failure
    assert outcome.indexed == 1 and outcome.matched == 1 and outcome.failed == 1
    assert partition_record["status"] == "completed"
    assert partition_record["failed"] == 1
    assert any(
        error["relative_path"] == "matched/bad.png"
        and error["partition"] == "matched"
        and error["code"] == "FILE_STAT_ERROR"
        and error["detail"] == "symlink loop while resolving file"
        for error in summary["errors"]
    )
    store.close()


def test_non_oserror_inspection_failure_is_recorded_and_good_file_completes(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "matched"
    partition.mkdir(parents=True)
    bad = partition / "bad.png"
    good = partition / "good.png"
    make_image(bad)
    make_image(good)
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    from upload_search_materials.assets import inspect_asset as real_inspect

    def fail_one_inspection(path, *args, **kwargs):
        if Path(path) == bad:
            raise Image.DecompressionBombError("image dimensions unsafe")
        return real_inspect(path, *args, **kwargs)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.inspect_asset",
        fail_one_inspection,
    )

    outcome = indexer.run("new")
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )

    partition_record = store.partition_record(
        indexer.partition_id_for("model", Path("matched"))
    )
    assert outcome.complete and not outcome.partial_failure
    assert outcome.indexed == 2 and outcome.matched == 1 and outcome.failed == 1
    assert partition_record["status"] == "completed"
    assert partition_record["failed"] == 1
    assert any(
        error["relative_path"] == "matched/bad.png"
        and error["partition"] == "matched"
        and error["code"] == "FILE_INSPECTION_ERROR"
        and error["detail"] == "image dimensions unsafe"
        for error in summary["errors"]
    )
    store.close()


def test_one_partition_oserror_sets_partial_failure_and_continues(tmp_path, monkeypatch):
    root = tmp_path / "root"
    bad = root / "bad"
    good = root / "good"
    bad.mkdir(parents=True)
    good.mkdir()
    make_image(good / "ok.png")
    real_scandir = os.scandir

    def fail_bad(path):
        if Path(path) == bad.resolve():
            raise OSError("unavailable")
        return real_scandir(path)

    monkeypatch.setattr("upload_search_materials.asset_index.os.scandir", fail_bad)
    store, indexer = make_indexer(tmp_path, root, depth=1)

    outcome = indexer.run("new")

    assert outcome.partial_failure and not outcome.complete
    assert outcome.failed == 1 and outcome.indexed == 1
    assert store.file_count(active_only=True) == 1
    store.close()


def test_first_partition_is_completed_before_later_partition_is_enumerated(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    for directory in ("a", "b"):
        (root / directory).mkdir(parents=True)
        make_image(root / directory / f"{directory}.png")
    store, indexer = make_indexer(tmp_path, root, depth=1, checkpoint_size=1)
    first_partition_id = indexer.partition_id_for("model", Path("a"))
    real_scandir = os.scandir
    observed_before_later_partition = []

    def observe_persisted_checkpoint(path):
        if Path(path) == (root / "b").resolve():
            row = store._connection.execute(
                "SELECT status, processed_count, completed_scan_id "
                "FROM partitions WHERE partition_id=?",
                (first_partition_id,),
            ).fetchone()
            observed_before_later_partition.append(
                None
                if row is None
                else (
                    row["status"],
                    row["processed_count"],
                    row["completed_scan_id"],
                )
            )
        return real_scandir(path)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        observe_persisted_checkpoint,
    )

    outcome = indexer.run("new")

    assert outcome.complete
    assert observed_before_later_partition == [
        ("completed", 1, outcome.scan_id)
    ]
    store.close()


def test_partition_discovery_never_runs_ahead_of_checkpoint_buffer(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    partition = root / "bulk"
    partition.mkdir(parents=True)
    for index in range(5):
        make_image(partition / f"{index}.png")
    checkpoint_size = 2
    store, indexer = make_indexer(
        tmp_path,
        root,
        depth=1,
        checkpoint_size=checkpoint_size,
    )
    partition_id = indexer.partition_id_for("model", Path("bulk"))
    real_scandir = os.scandir
    max_ahead = 0

    class GuardedScanner:
        def __init__(self, context):
            self._context = context
            self._iterator = None
            self._yielded = 0

        def __enter__(self):
            self._iterator = iter(self._context.__enter__())
            return self

        def __exit__(self, *args):
            return self._context.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal max_ahead
            row = store._connection.execute(
                "SELECT processed_count FROM partitions WHERE partition_id=?",
                (partition_id,),
            ).fetchone()
            assert row is not None, "partition must be registered before enumeration"
            processed = int(row["processed_count"])
            assert self._yielded - processed < checkpoint_size
            assert self._iterator is not None
            entry = next(self._iterator)
            self._yielded += 1
            max_ahead = max(max_ahead, self._yielded - processed)
            return entry

    def guarded_scandir(path):
        context = real_scandir(path)
        if Path(path) == partition.resolve():
            return GuardedScanner(context)
        return context

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        guarded_scandir,
    )

    outcome = indexer.run("new")

    assert outcome.complete and outcome.indexed == 5
    assert max_ahead <= checkpoint_size
    assert store.partition_record(partition_id)["processed_count"] == 5
    store.close()


def test_parent_scanner_does_not_run_ahead_of_discovered_child_partition(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    for directory in ("a", "b"):
        (root / directory).mkdir(parents=True)
        make_image(root / directory / f"{directory}.png")
    store, indexer = make_indexer(
        tmp_path,
        root,
        depth=1,
        checkpoint_size=1,
    )
    first_partition_id = indexer.partition_id_for("model", Path("a"))
    real_scandir = os.scandir

    class OrderedRootScanner:
        def __init__(self, entries):
            self._entries = iter(entries)
            self._requested = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            if self._requested == 1:
                row = store._connection.execute(
                    "SELECT status, processed_count FROM partitions "
                    "WHERE partition_id=?",
                    (first_partition_id,),
                ).fetchone()
                assert row is not None
                assert (row["status"], row["processed_count"]) == (
                    "completed",
                    1,
                )
            entry = next(self._entries)
            self._requested += 1
            return entry

    def ordered_scandir(path):
        if Path(path) != root.resolve():
            return real_scandir(path)
        with real_scandir(path) as scanner:
            entries = sorted(scanner, key=lambda entry: entry.name)
        return OrderedRootScanner(entries)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        ordered_scandir,
    )

    outcome = indexer.run("new")

    assert outcome.complete and outcome.indexed == 2
    store.close()


def test_interrupt_persists_unstarted_roots_as_pending_in_current_scan_summary(
    tmp_path, monkeypatch
):
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    for root in (first_root, second_root):
        (root / "partition").mkdir(parents=True)
        make_image(root / "partition" / "asset.png")
    roots = (
        NamedRoot("first", first_root),
        NamedRoot("second", second_root),
    )
    identity = IndexIdentity(
        "products.csv",
        "a" * 64,
        json.dumps(
            [
                {"path": str(root.path), "source_system": root.source_system}
                for root in roots
            ],
            sort_keys=True,
            separators=(",", ":"),
        ),
        1,
        1,
    )
    store = AssetIndexStore.create(tmp_path / "index.sqlite3", identity)
    indexer = IncrementalAssetIndexer(
        store,
        Matcher(),
        roots,
        IndexOptions(1, 1),
    )

    def interrupt_first_checkpoint(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "checkpoint_files", interrupt_first_checkpoint)

    with pytest.raises(KeyboardInterrupt):
        indexer.run("new")

    assert indexer._scan_id is not None
    scan = store.scan_statistics(indexer._scan_id)
    roots_by_source = {
        root["source_system"]: root for root in scan["roots"]
    }
    assert {
        source: (
            root["status"],
            root["current_scan_id"],
            root["discovered"],
            root["indexed"],
            root["matched"],
            root["failed"],
        )
        for source, root in roots_by_source.items()
    } == {
        "first": ("in_progress", indexer._scan_id, 0, 0, 0, 0),
        "second": ("pending", indexer._scan_id, 0, 0, 0, 0),
    }

    summary = asset_index_module.write_scan_summary(
        store,
        ScanOutcome(
            complete=False,
            partial_failure=True,
            discovered=0,
            indexed=0,
            matched=0,
            failed=0,
            elapsed_seconds=0.0,
            scan_id=indexer._scan_id,
        ),
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )
    assert [
        (root["source_system"], root["status"])
        for root in summary["roots"]
    ] == [("first", "in_progress"), ("second", "pending")]
    store.close()


def test_completed_scan_persists_current_root_partition_stats_and_summary(
    tmp_path,
):
    root = tmp_path / "root"
    partition = root / "catalog" / "matched"
    partition.mkdir(parents=True)
    make_image(partition / "a.png")
    make_image(partition / "b.png")
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )

    outcome = indexer.run("new")

    root_id = store._connection.execute(
        "SELECT root_id FROM roots WHERE source_system='model'"
    ).fetchone()[0]
    root_record = store.root_record(root_id)
    partition_record = store.partition_record(
        indexer.partition_id_for("model", Path("catalog"))
    )
    assert {
        key: root_record[key]
        for key in (
            "current_scan_id",
            "status",
            "error_code",
            "error_detail",
            "discovered",
            "indexed",
            "matched",
            "failed",
        )
    } == {
        "current_scan_id": outcome.scan_id,
        "status": "completed",
        "error_code": "",
        "error_detail": "",
        "discovered": 2,
        "indexed": 2,
        "matched": 2,
        "failed": 0,
    }
    assert root_record["checkpoint_at"] and root_record["completed_at"]
    assert {
        key: partition_record[key]
        for key in (
            "current_scan_id",
            "status",
            "error_code",
            "error_detail",
            "discovered",
            "indexed",
            "matched",
            "failed",
        )
    } == {
        "current_scan_id": outcome.scan_id,
        "status": "completed",
        "error_code": "",
        "error_detail": "",
        "discovered": 2,
        "indexed": 2,
        "matched": 2,
        "failed": 0,
    }
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )
    assert {
        key: summary[key]
        for key in ("discovered", "indexed", "matched", "failed")
    } == {"discovered": 2, "indexed": 2, "matched": 2, "failed": 0}
    assert summary["roots"][0]["source_system"] == "model"
    assert summary["roots"][0]["status"] == "completed"
    assert summary["roots"][0]["partitions"][0]["relative_path"] == "catalog"
    assert summary["roots"][0]["partitions"][0]["status"] == "completed"
    store.close()


def test_refresh_partial_failure_counts_only_current_scan_and_protects_history(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    good = root / "good" / "matched"
    bad = root / "bad"
    good.mkdir(parents=True)
    bad.mkdir()
    make_image(good / "good.png")
    make_image(bad / "old.png")
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    assert indexer.run("new").complete
    bad_file_id = store._connection.execute(
        "SELECT file_id FROM files WHERE relative_path='bad/old.png'"
    ).fetchone()[0]
    real_scandir = os.scandir

    def fail_bad_partition(path):
        if Path(path) == bad.resolve():
            raise OSError("partition offline")
        return real_scandir(path)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir",
        fail_bad_partition,
    )

    outcome = indexer.run("refresh")
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "refresh",
        "a" * 64,
    )

    root_id = store._connection.execute(
        "SELECT root_id FROM roots WHERE source_system='model'"
    ).fetchone()[0]
    root_record = store.root_record(root_id)
    bad_record = store.partition_record(
        indexer.partition_id_for("model", Path("bad"))
    )
    good_record = store.partition_record(
        indexer.partition_id_for("model", Path("good"))
    )
    assert outcome.partial_failure
    assert store.file_is_active(bad_file_id) is True
    assert root_record["status"] == "partial_failure"
    assert {
        key: root_record[key]
        for key in ("discovered", "indexed", "matched", "failed")
    } == {"discovered": 1, "indexed": 1, "matched": 1, "failed": 1}
    assert bad_record["status"] == "failed"
    assert bad_record["error_code"] == "PARTITION_ENUMERATION_ERROR"
    assert bad_record["discovered"] == 0
    assert good_record["status"] == "completed"
    assert {
        key: summary[key]
        for key in ("discovered", "indexed", "matched", "failed")
    } == {"discovered": 1, "indexed": 1, "matched": 1, "failed": 1}
    assert summary["database"]["active_files"] == 2
    assert any(
        error["code"] == "PARTITION_ENUMERATION_ERROR"
        and error["relative_path"] == "bad"
        and error["detail"] == "partition offline"
        for error in summary["errors"]
    )
    store.close()


def test_root_enumeration_failure_persists_current_scan_error_and_summary(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    root.mkdir()
    store, indexer = make_indexer(
        tmp_path, root, depth=1, checkpoint_size=1
    )
    real_scandir = os.scandir

    def fail_root(path):
        if Path(path) == root.resolve():
            raise OSError("root offline")
        return real_scandir(path)

    monkeypatch.setattr(
        "upload_search_materials.asset_index.os.scandir", fail_root
    )

    outcome = indexer.run("new")
    root_id = store._connection.execute(
        "SELECT root_id FROM roots WHERE source_system='model'"
    ).fetchone()[0]
    root_record = store.root_record(root_id)
    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        tmp_path / "scan-summary.json",
        "new",
        "a" * 64,
    )

    assert outcome.partial_failure
    assert root_record["current_scan_id"] == outcome.scan_id
    assert root_record["status"] == "failed"
    assert root_record["error_code"] == "ROOT_ENUMERATION_ERROR"
    assert root_record["error_detail"] == "root offline"
    assert root_record["failed"] == 1
    assert summary["failed"] == 1
    assert summary["roots"][0]["status"] == "failed"
    assert summary["errors"] == [
        {
            "source_system": "model",
            "relative_path": ".",
            "code": "ROOT_ENUMERATION_ERROR",
            "detail": "root offline",
        }
    ]
    store.close()


def test_scan_does_not_change_source_file_bytes_size_or_mtime(tmp_path):
    root = tmp_path / "root"
    (root / "matched").mkdir(parents=True)
    (root / "unmatched").mkdir()
    make_image(root / "matched" / "a.png")
    (root / "unmatched" / "broken.jpg").write_bytes(b"broken")
    (root / "movie.mp4").write_bytes(b"video")
    before = source_snapshot(root)
    store, indexer = make_indexer(tmp_path, root)

    indexer.run("new")

    assert source_snapshot(root) == before
    store.close()


def test_candidate_csv_aggregates_active_matches_with_stable_safety_fields(tmp_path):
    identity = IndexIdentity(
        str(tmp_path / "products.csv"),
        "a" * 64,
        json.dumps(
            [{"path": str(tmp_path / "root"), "source_system": "model"}],
            sort_keys=True,
            separators=(",", ":"),
        ),
        2,
        1000,
    )
    store = AssetIndexStore.create(tmp_path / "index.sqlite3", identity)
    root_id = store.upsert_root("model", str(tmp_path / "root"))
    partition_id = store.upsert_partition(root_id, "catalog/123")
    files = tuple(
        IndexedFile(
            "model",
            f"catalog/123/{name}.png",
            str(tmp_path / f"{name}.png"),
            ".png",
            10,
            index,
            "catalog/123",
        )
        for index, name in enumerate(("a", "b", "name", "inactive"), 1)
    )
    file_ids = store.checkpoint_files(partition_id, "scan-1", files)
    for file_id in (file_ids[0], file_ids[2], file_ids[3]):
        store.update_file_inspection(
            file_id,
            sha256="f" * 64,
            width=400,
            height=400,
            validation_status="valid",
            reason_codes=("Z_REASON", "A_REASON"),
        )
    exact_match = PathMatchRecord(
        "123", "SKU-1", "Hat", "exact_product_id", "matched_unlicensed", ("B_REASON", "A_REASON")
    )
    name_match = PathMatchRecord(
        "123", "SKU-1", "Hat", "name_candidate", "needs_manual_confirmation", ("NAME_CANDIDATE",)
    )
    store.replace_file_matches(file_ids[0], (exact_match,))
    store.replace_file_matches(file_ids[1], (exact_match,))
    store.replace_file_matches(file_ids[2], (name_match,))
    store.replace_file_matches(file_ids[3], (exact_match,))
    store._connection.execute("UPDATE files SET active=0 WHERE file_id=?", (file_ids[3],))
    store._connection.commit()
    output = tmp_path / "match-candidates.csv"

    count = asset_index_module.write_match_candidates(store, output)

    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
        assert stream.encoding == "utf-8-sig"
    assert count == 2
    assert list(rows[0]) == [
        "source_system", "candidate_directory", "product_id", "sku",
        "product_title", "match_type", "match_status", "image_count",
        "hashes_complete", "license_status", "reason_codes",
    ]
    assert [(row["match_type"], row["image_count"]) for row in rows] == [
        ("exact_product_id", "2"),
        ("name_candidate", "1"),
    ]
    assert rows[0]["hashes_complete"] == "false"
    assert rows[0]["license_status"] == "unknown"
    assert rows[0]["reason_codes"] == "A_REASON;B_REASON;Z_REASON"
    assert rows[1]["hashes_complete"] == "true"
    assert rows[1]["match_status"] == "needs_manual_confirmation"
    assert not (tmp_path / "confirmed-assets.csv").exists()
    store.close()


def test_candidate_csv_uses_public_query_without_private_connection(tmp_path):
    class QueryOnlyStore:
        def match_candidate_records(self):
            return (
                SimpleNamespace(
                    source_system="model",
                    candidate_directory="catalog/123",
                    relative_path="catalog/123/a.png",
                    sha256="f" * 64,
                    validation_status="valid",
                    file_reason_codes=("FILE_REASON",),
                    product_id="123",
                    sku="SKU-1",
                    product_title="Hat",
                    match_type="exact_product_id",
                    match_status="matched_unlicensed",
                    match_reason_codes=("MATCH_REASON",),
                ),
            )

    output = tmp_path / "match-candidates.csv"

    assert asset_index_module.write_match_candidates(
        QueryOnlyStore(), output
    ) == 1

    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [
        {
            "source_system": "model",
            "candidate_directory": "catalog/123",
            "product_id": "123",
            "sku": "SKU-1",
            "product_title": "Hat",
            "match_type": "exact_product_id",
            "match_status": "matched_unlicensed",
            "image_count": "1",
            "hashes_complete": "true",
            "license_status": "unknown",
            "reason_codes": "FILE_REASON;MATCH_REASON",
        }
    ]


def test_scan_summary_uses_persisted_statistics_errors_and_exact_resume_command(tmp_path):
    products = tmp_path / "products table.csv"
    root = tmp_path / "asset root"
    output = tmp_path / "output dir"
    output.mkdir()
    identity = IndexIdentity(
        str(products),
        "b" * 64,
        json.dumps(
            [{"path": str(root), "source_system": "model"}],
            sort_keys=True,
            separators=(",", ":"),
        ),
        3,
        25,
    )
    store = AssetIndexStore.create(output / "asset-index.sqlite3", identity)
    root_id = store.upsert_root("model", str(root))
    partition_id = store.upsert_partition(root_id, "catalog/123")
    (file_id,) = store.checkpoint_files(
        partition_id,
        "scan-1",
        (
            IndexedFile(
                "model", "catalog/123/a.png", str(root / "catalog/123/a.png"),
                ".png", 10, 1, "catalog/123",
            ),
        ),
    )
    store.update_file_inspection(
        file_id,
        sha256="f" * 64,
        width=400,
        height=400,
        validation_status="valid",
        reason_codes=(),
    )
    store.replace_file_matches(
        file_id,
        (PathMatchRecord("123", "SKU-1", "Hat", "exact_product_id", "matched_unlicensed", ()),),
    )
    store.advance_partition_progress(
        partition_id,
        "scan-1",
        0,
        discovered=0,
        indexed=0,
        matched=1,
    )
    store.fail_partition(partition_id, "permission denied")
    outcome = ScanOutcome(False, True, 99, 98, 97, 96, 1.25)

    summary = asset_index_module.write_scan_summary(
        store,
        outcome,
        output / "scan-summary.json",
        "resume",
        "b" * 64,
    )

    persisted = json.loads((output / "scan-summary.json").read_text(encoding="utf-8"))
    assert summary == persisted
    assert persisted["schema_version"] == 1
    assert persisted["mode"] == "resume"
    assert persisted["products_sha256"] == "b" * 64
    assert persisted["complete"] is False and persisted["partial_failure"] is True
    assert {key: persisted[key] for key in ("discovered", "indexed", "matched", "failed")} == {
        "discovered": 1,
        "indexed": 1,
        "matched": 1,
        "failed": 1,
    }
    assert persisted["database"]["active_files"] == 1
    assert persisted["database"]["match_candidates"] == 1
    assert persisted["errors"] == [
        {
            "source_system": "model",
            "relative_path": "catalog/123",
            "code": "PARTITION_ENUMERATION_ERROR",
            "detail": "permission denied",
        }
    ]
    command = persisted["resume_command"]
    for expected in (
        "index-assets", f'--products "{products}"', f'--root "model={root}"',
        f'--output "{output}"', "--partition-depth 3", "--checkpoint-size 25", "--resume",
    ):
        assert expected in command
    assert persisted["elapsed_seconds"] == 1.25
    store.close()


def test_scan_summary_resume_command_uses_every_immutable_identity_root(tmp_path):
    products = tmp_path / "products table.csv"
    roots = [
        {"path": str(tmp_path / "first root"), "source_system": "first"},
        {"path": str(tmp_path / "second root"), "source_system": "second"},
    ]
    output = tmp_path / "output dir"
    output.mkdir()
    identity = IndexIdentity(
        str(products),
        "c" * 64,
        json.dumps(roots, sort_keys=True, separators=(",", ":")),
        2,
        10,
    )
    store = AssetIndexStore.create(output / "asset-index.sqlite3", identity)
    store.upsert_root("first", roots[0]["path"])

    summary = asset_index_module.write_scan_summary(
        store,
        ScanOutcome(False, True, 0, 0, 0, 0, 0.1),
        output / "scan-summary.json",
        "refresh",
        "c" * 64,
    )

    assert f'--root "first={roots[0]["path"]}"' in summary["resume_command"]
    assert f'--root "second={roots[1]["path"]}"' in summary["resume_command"]
    store.close()
