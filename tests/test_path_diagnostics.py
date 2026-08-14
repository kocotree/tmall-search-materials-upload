import os
from pathlib import Path
import subprocess

import pytest

from upload_search_materials.path_diagnostics import (
    classify_windows_path,
    diagnose_image_source,
    diagnose_image_sources,
    portable_unc_suggestion,
    run_bounded_path_probe,
)
from upload_search_materials.path_probe import (
    probe_directory_metadata,
    reason_from_os_error,
)


def test_classifies_local_mapped_unc_and_invalid_paths(tmp_path):
    assert classify_windows_path(str(tmp_path)) == "drive"
    assert classify_windows_path(r"Y:\视觉部\1-模特图") == "drive"
    assert classify_windows_path(r"\\nas\media\视觉部") == "unc"
    assert classify_windows_path("relative/media") == "invalid"


@pytest.mark.parametrize(
    "path",
    [
        r"Y:\视觉部\1-模特图",
        r"Z:\浙江酷趣\运营中心\优质买家秀",
    ],
)
def test_missing_company_drive_mapping_has_specific_recovery(path):
    diagnostic = diagnose_image_source(
        {"label": "NAS", "path": path},
        drive_exists=lambda _root: False,
        remote_probe=lambda *_args, **_kwargs: pytest.fail(
            "missing mapping must not touch the network path"
        ),
    )

    assert diagnostic["status"] == "unavailable"
    assert diagnostic["available"] is False
    assert diagnostic["path_kind"] == "mapped_drive"
    assert diagnostic["reason_code"] == "DRIVE_NOT_MAPPED"
    assert "选择文件夹" in diagnostic["next_action"]
    assert "完整文件夹路径" in diagnostic["next_action"]


def test_local_directory_uses_metadata_only_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        os,
        "scandir",
        lambda *_args, **_kwargs: pytest.fail(
            "path diagnostics must not enumerate directory contents"
        ),
    )

    diagnostic = diagnose_image_source(
        {"label": "local", "path": str(tmp_path)}
    )

    assert diagnostic["reason_code"] == "PATH_AVAILABLE"
    assert diagnostic["available"] is True
    assert diagnostic["windows_error"] is None


def test_unc_reason_is_preserved_from_bounded_probe():
    diagnostic = diagnose_image_source(
        {"label": "NAS", "path": r"\\offline-nas\media\images"},
        remote_probe=lambda *_args, **_kwargs: {
            "available": False,
            "reason_code": "NETWORK_HOST_UNAVAILABLE",
            "windows_error": 53,
        },
    )

    assert diagnostic["path_kind"] == "unc"
    assert diagnostic["reason_code"] == "NETWORK_HOST_UNAVAILABLE"
    assert diagnostic["windows_error"] == 53


def test_missing_mapping_uses_configured_unc_fallback():
    calls = []

    def probe(path, **_kwargs):
        calls.append(path)
        return {"available": True, "reason_code": "PATH_AVAILABLE"}

    diagnostic = diagnose_image_source(
        {
            "source_id": "source-model",
            "label": "NAS",
            "path": r"Y:\model",
            "canonical_unc": r"\\nas\media\model",
        },
        drive_exists=lambda _root: False,
        remote_probe=probe,
    )

    assert calls == [r"\\nas\media\model"]
    assert diagnostic["available"] is True
    assert diagnostic["path_kind"] == "unc_fallback"
    assert diagnostic["source_id"] == "source-model"


def test_windows_errors_map_to_stable_reasons():
    error = OSError("denied")
    error.winerror = 5
    assert reason_from_os_error(error) == "ACCESS_DENIED"
    missing = OSError("share")
    missing.winerror = 67
    assert reason_from_os_error(missing) == "NETWORK_SHARE_NOT_FOUND"


def test_mapped_drive_returns_unc_suggestion_without_replacing_path():
    path = r"Y:\视觉部\1-模特图"
    diagnostic = diagnose_image_source(
        {"label": "NAS", "path": path},
        drive_exists=lambda _root: True,
        drive_type=lambda _root: 4,
        unc_resolver=lambda _root: r"\\nas\media",
        remote_probe=lambda *_args, **_kwargs: {
            "available": True,
            "reason_code": "PATH_AVAILABLE",
        },
    )

    assert diagnostic["path"] == path
    assert diagnostic["portable_path_suggestion"] == (
        r"\\nas\media\视觉部\1-模特图"
    )
    assert portable_unc_suggestion(path, r"\\nas\media").startswith(
        r"\\nas\media"
    )


def test_bounded_probe_kills_only_its_owned_timed_out_process():
    class TimedOut:
        returncode = None

        def __init__(self):
            self.killed = False
            self.waits = 0

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("probe", timeout)
            self.returncode = -9

        def kill(self):
            self.killed = True

    process = TimedOut()
    result = run_bounded_path_probe(
        r"\\offline\share",
        timeout_seconds=0.05,
        popen=lambda *_args, **_kwargs: process,
    )

    assert result["reason_code"] == "PATH_CHECK_TIMEOUT"
    assert process.killed is True


def test_metadata_probe_does_not_write_or_enumerate(tmp_path, monkeypatch):
    target = tmp_path / "source"
    target.mkdir()
    before = set(os.listdir(tmp_path))
    monkeypatch.setattr(
        os,
        "scandir",
        lambda *_args, **_kwargs: pytest.fail("must not enumerate"),
    )

    result = probe_directory_metadata(str(target))

    assert result["reason_code"] == "PATH_AVAILABLE"
    assert set(os.listdir(tmp_path)) == before


def test_batch_supports_fifty_local_sources_without_enumeration(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        os,
        "scandir",
        lambda *_args, **_kwargs: pytest.fail("must not enumerate"),
    )
    sources = [
        {"label": f"source-{index}", "path": str(tmp_path)}
        for index in range(50)
    ]

    diagnostics = diagnose_image_sources(sources)

    assert len(diagnostics) == 50
    assert all(item["reason_code"] == "PATH_AVAILABLE" for item in diagnostics)
