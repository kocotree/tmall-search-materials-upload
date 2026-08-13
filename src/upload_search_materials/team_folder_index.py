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
from .asset_matching import ProductPathMatcher
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


class TeamFolderIndexError(ValueError):
    """A shared snapshot cannot be used without risking corrupt state."""


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


def publish_snapshot(
    *,
    database_path: Path,
    shared_root: Path,
    source_id: str,
    canonical_source: str,
    publisher: str = "",
) -> dict[str, object]:
    """Publish one source as a new immutable snapshot and move its pointer."""

    source_id = _safe_source_id(source_id)
    database_path = Path(database_path)
    if not database_path.is_file():
        raise TeamFolderIndexError(f"TEAM_INDEX_LOCAL_DATABASE_MISSING: {database_path}")
    if not canonical_source.strip():
        raise TeamFolderIndexError("TEAM_INDEX_CANONICAL_SOURCE_REQUIRED")
    _assert_source_publishable(database_path, source_id)
    folders = _read_source_folders(database_path, source_id)
    if not folders:
        raise TeamFolderIndexError(f"TEAM_INDEX_SOURCE_EMPTY: {source_id}")

    source_root = Path(shared_root) / "sources" / source_id
    snapshots_root = source_root / "snapshots"
    snapshot_seed = json.dumps(folders, ensure_ascii=False, sort_keys=True).encode("utf-8")
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ-") + hashlib.sha256(snapshot_seed).hexdigest()[:12]
    staging = snapshots_root / f".staging-{snapshot_id}-{uuid.uuid4().hex}"
    final = snapshots_root / snapshot_id

    with _PublishLease(source_root / ".publish.lock"):
        staging.mkdir(parents=True, exist_ok=False)
        try:
            folders_path = staging / "folders.csv"
            atomic_write_dict_csv(folders_path, folders, fieldnames=PORTABLE_FOLDER_FIELDS)
            manifest = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "complete": True,
                "snapshot_id": snapshot_id,
                "source_id": source_id,
                "canonical_source": canonical_source.strip(),
                "created_at": _utc_now(),
                "publisher": publisher.strip() or f"{socket.gethostname()}:{os.getpid()}",
                "folder_count": len(folders),
                "files": {"folders.csv": {"sha256": _sha256(folders_path)}},
            }
            atomic_write_json(staging / "manifest.json", manifest, sort_keys=True)
            if final.exists():
                raise TeamFolderIndexError(f"TEAM_INDEX_SNAPSHOT_EXISTS: {snapshot_id}")
            os.replace(staging, final)
            atomic_write_json(source_root / "current.json", {
                "schema_version": 1,
                "source_id": source_id,
                "snapshot_id": snapshot_id,
                "updated_at": _utc_now(),
            }, sort_keys=True)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {"source_id": source_id, "snapshot_id": snapshot_id, "folder_count": len(folders)}


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
    if not shared_root.is_dir():
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
        canonical_source = str(binding.get("canonical_unc", "")).strip()
        if not canonical_source:
            raise TeamFolderIndexError(
                f"TEAM_INDEX_LOCAL_CANONICAL_SOURCE_MISSING: {source_id}"
            )
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
        created.append(publish_snapshot(
            database_path=database_path,
            shared_root=shared_root,
            source_id=source_id,
            canonical_source=canonical_source,
            publisher=publisher,
        ))
    return {
        "schema_version": 1,
        "mode": "automatic_missing_snapshot_bootstrap",
        "created": created,
        "existing_source_ids": existing,
    }


