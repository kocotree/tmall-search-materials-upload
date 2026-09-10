import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifest_exposes_both_business_skills():
    manifest = json.loads(
        (
            REPOSITORY_ROOT / ".codex-plugin" / "plugin.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["name"] == "tmall-search-materials"
    assert manifest["version"].startswith("0.1.0")
    assert manifest["skills"] == "./skills/"
    assert {
        path.name
        for path in (REPOSITORY_ROOT / "skills").iterdir()
        if (path / "SKILL.md").is_file()
    } == {"upload-search-materials", "maintain-team-folder-index"}


def test_plugin_does_not_publish_workspace_only_skills():
    plugin_skill_names = {
        path.name
        for path in (REPOSITORY_ROOT / "skills").iterdir()
        if path.is_dir()
    }

    assert not any(name.startswith("openspec-") for name in plugin_skill_names)


def test_team_marketplace_installs_plugin_from_github_win_over_https():
    marketplace = json.loads(
        (
            REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json"
        ).read_text(encoding="utf-8")
    )

    assert marketplace["name"] == "tmall-materials-team"
    assert marketplace["interface"]["displayName"] == "天猫搜推素材团队插件"
    assert marketplace["plugins"] == [
        {
            "name": "tmall-search-materials",
            "source": {
                "source": "url",
                "url": (
                    "https://github.com/kocotree/"
                    "tmall-search-materials-upload.git"
                ),
                "ref": "win",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ]
