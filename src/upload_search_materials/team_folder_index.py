"""Portable, decentralized snapshots for sharing folder indexes across machines."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import sqlite3
import time
import uuid
from typing import Iterable, Mapping, Sequence

from .asset_index import NamedRoot
from .asset_matching import PRODUCT_PATH_MATCHER_VERSION, ProductPathMatcher
from .folder_index import build_folder_index
from .io_tables import read_product_csv, validate_product_records
from .persistence import atomic_write_dict_csv, atomic_write_json, read_json

SNAPSHOT_SCHEMA_VERSION = 1
PORTABLE_FOLDER_FIELDS = (
    "folder_id",
    "source_id",
    "relative_path",
    "folder_name",
    "parent_relative_path",
)
CANDIDATE_FIELDS = (
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
)
DEFAULT_LEASE_SECONDS = 120.0
CACHE_VERIFICATION_SCHEMA_VERSION = 1


class TeamFolderIndexError(ValueError):
    """A shared snapshot cannot be used without risking corrupt state."""


def _directory_is_available(path: Path) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> str:
    text = str(value).replace("\\", "/").strip("/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise TeamFolderIndexError(f"TEAM_INDEX_RELATIVE_PATH_INVALID: {value}")
    return path.as_posix()


def _safe_source_id(value: str) -> str:
    source_id = str(value).strip().casefold()
    if not source_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in source_id):
        raise TeamFolderIndexError(f"TEAM_INDEX_SOURCE_ID_INVALID: {value}")
    return source_id


class _PublishLease:
    def __init__(self, path: Path, *, ttl_seconds: float = DEFAULT_LEASE_SECONDS):
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.nonce = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps({
            "schema_version": 1,
            "nonce": self.nonce,
            "host": socket.gethostname(),
            "created_at": _utc_now(),
            "expires_at_epoch_seconds": time.time() + self.ttl_seconds,
        }, ensure_ascii=False) + "\n").encode("utf-8")
        for _attempt in range(4):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    observed = self.path.stat()
                    existing = read_json(self.path)
                    expired = float(existing.get("expires_at_epoch_seconds", 0)) <= time.time()
                except (OSError, ValueError, TypeError, AttributeError):
                    observed = None
                    expired = False
                if not expired:
                    raise TeamFolderIndexError("TEAM_INDEX_PUBLISH_BUSY: 另一台机器正在发布该素材源")
                try:
                    current = self.path.stat()
                    if observed is not None and (current.st_size, current.st_mtime_ns) != (observed.st_size, observed.st_mtime_ns):
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
        raise TeamFolderIndexError("TEAM_INDEX_PUBLISH_BUSY: 无法取得发布租约")

    def __exit__(self, exc_type, exc_value, traceback):
        if not self.acquired:
            return
        try:
            existing = read_json(self.path)
            if existing.get("nonce") == self.nonce:
                self.path.unlink()
        except (FileNotFoundError, OSError, ValueError, AttributeError):
            pass


def _read_source_folders(database_path: Path, source_id: str) -> list[dict[str, str]]:
    connection = sqlite3.connect(Path(database_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT folder_id, source_system, relative_path, folder_name, parent_relative_path "
            "FROM folders WHERE active=1 AND source_system=? ORDER BY relative_path",
            (source_id,),
        ).fetchall()
    finally:
        connection.close()
    result = []
    for row in rows:
        relative_path = _safe_relative_path(row["relative_path"])
        result.append({
            "folder_id": row["folder_id"],
            "source_id": source_id,
            "relative_path": relative_path,
            "folder_name": row["folder_name"],
            "parent_relative_path": PurePosixPath(relative_path).parent.as_posix(),
        })
    return result


def _assert_source_publishable(database_path: Path, source_id: str) -> None:
    connection = sqlite3.connect(Path(database_path))
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='last_summary'"
        ).fetchone()
    except sqlite3.OperationalError as error:
        raise TeamFolderIndexError("TEAM_INDEX_LOCAL_SUMMARY_MISSING") from error
    finally:
        connection.close()
    if row is None:
        raise TeamFolderIndexError("TEAM_INDEX_LOCAL_SUMMARY_MISSING")
    try:
        summary = json.loads(row[0])
    except (json.JSONDecodeError, TypeError) as error:
        raise TeamFolderIndexError("TEAM_INDEX_LOCAL_SUMMARY_INVALID") from error
    if summary.get("complete") is not True:
        raise TeamFolderIndexError(
            f"TEAM_INDEX_LOCAL_SOURCE_INCOMPLETE: {source_id}"
        )


def _publish_portable_snapshot(
    *,
    shared_root: Path,
    source_id: str,
    folders: Sequence[Mapping[str, str]],
    publisher: str = "",
) -> dict[str, object]:
    """Publish already-portable metadata under one immutable source identity."""

    source_id = _safe_source_id(source_id)
    portable_folders = []
    for folder in folders:
        item = {field: str(folder.get(field, "")) for field in PORTABLE_FOLDER_FIELDS}
        item["source_id"] = source_id
        item["relative_path"] = _safe_relative_path(item["relative_path"])
        portable_folders.append(item)
    if not portable_folders:
        raise TeamFolderIndexError(f"TEAM_INDEX_SOURCE_EMPTY: {source_id}")

    source_root = Path(shared_root) / "sources" / source_id
    snapshots_root = source_root / "snapshots"
    snapshot_seed = json.dumps(
        portable_folders, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    snapshot_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ-")
        + hashlib.sha256(snapshot_seed).hexdigest()[:12]
    )
    staging = snapshots_root / f".staging-{snapshot_id}-{uuid.uuid4().hex}"
    final = snapshots_root / snapshot_id

    with _PublishLease(source_root / ".publish.lock"):
        staging.mkdir(parents=True, exist_ok=False)
        try:
            folders_path = staging / "folders.csv"
            atomic_write_dict_csv(
                folders_path,
                portable_folders,
                fieldnames=PORTABLE_FOLDER_FIELDS,
            )
            manifest = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "complete": True,
                "snapshot_id": snapshot_id,
                "source_id": source_id,
                "created_at": _utc_now(),
                "publisher": publisher.strip()
                or f"{socket.gethostname()}:{os.getpid()}",
                "folder_count": len(portable_folders),
                "files": {"folders.csv": {"sha256": _sha256(folders_path)}},
            }
            atomic_write_json(staging / "manifest.json", manifest, sort_keys=True)
            if final.exists():
                raise TeamFolderIndexError(
                    f"TEAM_INDEX_SNAPSHOT_EXISTS: {snapshot_id}"
                )
            os.replace(staging, final)
            atomic_write_json(
                source_root / "current.json",
                {
                    "schema_version": 1,
                    "source_id": source_id,
                    "snapshot_id": snapshot_id,
                    "updated_at": _utc_now(),
                },
                sort_keys=True,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "folder_count": len(portable_folders),
    }


def publish_snapshot(
    *,
    database_path: Path,
    shared_root: Path,
    source_id: str,
    publisher: str = "",
) -> dict[str, object]:
    """Publish one source as a new immutable snapshot and move its pointer."""

    source_id = _safe_source_id(source_id)
    database_path = Path(database_path)
    if not database_path.is_file():
        raise TeamFolderIndexError(
            f"TEAM_INDEX_LOCAL_DATABASE_MISSING: {database_path}"
        )
    _assert_source_publishable(database_path, source_id)
    folders = _read_source_folders(database_path, source_id)
    if not folders:
        raise TeamFolderIndexError(f"TEAM_INDEX_SOURCE_EMPTY: {source_id}")

    return _publish_portable_snapshot(
        shared_root=shared_root,
        source_id=source_id,
        folders=folders,
        publisher=publisher,
    )


def ensure_missing_snapshots(
    *,
    shared_root: Path,
    local_root: Path,
    products_path: Path,
    image_sources: Sequence[Mapping[str, str]],
    publisher: str = "automatic-upload-workflow",
) -> dict[str, object]:
    """Build and publish only configured sources without a valid snapshot."""

    shared_root = Path(shared_root)
    if not _directory_is_available(shared_root):
        raise TeamFolderIndexError("TEAM_INDEX_SHARED_ROOT_UNAVAILABLE")
    products_path = Path(products_path)
    products = read_product_csv(products_path)
    validation = validate_product_records(products)
    if not products or validation.batch_blocking:
        raise TeamFolderIndexError("TEAM_INDEX_PRODUCTS_INVALID")
    matcher = ProductPathMatcher.from_products(products, validation)
    products_sha256 = hashlib.sha256(products_path.read_bytes()).hexdigest()
    created = []
    existing = []
    for binding in image_sources:
        source_id = _safe_source_id(str(binding.get("source_id", "")))
        try:
            _snapshot_for_source(shared_root, source_id)
            existing.append(source_id)
            continue
        except TeamFolderIndexError:
            pass
        declared_root = Path(str(binding.get("path", "")))
        if declared_root.is_symlink() or not declared_root.is_dir():
            raise TeamFolderIndexError(
                f"TEAM_INDEX_LOCAL_BINDING_UNAVAILABLE: {source_id}"
            )
        build_root = Path(local_root) / "team-build" / source_id
        database_path = build_root / "folder-index.sqlite3"
        refreshing = database_path.is_file()
        build_folder_index(
            database_path=database_path,
            products_sha256=products_sha256,
            roots=(NamedRoot(source_id, declared_root.resolve()),),
            matcher=matcher,
            refresh=refreshing,
            target_sources=(source_id,) if refreshing else (),
        )
        try:
            _snapshot_for_source(shared_root, source_id)
            existing.append(source_id)
            continue
        except TeamFolderIndexError:
            pass
        created.append(
            publish_snapshot(
                database_path=database_path,
                shared_root=shared_root,
                source_id=source_id,
                publisher=publisher,
            )
        )
    return {
        "schema_version": 1,
        "mode": "automatic_missing_snapshot_bootstrap",
        "created": created,
        "existing_source_ids": existing,
    }


def _read_snapshot_manifest(
    snapshot_path: Path,
    *,
    expected_source_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> dict[str, object]:
    path = Path(snapshot_path)
    try:
        manifest = read_json(path / "manifest.json")
    except (OSError, ValueError, TypeError) as error:
        raise TeamFolderIndexError(f"TEAM_INDEX_MANIFEST_INVALID: {path}") from error
    if not isinstance(manifest, dict):
        raise TeamFolderIndexError(f"TEAM_INDEX_MANIFEST_INVALID: {path}")
    source_id = _safe_source_id(str(manifest.get("source_id", "")))
    if expected_source_id and source_id != _safe_source_id(expected_source_id):
        raise TeamFolderIndexError("TEAM_INDEX_SOURCE_MISMATCH")
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION or manifest.get("complete") is not True:
        raise TeamFolderIndexError("TEAM_INDEX_SNAPSHOT_INCOMPLETE_OR_UNSUPPORTED")
    if manifest.get("snapshot_id") != (expected_snapshot_id or path.name):
        raise TeamFolderIndexError("TEAM_INDEX_SNAPSHOT_ID_MISMATCH")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise TeamFolderIndexError("TEAM_INDEX_MANIFEST_INVALID")
    file_info = files.get("folders.csv")
    if not isinstance(file_info, dict):
        raise TeamFolderIndexError("TEAM_INDEX_MANIFEST_INVALID")
    folder_count = manifest.get("folder_count")
    if not isinstance(folder_count, int) or folder_count < 0:
        raise TeamFolderIndexError("TEAM_INDEX_MANIFEST_INVALID")
    folders_path = path / "folders.csv"
    if not folders_path.is_file() or not str(file_info.get("sha256", "")).strip():
        raise TeamFolderIndexError("TEAM_INDEX_MANIFEST_INVALID")
    return manifest


def validate_snapshot(
    snapshot_path: Path,
    *,
    expected_source_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> dict[str, object]:
    path = Path(snapshot_path)
    manifest = _read_snapshot_manifest(
        path,
        expected_source_id=expected_source_id,
        expected_snapshot_id=expected_snapshot_id,
    )
    file_info = manifest["files"]["folders.csv"]
    folders_path = path / "folders.csv"
    if file_info.get("sha256") != _sha256(folders_path):
        raise TeamFolderIndexError("TEAM_INDEX_SNAPSHOT_HASH_MISMATCH")
    return manifest


def _cache_verification_facts(
    snapshot_path: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    path = Path(snapshot_path)
    folders = path / "folders.csv"
    manifest_path = path / "manifest.json"
    folders_stat = folders.stat()
    manifest_stat = manifest_path.stat()
    return {
        "schema_version": CACHE_VERIFICATION_SCHEMA_VERSION,
        "source_id": str(manifest["source_id"]),
        "snapshot_id": str(manifest["snapshot_id"]),
        "folders_sha256": str(manifest["files"]["folders.csv"]["sha256"]),
        "folders_size": folders_stat.st_size,
        "folders_mtime_ns": folders_stat.st_mtime_ns,
        "folders_ctime_ns": folders_stat.st_ctime_ns,
        "folders_file_id": folders_stat.st_ino,
        "manifest_size": manifest_stat.st_size,
        "manifest_mtime_ns": manifest_stat.st_mtime_ns,
        "manifest_ctime_ns": manifest_stat.st_ctime_ns,
        "manifest_file_id": manifest_stat.st_ino,
    }


def _write_cache_verification(
    snapshot_path: Path,
    manifest: Mapping[str, object],
) -> None:
    atomic_write_json(
        Path(snapshot_path) / ".verified.json",
        _cache_verification_facts(snapshot_path, manifest),
        sort_keys=True,
    )


def _validate_cached_snapshot(
    snapshot_path: Path,
    *,
    expected_source_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> dict[str, object]:
    path = Path(snapshot_path)
    manifest = _read_snapshot_manifest(
        path,
        expected_source_id=expected_source_id,
        expected_snapshot_id=expected_snapshot_id,
    )
    try:
        verification = read_json(path / ".verified.json")
        if verification == _cache_verification_facts(path, manifest):
            return manifest
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    manifest = validate_snapshot(
        path,
        expected_source_id=expected_source_id,
        expected_snapshot_id=expected_snapshot_id,
    )
    _write_cache_verification(path, manifest)
    return manifest


def _snapshot_for_source(
    root: Path,
    source_id: str,
    *,
    cached: bool = False,
) -> tuple[Path, dict[str, object]]:
    source_root = Path(root) / "sources" / source_id
    candidates: list[Path] = []
    try:
        pointer = read_json(source_root / "current.json")
        snapshot_id = str(pointer.get("snapshot_id", ""))
        if snapshot_id and Path(snapshot_id).name == snapshot_id:
            candidates.append(source_root / "snapshots" / snapshot_id)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    snapshots_root = source_root / "snapshots"
    if snapshots_root.is_dir():
        candidates.extend(
            path for path in sorted(snapshots_root.iterdir(), reverse=True)
            if path.is_dir() and not path.name.startswith(".") and path not in candidates
        )
    errors = []
    validator = _validate_cached_snapshot if cached else validate_snapshot
    for candidate in candidates:
        try:
            return candidate, validator(candidate, expected_source_id=source_id)
        except TeamFolderIndexError as error:
            errors.append(str(error))
    detail = "; ".join(errors[:3])
    raise TeamFolderIndexError(f"TEAM_INDEX_NO_VALID_SNAPSHOT: {source_id}{': ' + detail if detail else ''}")


def _source_ids(root: Path) -> list[str]:
    sources_root = Path(root) / "sources"
    if not sources_root.is_dir():
        return []
    return sorted(path.name for path in sources_root.iterdir() if path.is_dir() and not path.name.startswith("."))


def _snapshot_by_id(
    root: Path,
    source_id: str,
    snapshot_id: str,
    *,
    cached: bool = False,
) -> tuple[Path, dict[str, object]]:
    source_id = _safe_source_id(source_id)
    if not snapshot_id or Path(snapshot_id).name != snapshot_id:
        raise TeamFolderIndexError("TEAM_INDEX_SNAPSHOT_ID_MISMATCH")
    path = Path(root) / "sources" / source_id / "snapshots" / snapshot_id
    validator = _validate_cached_snapshot if cached else validate_snapshot
    return path, validator(
        path,
        expected_source_id=source_id,
        expected_snapshot_id=snapshot_id,
    )


def _pointed_snapshot_manifest(
    root: Path,
    source_id: str,
) -> tuple[Path, dict[str, object]]:
    source_id = _safe_source_id(source_id)
    source_root = Path(root) / "sources" / source_id
    try:
        pointer = read_json(source_root / "current.json")
        snapshot_id = str(pointer.get("snapshot_id", ""))
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise TeamFolderIndexError(
            f"TEAM_INDEX_CURRENT_POINTER_INVALID: {source_id}"
        ) from error
    if not snapshot_id or Path(snapshot_id).name != snapshot_id:
        raise TeamFolderIndexError(
            f"TEAM_INDEX_CURRENT_POINTER_INVALID: {source_id}"
        )
    path = source_root / "snapshots" / snapshot_id
    return path, _read_snapshot_manifest(
        path,
        expected_source_id=source_id,
        expected_snapshot_id=snapshot_id,
    )


def _active_snapshot_bindings(cache_root: Path) -> list[dict[str, str]] | None:
    path = Path(cache_root) / "bindings.json"
    if not path.is_file():
        return None
    try:
        document = read_json(path)
    except (OSError, ValueError, TypeError) as error:
        raise TeamFolderIndexError("TEAM_INDEX_BINDINGS_INVALID") from error
    if not isinstance(document, dict):
        raise TeamFolderIndexError("TEAM_INDEX_BINDINGS_INVALID")
    rows = document.get("bindings")
    if document.get("schema_version") != 2:
        return None
    if not isinstance(rows, list):
        raise TeamFolderIndexError("TEAM_INDEX_BINDINGS_INVALID")
    bindings: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TeamFolderIndexError("TEAM_INDEX_BINDINGS_INVALID")
        source_id = _safe_source_id(str(row.get("source_id", "")))
        snapshot_id = str(row.get("snapshot_id", ""))
        if not snapshot_id or Path(snapshot_id).name != snapshot_id:
            raise TeamFolderIndexError("TEAM_INDEX_BINDINGS_INVALID")
        bindings.append(
            {
                "source_id": source_id,
                "snapshot_id": snapshot_id,
                "binding_status": "exact",
            }
        )
    return bindings


def _copy_snapshot_to_cache(snapshot_path: Path, manifest: Mapping[str, object], cache_root: Path) -> Path:
    source_id = str(manifest["source_id"])
    snapshot_id = str(manifest["snapshot_id"])
    target = Path(cache_root) / "sources" / source_id / "snapshots" / snapshot_id
    if not target.exists():
        staging = target.parent / f".staging-{snapshot_id}-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(snapshot_path / "folders.csv", staging / "folders.csv")
            shutil.copy2(snapshot_path / "manifest.json", staging / "manifest.json")
            validate_snapshot(
                staging,
                expected_source_id=source_id,
                expected_snapshot_id=snapshot_id,
            )
            os.replace(staging, target)
            _write_cache_verification(target, manifest)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    cached_manifest = _validate_cached_snapshot(
        target,
        expected_source_id=source_id,
        expected_snapshot_id=snapshot_id,
    )
    if (
        cached_manifest["files"]["folders.csv"]["sha256"]
        != manifest["files"]["folders.csv"]["sha256"]
    ):
        raise TeamFolderIndexError("TEAM_INDEX_CACHE_SNAPSHOT_CONFLICT")
    atomic_write_json(Path(cache_root) / "sources" / source_id / "current.json", {
        "schema_version": 1,
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "updated_at": _utc_now(),
    }, sort_keys=True)
    return target


def _read_portable_folders(snapshot_path: Path, source_id: str) -> list[dict[str, str]]:
    with (Path(snapshot_path) / "folders.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(PORTABLE_FOLDER_FIELDS).issubset(reader.fieldnames or ()):
            raise TeamFolderIndexError("TEAM_INDEX_FOLDERS_SCHEMA_INVALID")
        rows = []
        for row in reader:
            if row.get("source_id") != source_id:
                raise TeamFolderIndexError("TEAM_INDEX_SOURCE_MISMATCH")
            item = {field: str(row.get(field, "")) for field in PORTABLE_FOLDER_FIELDS}
            item["relative_path"] = _safe_relative_path(item["relative_path"])
            rows.append(item)
        return rows


def sync_snapshots(
    *,
    shared_root: Path,
    local_root: Path,
    image_sources: Sequence[Mapping[str, str]],
    source_ids: Iterable[str] = (),
) -> dict[str, object]:
    """Cache snapshots selected only by exact configured ``source_id``."""

    bindings = {
        _safe_source_id(str(item.get("source_id", ""))): item
        for item in image_sources
    }
    requested = sorted({_safe_source_id(value) for value in source_ids})
    selected_ids = requested or sorted(bindings)
    if not selected_ids:
        raise TeamFolderIndexError("TEAM_INDEX_NO_SOURCES")

    unknown_bindings = [
        source_id for source_id in selected_ids if source_id not in bindings
    ]
    if unknown_bindings:
        raise TeamFolderIndexError(
            "TEAM_INDEX_LOCAL_BINDING_MISSING: " + ", ".join(unknown_bindings)
        )

    shared_available = _directory_is_available(Path(shared_root))
    discovery_root = Path(shared_root) if shared_available else Path(local_root) / "team-cache"
    synced = []
    skipped = []
    errors = []
    folder_rows = 0
    cache_root = Path(local_root) / "team-cache"

    for source_id in selected_ids:
        try:
            origin = "local_cache"
            if shared_available:
                try:
                    shared_snapshot, shared_manifest = _pointed_snapshot_manifest(
                        discovery_root, source_id
                    )
                    local_snapshot = (
                        cache_root
                        / "sources"
                        / source_id
                        / "snapshots"
                        / str(shared_manifest["snapshot_id"])
                    )
                    if local_snapshot.is_dir():
                        local_manifest = _validate_cached_snapshot(
                            local_snapshot,
                            expected_source_id=source_id,
                            expected_snapshot_id=str(
                                shared_manifest["snapshot_id"]
                            ),
                        )
                        if (
                            local_manifest["files"]["folders.csv"]["sha256"]
                            != shared_manifest["files"]["folders.csv"]["sha256"]
                        ):
                            raise TeamFolderIndexError(
                                "TEAM_INDEX_CACHE_SNAPSHOT_CONFLICT"
                            )
                        snapshot_path, manifest = local_snapshot, local_manifest
                    else:
                        manifest = validate_snapshot(
                            shared_snapshot,
                            expected_source_id=source_id,
                            expected_snapshot_id=str(
                                shared_manifest["snapshot_id"]
                            ),
                        )
                        snapshot_path = _copy_snapshot_to_cache(
                            shared_snapshot, manifest, cache_root
                        )
                        origin = "shared"
                except TeamFolderIndexError:
                    shared_snapshot, manifest = _snapshot_for_source(
                        discovery_root, source_id
                    )
                    snapshot_path = _copy_snapshot_to_cache(
                        shared_snapshot, manifest, cache_root
                    )
                    origin = "shared"
            else:
                snapshot_path, manifest = _snapshot_for_source(
                    discovery_root, source_id, cached=True
                )
        except TeamFolderIndexError as error:
            errors.append({"source_id": source_id, "error": str(error)})
            continue
        folder_rows += int(manifest["folder_count"])
        synced.append(
            {
                "source_id": source_id,
                "snapshot_id": manifest["snapshot_id"],
                "folder_count": manifest["folder_count"],
                "origin": origin,
                "binding_status": "exact",
            }
        )

    missing_binding_ids = sorted(
        set(selected_ids) - {str(item["source_id"]) for item in synced}
    )
    for source_id in missing_binding_ids:
        skipped.append(
            {
                "source_id": source_id,
                "reason_code": "TEAM_INDEX_LOCAL_BINDING_HAS_NO_SNAPSHOT",
            }
        )
    if requested and errors:
        raise TeamFolderIndexError("; ".join(item["error"] for item in errors))
    if not synced:
        raise TeamFolderIndexError("TEAM_INDEX_NO_VALID_SNAPSHOTS")

    local_root = Path(local_root)
    summary = {
        "schema_version": 1,
        "complete": bool(synced) and not missing_binding_ids and not errors,
        "mode": "team_snapshot_sync",
        "shared_available": shared_available,
        "synced_at": _utc_now(),
        "folder_rows": folder_rows,
        "sources": synced,
        "skipped_sources": skipped,
        "superseded_sources": [],
        "errors": errors,
    }
    atomic_write_json(
        cache_root / "bindings.json",
        {
            "schema_version": 2,
            "generated_at": summary["synced_at"],
            "shared_available": shared_available,
            "bindings": synced,
        },
        sort_keys=True,
    )
    atomic_write_json(local_root / "team-sync.json", summary, sort_keys=True)
    return summary


def materialize_task_folder_candidates(
    *,
    local_root: Path,
    products_path: Path,
    image_sources: Sequence[Mapping[str, str]],
    selected_product_ids: Iterable[str],
    output_path: Path,
    source_ids: Iterable[str] = (),
) -> dict[str, object]:
    """Match cached folder metadata for one task using the current matcher."""

    products_path = Path(products_path)
    selected = sorted(
        {
            str(product_id).strip()
            for product_id in selected_product_ids
            if str(product_id).strip()
        }
    )
    if not selected:
        raise TeamFolderIndexError("TEAM_INDEX_SELECTED_PRODUCTS_REQUIRED")
    selected_set = set(selected)
    products = read_product_csv(products_path)
    validation = validate_product_records(products)
    if not products or validation.batch_blocking:
        raise TeamFolderIndexError("TEAM_INDEX_PRODUCTS_INVALID")
    selected_products = tuple(
        product for product in products if product.product_id in selected_set
    )
    matcher = ProductPathMatcher.from_products(selected_products, validation)

    cache_root = Path(local_root) / "team-cache"
    requested_sources = sorted({_safe_source_id(value) for value in source_ids})
    active_bindings = _active_snapshot_bindings(cache_root)
    if active_bindings is not None:
        snapshot_selections = [
            item
            for item in active_bindings
            if not requested_sources
            or item["source_id"] in requested_sources
        ]
    else:
        selected_sources = requested_sources or sorted(
            _safe_source_id(str(item.get("source_id", "")))
            for item in image_sources
        )
        snapshot_selections = [
            {
                "source_id": source_id,
                "snapshot_id": "",
                "binding_status": "exact",
            }
            for source_id in selected_sources
        ]
    if not snapshot_selections:
        raise TeamFolderIndexError(
            "TEAM_INDEX_NO_BOUND_LOCAL_SNAPSHOTS"
            if active_bindings is not None
            else "TEAM_INDEX_NO_VALID_LOCAL_CACHE"
        )

    bindings = {str(item.get("source_id", "")): item for item in image_sources}
    candidates: list[dict[str, str]] = []
    sources = []
    skipped = []
    for selection in snapshot_selections:
        source_id = selection["source_id"]
        try:
            if selection["snapshot_id"]:
                snapshot_path, manifest = _snapshot_by_id(
                    cache_root,
                    source_id,
                    selection["snapshot_id"],
                    cached=True,
                )
            else:
                snapshot_path, manifest = _snapshot_for_source(
                    cache_root, source_id, cached=True
                )
        except TeamFolderIndexError as error:
            if requested_sources:
                raise
            skipped.append({"source_id": source_id, "reason_code": str(error)})
            continue
        binding = bindings.get(source_id)
        binding_status = "exact"
        if not binding:
            skipped.append(
                {
                    "source_id": source_id,
                    "reason_code": binding_status,
                }
            )
            continue

        root = Path(str(binding["path"]))
        folders = _read_portable_folders(snapshot_path, source_id)
        if len(folders) != manifest.get("folder_count"):
            raise TeamFolderIndexError("TEAM_INDEX_FOLDER_COUNT_MISMATCH")
        for folder in folders:
            relative = Path(*PurePosixPath(folder["relative_path"]).parts)
            absolute = root / relative
            for match in matcher.match_folder_name(folder["folder_name"]):
                if match.product_id not in selected_set:
                    continue
                candidates.append(
                    {
                        "folder_id": folder["folder_id"],
                        "source_system": source_id,
                        "absolute_path": str(absolute),
                        "relative_path": folder["relative_path"],
                        "folder_name": folder["folder_name"],
                        "product_id": match.product_id,
                        "sku": match.sku,
                        "product_title": match.product_title,
                        "match_type": match.match_type,
                        "match_status": match.match_status,
                    }
                )
        source_summary = {
            "source_id": source_id,
            "snapshot_id": manifest["snapshot_id"],
            "folder_count": manifest["folder_count"],
            "folders_sha256": manifest["files"]["folders.csv"]["sha256"],
            "binding_status": binding_status,
        }
        sources.append(source_summary)

    if not sources:
        raise TeamFolderIndexError("TEAM_INDEX_NO_BOUND_LOCAL_SNAPSHOTS")
    candidates.sort(
        key=lambda row: (
            row["product_id"],
            row["source_system"],
            row["relative_path"],
            row["folder_id"],
        )
    )
    output_path = Path(output_path)
    atomic_write_dict_csv(output_path, candidates, fieldnames=CANDIDATE_FIELDS)
    matched_products = {row["product_id"] for row in candidates}
    return {
        "schema_version": 1,
        "mode": "task_candidate_materialization",
        "requested_products": len(selected),
        "matched_products": len(matched_products),
        "candidate_rows": len(candidates),
        "product_snapshot_sha256": _sha256(products_path),
        "matcher_version": PRODUCT_PATH_MATCHER_VERSION,
        "sources": sources,
        "skipped_sources": skipped,
        "superseded_sources": [],
        "created_at": _utc_now(),
    }


def snapshot_status(*, shared_root: Path, local_root: Path) -> dict[str, object]:
    """Inspect pointers and hashes without publishing or changing shared state."""

    result: dict[str, object] = {
        "shared_root": str(shared_root),
        "shared_available": _directory_is_available(Path(shared_root)),
        "sources": [],
    }
    root = Path(shared_root) if result["shared_available"] else Path(local_root) / "team-cache"
    result["using"] = "shared" if result["shared_available"] else "local_cache"
    sources = []
    for source_id in _source_ids(root):
        try:
            _path, manifest = _snapshot_for_source(root, source_id)
            sources.append({
                "source_id": source_id,
                "status": "valid",
                "snapshot_id": manifest["snapshot_id"],
                "folder_count": manifest["folder_count"],
            })
        except TeamFolderIndexError as error:
            sources.append({"source_id": source_id, "status": "invalid", "error": str(error)})
    result["sources"] = sources
    return result
