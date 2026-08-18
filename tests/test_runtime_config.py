import json
from pathlib import Path

import pytest

from upload_search_materials.runtime_config import (
    DEFAULT_TEAM_FOLDER_INDEX_NAS_SOURCE_ID,
    DEFAULT_TEAM_FOLDER_INDEX_ROOT,
    image_source_path_key,
    inspect_image_sources,
    load_runtime_config,
    normalize_image_sources,
    save_image_sources,
    save_selector_profile_path,
    stable_image_source_id,
)


PRODUCT_NAME = "天猫商品信息表_产品数据表_数据总表.csv"
RULE_NAME = "天猫商品信息表_每月推品规则（合并）_Grid View.csv"


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "portable-project"
    (workspace / "src" / "upload_search_materials" / "docs").mkdir(parents=True)
    (workspace / "config").mkdir(parents=True)
    (workspace / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (workspace / "pyproject.toml").write_text("[project]\nname='test'\n")
    return workspace


def test_discovers_unique_tables_from_workspace_relative_docs(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    products = workspace / "src" / "upload_search_materials" / "docs" / PRODUCT_NAME
    rules = workspace / "src" / "upload_search_materials" / "docs" / RULE_NAME
    products.write_text("product", encoding="utf-8")
    rules.write_text("rules", encoding="utf-8")

    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)},
        start=workspace / "src",
    )

    assert runtime.workspace_root == workspace.resolve()
    assert runtime.products.path == products.resolve()
    assert runtime.products.status == "discovered"
    assert runtime.rules.path == rules.resolve()
    assert runtime.runs_root == user_data / "runs"
    assert runtime.folder_index_root == user_data / "cache" / "folder-index"
    assert runtime.team_folder_index_root == DEFAULT_TEAM_FOLDER_INDEX_ROOT
    assert (
        runtime.team_folder_index_nas_source_id
        == DEFAULT_TEAM_FOLDER_INDEX_NAS_SOURCE_ID
    )
    assert runtime.browser_profile_dir == user_data / "browser-profile"
    assert runtime.user_data_root == user_data
    assert runtime.image_sources == ()


def test_ambiguous_table_match_is_not_selected(tmp_path):
    workspace = make_workspace(tmp_path)
    for suffix in ("A", "B"):
        (workspace / "src" / "upload_search_materials" / "docs" / f"天猫商品信息表_{suffix}_产品数据表_数据总表.csv").write_text(
            "product", encoding="utf-8"
        )

    runtime = load_runtime_config(environ={}, start=workspace)

    assert runtime.products.path is None
    assert runtime.products.status == "ambiguous"
    assert len(runtime.products.candidates) == 2


