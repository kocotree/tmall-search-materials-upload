"""Durable SQLite storage for the incremental asset index."""

from __future__ import annotations

import hashlib
import json
import sqlite3
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


@dataclass(frozen=True)
class MatchCandidateRecord:
    source_system: str
    candidate_directory: str
    relative_path: str
    sha256: str
    validation_status: str
    file_reason_codes: tuple[str, ...]
    product_id: str
    sku: str
    product_title: str
    match_type: str
    match_status: str
    match_reason_codes: tuple[str, ...]


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
  error_code TEXT NOT NULL DEFAULT '',
  current_scan_id TEXT,
  discovered_count INTEGER NOT NULL DEFAULT 0,
  indexed_count INTEGER NOT NULL DEFAULT 0,
  matched_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  checkpoint_at TEXT,
  completed_at TEXT
);
CREATE TABLE partitions (
  partition_id TEXT PRIMARY KEY,
  root_id INTEGER NOT NULL REFERENCES roots(root_id),
  relative_path TEXT NOT NULL,
  status TEXT NOT NULL,
  processed_count INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL DEFAULT '',
  discovered_count INTEGER NOT NULL DEFAULT 0,
  indexed_count INTEGER NOT NULL DEFAULT 0,
  matched_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  checkpoint_at TEXT,
  completed_at TEXT,
  current_scan_id TEXT,
  completed_scan_id TEXT,
  UNIQUE(root_id, relative_path)
);
CREATE TABLE files (
  file_id INTEGER PRIMARY KEY,
  partition_id TEXT NOT NULL REFERENCES partitions(partition_id),
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
CREATE TABLE file_errors (
  error_id INTEGER PRIMARY KEY,
  scan_id TEXT NOT NULL,
  partition_id TEXT NOT NULL REFERENCES partitions(partition_id),
  source_system TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  code TEXT NOT NULL,
  detail TEXT NOT NULL,
  UNIQUE(scan_id, source_system, relative_path, code)
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

    def scan_started(self) -> bool:
        row = self._connection.execute(
            "SELECT value FROM scan_meta WHERE key = 'scan_started'"
        ).fetchone()
        return row is not None and row["value"] == "1"

    def mark_scan_started(self) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO scan_meta(key, value) VALUES ('scan_started', '1')"
            )
            return cursor.rowcount == 1

    def active_scan_id(self) -> str | None:
        active = self.active_scan()
        return None if active is None else active[0]

    def active_scan(self) -> tuple[str, str] | None:
        rows = self._connection.execute(
            "SELECT key, value FROM scan_meta "
            "WHERE key IN ('active_scan_id', 'active_scan_mode')"
        ).fetchall()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        if not values:
            return None
        if set(values) != {"active_scan_id", "active_scan_mode"}:
            raise IndexIdentityError("active scan state is incomplete")
        mode = values["active_scan_mode"]
        if mode not in {"new", "refresh"}:
            raise IndexIdentityError("active scan mode is invalid")
        return values["active_scan_id"], mode

    def begin_scan(self, mode: str, scan_id: str) -> bool:
        if mode not in {"new", "refresh"}:
            raise ValueError("scan mode must be new or refresh")
        if not scan_id:
            raise ValueError("active scan identity must not be empty")
        with self._connection:
            active_keys = self._connection.execute(
                "SELECT COUNT(*) AS count FROM scan_meta "
                "WHERE key IN ('active_scan_id', 'active_scan_mode')"
            ).fetchone()
            if int(active_keys["count"]) != 0:
                return False
            if mode == "new":
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO scan_meta(key, value) "
                    "VALUES ('scan_started', '1')"
                )
                if cursor.rowcount != 1:
                    return False
            else:
                self._connection.execute(
                    "INSERT OR IGNORE INTO scan_meta(key, value) "
                    "VALUES ('scan_started', '1')"
                )
            self._connection.execute(
                "INSERT INTO scan_meta(key, value) VALUES ('active_scan_id', ?)",
                (scan_id,),
            )
            self._connection.execute(
                "INSERT INTO scan_meta(key, value) VALUES ('active_scan_mode', ?)",
                (mode,),
            )
        return True

    def clear_active_scan(self, scan_id: str) -> bool:
        with self._connection:
            rows = self._connection.execute(
                "SELECT key, value FROM scan_meta "
                "WHERE key IN ('active_scan_id', 'active_scan_mode')"
            ).fetchall()
            active = {str(row["key"]): str(row["value"]) for row in rows}
            if active.get("active_scan_id") != scan_id:
                return False
            self._connection.execute(
                "INSERT INTO scan_meta(key, value) VALUES ('last_scan_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (scan_id,),
            )
            self._connection.execute(
                "INSERT INTO scan_meta(key, value) VALUES ('last_scan_mode', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (active["active_scan_mode"],),
            )
            self._connection.execute(
                "DELETE FROM scan_meta "
                "WHERE key IN ('active_scan_id', 'active_scan_mode')"
            )
        return True

    def clear_active_scan_id(self, scan_id: str) -> bool:
        return self.clear_active_scan(scan_id)

    def latest_scan_id(self) -> str | None:
        active = self.active_scan_id()
        if active is not None:
            return active
        row = self._connection.execute(
            "SELECT value FROM scan_meta WHERE key='last_scan_id'"
        ).fetchone()
        if row is not None:
            return str(row["value"])
        row = self._connection.execute(
            "SELECT current_scan_id FROM partitions "
            "WHERE current_scan_id IS NOT NULL "
            "ORDER BY checkpoint_at DESC LIMIT 1"
        ).fetchone()
        return None if row is None else str(row["current_scan_id"])

    def index_identity(self) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT value FROM scan_meta WHERE key='identity'"
        ).fetchone()
        if row is None:
            raise IndexIdentityError("index identity is missing")
        return dict(json.loads(str(row["value"])))

    def upsert_root(self, source_system: str, root_path: str) -> int:
        with self._connection:
            self._connection.execute(
                "INSERT INTO roots(source_system, root_path, status) VALUES (?, ?, 'pending') "
                "ON CONFLICT(source_system) DO UPDATE SET root_path = excluded.root_path",
                (source_system, root_path),
            )
            return int(self._connection.execute("SELECT root_id FROM roots WHERE source_system = ?", (source_system,)).fetchone()["root_id"])

    def prepare_root(self, root_id: int, scan_id: str) -> None:
        """Bind an untouched configured root to this scan as pending."""

        if not scan_id:
            raise ValueError("scan identity must not be empty")
        row = self._connection.execute(
            "SELECT current_scan_id FROM roots WHERE root_id=?", (root_id,)
        ).fetchone()
        if row is None:
            raise KeyError(root_id)
        if row["current_scan_id"] == scan_id:
            return
        with self._connection:
            self._connection.execute(
                "UPDATE roots SET current_scan_id=?, status='pending', "
                "error='', error_code='', discovered_count=0, indexed_count=0, "
                "matched_count=0, failed_count=0, checkpoint_at=?, completed_at=NULL "
                "WHERE root_id=?",
                (scan_id, _now(), root_id),
            )

    def start_root(self, root_id: int, scan_id: str) -> None:
        row = self._connection.execute(
            "SELECT current_scan_id FROM roots WHERE root_id=?", (root_id,)
        ).fetchone()
        if row is None:
            raise KeyError(root_id)
        with self._connection:
            if row["current_scan_id"] == scan_id:
                self._connection.execute(
                    "UPDATE roots SET status='in_progress', error='', error_code='', "
                    "checkpoint_at=? WHERE root_id=?",
                    (_now(), root_id),
                )
            else:
                self._connection.execute(
                    "UPDATE roots SET current_scan_id=?, status='in_progress', "
                    "error='', error_code='', discovered_count=0, indexed_count=0, "
                    "matched_count=0, failed_count=0, checkpoint_at=?, completed_at=NULL "
                    "WHERE root_id=?",
                    (scan_id, _now(), root_id),
                )

    def sync_root_statistics(self, root_id: int, scan_id: str) -> None:
        totals = self._connection.execute(
            "SELECT COALESCE(SUM(discovered_count), 0) AS discovered, "
            "COALESCE(SUM(indexed_count), 0) AS indexed, "
            "COALESCE(SUM(matched_count), 0) AS matched, "
            "COALESCE(SUM(failed_count), 0) AS failed "
            "FROM partitions WHERE root_id=? AND current_scan_id=?",
            (root_id, scan_id),
        ).fetchone()
        with self._connection:
            self._connection.execute(
                "UPDATE roots SET discovered_count=?, indexed_count=?, "
                "matched_count=?, failed_count=?, checkpoint_at=? "
                "WHERE root_id=? AND current_scan_id=?",
                (
                    totals["discovered"],
                    totals["indexed"],
                    totals["matched"],
                    totals["failed"],
                    _now(),
                    root_id,
                    scan_id,
                ),
            )

    def complete_root(self, root_id: int, scan_id: str) -> None:
        self.sync_root_statistics(root_id, scan_id)
        failed = self._connection.execute(
            "SELECT COUNT(*) AS count FROM partitions "
            "WHERE root_id=? AND current_scan_id=? AND status='failed'",
            (root_id, scan_id),
        ).fetchone()
        status = "partial_failure" if int(failed["count"]) else "completed"
        with self._connection:
            self._connection.execute(
                "UPDATE roots SET status=?, error='', error_code='', "
                "checkpoint_at=?, completed_at=? "
                "WHERE root_id=? AND current_scan_id=?",
                (status, _now(), _now(), root_id, scan_id),
            )

    def fail_root(
        self, root_id: int, scan_id: str, error_code: str, detail: str
    ) -> None:
        self.sync_root_statistics(root_id, scan_id)
        with self._connection:
            self._connection.execute(
                "UPDATE roots SET status='failed', error_code=?, error=?, "
                "failed_count=CASE WHEN failed_count<1 THEN 1 ELSE failed_count END, "
                "checkpoint_at=?, completed_at=? "
                "WHERE root_id=? AND current_scan_id=?",
                (error_code, detail, _now(), _now(), root_id, scan_id),
            )

    def root_record(self, root_id: int) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT source_system, root_path, current_scan_id, status, "
            "error_code, error, discovered_count, indexed_count, matched_count, "
            "failed_count, checkpoint_at, completed_at "
            "FROM roots WHERE root_id=?",
            (root_id,),
        ).fetchone()
        if row is None:
            raise KeyError(root_id)
        return {
            "source_system": str(row["source_system"]),
            "root_path": str(row["root_path"]),
            "current_scan_id": row["current_scan_id"],
            "status": str(row["status"]),
            "error_code": str(row["error_code"]),
            "error_detail": str(row["error"]),
            "discovered": int(row["discovered_count"]),
            "indexed": int(row["indexed_count"]),
            "matched": int(row["matched_count"]),
            "failed": int(row["failed_count"]),
            "checkpoint_at": row["checkpoint_at"],
            "completed_at": row["completed_at"],
        }

    def upsert_partition(self, root_id: int, relative_path: str) -> str:
        root = self._connection.execute(
            "SELECT source_system FROM roots WHERE root_id = ?", (root_id,)
        ).fetchone()
        if root is None:
            raise KeyError(root_id)
        relative_path = Path(relative_path).as_posix()
        partition_id = hashlib.sha256(
            f"{root['source_system']}\0{relative_path}".encode("utf-8")
        ).hexdigest()[:24]
        with self._connection:
            self._connection.execute(
                "INSERT INTO partitions(partition_id, root_id, relative_path, status) VALUES (?, ?, ?, 'pending') "
                "ON CONFLICT(root_id, relative_path) DO NOTHING",
                (partition_id, root_id, relative_path),
            )
            return str(self._connection.execute("SELECT partition_id FROM partitions WHERE root_id = ? AND relative_path = ?", (root_id, relative_path)).fetchone()["partition_id"])

    def checkpoint_files(
        self,
        partition_id: str,
        scan_id: str,
        rows: Sequence[IndexedFile],
        *,
        count_progress: bool = True,
    ) -> tuple[int, ...]:
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
                        "INSERT INTO files(partition_id, source_system, relative_path, absolute_path, extension, size_bytes, mtime_ns, candidate_directory, active, seen_scan_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                        (partition_id, *file.__dict__.values(), scan_id),
                    )
                    result.append(int(cursor.lastrowid))
                    continue
                changed = existing["size_bytes"] != file.size_bytes or existing["mtime_ns"] != file.mtime_ns
                self._connection.execute(
                    "UPDATE files SET partition_id=?, absolute_path=?, extension=?, size_bytes=?, mtime_ns=?, candidate_directory=?, active=1, seen_scan_id=?, "
                    "sha256=CASE WHEN ? THEN '' ELSE sha256 END, width=CASE WHEN ? THEN NULL ELSE width END, "
                    "height=CASE WHEN ? THEN NULL ELSE height END, validation_status=CASE WHEN ? THEN 'not_inspected' ELSE validation_status END, "
                    "reason_codes_json=CASE WHEN ? THEN '[]' ELSE reason_codes_json END WHERE file_id=?",
                    (partition_id, file.absolute_path, file.extension, file.size_bytes, file.mtime_ns, file.candidate_directory, scan_id, changed, changed, changed, changed, changed, existing["file_id"]),
                )
                if changed:
                    self._connection.execute("DELETE FROM matches WHERE file_id = ?", (existing["file_id"],))
                result.append(int(existing["file_id"]))
            if partition["current_scan_id"] != scan_id:
                self._connection.execute(
                    "UPDATE partitions SET status='in_progress', processed_count=?, "
                    "current_scan_id=?, completed_scan_id=NULL, error='', error_code='', "
                    "discovered_count=?, indexed_count=?, matched_count=0, failed_count=0, "
                    "checkpoint_at=?, completed_at=NULL WHERE partition_id=?",
                    (
                        len(rows) if count_progress else 0,
                        scan_id,
                        len(rows) if count_progress else 0,
                        len(rows) if count_progress else 0,
                        _now(),
                        partition_id,
                    ),
                )
            elif count_progress:
                self._connection.execute(
                    "UPDATE partitions SET status='in_progress', "
                    "processed_count=processed_count + ?, "
                    "discovered_count=discovered_count + ?, "
                    "indexed_count=indexed_count + ?, checkpoint_at=? "
                    "WHERE partition_id=?",
                    (len(rows), len(rows), len(rows), _now(), partition_id),
                )
        return tuple(result)

    def restart_partition(self, partition_id: str, scan_id: str) -> None:
        """Restart one unfinished partition from its head without hiding old files."""

        if not scan_id:
            raise ValueError("scan identity must not be empty")
        self._partition(partition_id)
        with self._connection:
            self._connection.execute(
                "DELETE FROM file_errors WHERE partition_id=? AND scan_id=?",
                (partition_id, scan_id),
            )
            self._connection.execute(
                "UPDATE files SET seen_scan_id='' "
                "WHERE partition_id=? AND seen_scan_id=?",
                (partition_id, scan_id),
            )
            self._connection.execute(
                "UPDATE partitions SET status='in_progress', processed_count=0, "
                "error='', error_code='', current_scan_id=?, completed_scan_id=NULL, "
                "discovered_count=0, indexed_count=0, matched_count=0, failed_count=0, "
                "checkpoint_at=?, completed_at=NULL "
                "WHERE partition_id=?",
                (scan_id, _now(), partition_id),
            )

    def advance_partition_progress(
        self,
        partition_id: str,
        scan_id: str,
        processed_count: int,
        *,
        discovered: int | None = None,
        indexed: int | None = None,
        matched: int = 0,
        failed: int = 0,
    ) -> None:
        if processed_count < 0:
            raise ValueError("processed_count must not be negative")
        for value in (discovered, indexed, matched, failed):
            if value is not None and value < 0:
                raise ValueError("partition statistics must not be negative")
        partition = self._partition(partition_id)
        if partition["current_scan_id"] != scan_id:
            raise ValueError("partition is not bound to this scan")
        discovered = processed_count if discovered is None else discovered
        indexed = processed_count if indexed is None else indexed
        with self._connection:
            self._connection.execute(
                "UPDATE partitions SET status='in_progress', "
                "processed_count=processed_count + ?, "
                "discovered_count=discovered_count + ?, "
                "indexed_count=indexed_count + ?, matched_count=matched_count + ?, "
                "failed_count=failed_count + ?, checkpoint_at=? WHERE partition_id=?",
                (
                    processed_count,
                    discovered,
                    indexed,
                    matched,
                    failed,
                    _now(),
                    partition_id,
                ),
            )

    def record_file_error(
        self,
        partition_id: str,
        scan_id: str,
        *,
        source_system: str,
        relative_path: str,
        code: str,
        detail: str,
    ) -> None:
        partition = self._partition(partition_id)
        if partition["current_scan_id"] != scan_id:
            raise ValueError("partition is not bound to this scan")
        if partition["source_system"] != source_system:
            raise ValueError("file error source does not match partition")
        with self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO file_errors("
                "scan_id, partition_id, source_system, relative_path, code, detail"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    partition_id,
                    source_system,
                    Path(relative_path).as_posix(),
                    code,
                    detail,
                ),
            )
            self._connection.execute(
                "UPDATE file_errors SET detail=? "
                "WHERE scan_id=? AND source_system=? AND relative_path=? AND code=?",
                (
                    detail,
                    scan_id,
                    source_system,
                    Path(relative_path).as_posix(),
                    code,
                ),
            )
            if cursor.rowcount == 1:
                self._connection.execute(
                    "UPDATE partitions SET failed_count=failed_count + 1, "
                    "checkpoint_at=? WHERE partition_id=?",
                    (_now(), partition_id),
                )

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
                "UPDATE partitions SET status='completed', error='', error_code='', "
                "completed_scan_id=current_scan_id, checkpoint_at=?, completed_at=? "
                "WHERE partition_id=?",
                (_now(), _now(), partition_id),
            )

    def finalize_partition(self, partition_id: str, scan_id: str) -> int:
        """Atomically complete a partition and retire files unseen by this scan."""

        partition = self._partition(partition_id)
        if partition["current_scan_id"] != scan_id:
            raise ValueError("partition is not bound to this scan")
        completed_at = _now()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE files SET active=0 "
                "WHERE partition_id=? AND active=1 AND seen_scan_id<>?",
                (partition_id, scan_id),
            )
            self._connection.execute(
                "UPDATE partitions SET status='completed', error='', error_code='', "
                "completed_scan_id=?, checkpoint_at=?, completed_at=? "
                "WHERE partition_id=? AND current_scan_id=?",
                (
                    scan_id,
                    completed_at,
                    completed_at,
                    partition_id,
                    scan_id,
                ),
            )
            return cursor.rowcount

    def fail_partition(
        self,
        partition_id: str,
        error: str,
        error_code: str = "PARTITION_ENUMERATION_ERROR",
    ) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE partitions SET status='failed', error=?, error_code=?, "
                "failed_count=failed_count + 1, checkpoint_at=?, completed_at=? "
                "WHERE partition_id=?",
                (error, error_code, _now(), _now(), partition_id),
            )

    def mark_partition_missing_files_inactive(self, partition_id: str, scan_id: str) -> int:
        partition = self._partition(partition_id)
        if partition["status"] != "completed" or partition["completed_scan_id"] != scan_id:
            return 0
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE files SET active=0 WHERE partition_id=? AND active=1 AND seen_scan_id<>?",
                (partition_id, scan_id),
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
        row = self._connection.execute(
            "SELECT relative_path, status, processed_count, current_scan_id, "
            "completed_scan_id, error_code, error, discovered_count, indexed_count, "
            "matched_count, failed_count, checkpoint_at, completed_at "
            "FROM partitions WHERE partition_id=?",
            (partition_id,),
        ).fetchone()
        if row is None:
            raise KeyError(partition_id)
        return {
            "relative_path": str(row["relative_path"]),
            "status": row["status"],
            "processed_count": row["processed_count"],
            "current_scan_id": row["current_scan_id"],
            "completed_scan_id": row["completed_scan_id"],
            "error_code": str(row["error_code"]),
            "error_detail": str(row["error"]),
            "discovered": int(row["discovered_count"]),
            "indexed": int(row["indexed_count"]),
            "matched": int(row["matched_count"]),
            "failed": int(row["failed_count"]),
            "checkpoint_at": row["checkpoint_at"],
            "completed_at": row["completed_at"],
        }

    def partitions_for_root(self, root_id: int) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT partition_id, relative_path, status, processed_count, current_scan_id, completed_scan_id "
            "FROM partitions WHERE root_id=? ORDER BY relative_path, partition_id",
            (root_id,),
        ).fetchall()
        return tuple(
            {
                "partition_id": row["partition_id"],
                "relative_path": row["relative_path"],
                "status": row["status"],
                "processed_count": row["processed_count"],
                "current_scan_id": row["current_scan_id"],
                "completed_scan_id": row["completed_scan_id"],
            }
            for row in rows
        )

    def match_candidate_records(self) -> tuple[MatchCandidateRecord, ...]:
        rows = self._connection.execute(
            "SELECT files.source_system, files.candidate_directory, "
            "files.relative_path, files.sha256, files.validation_status, "
            "files.reason_codes_json AS file_reasons, matches.product_id, "
            "matches.sku, matches.product_title, matches.match_type, "
            "matches.match_status, matches.reason_codes_json AS match_reasons "
            "FROM files JOIN matches USING(file_id) WHERE files.active=1 "
            "ORDER BY files.source_system, files.candidate_directory, "
            "matches.product_id, matches.match_type, files.relative_path"
        ).fetchall()
        return tuple(
            MatchCandidateRecord(
                source_system=str(row["source_system"]),
                candidate_directory=str(row["candidate_directory"]),
                relative_path=str(row["relative_path"]),
                sha256=str(row["sha256"]),
                validation_status=str(row["validation_status"]),
                file_reason_codes=tuple(json.loads(row["file_reasons"])),
                product_id=str(row["product_id"]),
                sku=str(row["sku"]),
                product_title=str(row["product_title"]),
                match_type=str(row["match_type"]),
                match_status=str(row["match_status"]),
                match_reason_codes=tuple(json.loads(row["match_reasons"])),
            )
            for row in rows
        )

    def database_statistics(self) -> dict[str, int | dict[str, int]]:
        partition_counts = {
            str(row["status"]): int(row["count"])
            for row in self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM partitions GROUP BY status"
            ).fetchall()
        }
        total_files = int(
            self._connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        )
        active_files = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM files WHERE active=1"
            ).fetchone()[0]
        )
        return {
            "roots": int(
                self._connection.execute("SELECT COUNT(*) FROM roots").fetchone()[0]
            ),
            "partitions": sum(partition_counts.values()),
            "partition_status": dict(sorted(partition_counts.items())),
            "total_files": total_files,
            "active_files": active_files,
            "inactive_files": total_files - active_files,
            "matched_files": int(
                self._connection.execute(
                    "SELECT COUNT(DISTINCT files.file_id) "
                    "FROM files JOIN matches USING(file_id) WHERE files.active=1"
                ).fetchone()[0]
            ),
            "active_matches": int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM files JOIN matches USING(file_id) "
                    "WHERE files.active=1"
                ).fetchone()[0]
            ),
            "match_candidates": int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM files "
                    "JOIN matches USING(file_id) WHERE files.active=1 "
                    "GROUP BY files.source_system, files.candidate_directory, "
                    "matches.product_id, matches.match_type)"
                ).fetchone()[0]
            ),
        }

    def scan_statistics(self, scan_id: str) -> dict[str, object]:
        roots = []
        errors = []
        root_rows = self._connection.execute(
            "SELECT DISTINCT roots.root_id, roots.source_system FROM roots "
            "LEFT JOIN partitions ON partitions.root_id=roots.root_id "
            "WHERE roots.current_scan_id=? OR partitions.current_scan_id=? "
            "ORDER BY roots.source_system",
            (scan_id, scan_id),
        ).fetchall()
        for root_row in root_rows:
            root_id = int(root_row["root_id"])
            root = self.root_record(root_id)
            partition_rows = self._connection.execute(
                "SELECT partition_id FROM partitions "
                "WHERE root_id=? AND current_scan_id=? "
                "ORDER BY relative_path, partition_id",
                (root_id, scan_id),
            ).fetchall()
            partitions = [
                {
                    "partition_id": str(row["partition_id"]),
                    **self.partition_record(str(row["partition_id"])),
                }
                for row in partition_rows
            ]
            if partitions:
                for key in ("discovered", "indexed", "matched"):
                    root[key] = sum(int(partition[key]) for partition in partitions)
                partition_failed = sum(
                    int(partition["failed"]) for partition in partitions
                )
                root["failed"] = max(
                    partition_failed,
                    1
                    if root["current_scan_id"] == scan_id
                    and root["error_code"]
                    else 0,
                )
            if root["current_scan_id"] != scan_id:
                root["current_scan_id"] = scan_id
                root["status"] = (
                    "partial_failure"
                    if any(partition["status"] == "failed" for partition in partitions)
                    else "completed"
                )
            root["partitions"] = partitions
            roots.append(root)
            if root["error_code"]:
                errors.append(
                    {
                        "source_system": root["source_system"],
                        "relative_path": ".",
                        "code": root["error_code"],
                        "detail": root["error_detail"],
                    }
                )
            for partition in partitions:
                if partition["error_code"]:
                    errors.append(
                        {
                            "source_system": root["source_system"],
                            "relative_path": partition["relative_path"],
                            "code": partition["error_code"],
                            "detail": partition["error_detail"],
                        }
                    )
                file_error_rows = self._connection.execute(
                    "SELECT relative_path, code, detail FROM file_errors "
                    "WHERE scan_id=? AND partition_id=? "
                    "ORDER BY relative_path, code",
                    (scan_id, partition["partition_id"]),
                ).fetchall()
                for file_error in file_error_rows:
                    errors.append(
                        {
                            "source_system": root["source_system"],
                            "relative_path": str(file_error["relative_path"]),
                            "partition": partition["relative_path"],
                            "code": str(file_error["code"]),
                            "detail": str(file_error["detail"]),
                        }
                    )
        totals = {
            key: sum(int(root[key]) for root in roots)
            for key in ("discovered", "indexed", "matched", "failed")
        }
        return {
            "scan_id": scan_id,
            "totals": totals,
            "roots": roots,
            "errors": errors,
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
