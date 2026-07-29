import json
from pathlib import Path

import pytest
import yaml

from upload_search_materials.browser.config import (
    SelectorConfigError,
    discover_selector_profile,
    load_selector_profile,
)


def write_profile(path: Path, **overrides) -> None:
    document = {
        "schema_version": 1,
        "profile_name": "current-store",
        "profile_version": "2026.07.29",
        "production": True,
        "supported_purposes": ["high_value_collection"],
        "material_center_url": (
            "https://myseller.taobao.com/home.htm/"
            "material-center/material-management"
        ),
        "store_name": "[data-store-name]",
        "human_check": "[data-human-check]",
        "promotion_tab": 'li[role="tab"]:has-text("搜推素材")',
        "high_value_filter": '[role="checkbox"]:has-text("搜推高价值")',
        "promotion_rows": "tbody tr",
        "promotion_next_page": 'button:has-text("下一页")',
    }
    document.update(overrides)
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_high_value_profile_does_not_require_publish_selectors(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    write_profile(path)

    profile = load_selector_profile(
        path, purpose="high_value_collection", production=True
    )

    assert profile.name == "current-store"
    assert profile.version == "2026.07.29"
    assert len(profile.sha256) == 64
    assert "publish_button" not in profile.selectors


def test_production_profile_rejects_placeholders(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    write_profile(path, store_name="#store")

    with pytest.raises(
        SelectorConfigError, match="SELECTOR_PLACEHOLDER_REJECTED"
    ):
        load_selector_profile(
            path, purpose="high_value_collection", production=True
        )


def test_profile_must_declare_requested_purpose(tmp_path):
    path = tmp_path / "selectors.local.yaml"
    write_profile(path, supported_purposes=["publish"])

    with pytest.raises(
        SelectorConfigError, match="SELECTOR_PURPOSE_NOT_DECLARED"
    ):
        load_selector_profile(
            path, purpose="high_value_collection", production=True
        )


def test_selector_discovery_precedence_and_example_rejection(tmp_path):
    workspace = tmp_path / "workspace"
    config_dir = workspace / "upload-search-materials" / "config"
    config_dir.mkdir(parents=True)
    default = config_dir / "selectors.local.yaml"
    environment = tmp_path / "environment.yaml"
    explicit = tmp_path / "explicit.yaml"
    for path in (default, environment, explicit):
        write_profile(path)

    selected = discover_selector_profile(
        explicit=explicit,
        environment={"TMALL_SELECTORS_FILE": str(environment)},
        local_config={"selectors_file": str(default)},
        workspace_root=workspace,
    )
    assert selected == explicit.resolve()

    example = config_dir / "selectors.example.yaml"
    write_profile(example)
    with pytest.raises(
        SelectorConfigError, match="SELECTOR_EXAMPLE_NOT_PRODUCTION"
    ):
        discover_selector_profile(
            explicit=example,
            environment={},
            local_config={},
            workspace_root=workspace,
        )
