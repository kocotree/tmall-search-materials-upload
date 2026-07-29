import json
from pathlib import Path

import pytest

from upload_search_materials.runtime_config import (
    inspect_image_sources,
    load_runtime_config,
    normalize_image_sources,
    save_image_sources,
)


PRODUCT_NAME = "天猫商品信息表_产品数据表_数据总表.csv"
RULE_NAME = "天猫商品信息表_每月推品规则（合并）_Grid View.csv"


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "portable-project"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "upload-search-materials" / "config").mkdir(parents=True)
    return workspace


def test_discovers_unique_tables_from_workspace_relative_docs(tmp_path):
    workspace = make_workspace(tmp_path)
    products = workspace / "docs" / PRODUCT_NAME
    rules = workspace / "docs" / RULE_NAME
    products.write_text("product", encoding="utf-8")
    rules.write_text("rules", encoding="utf-8")

    runtime = load_runtime_config(environ={}, start=workspace / "upload-search-materials")

    assert runtime.workspace_root == workspace.resolve()
    assert runtime.products.path == products.resolve()
    assert runtime.products.status == "discovered"
    assert runtime.rules.path == rules.resolve()
    assert runtime.runs_root == workspace / "runs"
    assert runtime.folder_index_root == workspace / ".local-cache" / "folder-index"
    assert runtime.image_sources == ()


def test_ambiguous_table_match_is_not_selected(tmp_path):
    workspace = make_workspace(tmp_path)
    for suffix in ("A", "B"):
        (workspace / "docs" / f"天猫商品信息表_{suffix}_产品数据表_数据总表.csv").write_text(
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


def test_missing_explicit_config_fails_with_precise_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="runtime config not found"):
        load_runtime_config(tmp_path / "missing.json", environ={}, start=tmp_path)


def test_saves_one_or_many_image_sources_to_ignored_machine_config(tmp_path):
    workspace = make_workspace(tmp_path)
    runtime = load_runtime_config(environ={}, start=workspace)
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

    assert updated.config_path == workspace / "upload-search-materials/config/local-paths.json"
    saved = json.loads(updated.config_path.read_text(encoding="utf-8"))
    assert [item["label"] for item in saved["image_sources"]] == ["模特图", "买家秀"]
    statuses = inspect_image_sources(updated, saved["image_sources"])
    assert [item["status"] for item in statuses] == ["available", "unavailable"]
    assert statuses[0]["reason_code"] == "PATH_AVAILABLE"
    assert statuses[1]["reason_code"] == "PATH_NOT_FOUND"


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


def test_runtime_source_contains_no_machine_specific_drive_or_username():
    package = Path(__file__).parents[1] / "src" / "upload_search_materials"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (package / "runtime_config.py", package / "interaction" / "web.py")
    )

    assert r"C:\Users" not in source
    assert "Y:\\" not in source
    assert "Z:\\" not in source
