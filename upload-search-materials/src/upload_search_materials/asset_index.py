"""Read-only, partitioned orchestration for the incremental asset index."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Literal, Sequence
import uuid

from .asset_index_store import AssetIndexStore, IndexedFile, PathMatchRecord
from .assets import IMAGE_EXTENSIONS, inspect_asset


_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CANDIDATE_FIELDS = [
    "source_system",
    "candidate_directory",
    "product_id",
    "sku",
    "product_title",
    "match_type",
    "match_status",
    "image_count",
    "hashes_complete",
    "license_status",
    "reason_codes",
]


@dataclass(frozen=True)
class NamedRoot:
    source_system: str
    path: Path

    @classmethod
    def parse(cls, value: str) -> NamedRoot:
        source_system, separator, path = value.partition("=")
        if not separator or not _SOURCE_PATTERN.fullmatch(source_system):
            raise ValueError("root must use a non-empty source name followed by '='")
        if not path.strip():
            raise ValueError("root path must not be empty")
        return cls(source_system, Path(path))


@dataclass(frozen=True)
class IndexOptions:
    partition_depth: int = 2
    checkpoint_size: int = 1000

    def __post_init__(self) -> None:
        if self.partition_depth < 1:
            raise ValueError("partition_depth must be positive")
        if self.checkpoint_size < 1:
            raise ValueError("checkpoint_size must be positive")


@dataclass(frozen=True)
class ScanOutcome:
    complete: bool
    partial_failure: bool
    discovered: int
    indexed: int
    matched: int
    failed: int
    elapsed_seconds: float


@dataclass(frozen=True)
class _Candidate:
    indexed_file: IndexedFile
    relative_path: Path
    absolute_path: Path


@dataclass
class _Discovery:
    candidates: dict[tuple[str, str], list[_Candidate]]
    errors: dict[tuple[str, str], list[str]]
    roots: dict[str, Path]
    discovered: int = 0


def write_match_candidates(store, path: Path) -> int:
    """Write stable, review-only match groups from the durable index."""

    rows = store._connection.execute(
        "SELECT files.source_system, files.candidate_directory, files.relative_path, "
        "files.sha256, files.validation_status, files.reason_codes_json AS file_reasons, "
        "matches.product_id, matches.sku, matches.product_title, matches.match_type, "
        "matches.match_status, matches.reason_codes_json AS match_reasons "
        "FROM files JOIN matches USING(file_id) WHERE files.active=1 "
        "ORDER BY files.source_system, files.candidate_directory, matches.product_id, "
        "matches.match_type, files.relative_path"
    ).fetchall()
    groups: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for row in rows:
        key = (
            str(row["source_system"]),
            str(row["candidate_directory"]),
            str(row["product_id"]),
            str(row["match_type"]),
        )
        group = groups.setdefault(
            key,
            {
                "source_system": key[0],
                "candidate_directory": key[1],
                "product_id": key[2],
                "sku": str(row["sku"]),
                "product_title": str(row["product_title"]),
                "match_type": key[3],
                "match_status": str(row["match_status"]),
                "image_count": 0,
                "hashes_complete": True,
                "license_status": "unknown",
                "reason_codes": set(),
            },
        )
        group["image_count"] = int(group["image_count"]) + 1
        group["hashes_complete"] = bool(group["hashes_complete"]) and bool(
            row["sha256"]
        ) and row["validation_status"] != "not_inspected"
        reasons = group["reason_codes"]
        assert isinstance(reasons, set)
        reasons.update(json.loads(row["file_reasons"]))
        reasons.update(json.loads(row["match_reasons"]))

    output_rows = []
    for key in sorted(groups):
        group = groups[key]
        output_rows.append(
            {
                **group,
                "hashes_complete": "true" if group["hashes_complete"] else "false",
                "reason_codes": ";".join(sorted(group["reason_codes"])),
            }
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    return len(output_rows)


def write_scan_summary(
    store,
    outcome,
    path: Path,
    mode: str,
    products_sha256: str,
) -> dict:
    """Write a stable summary whose counts come from the final SQLite state."""

    connection = store._connection
    total_files = _count(connection, "SELECT COUNT(*) FROM files")
    active_files = _count(connection, "SELECT COUNT(*) FROM files WHERE active=1")
    matched_files = _count(
        connection,
        "SELECT COUNT(DISTINCT files.file_id) FROM files JOIN matches USING(file_id) "
        "WHERE files.active=1",
    )
    active_matches = _count(
        connection,
        "SELECT COUNT(*) FROM files JOIN matches USING(file_id) WHERE files.active=1",
    )
    candidate_groups = _count(
        connection,
        "SELECT COUNT(*) FROM (SELECT 1 FROM files JOIN matches USING(file_id) "
        "WHERE files.active=1 GROUP BY files.source_system, files.candidate_directory, "
        "matches.product_id, matches.match_type)",
    )
    partition_counts = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM partitions GROUP BY status"
        ).fetchall()
    }
    failed = partition_counts.get("failed", 0)
    errors = [
        {
            "source_system": str(row["source_system"]),
            "candidate_directory": str(row["candidate_directory"]),
            "error": str(row["error"]),
        }
        for row in connection.execute(
            "SELECT roots.source_system, partitions.relative_path AS candidate_directory, "
            "partitions.error FROM partitions JOIN roots USING(root_id) "
            "WHERE partitions.error<>'' ORDER BY roots.source_system, partitions.relative_path"
        ).fetchall()
    ]
    identity_row = connection.execute(
        "SELECT value FROM scan_meta WHERE key='identity'"
    ).fetchone()
    identity = json.loads(identity_row["value"])
    roots = [
        (str(root["source_system"]), str(root["path"]))
        for root in json.loads(identity["roots_json"])
    ]
    path = Path(path)
    resume_argv = [
        "tmall-materials",
        "index-assets",
        "--products",
        str(identity["products_path"]),
    ]
    for source_system, root_path in roots:
        resume_argv.extend(("--root", f"{source_system}={root_path}"))
    resume_argv.extend(
        (
            "--output",
            str(path.parent.resolve()),
            "--partition-depth",
            str(identity["partition_depth"]),
            "--checkpoint-size",
            str(identity["checkpoint_size"]),
            "--resume",
        )
    )
    database = {
        "roots": _count(connection, "SELECT COUNT(*) FROM roots"),
        "partitions": sum(partition_counts.values()),
        "partition_status": dict(sorted(partition_counts.items())),
        "total_files": total_files,
        "active_files": active_files,
        "inactive_files": total_files - active_files,
        "matched_files": matched_files,
        "active_matches": active_matches,
        "match_candidates": candidate_groups,
    }
    summary = {
        "schema_version": 1,
        "mode": mode,
        "products_sha256": products_sha256,
        "complete": bool(outcome.complete) and failed == 0,
        "partial_failure": bool(outcome.partial_failure) or failed > 0,
        "discovered": active_files,
        "indexed": total_files,
        "matched": matched_files,
        "failed": failed,
        "database": database,
        "errors": errors,
        "elapsed_seconds": outcome.elapsed_seconds,
        "resume_command": subprocess.list2cmdline(resume_argv),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _count(connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


class IncrementalAssetIndexer:
    def __init__(
        self,
        store: AssetIndexStore,
        matcher,
        roots: Sequence[NamedRoot],
        options: IndexOptions = IndexOptions(),
    ) -> None:
        self.store = store
        self.matcher = matcher
        self.roots = tuple(roots)
        self.options = options
        self._scan_id: str | None = None
        self._new_started = False

    @staticmethod
    def partition_id_for(source_system: str, relative_partition: Path) -> str:
        raw = f"{source_system}\0{relative_partition.as_posix()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def run(self, mode: Literal["new", "resume", "refresh"]) -> ScanOutcome:
        if mode not in {"new", "resume", "refresh"}:
            raise ValueError(f"unsupported scan mode: {mode}")
        scan_was_started = self.store.scan_started()
        if mode == "new":
            if self._new_started:
                raise ValueError("new mode requires a new index scan")
            scan_id = uuid.uuid4().hex
            if not self.store.begin_scan("new", scan_id):
                raise ValueError("new mode requires a new index scan")
            effective_mode = "new"
        elif mode == "refresh":
            scan_id = uuid.uuid4().hex
            if not self.store.begin_scan("refresh", scan_id):
                raise ValueError("an active index scan already requires resume")
            effective_mode = "refresh"
        else:
            active_scan = self.store.active_scan()
            if active_scan is None:
                if not scan_was_started:
                    raise ValueError("resume requires an active or completed index scan")
                return ScanOutcome(
                    complete=True,
                    partial_failure=False,
                    discovered=0,
                    indexed=0,
                    matched=0,
                    failed=0,
                    elapsed_seconds=0.0,
                )
            scan_id, effective_mode = active_scan
        self._scan_id = scan_id

        started = time.perf_counter()
        discovery = self._discover()
        partition_keys = set(discovery.candidates) | set(discovery.errors)
        root_ids = {
            source_system: self.store.upsert_root(source_system, str(root))
            for source_system, root in discovery.roots.items()
        }
        historical_records: dict[tuple[str, str], dict[str, object]] = {}
        if mode in {"resume", "refresh"}:
            for source_system, root_id in root_ids.items():
                for record in self.store.partitions_for_root(root_id):
                    key = (source_system, str(record["relative_path"]))
                    historical_records[key] = record
                    partition_keys.add(key)

            discovered_errors = tuple(discovery.errors.items())
            for historical_key in historical_records:
                source_system, relative_partition = historical_key
                historical_parts = self._clean_parts(Path(relative_partition))
                for error_key, errors in discovered_errors:
                    error_source, error_partition = error_key
                    error_parts = self._clean_parts(Path(error_partition))
                    if (
                        error_source == source_system
                        and len(error_parts) < len(historical_parts)
                        and historical_parts[: len(error_parts)] == error_parts
                    ):
                        discovery.errors.setdefault(historical_key, []).extend(errors)

        registrations: dict[tuple[str, str], str] = {}
        records: dict[tuple[str, str], dict[str, object]] = {}
        for source_system, relative_partition in sorted(partition_keys):
            root_id = root_ids[source_system]
            store_partition_id = self.store.upsert_partition(root_id, relative_partition)
            registrations[(source_system, relative_partition)] = store_partition_id
            records[(source_system, relative_partition)] = historical_records.get(
                (source_system, relative_partition),
                self.store.partition_record(store_partition_id),
            )

        indexed = matched = failed = 0
        for key in sorted(partition_keys):
            partition_id = registrations[key]
            prior = records[key]
            if (
                mode == "resume"
                and prior["status"] == "completed"
                and prior["completed_scan_id"] == scan_id
            ):
                continue

            candidates = discovery.candidates.get(key, [])
            errors = discovery.errors.get(key, [])
            start_offset = 0
            if (
                mode == "resume"
                and prior["current_scan_id"] == scan_id
                and prior["status"] in {"in_progress", "failed"}
            ):
                start_offset = int(prior["processed_count"])
            try:
                self.store.checkpoint_files(
                    partition_id, scan_id, (), count_progress=False
                )
                for offset in range(
                    start_offset, len(candidates), self.options.checkpoint_size
                ):
                    batch = candidates[offset : offset + self.options.checkpoint_size]
                    file_ids = self.store.checkpoint_files(
                        partition_id,
                        scan_id,
                        tuple(candidate.indexed_file for candidate in batch),
                        count_progress=False,
                    )
                    indexed += len(batch)
                    for candidate, file_id in zip(batch, file_ids):
                        matches = self.matcher.match(candidate.relative_path)
                        if not matches:
                            continue
                        matched += 1
                        persisted = self.store.file_record(file_id)
                        if (
                            persisted["validation_status"] != "not_inspected"
                            and self.store.matches_for(file_id)
                        ):
                            continue
                        primary = matches[0]
                        inspected = inspect_asset(
                            candidate.absolute_path,
                            primary.product_id,
                            "unknown",
                            source_system=key[0],
                            sku=primary.sku,
                        )
                        self.store.update_file_inspection(
                            file_id,
                            sha256=inspected.sha256,
                            width=inspected.width,
                            height=inspected.height,
                            validation_status=inspected.validation_status,
                            reason_codes=inspected.reason_codes,
                        )
                        self.store.replace_file_matches(
                            file_id,
                            tuple(
                                PathMatchRecord(
                                    product_id=match.product_id,
                                    sku=match.sku,
                                    product_title=match.product_title,
                                    match_type=match.match_type,
                                    match_status=match.match_status,
                                    reason_codes=match.reason_codes,
                                )
                                for match in matches
                            ),
                        )
                    self.store.advance_partition_progress(
                        partition_id, scan_id, len(batch)
                    )
                if errors:
                    raise OSError("; ".join(errors))
                self.store.complete_partition(partition_id)
                if effective_mode == "refresh":
                    self.store.mark_partition_missing_files_inactive(partition_id, scan_id)
            except KeyboardInterrupt:
                raise
            except OSError as error:
                self.store.fail_partition(partition_id, str(error))
                failed += 1

        self._scan_id = scan_id
        if mode == "new":
            self._new_started = True
        elapsed = time.perf_counter() - started
        if failed == 0:
            self.store.clear_active_scan(scan_id)
        return ScanOutcome(
            complete=failed == 0,
            partial_failure=failed > 0,
            discovered=discovery.discovered,
            indexed=indexed,
            matched=matched,
            failed=failed,
            elapsed_seconds=elapsed,
        )

    def _discover(self) -> _Discovery:
        discovery = _Discovery(candidates={}, errors={}, roots={})
        for named_root in self.roots:
            root = named_root.path.resolve()
            discovery.roots[named_root.source_system] = root
            self._walk_directory(
                discovery,
                named_root.source_system,
                root,
                root,
                Path("."),
            )
        return discovery

    def _walk_directory(
        self,
        discovery: _Discovery,
        source_system: str,
        root: Path,
        directory: Path,
        relative_directory: Path,
    ) -> None:
        if len(self._clean_parts(relative_directory)) == self.options.partition_depth:
            self._partition_candidates(discovery, source_system, relative_directory)
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda entry: entry.name.casefold())
        except OSError as error:
            self._record_error(discovery, source_system, relative_directory, error)
            return

        for entry in entries:
            relative_path = relative_directory / entry.name
            try:
                if entry.is_symlink():
                    continue
                metadata = entry.stat(follow_symlinks=False)
                if getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
                    continue
                resolved = Path(entry.path).resolve()
                if not resolved.is_relative_to(root):
                    self._record_error(
                        discovery,
                        source_system,
                        relative_path.parent,
                        OSError("resolved path escapes declared root"),
                    )
                    continue
                if entry.is_dir(follow_symlinks=False):
                    self._walk_directory(
                        discovery,
                        source_system,
                        root,
                        resolved,
                        relative_path,
                    )
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                extension = Path(entry.name).suffix.casefold()
                if extension not in IMAGE_EXTENSIONS:
                    continue
                relative_path = Path(*self._clean_parts(relative_path))
                partition = self._relative_partition(relative_path.parent)
                key = (source_system, partition.as_posix())
                discovery.candidates.setdefault(key, []).append(
                    _Candidate(
                        indexed_file=IndexedFile(
                            source_system=source_system,
                            relative_path=relative_path.as_posix(),
                            absolute_path=str(resolved),
                            extension=extension,
                            size_bytes=metadata.st_size,
                            mtime_ns=metadata.st_mtime_ns,
                            candidate_directory=partition.as_posix(),
                        ),
                        relative_path=relative_path,
                        absolute_path=resolved,
                    )
                )
                discovery.discovered += 1
            except OSError as error:
                self._record_error(
                    discovery,
                    source_system,
                    relative_path.parent,
                    error,
                )

    def _partition_candidates(
        self,
        discovery: _Discovery,
        source_system: str,
        relative_directory: Path,
    ) -> list[_Candidate]:
        partition = self._relative_partition(relative_directory)
        return discovery.candidates.setdefault((source_system, partition.as_posix()), [])

    def _record_error(
        self,
        discovery: _Discovery,
        source_system: str,
        relative_directory: Path,
        error: OSError,
    ) -> None:
        partition = self._relative_partition(relative_directory)
        key = (source_system, partition.as_posix())
        discovery.candidates.setdefault(key, [])
        discovery.errors.setdefault(key, []).append(str(error))

    def _relative_partition(self, relative_directory: Path) -> Path:
        parts = self._clean_parts(relative_directory)
        if not parts:
            return Path(".")
        return Path(*parts[: self.options.partition_depth])

    @staticmethod
    def _clean_parts(path: Path) -> tuple[str, ...]:
        return tuple(part for part in path.parts if part not in {"", "."})
