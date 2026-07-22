"""Durable SQLite storage for the incremental asset index."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IndexIdentity:
    products_path: str
    products_sha256: str
    roots_json: str
    partition_depth: int
    checkpoint_size: int


@dataclass(frozen=True)
class IndexedFile:
    source_system: str
    relative_path: str
    absolute_path: str
    extension: str
    size_bytes: int
    mtime_ns: int
    candidate_directory: str


@dataclass(frozen=True)
class PathMatchRecord:
    product_id: str
    sku: str
    product_title: str
    match_type: str
    match_status: str
    reason_codes: tuple[str, ...]


class IndexIdentityError(ValueError):
    """Raised when a database belongs to a different index identity."""


_SCHEMA = """
CREATE TABLE scan_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE roots (
  root_id INTEGER PRIMARY KEY,
  source_system TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  completed_at TEXT
);
CREATE TABLE partitions (
  partition_id TEXT PRIMARY KEY,
  root_id INTEGER NOT NULL REFERENCES roots(root_id),
  relative_path TEXT NOT NULL,
  status TEXT NOT NULL,
  processed_count INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  checkpoint_at TEXT,
  current_scan_id TEXT,
  completed_scan_id TEXT,
  UNIQUE(root_id, relative_path)
);
CREATE TABLE files (
  file_id INTEGER PRIMARY KEY,
  source_system TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  absolute_path TEXT NOT NULL,
  extension TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  candidate_directory TEXT NOT NULL,
  active INTEGER NOT NULL,
  seen_scan_id TEXT NOT NULL,
  sha256 TEXT NOT NULL DEFAULT '',
  width INTEGER,
  height INTEGER,
  validation_status TEXT NOT NULL DEFAULT 'not_inspected',
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(source_system, relative_path)
);
CREATE TABLE matches (
  file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
  product_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  product_title TEXT NOT NULL,
  match_type TEXT NOT NULL,
  match_status TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  PRIMARY KEY(file_id, product_id, match_type)
);
"""


class AssetIndexStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def create(cls, path: Path, identity: IndexIdentity) -> "AssetIndexStore":
        connection = sqlite3.connect(path)
        store = cls(connection)
        try:
            with connection:
                connection.executescript(_SCHEMA)
                connection.execute("INSERT INTO scan_meta(key, value) VALUES (?, ?)", ("schema_version", str(SCHEMA_VERSION)))
                connection.execute("INSERT INTO scan_meta(key, value) VALUES (?, ?)", ("identity", _identity_json(identity)))
        except Exception:
            connection.close()
            raise
        return store

    @classmethod
    def open(cls, path: Path, identity: IndexIdentity) -> "AssetIndexStore":
        store = cls(sqlite3.connect(path))
        try:
            version = store._connection.execute("SELECT value FROM scan_meta WHERE key = 'schema_version'").fetchone()
            if version is None or int(version["value"]) != SCHEMA_VERSION:
                raise IndexIdentityError("schema version does not match this store")
            actual = store._connection.execute("SELECT value FROM scan_meta WHERE key = 'identity'").fetchone()
            if actual is None or actual["value"] != _identity_json(identity):
                raise IndexIdentityError("index identity does not match this database")
        except Exception:
            store.close()
            raise
        return store

    def __enter__(self) -> "AssetIndexStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def schema_version(self) -> int:
        row = self._connection.execute("SELECT value FROM scan_meta WHERE key = 'schema_version'").fetchone()
        return int(row["value"])

    def upsert_root(self, source_system: str, root_path: str) -> int:
        with self._connection:
            self._connection.execute(
                "INSERT INTO roots(source_system, root_path, status) VALUES (?, ?, 'pending') "
                "ON CONFLICT(source_system) DO UPDATE SET root_path = excluded.root_path",
                (source_system, root_path),
            )
            return int(self._connection.execute("SELECT root_id FROM roots WHERE source_system = ?", (source_system,)).fetchone()["root_id"])

    def upsert_partition(self, root_id: int, relative_path: str) -> str:
        partition_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"asset-index:{root_id}:{relative_path}"))
        with self._connection:
            self._connection.execute(
                "INSERT INTO partitions(partition_id, root_id, relative_path, status) VALUES (?, ?, ?, 'pending') "
                "ON CONFLICT(root_id, relative_path) DO NOTHING",
                (partition_id, root_id, relative_path),
            )
            return str(self._connection.execute("SELECT partition_id FROM partitions WHERE root_id = ? AND relative_path = ?", (root_id, relative_path)).fetchone()["partition_id"])

    def checkpoint_files(self, partition_id: str, scan_id: str, rows: Sequence[IndexedFile]) -> tuple[int, ...]:
        partition = self._partition(partition_id)
        result: list[int] = []
        with self._connection:
            for file in rows:
                if file.source_system != partition["source_system"] or file.candidate_directory != partition["relative_path"]:
                    raise ValueError("file does not belong to partition")
                existing = self._connection.execute(
                    "SELECT file_id, size_bytes, mtime_ns FROM files WHERE source_system = ? AND relative_path = ?",
                    (file.source_system, file.relative_path),
                ).fetchone()
                if existing is None:
                    cursor = self._connection.execute(
                        "INSERT INTO files(source_system, relative_path, absolute_path, extension, size_bytes, mtime_ns, candidate_directory, active, seen_scan_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                        (*file.__dict__.values(), scan_id),
                    )
                    result.append(int(cursor.lastrowid))
                    continue
                changed = existing["size_bytes"] != file.size_bytes or existing["mtime_ns"] != file.mtime_ns
                self._connection.execute(
                    "UPDATE files SET absolute_path=?, extension=?, size_bytes=?, mtime_ns=?, candidate_directory=?, active=1, seen_scan_id=?, "
                    "sha256=CASE WHEN ? THEN '' ELSE sha256 END, width=CASE WHEN ? THEN NULL ELSE width END, "
                    "height=CASE WHEN ? THEN NULL ELSE height END, validation_status=CASE WHEN ? THEN 'not_inspected' ELSE validation_status END, "
                    "reason_codes_json=CASE WHEN ? THEN '[]' ELSE reason_codes_json END WHERE file_id=?",
                    (file.absolute_path, file.extension, file.size_bytes, file.mtime_ns, file.candidate_directory, scan_id, changed, changed, changed, changed, changed, existing["file_id"]),
                )
                if changed:
                    self._connection.execute("DELETE FROM matches WHERE file_id = ?", (existing["file_id"],))
                result.append(int(existing["file_id"]))
            if partition["current_scan_id"] != scan_id:
                self._connection.execute(
                    "UPDATE partitions SET status='in_progress', processed_count=?, current_scan_id=?, completed_scan_id=NULL, checkpoint_at=? WHERE partition_id=?",
                    (len(rows), scan_id, _now(), partition_id),
                )
            else:
                self._connection.execute(
                    "UPDATE partitions SET status='in_progress', processed_count=processed_count + ?, checkpoint_at=? WHERE partition_id=?",
                    (len(rows), _now(), partition_id),
                )
        return tuple(result)

    def update_file_inspection(self, file_id: int, *, sha256: str, width: int | None, height: int | None, validation_status: str, reason_codes: Sequence[str]) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE files SET sha256=?, width=?, height=?, validation_status=?, reason_codes_json=? WHERE file_id=?",
                (sha256, width, height, validation_status, json.dumps(list(reason_codes)), file_id),
            )

    def replace_file_matches(self, file_id: int, matches: Sequence[PathMatchRecord]) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM matches WHERE file_id = ?", (file_id,))
            self._connection.executemany(
                "INSERT INTO matches(file_id, product_id, sku, product_title, match_type, match_status, reason_codes_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(file_id, item.product_id, item.sku, item.product_title, item.match_type, item.match_status, json.dumps(list(item.reason_codes))) for item in matches],
            )

    def complete_partition(self, partition_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE partitions SET status='completed', error='', completed_scan_id=current_scan_id, checkpoint_at=? WHERE partition_id=?",
                (_now(), partition_id),
            )

    def fail_partition(self, partition_id: str, error: str) -> None:
        with self._connection:
            self._connection.execute("UPDATE partitions SET status='failed', error=?, checkpoint_at=? WHERE partition_id=?", (error, _now(), partition_id))

    def mark_partition_missing_files_inactive(self, partition_id: str, scan_id: str) -> int:
        partition = self._partition(partition_id)
        if partition["status"] != "completed" or partition["completed_scan_id"] != scan_id:
            return 0
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE files SET active=0 WHERE source_system=? AND candidate_directory=? AND active=1 AND seen_scan_id<>?",
                (partition["source_system"], partition["relative_path"], scan_id),
            )
            return cursor.rowcount

    def file_count(self, *, active_only: bool = False) -> int:
        sql = "SELECT COUNT(*) AS count FROM files" + (" WHERE active=1" if active_only else "")
        return int(self._connection.execute(sql).fetchone()["count"])

    def file_record(self, file_id: int) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT sha256, width, height, validation_status, reason_codes_json FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        if row is None:
            raise KeyError(file_id)
        return {"sha256": row["sha256"], "width": row["width"], "height": row["height"], "validation_status": row["validation_status"], "reason_codes": tuple(json.loads(row["reason_codes_json"]))}

    def file_is_active(self, file_id: int) -> bool:
        row = self._connection.execute("SELECT active FROM files WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            raise KeyError(file_id)
        return bool(row["active"])

    def matches_for(self, file_id: int) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT product_id, sku, product_title, match_type, match_status, reason_codes_json FROM matches WHERE file_id=? ORDER BY product_id, match_type",
            (file_id,),
        ).fetchall()
        return tuple({"product_id": row["product_id"], "sku": row["sku"], "product_title": row["product_title"], "match_type": row["match_type"], "match_status": row["match_status"], "reason_codes": tuple(json.loads(row["reason_codes_json"]))} for row in rows)

    def partition_record(self, partition_id: str) -> dict[str, object]:
        row = self._partition(partition_id)
        return {
            "status": row["status"],
            "processed_count": row["processed_count"],
            "current_scan_id": row["current_scan_id"],
            "completed_scan_id": row["completed_scan_id"],
        }

    def _partition(self, partition_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT partitions.relative_path, partitions.status, partitions.processed_count, partitions.current_scan_id, partitions.completed_scan_id, roots.source_system FROM partitions JOIN roots USING(root_id) WHERE partition_id=?",
            (partition_id,),
        ).fetchone()
        if row is None:
            raise KeyError(partition_id)
        return row


def _identity_json(identity: IndexIdentity) -> str:
    return json.dumps(identity.__dict__, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
