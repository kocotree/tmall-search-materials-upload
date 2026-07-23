"""Read-only, partitioned orchestration for the incremental asset index."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal, Sequence
import uuid

from .asset_index_store import AssetIndexStore, IndexedFile, PathMatchRecord
from .assets import IMAGE_EXTENSIONS, inspect_asset


_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class NamedRoot:
    source_system: str
    path: Path

    @classmethod
    def parse(cls, value: str) -> NamedRoot:
        source_system, separator, path = value.partition("=")
        if not separator or not _SOURCE_PATTERN.fullmatch(source_system):
            raise ValueError("root must use a non-empty source name followed by '='")
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
        self._known_partitions: dict[tuple[str, str], Path] = {}

    @staticmethod
    def partition_id_for(source_system: str, relative_partition: Path) -> str:
        raw = f"{source_system}\0{relative_partition.as_posix()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def run(self, mode: Literal["new", "resume", "refresh"]) -> ScanOutcome:
        if mode not in {"new", "resume", "refresh"}:
            raise ValueError(f"unsupported scan mode: {mode}")
        if mode == "new" and self._new_started:
            raise ValueError("new mode requires a new index scan")

        started = time.perf_counter()
        discovery = self._discover()
        for key in set(discovery.candidates) | set(discovery.errors):
            self._known_partitions[key] = discovery.roots[key[0]]
        partition_keys = set(discovery.candidates) | set(discovery.errors)
        if mode in {"resume", "refresh"}:
            partition_keys |= set(self._known_partitions)

        registrations: dict[tuple[str, str], str] = {}
        records: dict[tuple[str, str], dict[str, object]] = {}
        for source_system, relative_partition in sorted(partition_keys):
            root = discovery.roots.get(source_system) or self._known_partitions[
                (source_system, relative_partition)
            ]
            root_id = self.store.upsert_root(source_system, str(root))
            store_partition_id = self.store.upsert_partition(root_id, relative_partition)
            registrations[(source_system, relative_partition)] = store_partition_id
            records[(source_system, relative_partition)] = self.store.partition_record(
                store_partition_id
            )

        scan_id = self._select_scan_id(mode, records)
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
            try:
                self.store.checkpoint_files(partition_id, scan_id, ())
                for offset in range(0, len(candidates), self.options.checkpoint_size):
                    batch = candidates[offset : offset + self.options.checkpoint_size]
                    file_ids = self.store.checkpoint_files(
                        partition_id,
                        scan_id,
                        tuple(candidate.indexed_file for candidate in batch),
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
                if errors:
                    raise OSError("; ".join(errors))
                self.store.complete_partition(partition_id)
                if mode == "refresh":
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
        return ScanOutcome(
            complete=failed == 0,
            partial_failure=failed > 0,
            discovered=discovery.discovered,
            indexed=indexed,
            matched=matched,
            failed=failed,
            elapsed_seconds=elapsed,
        )

    def _select_scan_id(
        self,
        mode: str,
        records: dict[tuple[str, str], dict[str, object]],
    ) -> str:
        if mode == "refresh":
            return uuid.uuid4().hex
        if mode == "new":
            if any(record["current_scan_id"] is not None for record in records.values()):
                raise ValueError("new mode requires an unused index database")
            return uuid.uuid4().hex
        if self._scan_id is not None:
            return self._scan_id
        scan_ids = {
            str(record["current_scan_id"])
            for record in records.values()
            if record["current_scan_id"] is not None
        }
        if len(scan_ids) != 1:
            raise ValueError("resume requires exactly one incomplete scan identity")
        return scan_ids.pop()

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