def test_machine_local_config_overrides_drive_letters_and_relative_paths(tmp_path):
    workspace = make_workspace(tmp_path)
    config = workspace / "machine.json"
    config.write_text(
        json.dumps(
            {
                "workspace_root": str(workspace),
                "products_csv": "inputs/products.csv",
                "runs_root": "task-runs",
                "folder_index_root": "machine-cache/folders",
                "team_folder_index_root": r"\\192.168.110.20\浙江酷趣\team-index",
                "team_folder_index_nas_source_id": "zhejiang-kuqu",
                "selectors_file": "machine/selectors.yaml",
                "cdp_url": "http://127.0.0.1:9333",
                "browser_executable": "bin/browser.exe",
                "browser_profile_dir": "machine/chrome-profile",
                "material_center_url": "https://example.test/materials",
                "image_sources": [
                    {"label": "NAS", "path": r"X:\\company-media"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runtime = load_runtime_config(config, environ={}, start=tmp_path)

    assert runtime.products.path == (workspace / "inputs" / "products.csv").resolve()
    assert runtime.products.status == "missing"
    assert runtime.runs_root == (workspace / "task-runs").resolve()
    assert runtime.folder_index_root == (
        workspace / "machine-cache" / "folders"
    ).resolve()
    assert runtime.team_folder_index_root == Path(
        r"\\192.168.110.20\浙江酷趣\team-index"
    )
    assert runtime.team_folder_index_nas_source_id == "zhejiang-kuqu"
    assert runtime.selectors_file is None
    assert runtime.cdp_url == "http://127.0.0.1:9333"
    assert runtime.browser_executable == (workspace / "bin/browser.exe").resolve()
    assert runtime.browser_profile_dir == (
        workspace / "machine/chrome-profile"
    ).resolve()
    assert runtime.material_center_url == "https://example.test/materials"
    assert runtime.image_sources[0]["path"] == r"X:\company-media"


def test_folder_index_root_can_be_overridden_by_environment(tmp_path):
    workspace = make_workspace(tmp_path)
    shared_index = tmp_path / "shared-folder-index"

    runtime = load_runtime_config(
        environ={"TMALL_FOLDER_INDEX_ROOT": str(shared_index)},
        start=workspace,
    )

    assert runtime.folder_index_root == shared_index.resolve()


def test_team_folder_index_root_can_be_overridden_by_environment(tmp_path):
    workspace = make_workspace(tmp_path)
    shared_index = tmp_path / "team-folder-index"

    runtime = load_runtime_config(
        environ={
            "TMALL_TEAM_FOLDER_INDEX_ROOT": str(shared_index),
            "TMALL_TEAM_FOLDER_INDEX_NAS_SOURCE_ID": "ZHEJIANG-KUQU",
        },
        start=workspace,
    )

    assert runtime.team_folder_index_root == shared_index.resolve()
    assert runtime.team_folder_index_nas_source_id == "zhejiang-kuqu"


def test_missing_explicit_config_fails_with_precise_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="runtime config not found"):
        load_runtime_config(tmp_path / "missing.json", environ={}, start=tmp_path)


def test_saves_one_or_many_image_sources_to_ignored_machine_config(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )
    first = workspace / "media" / "model"
    second = workspace / "media" / "buyer"
    first.mkdir(parents=True)

    updated = save_image_sources(
        runtime,
        [
            {"label": "模特图", "path": str(first)},
            {"label": "买家秀", "path": str(second)},
        ],
    )

    assert updated.config_path == user_data / "config/runtime.json"
    saved = json.loads(updated.config_path.read_text(encoding="utf-8"))
    assert [item["label"] for item in saved["image_sources"]] == ["模特图", "买家秀"]
    assert saved["team_folder_index_root"] == str(
        DEFAULT_TEAM_FOLDER_INDEX_ROOT
    )
    assert (
        saved["team_folder_index_nas_source_id"]
        == DEFAULT_TEAM_FOLDER_INDEX_NAS_SOURCE_ID
    )
    statuses = inspect_image_sources(updated, saved["image_sources"])
    assert [item["status"] for item in statuses] == ["available", "unavailable"]
    assert statuses[0]["reason_code"] == "PATH_AVAILABLE"
    assert statuses[1]["reason_code"] == "PATH_NOT_FOUND"


def test_loads_machine_config_from_user_data_root(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    config = user_data / "config/runtime.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"cdp_url": "http://127.0.0.1:9333"}),
        encoding="utf-8",
    )

    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )

    assert runtime.config_path == config
    assert runtime.cdp_url == "http://127.0.0.1:9333"


def test_selector_profile_is_installed_in_stable_user_data(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    source = workspace / "temporary-plugin-cache" / "selectors.local.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("production: true\nprofile_name: local\n", encoding="utf-8")
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )

    updated = save_selector_profile_path(runtime, source)
    installed = user_data / "config" / "selectors.local.yaml"
    source.unlink()
    reloaded = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )

    assert updated.selectors_file == installed.resolve()
    assert installed.read_text(encoding="utf-8") == (
        "production: true\nprofile_name: local\n"
    )
    assert reloaded.selectors_file == installed.resolve()
    saved = json.loads(
        (user_data / "config/runtime.json").read_text(encoding="utf-8")
    )
    assert saved["selectors_file"] == str(installed.resolve())


def test_image_source_configuration_requires_unique_nonempty_items(tmp_path):
    with pytest.raises(ValueError, match="1-50"):
        normalize_image_sources([], tmp_path)
    with pytest.raises(ValueError, match="duplicate image source path"):
        normalize_image_sources(
            [
                {"label": "A", "path": str(tmp_path)},
                {"label": "B", "path": str(tmp_path)},
            ],
            tmp_path,
        )


def test_legacy_source_migrates_to_stable_id_across_machine_bindings(
    tmp_path
):
    first = normalize_image_sources(
        [{"label": "共享模特图", "path": r"Y:\model"}],
        tmp_path,
    )[0]
    second = normalize_image_sources(
        [
            {
                "source_id": first["source_id"],
                "label": "共享模特图",
                "path": r"M:\model",
                "canonical_unc": r"\\nas\media\model",
            }
        ],
        tmp_path,
    )[0]

    assert first["source_id"] == second["source_id"]
    assert first["path"] != second["path"]
    assert second["canonical_unc"] == r"\\nas\media\model"


def test_source_id_uses_path_without_drive_or_unc_server_and_ignores_label(
    tmp_path,
):
    drive_path = r"Y:\视觉部\1-模特图"
    unc_path = r"\\192.168.124.85\视觉部\1-模特图"

    first = normalize_image_sources(
        [{"label": "图片源 3", "path": drive_path}],
        tmp_path,
    )[0]
    renamed = normalize_image_sources(
        [{"label": "公司模特图", "path": drive_path}],
        tmp_path,
    )[0]
    unc = normalize_image_sources(
        [{"label": "视觉部模特图", "path": unc_path}],
        tmp_path,
    )[0]

    assert image_source_path_key(drive_path) == r"视觉部\1-模特图"
    assert image_source_path_key(unc_path) == r"视觉部\1-模特图"
    assert first["source_id"] == renamed["source_id"] == unc["source_id"]
    assert first["source_id"] == stable_image_source_id(drive_path)


def test_deleted_source_reuses_its_id_when_same_path_is_added_again(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    first_path = workspace / "media" / "model"
    second_path = workspace / "media" / "buyer"
    first_path.mkdir(parents=True)
    second_path.mkdir(parents=True)
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )
    initial = save_image_sources(
        runtime,
        [
            {"label": "模特图", "path": str(first_path)},
            {"label": "买家秀", "path": str(second_path)},
        ],
    )
    original_id = initial.image_sources[0]["source_id"]

    after_delete = save_image_sources(initial, [initial.image_sources[1]])
    reloaded = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )
    restored = save_image_sources(
        reloaded,
        [
            reloaded.image_sources[0],
            {"label": "图片源 2", "path": str(first_path)},
        ],
    )

    restored_source = next(
        item for item in restored.image_sources if item["path"] == str(first_path)
    )
    assert restored_source["source_id"] == original_id
    assert any(
        item["source_id"] == original_id
        and item["path"] == str(first_path)
        for item in after_delete.image_source_history
    )
    saved = json.loads(restored.config_path.read_text(encoding="utf-8"))
    assert "image_source_history" in saved


def test_image_source_check_reuses_unique_current_path_binding(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    source_path = workspace / "media" / "model"
    source_path.mkdir(parents=True)
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )
    saved = save_image_sources(
        runtime,
        [{"label": "模特图", "path": str(source_path)}],
    )

    checked = inspect_image_sources(
        saved,
        [{"label": "重新添加的图片源", "path": str(source_path)}],
    )

    assert checked[0]["source_id"] == saved.image_sources[0]["source_id"]


def test_path_derived_id_replaces_conflicting_legacy_ids(tmp_path):
    workspace = make_workspace(tmp_path)
    user_data = tmp_path / "user-data"
    source_path = workspace / "media" / "model"
    source_path.mkdir(parents=True)
    config = user_data / "config/runtime.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "image_sources": [
                    {
                        "source_id": "source-current",
                        "label": "当前来源",
                        "path": str(source_path),
                    }
                ],
                "image_source_history": [
                    {
                        "source_id": "source-previous",
                        "label": "历史来源",
                        "path": str(source_path),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )

    checked = inspect_image_sources(
        runtime,
        [{"label": "重新添加的图片源", "path": str(source_path)}],
    )

    assert checked[0]["source_id"] == stable_image_source_id(source_path)
    assert {item["source_id"] for item in runtime.image_source_history} == {
        "source-current",
        "source-previous",
    }


def test_runtime_source_contains_no_machine_specific_drive_or_username():
    package = Path(__file__).parents[1] / "src" / "upload_search_materials"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (package / "runtime_config.py", package / "interaction" / "web.py")
    )

    assert r"C:\Users" not in source
    assert "Y:\\" not in source
    assert "Z:\\" not in source
