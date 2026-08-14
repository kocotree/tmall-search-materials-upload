import json
from pathlib import Path

import pytest

from upload_search_materials.nas_sources import (
    NasSource,
    NasSourceStatus,
    check_nas_source,
    launch_nas_mount,
    load_nas_selection,
    load_nas_sources,
    nas_config_sha256,
    prepare_nas_source,
    select_nas_folders,
)
from upload_search_materials.platform_support import AssetSourceUnavailable


def source(path: Path | str) -> NasSource:
    return NasSource(
        source_id="zhejiang-kuqu",
        label="浙江酷趣",
        host="192.168.110.20",
        share="浙江酷趣",
        windows_path=str(path),
        subpaths=("运营中心/营销板块",),
    )


def test_repository_catalog_has_the_two_expected_share_roots():
    config = Path(__file__).parents[1] / "config" / "nas-sources.yaml"
    sources = load_nas_sources(config)

    assert set(sources) == {"zhejiang-kuqu", "visual-department"}
    assert sources["zhejiang-kuqu"].share == "浙江酷趣"
    assert sources["zhejiang-kuqu"].windows_path == r"\\192.168.110.20\浙江酷趣"
    assert sources["visual-department"].share == "视觉部"
    assert sources["visual-department"].windows_path == r"\\192.168.124.85\视觉部"


def test_config_rejects_credentials(tmp_path):
    config = tmp_path / "nas.yaml"
    config.write_text(
        "nas_sources:\n  - id: source-one\n    host: nas\n    share: media\n"
        "    password: forbidden\n    subpaths: []\n",
        encoding="utf-8",
    )

    with pytest.raises(AssetSourceUnavailable) as captured:
        load_nas_sources(config)

    assert captured.value.reason_code == "NAS_CONFIG_CONTAINS_SECRET"


def test_accessible_windows_source_is_ready(tmp_path):
    status = check_nas_source(source(tmp_path), platform="windows")

    assert status.state == "ready"


def test_windows_connection_opens_unc_in_explorer(tmp_path):
    commands = []
    launch_nas_mount(
        source(r"\\192.168.110.20\浙江酷趣"),
        platform="windows",
        launcher=commands.append,
    )

    assert commands == [["explorer.exe", r"\\192.168.110.20\浙江酷趣"]]


def test_prepare_never_connects_without_explicit_permission(tmp_path):
    configured = source(tmp_path / "missing")
    calls = []
    status = prepare_nas_source(
        configured,
        allow_mount=False,
        platform="windows",
        checker=lambda value: NasSourceStatus(
            value.source_id, "not_mounted", "NAS_NOT_MOUNTED",
            value.windows_path, "not mounted",
        ),
        launcher=calls.append,
    )

    assert status.reason_code == "NAS_NOT_MOUNTED"
    assert calls == []


def test_selected_symlink_cannot_escape_share(tmp_path, monkeypatch):
    mount_root = tmp_path / "share"
    outside = tmp_path / "outside"
    mount_root.mkdir()
    outside.mkdir()
    try:
        (mount_root / "运营中心").symlink_to(
            outside, target_is_directory=True
        )
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows 未启用创建符号链接所需的开发者权限")
        raise
    configured = source(mount_root)
    monkeypatch.setattr(
        "upload_search_materials.nas_sources.prepare_nas_source",
        lambda *_args, **_kwargs: NasSourceStatus(
            configured.source_id, "ready", "", str(mount_root), "ready"
        ),
    )

    with pytest.raises(AssetSourceUnavailable) as captured:
        select_nas_folders(
            {configured.source_id: configured},
            ["zhejiang-kuqu:运营中心/营销板块"],
            platform="windows",
        )

    assert captured.value.reason_code == "NAS_SUBPATH_OUTSIDE_SHARE"


def test_selection_is_bound_to_catalog_hash(tmp_path):
    config = tmp_path / "nas.yaml"
    config.write_text("nas_sources: []\n", encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps({
            "schema_version": 1,
            "nas_config_sha256": nas_config_sha256(config),
            "selected_folders": ["source:folder"],
        }),
        encoding="utf-8",
    )
    assert load_nas_selection(selection, expected_config_path=config) == ["source:folder"]
    config.write_text("nas_sources: [changed]\n", encoding="utf-8")

    with pytest.raises(AssetSourceUnavailable) as captured:
        load_nas_selection(selection, expected_config_path=config)

    assert captured.value.reason_code == "NAS_SELECTION_CONFIG_CHANGED"