def validate_snapshot(
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
    folders_path = path / "folders.csv"
    if not folders_path.is_file() or file_info.get("sha256") != _sha256(folders_path):
        raise TeamFolderIndexError("TEAM_INDEX_SNAPSHOT_HASH_MISMATCH")
    return manifest


def _snapshot_for_source(root: Path, source_id: str) -> tuple[Path, dict[str, object]]:
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
    for candidate in candidates:
        try:
            return candidate, validate_snapshot(candidate, expected_source_id=source_id)
        except TeamFolderIndexError as error:
            errors.append(str(error))
    detail = "; ".join(errors[:3])
    raise TeamFolderIndexError(f"TEAM_INDEX_NO_VALID_SNAPSHOT: {source_id}{': ' + detail if detail else ''}")


def _source_ids(root: Path) -> list[str]:
    sources_root = Path(root) / "sources"
    if not sources_root.is_dir():
        return []
    return sorted(path.name for path in sources_root.iterdir() if path.is_dir() and not path.name.startswith("."))


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
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    validate_snapshot(target, expected_source_id=source_id)
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
    products_path: Path,
    image_sources: Sequence[Mapping[str, str]],
    source_ids: Iterable[str] = (),
) -> dict[str, object]:
    """Cache latest valid snapshots and materialize machine-local candidates."""

    products = read_product_csv(Path(products_path))
    validation = validate_product_records(products)
    if not products or validation.batch_blocking:
        raise TeamFolderIndexError("TEAM_INDEX_PRODUCTS_INVALID")
    matcher = ProductPathMatcher.from_products(products, validation)
    bindings = {str(item.get("source_id", "")): item for item in image_sources}
    requested = sorted({_safe_source_id(value) for value in source_ids})
    shared_available = Path(shared_root).is_dir()
    discovery_root = Path(shared_root) if shared_available else Path(local_root) / "team-cache"
    selected_ids = requested or _source_ids(discovery_root)
    if not selected_ids:
        raise TeamFolderIndexError("TEAM_INDEX_NO_SOURCES")

    snapshots: list[tuple[str, Path, dict[str, object], str]] = []
    errors = []
    for source_id in selected_ids:
        try:
            snapshot_path, manifest = _snapshot_for_source(discovery_root, source_id)
            origin = "shared" if shared_available else "local_cache"
            if shared_available:
                snapshot_path = _copy_snapshot_to_cache(
                    snapshot_path, manifest, Path(local_root) / "team-cache"
                )
            snapshots.append((source_id, snapshot_path, manifest, origin))
        except TeamFolderIndexError as error:
            errors.append({"source_id": source_id, "error": str(error)})
    if requested and errors:
        raise TeamFolderIndexError("; ".join(item["error"] for item in errors))
    if not snapshots:
        raise TeamFolderIndexError("TEAM_INDEX_NO_VALID_SNAPSHOTS")

    candidates = []
    synced = []
    skipped = []
    for source_id, snapshot_path, manifest, origin in snapshots:
        binding = bindings.get(source_id)
        if not binding:
            skipped.append({"source_id": source_id, "reason_code": "TEAM_INDEX_LOCAL_BINDING_MISSING"})
            continue
        canonical_source = str(manifest.get("canonical_source", "")).casefold().rstrip("\\/")
        local_canonical = str(binding.get("canonical_unc", "")).casefold().rstrip("\\/")
        if not local_canonical:
            skipped.append({"source_id": source_id, "reason_code": "TEAM_INDEX_LOCAL_CANONICAL_SOURCE_MISSING"})
            continue
        if not canonical_source or canonical_source != local_canonical:
            skipped.append({"source_id": source_id, "reason_code": "TEAM_INDEX_CANONICAL_SOURCE_MISMATCH"})
            continue
        root = Path(str(binding["path"]))
        portable_folders = _read_portable_folders(snapshot_path, source_id)
        if len(portable_folders) != manifest.get("folder_count"):
            raise TeamFolderIndexError("TEAM_INDEX_FOLDER_COUNT_MISMATCH")
        for folder in portable_folders:
            relative = Path(*PurePosixPath(folder["relative_path"]).parts)
            absolute = root / relative
            for match in matcher.match_folder_name(folder["folder_name"]):
                candidates.append({
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
                })
        synced.append({
            "source_id": source_id,
            "snapshot_id": manifest["snapshot_id"],
            "folder_count": manifest["folder_count"],
            "origin": origin,
        })
    candidates.sort(key=lambda row: (row["product_id"], row["source_system"], row["relative_path"], row["folder_id"]))
    local_root = Path(local_root)
    atomic_write_dict_csv(local_root / "folder-candidates.csv", candidates, fieldnames=CANDIDATE_FIELDS)
    summary = {
        "schema_version": 1,
        "complete": not skipped and not errors,
        "mode": "team_snapshot_sync",
        "shared_available": shared_available,
        "synced_at": _utc_now(),
        "candidate_rows": len(candidates),
        "sources": synced,
        "skipped_sources": skipped,
        "errors": errors,
    }
    atomic_write_json(local_root / "folder-scan-summary.json", summary, sort_keys=True)
    atomic_write_json(local_root / "team-sync.json", summary, sort_keys=True)
    return summary


def snapshot_status(*, shared_root: Path, local_root: Path) -> dict[str, object]:
    """Inspect pointers and hashes without publishing or changing shared state."""

    result: dict[str, object] = {
        "shared_root": str(shared_root),
        "shared_available": Path(shared_root).is_dir(),
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
