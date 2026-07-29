"""Validate the canonical Skill, project discovery entry, and Agent metadata."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import yaml


FRONTEND_CONTRACT_TERMS = (
    "interaction UI",
    "store",
    "image",
)
TRIGGER_TERMS = (
    "starting",
    "configuring",
    "testing",
    "resuming",
    "reviewing",
    "auditing",
    "dry-running",
    "uploading",
)


def _frontmatter(text: str, path: Path) -> dict:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter")
    value = yaml.safe_load(match.group(1))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: frontmatter must be an object")
    return value


def validate(repository_root: Path) -> list[str]:
    errors: list[str] = []
    canonical = repository_root / "upload-search-materials" / "SKILL.md"
    entry = repository_root / ".codex" / "skills" / "upload-search-materials" / "SKILL.md"
    metadata = repository_root / "upload-search-materials" / "agents" / "openai.yaml"
    entry_metadata = entry.parent / "agents" / "openai.yaml"
    for path in (canonical, entry, metadata, entry_metadata):
        if not path.is_file():
            errors.append(f"missing artifact: {path}")
    if errors:
        return errors

    canonical_text = canonical.read_text(encoding="utf-8")
    entry_text = entry.read_text(encoding="utf-8")
    canonical_meta = _frontmatter(canonical_text, canonical)
    entry_meta = _frontmatter(entry_text, entry)
    expected_sha = hashlib.sha256(canonical.read_bytes()).hexdigest()
    if canonical_meta.get("name") != entry_meta.get("name"):
        errors.append("stale artifact: discovery entry name differs from canonical Skill")
    if expected_sha not in entry_text:
        errors.append(
            "stale artifact: discovery entry source SHA differs; "
            "run scripts/update-skill-entry.cmd"
        )

    description = str(entry_meta.get("description", "")).lower()
    for term in TRIGGER_TERMS:
        if term not in description:
            errors.append(f"discovery trigger scope missing: {term}")
    for term in FRONTEND_CONTRACT_TERMS:
        if term.lower() not in description:
            errors.append(f"frontend-first contract missing from description: {term}")

    for path in (metadata, entry_metadata):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        interface = document.get("interface") if isinstance(document, dict) else None
        if not isinstance(interface, dict):
            errors.append(f"{path}: interface metadata missing")
            continue
        prompt = str(interface.get("default_prompt", "")).lower()
        if "ui" not in prompt or "chat" not in prompt:
            errors.append(f"{path}: default_prompt lacks frontend-first routing")
        for key in ("display_name", "short_description", "default_prompt"):
            if not str(interface.get(key, "")).strip():
                errors.append(f"{path}: interface.{key} is empty")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()
    errors = validate(args.repository_root.resolve())
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Skill discovery entry and metadata are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
