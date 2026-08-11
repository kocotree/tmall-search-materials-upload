"""Lightweight, read-only folder index for large NAS media roots."""

from __future__ import annotations

import csv
import hashlib
from itertools import chain
import json
import os
from pathlib import Path
import queue
import sqlite3
import stat
import threading
import time
from typing import Callable, Sequence
import uuid

from .asset_index import NamedRoot
from .asset_matching import normalize_match_text
from .assets import IMAGE_EXTENSIONS
from .persistence import atomic_write_json, read_json


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
DEFAULT_DIRECTORY_INACTIVITY_TIMEOUT_SECONDS = 10.0
FOLDER_INDEX_SCHEMA_VERSION = 2
FOLDER_INDEX_PROGRESS_FILENAME = "folder-index-progress.json"
FOLDER_INDEX_LOCK_FILENAME = ".folder-index.lock"
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


def _filesystem_identity(roots: Sequence[NamedRoot]) -> str:
    value = {
        "roots": [
            {"source_system": root.source_system, "path": str(root.path)}
            for root in roots
        ],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _filesystem_identity_from_legacy(value: str) -> str | None:
    try:
        document = json.loads(value)
        roots = document["roots"]
        if not isinstance(roots, list):
            return None
        return json.dumps(
            {"roots": roots},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


class FolderIndexBusyError(ValueError):
    """Raised when another live process owns the machine-local index."""


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _FolderIndexLease:
    def __init__(self, path: Path, *, operation_id: str):
        self.path = Path(path)
        self.operation_id = operation_id
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": 1,
            "pid": os.getpid(),
            "operation_id": self.operation_id,
            "started_at_epoch_seconds": time.time(),
        }
        payload = (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
        for _attempt in range(3):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                try:
                    observed = self.path.stat()
                    existing = read_json(self.path)
                    owner_pid = int(existing.get("pid", 0))
                except (OSError, ValueError, TypeError, AttributeError):
                    observed = None
                    owner_pid = 0
                if _pid_is_alive(owner_pid):
                    raise FolderIndexBusyError(
                        f"FOLDER_INDEX_BUSY: 索引正由进程 {owner_pid} 更新"
                    )
                try:
                    current = self.path.stat()
                    if observed is not None and (
                        current.st_ino,
                        current.st_size,
                        current.st_mtime_ns,
                    ) != (
                        observed.st_ino,
                        observed.st_size,
                        observed.st_mtime_ns,
                    ):
                        continue
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self.acquired = True
            return self
        raise FolderIndexBusyError("FOLDER_INDEX_BUSY: 无法取得索引租约")

    def __exit__(self, exc_type, exc_value, traceback):
        if not self.acquired:
            return
        try:
            existing = read_json(self.path)
            if existing.get("operation_id") == self.operation_id:
                self.path.unlink()
        except (FileNotFoundError, OSError, ValueError, AttributeError):
            pass


class _ProgressReporter:
    def __init__(self, path: Path, *, operation_id: str, mode: str):
        self.path = Path(path)
        self.operation_id = operation_id
        self.mode = mode
        self.started_monotonic = time.monotonic()
        self.started_epoch = time.time()
        self.last_write_monotonic = 0.0
        self.last_values: dict[str, object] = {}

    def write(
        self,
        status: str,
        *,
        force: bool = True,
        **values: object,
    ) -> None:
        now = time.monotonic()
        self.last_values.update(values)
        if not force and now - self.last_write_monotonic < 1.0:
            return
        document = {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "mode": self.mode,
            "status": status,
            "pid": os.getpid(),
            "started_at_epoch_seconds": self.started_epoch,
            "heartbeat_epoch_seconds": time.time(),
            "elapsed_seconds": round(
                max(0.0, time.monotonic() - self.started_monotonic), 3
            ),
            **self.last_values,
        }
        atomic_write_json(self.path, document, sort_keys=True)
        self.last_write_monotonic = now


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


def normalize_folder_refresh_prefix(value: str) -> str:
    """Validate a machine-independent source-relative refresh prefix."""

    raw = str(value).strip().replace("\\", "/")
    if not raw or raw == ".":
        return "."
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError("定向刷新子路径必须是 source-relative 路径")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("定向刷新子路径不得包含空段、. 或 ..")
    return "/".join(parts)


def _open_database(
    path: Path,
    *,
    filesystem_identity: str,
    products_sha256: str,
    roots: Sequence[NamedRoot],
    refresh: bool,
) -> sqlite3.Connection:
    if path.exists():
        if not refresh:
            raise ValueError("new 文件夹索引要求空输出目录")
        connection = sqlite3.connect(path)
        stored_filesystem = connection.execute(
            "SELECT value FROM metadata WHERE key='filesystem_identity'"
        ).fetchone()
        if stored_filesystem is None:
            stored_legacy = connection.execute(
                "SELECT value FROM metadata WHERE key='identity'"
            ).fetchone()
            if (
                stored_legacy is None
                or _filesystem_identity_from_legacy(stored_legacy[0])
                != filesystem_identity
            ):
                connection.close()
                raise ValueError("文件夹索引 roots 身份与当前配置不一致")
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('filesystem_identity', ?)",
                (filesystem_identity,),
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('products_sha256', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (products_sha256,),
            )
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='schema_version'",
                (str(FOLDER_INDEX_SCHEMA_VERSION),),
            )
            connection.commit()
        elif stored_filesystem[0] != filesystem_identity:
            connection.close()
            raise ValueError("文件夹索引 roots 身份与当前配置不一致")
        return connection
    if refresh:
        raise ValueError("--refresh 需要已有 folder-index.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
        (str(FOLDER_INDEX_SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('filesystem_identity', ?)",
        (filesystem_identity,),
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('products_sha256', ?)",
        (products_sha256,),
    )
    connection.commit()
    return connection


def _enumeration_reason_code(error: OSError) -> str:
    if isinstance(error, PermissionError):
        return "FOLDER_ENUMERATION_ACCESS_DENIED"
    if isinstance(error, FileNotFoundError):
        return "FOLDER_ENUMERATION_PATH_UNAVAILABLE"
    return "FOLDER_ENUMERATION_ERROR"


def _directory_entries(directory: Path):
    """Yield progress and child-directory records without reading file data."""

    with os.scandir(directory) as scanner:
        for entry in scanner:
            # Emit progress before metadata calls so a slow stat/is_dir is also
            # covered by the inactivity deadline in the parent thread.
            yield "progress", entry.name, "", ""
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
                yield (
                    "error",
                    entry.name,
                    _enumeration_reason_code(error),
                    str(error),
                )
                continue
            yield "directory", entry.name, entry.path, ""


def _enumerate_directory_with_timeout(
    directory: Path,
    *,
    inactivity_timeout_seconds: float,
    on_progress: Callable[[], None] | None = None,
):
    """Enumerate one directory without letting blocked NAS I/O stall the scan."""

    results: queue.Queue[tuple[str, object]] = queue.Queue()

    def worker() -> None:
        try:
            for record in _directory_entries(directory):
                results.put(("record", record))
        except BaseException as error:
            results.put(("raised", error))
        else:
            results.put(("done", None))

    thread = threading.Thread(
        target=worker,
        name="folder-index-directory-enumerator",
        daemon=True,
    )
    thread.start()
    while True:
        try:
            kind, payload = results.get(
                timeout=inactivity_timeout_seconds
            )
        except queue.Empty:
            yield (
                "timeout",
                "",
                "FOLDER_ENUMERATION_TIMEOUT",
                (
                    "directory enumeration produced no progress for "
                    f"{inactivity_timeout_seconds:g} seconds"
                ),
            )
            return
        if kind == "done":
            return
        if kind == "raised":
            assert isinstance(payload, BaseException)
            if isinstance(payload, OSError):
                yield (
                    "error",
                    "",
                    _enumeration_reason_code(payload),
                    str(payload),
                )
                return
            raise payload
        assert kind == "record"
        assert isinstance(payload, tuple)
        if on_progress is not None:
            on_progress()
        yield payload


def _walk_directories(
    root: Path,
    *,
    initial_relative: Path = Path("."),
    inactivity_timeout_seconds: float = (
        DEFAULT_DIRECTORY_INACTIVITY_TIMEOUT_SECONDS
    ),
    on_progress: Callable[[Path, Path], None] | None = None,
    initial_pending: Sequence[tuple[Path, Path]] | None = None,
    on_directory_complete: Callable[
        [Sequence[tuple[Path, Path]]], None
    ]
    | None = None,
):
    if inactivity_timeout_seconds <= 0:
        raise ValueError("directory inactivity timeout must be positive")
    pending = list(initial_pending or ((root, initial_relative),))
    while pending:
        directory, relative = pending.pop()
        children = []
        for kind, name, value, detail in _enumerate_directory_with_timeout(
            directory,
            inactivity_timeout_seconds=inactivity_timeout_seconds,
            on_progress=(
                (lambda: on_progress(directory, relative))
                if on_progress is not None
                else None
            ),
        ):
            if kind == "progress":
                continue
            if kind == "directory":
                child_relative = relative / name
                child = Path(value)
                yield child, child_relative, "", ""
                children.append((child, child_relative))
                continue
            error_relative = relative / name if name else relative
            yield None, error_relative, str(value), detail
        pending.extend(
            sorted(
                children,
                key=lambda item: item[1].as_posix().casefold(),
                reverse=True,
            )
        )
        if on_directory_complete is not None:
            on_directory_complete(tuple(pending))


def _build_folder_index_owned(
    *,
    database_path: Path,
    products_sha256: str,
    roots: Sequence[NamedRoot],
    matcher,
    refresh: bool = False,
    checkpoint_size: int = 1000,
    directory_inactivity_timeout_seconds: float = (
        DEFAULT_DIRECTORY_INACTIVITY_TIMEOUT_SECONDS
    ),
    reporter: _ProgressReporter,
    target_sources: Sequence[str] = (),
    target_prefixes: Sequence[tuple[str, str]] = (),
    resume: bool = False,
    candidates_path: Path | None = None,
) -> dict[str, object]:
    """Index directory names only; never open or hash image files."""

    if checkpoint_size < 1:
        raise ValueError("checkpoint_size must be positive")
    if directory_inactivity_timeout_seconds <= 0:
        raise ValueError("directory inactivity timeout must be positive")
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
    filesystem_identity = _filesystem_identity(normalized_roots)
    connection = _open_database(
        Path(database_path),
        filesystem_identity=filesystem_identity,
        products_sha256=products_sha256,
        roots=normalized_roots,
        refresh=refresh,
    )
    connection.execute("PRAGMA foreign_keys=ON")
    scan_id = uuid.uuid4().hex
    discovered = 0
    matched = 0
    errors = []
    pending = 0
    completed_sources = []
    completed_scopes: list[dict[str, str]] = []
    abort_scan = False
    known_sources = {root.source_system for root in normalized_roots}
    unknown_sources = (
        set(target_sources)
        | {source for source, _prefix in target_prefixes}
    ) - known_sources
    if unknown_sources:
        raise ValueError(
            "定向刷新包含未知来源: " + ", ".join(sorted(unknown_sources))
        )
    requested_scopes: dict[str, list[str]] = {}
    for source in target_sources:
        requested_scopes.setdefault(source, []).append(".")
    for source, prefix in target_prefixes:
        requested_scopes.setdefault(source, []).append(prefix)
    targeted = bool(requested_scopes)
    if not targeted:
        requested_scopes = {
            root.source_system: ["."] for root in normalized_roots
        }
    for source, prefixes in tuple(requested_scopes.items()):
        compact: list[str] = []
        for prefix in sorted(set(prefixes), key=lambda value: (value.count("/"), value)):
            if prefix == ".":
                compact = ["."]
                break
            if not any(
                prefix == parent or prefix.startswith(parent + "/")
                for parent in compact
            ):
                compact.append(prefix)
        requested_scopes[source] = compact
    active_row = connection.execute(
        "SELECT value FROM metadata WHERE key='active_scan'"
    ).fetchone()
    resume_pending: list[tuple[Path, Path]] | None = None
    resume_source = ""
    resume_prefix = ""
    if resume:
        if active_row is None:
            raise ValueError("--resume 需要未完成的文件夹索引检查点")
        try:
            active_scan = json.loads(active_row[0])
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError("文件夹索引检查点损坏") from error
        if active_scan.get("filesystem_identity") != filesystem_identity:
            raise ValueError("文件夹索引检查点 roots 身份不一致")
        if active_scan.get("products_sha256") != products_sha256:
            raise ValueError("文件夹索引检查点商品表身份不一致")
        requested_scopes = {
            str(source): [str(prefix) for prefix in prefixes]
            for source, prefixes in active_scan.get("requested_scopes", {}).items()
        }
        targeted = bool(active_scan.get("targeted", False))
        scan_id = str(active_scan["scan_id"])
        discovered = int(active_scan.get("folders_discovered", 0))
        matched = int(active_scan.get("matched_folders", 0))
        errors = list(active_scan.get("errors", []))
        completed_sources = list(active_scan.get("completed_sources", []))
        completed_scopes = list(active_scan.get("completed_scopes", []))
        resume_source = str(active_scan.get("current_source", ""))
        resume_prefix = str(active_scan.get("current_prefix", ""))
        root_by_source = {
            root.source_system: root.path for root in normalized_roots
        }
        resume_pending = [
            (
                root_by_source[resume_source] / relative,
                Path(relative),
            )
            for relative in active_scan.get("pending_relative_paths", [])
        ]

    def save_active_scan(
        source: str,
        prefix: str,
        pending_items: Sequence[tuple[Path, Path]],
    ) -> None:
        document = {
            "schema_version": 1,
            "scan_id": scan_id,
            "filesystem_identity": filesystem_identity,
            "products_sha256": products_sha256,
            "requested_scopes": requested_scopes,
            "targeted": targeted,
            "current_source": source,
            "current_prefix": prefix,
            "pending_relative_paths": [
                relative.as_posix() for _absolute, relative in pending_items
            ],
            "folders_discovered": discovered,
            "matched_folders": matched,
            "errors": errors,
            "completed_sources": completed_sources,
            "completed_scopes": completed_scopes,
        }
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('active_scan', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(document, ensure_ascii=False),),
        )
        connection.commit()

    if not resume:
        first_source = next(iter(requested_scopes), "")
        first_prefix = requested_scopes.get(first_source, [""])[0]
        first_root = next(
            (root.path for root in normalized_roots if root.source_system == first_source),
            Path("."),
        )
        first_path = first_root if first_prefix == "." else first_root / first_prefix
        save_active_scan(
            first_source,
            first_prefix,
            ((first_path, Path(first_prefix)),) if first_source else (),
        )
    try:
        reporter.write(
            "running",
            phase="enumerating",
            scan_id=scan_id,
            folders_discovered=0,
            matched_folders=0,
            error_count=0,
            completed_sources=[],
        )
        for named_root in normalized_roots:
            if named_root.source_system not in requested_scopes:
                continue
            root = named_root.path
            source_completed = True
            for scope_prefix in requested_scopes[named_root.source_system]:
                scope_key = {
                    "source_system": named_root.source_system,
                    "relative_prefix": scope_prefix,
                }
                if scope_key in completed_scopes:
                    continue
                scope_completed = True
                scope_root = root if scope_prefix == "." else root / scope_prefix
                if scope_prefix != ".":
                    try:
                        scope_metadata = scope_root.lstat()
                    except OSError:
                        scope_metadata = None
                    if scope_metadata is not None and (
                        stat.S_ISLNK(scope_metadata.st_mode)
                        or (
                            getattr(scope_metadata, "st_file_attributes", 0)
                            & _REPARSE_POINT
                        )
                    ):
                        source_completed = False
                        scope_completed = False
                        errors.append(
                            {
                                "source_system": named_root.source_system,
                                "relative_path": scope_prefix,
                                "reason_code": "FOLDER_REFRESH_SCOPE_UNSAFE",
                                "detail": "target prefix is a symlink or reparse point",
                            }
                        )
                        connection.commit()
                        continue
                initial_pending = None
                if (
                    resume_pending is not None
                    and named_root.source_system == resume_source
                    and scope_prefix == resume_prefix
                ):
                    initial_pending = tuple(resume_pending)
                records = _walk_directories(
                    scope_root,
                    initial_relative=Path(scope_prefix),
                    inactivity_timeout_seconds=(
                        directory_inactivity_timeout_seconds
                    ),
                    on_progress=lambda _directory, relative: reporter.write(
                        "running",
                        force=False,
                        phase="enumerating",
                        source_system=named_root.source_system,
                        current_relative_path=(
                            relative.as_posix()
                            if relative.as_posix() != "."
                            else "."
                        ),
                        folders_discovered=discovered,
                        matched_folders=matched,
                        error_count=len(errors),
                        completed_sources=list(completed_sources),
                    ),
                    initial_pending=initial_pending,
                    on_directory_complete=lambda pending_items: save_active_scan(
                        named_root.source_system,
                        scope_prefix,
                        pending_items,
                    ),
                )
                if scope_prefix != ".":
                    records = chain(
                        ((scope_root, Path(scope_prefix), "", ""),),
                        records,
                    )
                for (
                    absolute_path,
                    relative_path,
                    reason_code,
                    error,
                ) in records:
                    relative_key = Path(
                        *(
                            part
                            for part in relative_path.parts
                            if part not in {"", "."}
                        )
                    ).as_posix()
                    if error:
                        source_completed = False
                        scope_completed = False
                        errors.append(
                            {
                                "source_system": named_root.source_system,
                                "relative_path": relative_key or ".",
                                "reason_code": reason_code,
                                "detail": error,
                            }
                        )
                        # Preserve metadata discovered before a broken NAS path.
                        connection.commit()
                        pending = 0
                        reporter.write(
                            "running",
                            phase="enumerating",
                            source_system=named_root.source_system,
                            current_relative_path=relative_key or ".",
                            folders_discovered=discovered,
                            matched_folders=matched,
                            error_count=len(errors),
                            completed_sources=list(completed_sources),
                        )
                        if reason_code == "FOLDER_ENUMERATION_TIMEOUT":
                            # A Python thread blocked in native NAS I/O cannot
                            # be cancelled safely. Stop scheduling more work so
                            # one scan can retain at most one orphaned daemon.
                            abort_scan = True
                            break
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
                    # Match only the folder's own name, never its ancestors.
                    folder_matcher = getattr(matcher, "match_folder_name", None)
                    matches = (
                        folder_matcher(absolute_path.name)
                        if callable(folder_matcher)
                        else matcher.match(
                            Path(absolute_path.name) / "__folder__.jpg"
                        )
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
                        reporter.write(
                            "running",
                            phase="checkpoint",
                            source_system=named_root.source_system,
                            current_relative_path=relative_key,
                            folders_discovered=discovered,
                            matched_folders=matched,
                            error_count=len(errors),
                            completed_sources=list(completed_sources),
                        )
                if scope_completed:
                    completed_scopes.append(scope_key)
                    if scope_prefix == ".":
                        connection.execute(
                            "UPDATE folders SET active=0 WHERE source_system=? AND last_seen_scan_id<>?",
                            (named_root.source_system, scan_id),
                        )
                    else:
                        child_prefix = scope_prefix + "/"
                        connection.execute(
                            "UPDATE folders SET active=0 WHERE source_system=? "
                            "AND last_seen_scan_id<>? AND (relative_path=? OR "
                            "substr(relative_path, 1, ?)=?)",
                            (
                                named_root.source_system,
                                scan_id,
                                scope_prefix,
                                len(child_prefix),
                                child_prefix,
                            ),
                        )
                connection.commit()
                save_active_scan(named_root.source_system, scope_prefix, ())
                if abort_scan:
                    break
            if source_completed and requested_scopes[named_root.source_system] == ["."]:
                completed_sources.append(named_root.source_system)
            if abort_scan:
                break
        summary = {
            "schema_version": FOLDER_INDEX_SCHEMA_VERSION,
            "scan_id": scan_id,
            "mode": "resume" if resume else ("refresh" if refresh else "new"),
            "complete": not errors,
            "folders_discovered": discovered,
            "matched_folders": matched,
            "completed_sources": completed_sources,
            "completed_scopes": completed_scopes,
            "targeted": targeted,
            "errors": errors,
        }
        connection.execute("DELETE FROM metadata WHERE key='active_scan'")
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('products_sha256', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (products_sha256,),
        )
        connection.commit()
        if candidates_path is not None:
            summary["candidate_rows"] = write_folder_candidates(
                database_path, candidates_path
            )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('last_summary', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(summary, ensure_ascii=False),),
        )
        connection.commit()
        reporter.write(
            "complete" if summary["complete"] else "partial",
            phase="finished",
            **summary,
            error_count=len(errors),
        )
        return summary
    except BaseException as error:
        reporter.write(
            "failed",
            phase="failed",
            scan_id=scan_id,
            folders_discovered=discovered,
            matched_folders=matched,
            error_count=len(errors),
            completed_sources=list(completed_sources),
            reason_code="FOLDER_INDEX_FAILED",
            detail=str(error),
        )
        raise
    finally:
        connection.close()


def build_folder_index(
    *,
    database_path: Path,
    products_sha256: str,
    roots: Sequence[NamedRoot],
    matcher,
    refresh: bool = False,
    checkpoint_size: int = 1000,
    directory_inactivity_timeout_seconds: float = (
        DEFAULT_DIRECTORY_INACTIVITY_TIMEOUT_SECONDS
    ),
    target_sources: Sequence[str] = (),
    target_prefixes: Sequence[tuple[str, str]] = (),
    resume: bool = False,
    candidates_path: Path | None = None,
) -> dict[str, object]:
    """Index directory names with a durable lease and observable progress."""

    if checkpoint_size < 1:
        raise ValueError("checkpoint_size must be positive")
    if directory_inactivity_timeout_seconds <= 0:
        raise ValueError("directory inactivity timeout must be positive")
    normalized_target_sources = tuple(
        source.strip() for source in target_sources if source.strip()
    )
    normalized_target_prefixes = tuple(
        (source.strip(), normalize_folder_refresh_prefix(prefix))
        for source, prefix in target_prefixes
        if source.strip()
    )
    if resume and (refresh or normalized_target_sources or normalized_target_prefixes):
        raise ValueError("--resume 不能与 refresh 或定向刷新范围同时使用")
    if (normalized_target_sources or normalized_target_prefixes) and not refresh:
        raise ValueError("定向范围只能与 refresh 模式一起使用")
    known_sources = {root.source_system for root in roots}
    unknown_sources = (
        set(normalized_target_sources)
        | {source for source, _prefix in normalized_target_prefixes}
    ) - known_sources
    if unknown_sources:
        raise ValueError(
            "定向刷新包含未知来源: " + ", ".join(sorted(unknown_sources))
        )
    database_path = Path(database_path)
    operation_id = uuid.uuid4().hex
    mode = "resume" if resume else ("refresh" if refresh else "new")
    reporter = _ProgressReporter(
        database_path.parent / FOLDER_INDEX_PROGRESS_FILENAME,
        operation_id=operation_id,
        mode=mode,
    )
    with _FolderIndexLease(
        database_path.parent / FOLDER_INDEX_LOCK_FILENAME,
        operation_id=operation_id,
    ):
        return _build_folder_index_owned(
            database_path=database_path,
            products_sha256=products_sha256,
            roots=roots,
            matcher=matcher,
            refresh=refresh or resume,
            checkpoint_size=checkpoint_size,
            directory_inactivity_timeout_seconds=(
                directory_inactivity_timeout_seconds
            ),
            reporter=reporter,
            target_sources=normalized_target_sources,
            target_prefixes=normalized_target_prefixes,
            resume=resume,
            candidates_path=candidates_path,
        )


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
    candidates_path: Path | None = None,
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
    database_path = Path(database_path)
    operation_id = uuid.uuid4().hex
    reporter = _ProgressReporter(
        database_path.parent / FOLDER_INDEX_PROGRESS_FILENAME,
        operation_id=operation_id,
        mode="rematch_only",
    )
    with _FolderIndexLease(
        database_path.parent / FOLDER_INDEX_LOCK_FILENAME,
        operation_id=operation_id,
    ):
        connection = _open_database(
            database_path,
            filesystem_identity=_filesystem_identity(normalized_roots),
            products_sha256=products_sha256,
            roots=normalized_roots,
            refresh=True,
        )
        rows = connection.execute(
            "SELECT folder_id, folder_name FROM folders WHERE active=1 "
            "ORDER BY source_system, relative_path"
        ).fetchall()
        matched_folders = 0
        candidate_rows = 0
        try:
            reporter.write(
                "running",
                phase="rematching",
                folders_discovered=len(rows),
                folders_processed=0,
                matched_folders=0,
                error_count=0,
            )
            for processed, (folder_id, folder_name) in enumerate(rows, start=1):
                folder_matcher = getattr(matcher, "match_folder_name", None)
                matches = (
                    folder_matcher(folder_name)
                    if callable(folder_matcher)
                    else matcher.match(Path(folder_name) / "__folder__.jpg")
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
                    matched_folders += 1
                    candidate_rows += len(matches)
                if processed % 1000 == 0:
                    connection.commit()
                    reporter.write(
                        "running",
                        phase="rematching",
                        folders_processed=processed,
                        matched_folders=matched_folders,
                    )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('products_sha256', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (products_sha256,),
            )
            connection.commit()
            summary = {
                "schema_version": FOLDER_INDEX_SCHEMA_VERSION,
                "mode": "rematch_only",
                "complete": True,
                "folders_discovered": len(rows),
                "matched_folders": matched_folders,
                "candidate_rows": candidate_rows,
                "errors": [],
            }
            if candidates_path is not None:
                summary["candidate_rows"] = write_folder_candidates(
                    database_path, candidates_path
                )
            reporter.write(
                "complete",
                phase="finished",
                folders_processed=len(rows),
                error_count=0,
                **summary,
            )
            return summary
        except BaseException as error:
            connection.rollback()
            reporter.write(
                "failed",
                phase="failed",
                reason_code="FOLDER_REMATCH_FAILED",
                detail=str(error),
            )
            raise
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
