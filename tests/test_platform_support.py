from pathlib import Path

import pytest

from upload_search_materials.platform_support import (
    AssetSourceUnavailable,
    detect_runtime_platform,
    resolve_asset_root,
)


def test_detects_windows_and_rejects_other_platforms():
    assert detect_runtime_platform("win32") == "windows"
    with pytest.raises(AssetSourceUnavailable) as captured:
        detect_runtime_platform("unsupported")
    assert captured.value.reason_code == "PLATFORM_UNSUPPORTED"


def test_resolves_existing_local_directory(tmp_path):
    checked = resolve_asset_root(tmp_path, platform="windows")
    assert checked.path == Path(tmp_path).resolve()


def test_rejects_an_explicit_non_windows_platform(tmp_path):
    with pytest.raises(AssetSourceUnavailable) as captured:
        resolve_asset_root(tmp_path, platform="unsupported")
    assert captured.value.reason_code == "PLATFORM_UNSUPPORTED"
