from pathlib import Path

import pytest

from upload_search_materials.platform_support import (
    AssetSourceUnavailable,
    detect_runtime_platform,
    resolve_asset_root,
)


def test_detects_windows_macos_and_linux():
    assert detect_runtime_platform("win32") == "windows"
    assert detect_runtime_platform("darwin") == "macos"
    assert detect_runtime_platform("linux") == "linux"


def test_macos_rejects_unc_until_it_is_mounted():
    with pytest.raises(AssetSourceUnavailable) as captured:
        resolve_asset_root(r"\\nas\share", platform="macos")
    assert captured.value.reason_code == "NAS_PATH_REQUIRES_MOUNT"


def test_resolves_existing_local_directory(tmp_path):
    checked = resolve_asset_root(tmp_path, platform="macos")
    assert checked.path == Path(tmp_path).resolve()
