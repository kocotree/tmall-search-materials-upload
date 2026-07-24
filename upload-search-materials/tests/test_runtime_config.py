import json
from pathlib import Path

import pytest

from upload_search_materials.runtime_config import load_runtime_config


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
    assert runtime.image_sources[0]["path"] == r"X:\company-media"


def test_missing_explicit_config_fails_with_precise_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="runtime config not found"):
        load_runtime_config(tmp_path / "missing.json", environ={}, start=tmp_path)


def test_runtime_source_contains_no_machine_specific_drive_or_username():
    package = Path(__file__).parents[1] / "src" / "upload_search_materials"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (package / "runtime_config.py", package / "interaction" / "web.py")
    )

    assert r"C:\Users" not in source
    assert "Y:\\" not in source
    assert "Z:\\" not in source
