from __future__ import annotations

import shutil
from pathlib import Path

from scripts.validate_skill_entry import validate


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_project_skill_entry_is_discoverable_and_consistent():
    assert validate(REPOSITORY_ROOT) == []


def test_missing_canonical_skill_returns_stable_validation_error(tmp_path):
    shutil.copytree(REPOSITORY_ROOT / "skills", tmp_path / "skills")
    (tmp_path / "agents").mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "agents" / "openai.yaml",
        tmp_path / "agents" / "openai.yaml",
    )
    errors = validate(tmp_path)
    assert any("missing artifact" in error and "SKILL.md" in error for error in errors)


def test_stale_entry_sha_is_rejected(tmp_path):
    shutil.copytree(REPOSITORY_ROOT / "skills", tmp_path / "skills")
    target_skill = tmp_path
    (target_skill / "agents").mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "SKILL.md",
        target_skill / "SKILL.md",
    )
    shutil.copy2(
        REPOSITORY_ROOT / "agents" / "openai.yaml",
        target_skill / "agents" / "openai.yaml",
    )
    canonical = tmp_path / "SKILL.md"
    canonical.write_text(
        canonical.read_text(encoding="utf-8") + "\nchanged\n",
        encoding="utf-8",
    )
    errors = validate(tmp_path)
    assert any("source SHA differs" in error for error in errors)
