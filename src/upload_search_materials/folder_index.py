"""Lightweight, read-only folder index for large NAS media roots."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Callable, Sequence
import uuid

from .asset_index import NamedRoot
from .asset_matching import normalize_match_text
from .assets import IMAGE_EXTENSIONS


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE folders (
  folder_id TEXT PRIMARY KEY,
  source_system TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  absolute_path TEXT NOT NULL,
  folder_name TEXT NOT NULL,
  parent_relative_path TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  last_seen_scan_id TEXT NOT NULL,
  UNIQUE(source_system, relative_path)
);
CREATE TABLE matches (
  folder_id TEXT NOT NULL REFERENCES folders(folder_id) ON DELETE CASCADE,
  product_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  product_title TEXT NOT NULL,
  match_type TEXT NOT NULL,
  match_status TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  PRIMARY KEY(folder_id, product_id, match_type)
);
"""


def _folder_id(source_system: str, relative_path: str) -> str:
    return hashlib.sha256(
        f"{source_system}\0{relative_path}".encode("utf-8")
    ).hexdigest()[:24]


def _identity(products_sha256: str, roots: Sequence[NamedRoot]) -> str:
    value = {
        "products_sha256": products_sha256,
        "roots": [
            {"source_system": root.source_system, "path": str(root.path)}
            for root in roots
        ],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def count_candidate_folder_images(
    folder_path: Path,
    *,
    deadline_seconds: float | None = 10.0,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Count supported images without opening or hashing their contents."""

    folder = Path(folder_path)
    started_at = time.monotonic()
    try:
        if not folder.is_dir():
            return {
                "image_count_status": "unknown",
                "raw_recursive_image_count": None,
                "image_count_reason_code": "FOLDER_COUNT_PATH_UNAVAILABLE",
            }
        count = 0
        for path in folder.rglob("*"):
            if cancelled is not None and cancelled():
                return {
                    "image_count_status": "unknown",
                    "raw_recursive_image_count": None,
                    "image_count_reason_code": "FOLDER_COUNT_CANCELLED",
                }
            if (
                deadline_seconds is not None
                and time.monotonic() - started_at > deadline_seconds
            ):
                return {
                    "image_count_status": "unknown",
                    "raw_recursive_image_count": None,
                    "image_count_reason_code": "FOLDER_COUNT_TIMEOUT",
                }
            if (
                path.is_file()
                and path.suffix.casefold() in IMAGE_EXTENSIONS
            ):
                count += 1
    except PermissionError:
        return {
            "image_count_status": "unknown",
            "raw_recursive_image_count": None,
            "image_count_reason_code": "FOLDER_COUNT_ACCESS_DENIED",
        }
    except OSError:
        return {
            "image_count_status": "unknown",
            "raw_recursive_image_count": None,
            "image_count_reason_code": "FOLDER_COUNT_FAILED",
        }
    return {
        "image_count_status": "ready",
        "raw_recursive_image_count": count,
        "image_count_reason_code": "",
    }


def absolute_path_without_io(path: Path) -> Path:
    """Normalize an absolute root path without touching an unavailable NAS."""

    path_text = os.fspath(path)
    if not os.path.isabs(path_text):
        path_text = os.path.join(os.getcwd(), path_text)
    return Path(os.path.normpath(path_text))


def _open_database(
    path: Path,
    *,
    identity: str,
    refresh: bool,
) -> sqlite3.Connection:
    if path.exists():
        if not refresh:
            raise ValueError("new 文件夹索引要求空输出目录")
        connection = sqlite3.connect(path)
        stored = connection.execute(
            "SELECT value FROM metadata WHERE key='identity'"
        ).fetchone()
        if stored is None or stored[0] != identity:
            connection.close()
            raise ValueError("文件夹索引身份与当前商品表或 roots 不一致")
        return connection
    if refresh:
        raise ValueError("--refresh 需要已有 folder-index.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('schema_version', '1')"
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('identity', ?)", (identity,)
    )
    connection.commit()
    return connection


def _walk_directories(root: Path):
    pending = [(root, Path("."))]
    while pending:
        directory, relative = pending.pop()
        try:
            with os.scandir(directory) as scanner:
                children = []
                for entry in scanner:
                    try:
                        if entry.is_symlink() or not entry.is_dir(
                            follow_symlinks=False
                        ):
                            continue
                        metadata = entry.stat(follow_symlinks=False)
                        if (
                            getattr(metadata, "st_file_attributes", 0)
                            & _REPARSE_POINT
                        ):
                            continue
                    except OSError as error:
                        yield None, relative / entry.name, str(error)
                        continue
                    child_relative = relative / entry.name
                    child = Path(entry.path)
                    yield child, child_relative, ""
                    children.append((child, child_relative))
                pending.extend(reversed(children))
        except OSError as error:
            yield None, relative, str(error)


def build_folder_index(
    *,
    database_path: Path,
    products_sha256: str,
    roots: Sequence[NamedRoot],
    matcher,
    refresh: bool = False,
    checkpoint_size: int = 1000,
) -> dict[str, object]:
    """Index directory names only; never open or hash image files."""

    if checkpoint_size < 1:
        raise ValueError("checkpoint_size must be positive")
    normalized_roots = tuple(
        sorted(
            (
                NamedRoot(
                    root.source_system,
                    root.path.resolve(),
                )
                for root in roots
            ),
            key=lambda item: item.source_system,
        )
    )
    identity = _identity(products_sha256, normalized_roots)
    connection = _open_database(
        Path(database_path), identity=identity, refresh=refresh
    )
    connection.execute("PRAGMA foreign_keys=ON")
    scan_id = uuid.uuid4().hex
    discovered = 0
    matched = 0
    errors = []
    pending = 0
    completed_sources = []
    try:
        for named_root in normalized_roots:
            root = named_root.path
            source_completed = True
            for absolute_path, relative_path, error in _walk_directories(root):
                relative_key = Path(
                    *(part for part in relative_path.parts if part not in {"", "."})
                ).as_posix()
                if error:
                    source_completed = False
                    errors.append(
                        {
                            "source_system": named_root.source_system,
                            "relative_path": relative_key or ".",
                            "reason_code": "FOLDER_ENUMERATION_ERROR",
                            "detail": error,
                        }
                    )
                    continue
                assert absolute_path is not None
                discovered += 1
                folder_id = _folder_id(named_root.source_system, relative_key)
                parent = Path(relative_key).parent.as_posix()
                connection.execute(
                    "INSERT INTO folders(folder_id, source_system, relative_path, absolute_path, folder_name, parent_relative_path, active, last_seen_scan_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?) "
                    "ON CONFLICT(source_system, relative_path) DO UPDATE SET "
                    "absolute_path=excluded.absolute_path, folder_name=excluded.folder_name, "
                    "parent_relative_path=excluded.parent_relative_path, active=1, "
                    "last_seen_scan_id=excluded.last_seen_scan_id",
                    (
                        folder_id,
                        named_root.source_system,
                        relative_key,
                        str(absolute_path),
                        absolute_path.name,
                        parent,
                        scan_id,
                    ),
                )
                # Match the folder's own name only. Ancestor matches remain
                # represented by their own folder record and must not make
                # generic descendants such as "KV" or "1" duplicate candidates.
                folder_matcher = getattr(matcher, "match_folder_name", None)
                matches = (
                    folder_matcher(absolute_path.name)
                    if callable(folder_matcher)
                    else matcher.match(Path(absolute_path.name) / "__folder__.jpg")
                )
                connection.execute(
                    "DELETE FROM matches WHERE folder_id=?", (folder_id,)
                )
                for item in matches:
                    connection.execute(
                        "INSERT INTO matches(folder_id, product_id, sku, product_title, match_type, match_status, reason_codes_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            folder_id,
                            item.product_id,
                            item.sku,
                            item.product_title,
                            item.match_type,
                            item.match_status,
                            json.dumps(list(item.reason_codes)),
                        ),
                    )
                if matches:
                    matched += 1
                pending += 1
                if pending >= checkpoint_size:
                    connection.commit()
                    pending = 0
            if source_completed:
                completed_sources.append(named_root.source_system)
                connection.execute(
                    "UPDATE folders SET active=0 WHERE source_system=? AND last_seen_scan_id<>?",
                    (named_root.source_system, scan_id),
                )
            connection.commit()
        summary = {
            "schema_version": 1,
            "scan_id": scan_id,
            "mode": "refresh" if refresh else "new",
            "complete": not errors,
            "folders_discovered": discovered,
            "matched_folders": matched,
            "completed_sources": completed_sources,
            "errors": errors,
        }
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('last_summary', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(summary, ensure_ascii=False),),
        )
        connection.commit()
        return summary
    finally:
        connection.close()


def write_folder_candidates(database_path: Path, output_path: Path) -> int:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT folders.folder_id, folders.source_system, folders.absolute_path, "
        "folders.relative_path, folders.folder_name, matches.product_id, matches.sku, "
        "matches.product_title, matches.match_type, matches.match_status "
        "FROM folders JOIN matches USING(folder_id) WHERE folders.active=1 "
        "ORDER BY matches.product_id, folders.source_system, folders.relative_path"
    ).fetchall()
    connection.close()
    fields = [
        "folder_id",
        "source_system",
        "absolute_path",
        "relative_path",
        "folder_name",
        "product_id",
        "sku",
        "product_title",
        "match_type",
        "match_status",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return len(rows)


def snapshot_folder_candidates(
    candidates_path: Path,
    output_path: Path,
    product_ids: Sequence[str],
) -> dict[str, int]:
    """Copy only the requested products from a shared candidate CSV."""

    requested = {str(product_id).strip() for product_id in product_ids if str(product_id).strip()}
    if not requested:
        raise ValueError("至少提供一个 --product-id")
    with Path(candidates_path).open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        required = {"product_id", "folder_id", "source_system", "absolute_path"}
        if not required.issubset(fields):
            raise ValueError("共享文件夹候选 CSV 缺少必需表头")
        rows = [dict(row) for row in reader if row.get("product_id", "").strip() in requested]
    rows.sort(
        key=lambda row: (
            row.get("product_id", ""),
            row.get("source_system", ""),
            row.get("relative_path", ""),
            row.get("folder_id", ""),
        )
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    matched_products = {row.get("product_id", "") for row in rows}
    return {
        "requested_products": len(requested),
        "matched_products": len(matched_products),
        "candidate_rows": len(rows),
    }


def rematch_folder_index(
    *,
    database_path: Path,
    products_sha256: str,
    roots: Sequence[NamedRoot],
    matcher,
) -> dict[str, object]:
    """Recompute product matches from stored folder names without touching NAS."""

    normalized_roots = tuple(
        sorted(
            (
                NamedRoot(
                    root.source_system,
                    absolute_path_without_io(root.path),
                )
                for root in roots
            ),
            key=lambda item: item.source_system,
        )
    )
    connection = sqlite3.connect(database_path)
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='identity'"
    ).fetchone()
    if stored is None or stored[0] != _identity(products_sha256, normalized_roots):
        connection.close()
        raise ValueError("文件夹索引身份与当前商品表或 roots 不一致")
    rows = connection.execute(
        "SELECT folder_id, folder_name FROM folders WHERE active=1 "
        "ORDER BY source_system, relative_path"
    ).fetchall()
    matched_folders = 0
    candidate_rows = 0
    try:
        for folder_id, folder_name in rows:
            folder_matcher = getattr(matcher, "match_folder_name", None)
            matches = (
                folder_matcher(folder_name)
                if callable(folder_matcher)
                else matcher.match(Path(folder_name) / "__folder__.jpg")
            )
            connection.execute("DELETE FROM matches WHERE folder_id=?", (folder_id,))
            for item in matches:
                connection.execute(
                    "INSERT INTO matches(folder_id, product_id, sku, product_title, match_type, match_status, reason_codes_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        folder_id,
                        item.product_id,
                        item.sku,
                        item.product_title,
                        item.match_type,
                        item.match_status,
                        json.dumps(list(item.reason_codes)),
                    ),
                )
            if matches:
                matched_folders += 1
                candidate_rows += len(matches)
        connection.commit()
        return {
            "schema_version": 1,
            "mode": "rematch_only",
            "complete": True,
            "folders_discovered": len(rows),
            "matched_folders": matched_folders,
            "candidate_rows": candidate_rows,
            "errors": [],
        }
    finally:
        connection.close()


def build_folder_review_data(
    candidates_path: Path,
    *,
    decisions: Sequence[dict[str, object]] = (),
    exact_folder_queries: Sequence[dict[str, object]] = (),
    include_image_counts: bool = False,
) -> dict[str, object]:
    """Build deterministic UI data from folder candidates and saved decisions."""

    with Path(candidates_path).open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        candidates = list(csv.DictReader(stream))
    decision_by_key = {
        (str(item.get("product_id", "")), str(item.get("folder_id", ""))): dict(
            item
        )
        for item in decisions
        if item.get("product_id") and item.get("folder_id")
    }
    exact_query_keys = {
        (
            str(item.get("product_id", "")).strip(),
            normalize_match_text(str(item.get("folder_name", ""))),
        )
        for item in exact_folder_queries
        if item.get("product_id") and item.get("folder_name")
    }
    rows = []
    for candidate in candidates:
        match_type = str(candidate.get("match_type", "")).strip()
        product_id = str(candidate.get("product_id", "")).strip()
        folder_name = str(candidate.get("folder_name", "")).strip()
        is_exact_query = (
            product_id,
            normalize_match_text(folder_name),
        ) in exact_query_keys
        if match_type not in {
            "exact_product_id",
            "exact_sku",
            "name_candidate",
            "fuzzy_name_candidate",
        } and not is_exact_query:
            # Ignore legacy alias rows from an older shared index snapshot.
            continue
        folder_id = str(candidate.get("folder_id", "")).strip()
        if not product_id or not folder_id:
            continue
        decision = decision_by_key.get((product_id, folder_id), {})
        saved_decision = str(decision.get("decision", "")).strip()
        default_decision = (
            "rejected"
            if match_type == "fuzzy_name_candidate" and not is_exact_query
            else "confirmed"
        )
        row: dict[str, object] = {
                "folder_id": folder_id,
                "product_id": product_id,
                "product_title": str(candidate.get("product_title", "")).strip(),
                "sku": str(candidate.get("sku", "")).strip(),
                "source_system": str(
                    candidate.get("source_system", "")
                ).strip(),
                "source_id": str(
                    candidate.get("source_id")
                    or candidate.get("source_system", "")
                ).strip(),
                "relative_path": str(
                    candidate.get("relative_path", "")
                ).strip(),
                "folder_name": folder_name,
                "folder_path": str(candidate.get("absolute_path", "")).strip(),
                "match_type": (
                    "exact_folder_query" if is_exact_query else match_type
                ),
                "match_status": (
                    "needs_manual_confirmation"
                    if is_exact_query
                    else str(candidate.get("match_status", "")).strip()
                ),
                "decision": (
                    saved_decision
                    if saved_decision in {"confirmed", "rejected"}
                    else default_decision
                ),
                "note": str(decision.get("note", "")),
                "image_count_status": "pending",
                "raw_recursive_image_count": None,
                "image_count_reason_code": "",
            }
        if include_image_counts:
            row.update(
                count_candidate_folder_images(
                    Path(str(row["folder_path"]))
                )
            )
        rows.append(row)
    rows.sort(
        key=lambda item: (
            item["product_id"],
            item["source_system"],
            item["folder_path"],
            item["folder_id"],
        )
    )
    product_groups = []
    for product_id in sorted({str(row["product_id"]) for row in rows}):
        group = [row for row in rows if row["product_id"] == product_id]
        product_groups.append(
            {
                "product_id": product_id,
                "product_title": str(group[0]["product_title"]),
                "sku": str(group[0]["sku"]),
                "candidate_count": len(group),
            }
        )
    return {
        "schema_version": 1,
        "review_type": "folder_ownership",
        "safety_status": "folders_only",
        "folder_candidates": rows,
        "folder_products": product_groups,
        "next_action": "确认或排除每个候选文件夹；确认前不读取图片。",
    }
