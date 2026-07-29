from __future__ import annotations

import shutil
from pathlib import Path

from scripts.validate_skill_entry import validate


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_project_skill_entry_is_discoverable_and_consistent():
    assert validate(REPOSITORY_ROOT) == []


def test_missing_canonical_skill_returns_stable_validation_error(tmp_path):
    shutil.copytree(
        REPOSITORY_ROOT / ".codex",
        tmp_path / ".codex",
    )
    (tmp_path / "upload-search-materials" / "agents").mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "upload-search-materials" / "agents" / "openai.yaml",
        tmp_path / "upload-search-materials" / "agents" / "openai.yaml",
    )
    errors = validate(tmp_path)
    assert any("missing artifact" in error and "SKILL.md" in error for error in errors)


def test_stale_entry_sha_is_rejected(tmp_path):
    shutil.copytree(
        REPOSITORY_ROOT / ".codex",
        tmp_path / ".codex",
    )
    target_skill = tmp_path / "upload-search-materials"
    (target_skill / "agents").mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "upload-search-materials" / "SKILL.md",
        target_skill / "SKILL.md",
    )
    shutil.copy2(
        REPOSITORY_ROOT / "upload-search-materials" / "agents" / "openai.yaml",
        target_skill / "agents" / "openai.yaml",
    )
    canonical = tmp_path / "upload-search-materials" / "SKILL.md"
    canonical.write_text(
        canonical.read_text(encoding="utf-8") + "\nchanged\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("source SHA differs" in error for error in errors)
