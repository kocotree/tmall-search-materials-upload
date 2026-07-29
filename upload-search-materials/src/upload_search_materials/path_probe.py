"""Metadata-only child process used for bounded directory reachability checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
from typing import Any


WINDOWS_REASON_CODES = {
    2: "PATH_NOT_FOUND",
    3: "PATH_NOT_FOUND",
    5: "ACCESS_DENIED",
    15: "NETWORK_HOST_UNAVAILABLE",
    53: "NETWORK_HOST_UNAVAILABLE",
    64: "NETWORK_HOST_UNAVAILABLE",
    67: "NETWORK_SHARE_NOT_FOUND",
    86: "ACCESS_DENIED",
    121: "PATH_CHECK_TIMEOUT",
    123: "INVALID_PATH",
    124: "INVALID_PATH",
    1326: "ACCESS_DENIED",
    2250: "DRIVE_NOT_MAPPED",
}


def reason_from_os_error(error: OSError) -> str:
    """Map safe filesystem error information to a stable public reason."""

    winerror = getattr(error, "winerror", None)
    if isinstance(winerror, int) and winerror in WINDOWS_REASON_CODES:
        return WINDOWS_REASON_CODES[winerror]
    if isinstance(error, PermissionError):
        return "ACCESS_DENIED"
    if isinstance(error, (FileNotFoundError, NotADirectoryError)):
        return "PATH_NOT_FOUND"
    return "PATH_CHECK_FAILED"


def probe_directory_metadata(path: str) -> dict[str, Any]:
    """Read only directory metadata; never enumerate children."""

    try:
        metadata = os.stat(path)
    except OSError as error:
        return {
            "available": False,
            "reason_code": reason_from_os_error(error),
            "windows_error": getattr(error, "winerror", None),
        }
    if not stat.S_ISDIR(metadata.st_mode):
        return {
            "available": False,
            "reason_code": "PATH_NOT_FOUND",
            "windows_error": None,
        }
    return {
        "available": True,
        "reason_code": "PATH_AVAILABLE",
        "windows_error": None,
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args(argv)
    request_path = Path(args.request)
    response_path = Path(args.response)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        raw_path = request.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            response = {
                "available": False,
                "reason_code": "INVALID_PATH",
                "windows_error": None,
            }
        else:
            response = probe_directory_metadata(raw_path)
    except (OSError, ValueError, json.JSONDecodeError):
        response = {
            "available": False,
            "reason_code": "PATH_CHECK_FAILED",
            "windows_error": None,
        }
    _write_json(response_path, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
