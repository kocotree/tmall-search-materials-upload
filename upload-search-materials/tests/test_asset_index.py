from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
from PIL import Image

from upload_search_materials.asset_index_store import AssetIndexStore, IndexIdentity
from upload_search_materials.asset_index import IncrementalAssetIndexer, IndexOptions, NamedRoot
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
    assert outcome.indexed == 1
    assert reopened.partition_record(partition_id)["processed_count"] == 2
    assert reopened.file_count() == 2
    assert sum(len(reopened.matches_for(file_id)) for file_id in file_ids(reopened)) == 2
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

    assert second.complete and second.indexed == 1
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

    assert outcome.failed == 1 and store.file_count() == 0
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
