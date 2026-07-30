import json
import os
import threading
import stat

import pytest

from upload_search_materials.persistence import (
    atomic_write_dict_csv,
    atomic_write_json,
    canonical_json_bytes,
    PersistenceAccessDenied,
    read_json,
)
import upload_search_materials.persistence as persistence


def test_atomic_json_creates_first_parent_and_uses_utf8_without_bom(tmp_path):
    target = tmp_path / "deep" / "history" / "result.json"

    atomic_write_json(target, {"标题": "完成"})

    assert target.read_bytes() == canonical_json_bytes({"标题": "完成"})
    assert not target.read_bytes().startswith(b"\xef\xbb\xbf")


def test_json_reader_accepts_utf8_sig(tmp_path):
    target = tmp_path / "bom.json"
    target.write_text('{"status": "ok"}', encoding="utf-8-sig")

    assert read_json(target) == {"status": "ok"}


def test_concurrent_json_writers_never_publish_partial_document(tmp_path):
    target = tmp_path / "state.json"
    barrier = threading.Barrier(3)

    def write(value):
        barrier.wait()
        atomic_write_json(target, {"value": value, "payload": "x" * 1000})

    threads = [
        threading.Thread(target=write, args=(value,))
        for value in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert json.loads(target.read_text(encoding="utf-8"))["value"] in {
        "first",
        "second",
    }
    assert not list(tmp_path.glob(".atomic-*.tmp"))


def test_csv_is_excel_compatible_and_atomic(tmp_path):
    target = tmp_path / "nested" / "status.csv"

    atomic_write_dict_csv(
        target,
        [{"商品ID": "1", "状态": "完成"}],
        fieldnames=["商品ID", "状态"],
    )

    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "完成" in target.read_text(encoding="utf-8-sig")


def test_failed_serialization_does_not_leave_temporary_file(tmp_path):
    target = tmp_path / "state.json"

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    assert not target.exists()
    assert not list(tmp_path.glob(".atomic-*.tmp"))


def test_interrupted_replace_cleans_only_owned_temporary_file(
    tmp_path, monkeypatch
):
    target = tmp_path / "state.json"
    neighbor = tmp_path / ".atomic-neighbor.tmp"
    neighbor.write_text("owned elsewhere", encoding="utf-8")
    monkeypatch.setattr(
        persistence,
        "_replace_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("replace interrupted")
        ),
    )

    with pytest.raises(OSError, match="replace interrupted"):
        atomic_write_json(target, {"status": "pending"})

    assert neighbor.read_text(encoding="utf-8") == "owned elsewhere"
    assert not target.exists()
    assert list(tmp_path.glob(".atomic-*.tmp")) == [neighbor]


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement retry")
def test_windows_sharing_violation_is_retried(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    real_replace = os.replace
    calls = 0

    def transient_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = OSError("sharing violation")
            error.winerror = 32
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", transient_once)

    atomic_write_json(target, {"status": "complete"})

    assert calls == 2
    assert read_json(target)["status"] == "complete"


@pytest.mark.skipif(os.name != "nt", reason="Windows access-denied retry")
def test_writable_target_winerror5_is_retried(tmp_path, monkeypatch):
    target = tmp_path / "session.json"
    target.write_text('{"status":"old"}', encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def transient_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError("brief reader")
            error.winerror = 5
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", transient_once)

    atomic_write_json(target, {"status": "complete"})

    assert calls == 2
    assert read_json(target)["status"] == "complete"
    assert not list(tmp_path.glob(".atomic-probe-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows access-denied retry")
def test_read_only_target_winerror5_fails_without_retry(
    tmp_path, monkeypatch
):
    target = tmp_path / "session.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(stat.S_IREAD)
    calls = 0

    def denied(*_args):
        nonlocal calls
        calls += 1
        error = PermissionError("read only")
        error.winerror = 5
        raise error

    monkeypatch.setattr(persistence.os, "replace", denied)
    try:
        with pytest.raises(
            PersistenceAccessDenied, match="PERSISTENCE_ACCESS_DENIED"
        ):
            atomic_write_json(target, {"status": "blocked"})
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)

    assert calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows access-denied retry")
def test_persistent_writable_winerror5_is_bounded(tmp_path, monkeypatch):
    target = tmp_path / "session.json"
    target.write_text("{}", encoding="utf-8")
    calls = 0

    def denied(*_args):
        nonlocal calls
        calls += 1
        error = PermissionError("persistent")
        error.winerror = 5
        raise error

    monkeypatch.setattr(persistence.os, "replace", denied)

    with pytest.raises(
        PersistenceAccessDenied, match="PERSISTENCE_ACCESS_DENIED"
    ):
        atomic_write_json(target, {"status": "blocked"})

    assert calls == 3
    assert not list(tmp_path.glob(".atomic-probe-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows access-denied retry")
def test_parent_probe_denial_classifies_winerror5_as_permanent(
    tmp_path, monkeypatch
):
    target = tmp_path / "session.json"
    target.write_text("{}", encoding="utf-8")
    real_mkstemp = persistence.tempfile.mkstemp
    replace_calls = 0

    def deny_probe(*args, **kwargs):
        if kwargs.get("prefix") == ".atomic-probe-":
            error = PermissionError("parent denied")
            error.winerror = 5
            raise error
        return real_mkstemp(*args, **kwargs)

    def denied_replace(*_args):
        nonlocal replace_calls
        replace_calls += 1
        error = PermissionError("replace denied")
        error.winerror = 5
        raise error

    monkeypatch.setattr(persistence.tempfile, "mkstemp", deny_probe)
    monkeypatch.setattr(persistence.os, "replace", denied_replace)

    with pytest.raises(
        PersistenceAccessDenied, match="PERSISTENCE_ACCESS_DENIED"
    ):
        atomic_write_json(target, {"status": "blocked"})

    assert replace_calls == 1
    assert not list(tmp_path.glob(".atomic-*.tmp"))


def test_short_atomic_temp_name_survives_deep_windows_style_path(tmp_path):
    deep = tmp_path
    while len(str(deep)) < 220:
        deep = deep / "nested-segment"
    target = deep / "promotion-material-status.checkpoint.json"

    atomic_write_json(target, {"status": "complete"})

    assert read_json(target)["status"] == "complete"
