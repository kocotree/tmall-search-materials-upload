#!/usr/bin/env python3
"""Write the dependency-only runtime fingerprint used by bootstrap scripts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--uv-cache-dir", required=True)
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    runtime_root = Path(args.runtime_root).resolve()
    python = Path(args.python).resolve()
    lock_path = project_root / "uv.lock"
    payload = {
        "schema_version": 2,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "python": str(python),
        "dependency_mode": "no-install-project",
        "launch_mode": "current-plugin-source",
        "uv_cache_dir": str(Path(args.uv_cache_dir).resolve()),
        "prepared_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    target = runtime_root / "environment-fingerprint.json"
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
