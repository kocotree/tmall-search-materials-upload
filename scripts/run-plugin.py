#!/usr/bin/env python3
"""Run the CLI from this Plugin checkout with a prepared dependency runtime."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def _prepare_plugin_source() -> Path:
    plugin_root = Path(__file__).resolve().parents[1]
    source_root = plugin_root / "src"
    package_root = source_root / "upload_search_materials"
    if not package_root.is_dir():
        raise SystemExit(
            "PLUGIN_SOURCE_MISSING: current Plugin does not contain "
            "src/upload_search_materials."
        )

    source_text = str(source_root)
    sys.path[:] = [
        source_text,
        *(entry for entry in sys.path if entry != source_text),
    ]
    inherited = [
        entry
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if entry and entry != source_text
    ]
    os.environ["PYTHONPATH"] = os.pathsep.join([source_text, *inherited])
    os.environ["TMALL_PLUGIN_ROOT"] = str(plugin_root)
    os.environ["TMALL_WORKSPACE_ROOT"] = str(plugin_root)
    return plugin_root


def main() -> int:
    plugin_root = _prepare_plugin_source()
    from upload_search_materials.cli import main as cli_main

    imported_root = Path(sys.modules["upload_search_materials"].__file__).resolve()
    if plugin_root not in imported_root.parents:
        raise SystemExit(
            "PLUGIN_SOURCE_MISMATCH: upload_search_materials was not loaded "
            "from the current Plugin."
        )
    return int(cli_main())


if __name__ == "__main__":
    raise SystemExit(main())
