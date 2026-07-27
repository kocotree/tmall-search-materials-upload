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
from typing import Iterable, Iterator, Literal, Mapping, Sequence
import uuid

from .asset_index_store import AssetIndexStore, IndexedFile, PathMatchRecord
from .assets import IMAGE_EXTENSIONS, inspect_asset
from .models import ProductRecord


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
    scan_id: str = ""


@dataclass(frozen=True)
class _Candidate:
    indexed_file: IndexedFile
    relative_path: Path
    absolute_path: Path


@dataclass(frozen=True)
class _FileFailure:
    relative_path: Path
    code: str
    detail: str


@dataclass(frozen=True)
class _DirectoryCandidate:
    relative_path: Path
    absolute_path: Path


@dataclass(frozen=True)
class _DirectoryFailure:
    relative_path: Path
    detail: str


def bind_confirmed_folder_matches(
    store: AssetIndexStore,
    products: Sequence[ProductRecord],
    decisions: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Bind every active file under an indexed root to a reviewed product.

    Folder decisions are authoritative here. The indexed root path must match
    exactly one confirmed decision, so an already-reviewed folder is never
    sent back through heuristic product-name matching.
    """

    products_by_id = {str(product.product_id): product for product in products}
    confirmed_by_path: dict[str, Mapping[str, object]] = {}
    for decision in decisions:
        state = str(decision.get("decision", "")).strip()
        if state != "confirmed":
            continue
        folder_path = str(decision.get("folder_path", "")).strip()
        product_id = str(decision.get("product_id", "")).strip()
        if not folder_path or not product_id:
            raise ValueError("confirmed folder decisions require folder_path and product_id")
        if product_id not in products_by_id:
            raise ValueError(f"confirmed folder product is absent from products CSV: {product_id}")
        path_key = os.path.normcase(os.path.normpath(folder_path))
        if path_key in confirmed_by_path:
            raise ValueError(f"confirmed folder path is duplicated: {folder_path}")
        confirmed_by_path[path_key] = decision

    if not confirmed_by_path:
        raise ValueError("at least one confirmed folder decision is required")

    bound_roots = 0
    bound_files = 0
    inspection_failures = 0
    for root in store.root_records():
        root_path = str(root["root_path"])
        decision = confirmed_by_path.get(
            os.path.normcase(os.path.normpath(root_path))
        )
        if decision is None:
            raise ValueError(
                f"indexed root has no matching confirmed folder decision: {root_path}"
            )
        product_id = str(decision["product_id"]).strip()
        product = products_by_id[product_id]
        match = PathMatchRecord(
            product_id=product_id,
            sku=str(product.sku),
            product_title=str(product.title),
            match_type="name_candidate",
            match_status="confirmed",
            reason_codes=("CONFIRMED_FOLDER_BINDING",),
        )
        files = store.active_files_for_source(str(root["source_system"]))
        for file_record in files:
            file_id = int(file_record["file_id"])
            try:
                inspected = inspect_asset(
                    Path(str(file_record["absolute_path"])),
                    product_id,
                    "confirmed",
                    source_system=str(root["source_system"]),
                    sku=str(product.sku),
                )
                store.update_file_inspection(
                    file_id,
                    sha256=inspected.sha256,
                    width=inspected.width,
                    height=inspected.height,
                    validation_status=inspected.validation_status,
                    reason_codes=inspected.reason_codes,
                )
            except Exception:
                inspection_failures += 1
                store.update_file_inspection(
                    file_id,
                    sha256="",
                    width=None,
                    height=None,
                    validation_status="inspection_failed",
                    reason_codes=("FILE_INSPECTION_ERROR",),
                )
            store.replace_file_matches(file_id, (match,))
        bound_roots += 1
        bound_files += len(files)

    store.refresh_match_statistics()
    return {
        "bound_roots": bound_roots,
        "bound_files": bound_files,
        "inspection_failures": inspection_failures,
    }


class _PartitionWriter:
    """Bounded, one-pass persistence for a single discovered partition."""

    def __init__(
        self,
        indexer: IncrementalAssetIndexer,
        source_system: str,
        root_id: int,
        relative_partition: Path,
        scan_id: str,
        mode: str,
        historical: dict[str, dict[str, object]],
        encountered: set[str],
        *,
        force: bool,
    ) -> None:
        self.indexer = indexer
        self.source_system = source_system
        self.root_id = root_id
        self.relative_partition = relative_partition
        self.relative_key = relative_partition.as_posix()
        self.scan_id = scan_id
        self.encountered = encountered
        prior = historical.get(self.relative_key)
        self.skipped = prior is not None and indexer._completed_for_resume(
            prior, scan_id, mode
        )
        self.partition_id: str | None = None
        self.batch: list[_Candidate] = []
        self.partition_errors: list[str] = []
        if self.skipped:
            encountered.add(self.relative_key)
        elif force:
            self._start()

    def _start(self) -> str:
        if self.partition_id is None:
            self.partition_id = self.indexer.store.upsert_partition(
                self.root_id, self.relative_key
            )
            self.encountered.add(self.relative_key)
            self.indexer.store.restart_partition(
                self.partition_id, self.scan_id
            )
        return self.partition_id

    def add_candidate(self, candidate: _Candidate) -> None:
        if self.skipped:
            return
        self._start()
        self.batch.append(candidate)
        if len(self.batch) >= self.indexer.options.checkpoint_size:
            self.flush()

    def add_file_failure(self, failure: _FileFailure) -> None:
        if self.skipped:
            return
        partition_id = self._start()
        self.indexer.store.record_file_error(
            partition_id,
            self.scan_id,
            source_system=self.source_system,
            relative_path=failure.relative_path.as_posix(),
            code=failure.code,
            detail=failure.detail,
        )

    def note_partition_error(self, detail: str) -> None:
        if self.skipped:
            return
        self._start()
        if detail not in self.partition_errors:
            self.partition_errors.append(detail)

    def flush(self) -> None:
        if self.skipped or not self.batch:
            return
        assert self.partition_id is not None
        self.indexer._process_batch(
            self.source_system,
            self.root_id,
            self.partition_id,
            self.scan_id,
            tuple(self.batch),
        )
        self.batch.clear()

    def finish(self) -> None:
        if self.skipped or self.partition_id is None:
            return
        self.flush()
        if self.partition_errors:
            self.indexer.store.fail_partition(
                self.partition_id,
                "; ".join(self.partition_errors),
                "PARTITION_ENUMERATION_ERROR",
            )
        else:
            self.indexer.store.finalize_partition(
                self.partition_id, self.scan_id
            )
        self.indexer.store.sync_root_statistics(self.root_id, self.scan_id)


def write_match_candidates(store, path: Path) -> int:
    """Write stable, review-only match groups from the durable index."""

    rows = store.match_candidate_records()
    groups: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for row in rows:
        key = (
            row.source_system,
            row.candidate_directory,
            row.product_id,
            row.match_type,
        )
        group = groups.setdefault(
            key,
            {
                "source_system": key[0],
                "candidate_directory": key[1],
                "product_id": key[2],
                "sku": row.sku,
                "product_title": row.product_title,
                "match_type": key[3],
                "match_status": row.match_status,
                "image_count": 0,
                "hashes_complete": True,
                "license_status": "unknown",
                "reason_codes": set(),
            },
        )
        group["image_count"] = int(group["image_count"]) + 1
        group["hashes_complete"] = bool(group["hashes_complete"]) and bool(
            row.sha256
        ) and row.validation_status != "not_inspected"
        reasons = group["reason_codes"]
        assert isinstance(reasons, set)
        reasons.update(row.file_reason_codes)
        reasons.update(row.match_reason_codes)

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
    product_validation: dict[str, object] | None = None,
) -> dict:
    """Write a stable summary whose counts come from the final SQLite state."""

    database = store.database_statistics()
    scan_id = outcome.scan_id or store.latest_scan_id()
    scan = (
        store.scan_statistics(scan_id)
        if scan_id is not None
        else {
            "scan_id": "",
            "totals": {
                "discovered": 0,
                "indexed": 0,
                "matched": 0,
                "failed": 0,
            },
            "roots": [],
            "errors": [],
        }
    )
    totals = scan["totals"]
    failed = int(totals["failed"])
    identity = store.index_identity()
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
    summary = {
        "schema_version": 1,
        "scan_id": scan["scan_id"],
        "mode": mode,
        "products_sha256": products_sha256,
        "product_validation": dict(product_validation or {}),
        "complete": bool(outcome.complete),
        "partial_failure": bool(outcome.partial_failure),
        "discovered": int(totals["discovered"]),
        "indexed": int(totals["indexed"]),
        "matched": int(totals["matched"]),
        "failed": failed,
        "database": database,
        "roots": scan["roots"],
        "errors": scan["errors"],
        "elapsed_seconds": outcome.elapsed_seconds,
        "resume_command": subprocess.list2cmdline(resume_argv),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


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
                    scan_id=self.store.latest_scan_id() or "",
                )
            scan_id, effective_mode = active_scan
        self._scan_id = scan_id

        started = time.perf_counter()
        root_ids = {
            named_root.source_system: self.store.upsert_root(
                named_root.source_system, str(named_root.path.resolve())
            )
            for named_root in self.roots
        }
        for root_id in root_ids.values():
            self.store.prepare_root(root_id, scan_id)
        for named_root in self.roots:
            source_system = named_root.source_system
            root = named_root.path.resolve()
            root_id = root_ids[source_system]
            historical = {
                str(record["relative_path"]): record
                for record in self.store.partitions_for_root(root_id)
            }
            encountered: set[str] = set()
            self.store.start_root(root_id, scan_id)
            try:
                self._walk_scaffold(
                    source_system,
                    root_id,
                    root,
                    root,
                    Path("."),
                    scan_id,
                    mode,
                    historical,
                    encountered,
                )
            except KeyboardInterrupt:
                raise
            except OSError as error:
                for relative_partition, prior in sorted(historical.items()):
                    if self._completed_for_resume(prior, scan_id, mode):
                        continue
                    self._process_partition(
                        source_system,
                        root_id,
                        Path(relative_partition),
                        (),
                        [str(error)],
                        [],
                        scan_id,
                        mode,
                        historical,
                        encountered,
                        force=True,
                    )
                self.store.fail_root(
                    root_id, scan_id, "ROOT_ENUMERATION_ERROR", str(error)
                )
                continue

            for relative_partition, prior in sorted(historical.items()):
                if relative_partition in encountered:
                    continue
                if self._completed_for_resume(prior, scan_id, mode):
                    continue
                self._process_partition(
                    source_system,
                    root_id,
                    Path(relative_partition),
                    (),
                    [],
                    [],
                    scan_id,
                    mode,
                    historical,
                    encountered,
                    force=True,
                )
            self.store.complete_root(root_id, scan_id)

        self._scan_id = scan_id
        if mode == "new":
            self._new_started = True
        elapsed = time.perf_counter() - started
        scan = self.store.scan_statistics(scan_id)
        roots = scan["roots"]
        partial_failure = any(
            root["status"] in {"failed", "partial_failure"} for root in roots
        )
        if not partial_failure:
            self.store.clear_active_scan(scan_id)
        totals = scan["totals"]
        return ScanOutcome(
            complete=not partial_failure,
            partial_failure=partial_failure,
            discovered=int(totals["discovered"]),
            indexed=int(totals["indexed"]),
            matched=int(totals["matched"]),
            failed=int(totals["failed"]),
            elapsed_seconds=elapsed,
            scan_id=scan_id,
        )

    def _walk_scaffold(
        self,
        source_system: str,
        root_id: int,
        root: Path,
        directory: Path,
        relative_directory: Path,
        scan_id: str,
        mode: str,
        historical: dict[str, dict[str, object]],
        encountered: set[str],
    ) -> None:
        depth = len(self._clean_parts(relative_directory))
        relative_partition = self._relative_partition(relative_directory)
        relative_key = relative_partition.as_posix()
        prior = historical.get(relative_key)
        if depth >= self.options.partition_depth and prior is not None:
            if self._completed_for_resume(prior, scan_id, mode):
                encountered.add(relative_key)
                return
        writer = _PartitionWriter(
            self,
            source_system,
            root_id,
            relative_partition,
            scan_id,
            mode,
            historical,
            encountered,
            force=depth >= self.options.partition_depth
            or relative_key in historical,
        )
        if depth >= self.options.partition_depth:
            self._scan_partition_directory(
                source_system,
                root,
                directory,
                relative_directory,
                writer,
            )
            writer.finish()
            return

        try:
            with os.scandir(directory) as scanner:
                for entry in scanner:
                    discovered = self._classify_entry(
                        source_system,
                        root,
                        entry,
                        relative_directory,
                    )
                    if isinstance(discovered, _Candidate):
                        writer.add_candidate(discovered)
                    elif isinstance(discovered, _FileFailure):
                        writer.add_file_failure(discovered)
                    elif isinstance(discovered, _DirectoryFailure):
                        writer.flush()
                        self._fail_partition_scope(
                            source_system,
                            root_id,
                            discovered.relative_path,
                            discovered.detail,
                            scan_id,
                            mode,
                            historical,
                            encountered,
                        )
                    elif isinstance(discovered, _DirectoryCandidate):
                        writer.flush()
                        self._walk_scaffold(
                            source_system,
                            root_id,
                            root,
                            discovered.absolute_path,
                            discovered.relative_path,
                            scan_id,
                            mode,
                            historical,
                            encountered,
                        )
        except OSError as error:
            if depth == 0:
                raise
            writer.note_partition_error(str(error))
            writer.finish()
            self._fail_unencountered_descendants(
                source_system,
                root_id,
                relative_directory,
                relative_key,
                str(error),
                scan_id,
                mode,
                historical,
                encountered,
            )
            return
        writer.finish()

    def _scan_partition_directory(
        self,
        source_system: str,
        root: Path,
        directory: Path,
        relative_directory: Path,
        writer: _PartitionWriter,
    ) -> None:
        try:
            with os.scandir(directory) as scanner:
                for entry in scanner:
                    discovered = self._classify_entry(
                        source_system,
                        root,
                        entry,
                        relative_directory,
                    )
                    if isinstance(discovered, _Candidate):
                        writer.add_candidate(discovered)
                    elif isinstance(discovered, _FileFailure):
                        writer.add_file_failure(discovered)
                    elif isinstance(discovered, _DirectoryFailure):
                        writer.note_partition_error(discovered.detail)
                    elif isinstance(discovered, _DirectoryCandidate):
                        writer.flush()
                        self._scan_partition_directory(
                            source_system,
                            root,
                            discovered.absolute_path,
                            discovered.relative_path,
                            writer,
                        )
        except OSError as error:
            writer.note_partition_error(str(error))

    def _classify_entry(
        self,
        source_system: str,
        root: Path,
        entry: os.DirEntry[str],
        relative_directory: Path,
    ) -> _Candidate | _DirectoryCandidate | _DirectoryFailure | _FileFailure | None:
        relative_path = relative_directory / entry.name
        clean_relative = Path(*self._clean_parts(relative_path))
        is_directory = False
        try:
            if entry.is_symlink():
                return None
            is_directory = entry.is_dir(follow_symlinks=False)
            metadata = entry.stat(follow_symlinks=False)
            if getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
                return None
            is_file = (
                False
                if is_directory
                else entry.is_file(follow_symlinks=False)
            )
            resolved = Path(entry.path).resolve()
        except (OSError, RuntimeError) as error:
            if is_directory:
                return _DirectoryFailure(clean_relative, str(error))
            return _FileFailure(
                clean_relative,
                "FILE_STAT_ERROR",
                str(error),
            )

        if not resolved.is_relative_to(root):
            if is_directory:
                return _DirectoryFailure(
                    clean_relative,
                    "resolved path escapes declared root",
                )
            return _FileFailure(
                clean_relative,
                "PATH_OUTSIDE_ROOT",
                "resolved path escapes declared root",
            )
        if is_directory:
            return _DirectoryCandidate(clean_relative, resolved)
        if not is_file:
            return None
        extension = Path(entry.name).suffix.casefold()
        if extension not in IMAGE_EXTENSIONS:
            return None
        return self._candidate(
            source_system,
            clean_relative,
            resolved,
            extension,
            metadata.st_size,
            metadata.st_mtime_ns,
        )

    def _fail_unencountered_descendants(
        self,
        source_system: str,
        root_id: int,
        relative_directory: Path,
        current_partition: str,
        detail: str,
        scan_id: str,
        mode: str,
        historical: dict[str, dict[str, object]],
        encountered: set[str],
    ) -> None:
        prefix = self._clean_parts(relative_directory)
        for relative_partition, prior in sorted(historical.items()):
            if (
                relative_partition == current_partition
                or relative_partition in encountered
                or self._clean_parts(Path(relative_partition))[: len(prefix)]
                != prefix
                or self._completed_for_resume(prior, scan_id, mode)
            ):
                continue
            self._process_partition(
                source_system,
                root_id,
                Path(relative_partition),
                (),
                [detail],
                [],
                scan_id,
                mode,
                historical,
                encountered,
                force=True,
            )

    def _candidate(
        self,
        source_system: str,
        relative_path: Path,
        resolved: Path,
        extension: str,
        size_bytes: int,
        mtime_ns: int,
    ) -> _Candidate:
        relative_path = Path(*self._clean_parts(relative_path))
        partition = self._relative_partition(relative_path.parent)
        return _Candidate(
            indexed_file=IndexedFile(
                source_system=source_system,
                relative_path=relative_path.as_posix(),
                absolute_path=str(resolved),
                extension=extension,
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                candidate_directory=partition.as_posix(),
            ),
            relative_path=relative_path,
            absolute_path=resolved,
        )

    def _process_partition(
        self,
        source_system: str,
        root_id: int,
        relative_partition: Path,
        candidates: Iterable[_Candidate],
        errors: list[str],
        file_failures: list[_FileFailure],
        scan_id: str,
        mode: str,
        historical: dict[str, dict[str, object]],
        encountered: set[str],
        *,
        force: bool,
        consume_when_skipped: bool = False,
    ) -> None:
        relative_key = relative_partition.as_posix()
        prior = historical.get(relative_key)
        if prior is not None and self._completed_for_resume(
            prior, scan_id, mode
        ):
            encountered.add(relative_key)
            if consume_when_skipped:
                for _ in candidates:
                    pass
            return

        iterator = iter(candidates)
        partition_id: str | None = None
        if force:
            partition_id = self.store.upsert_partition(root_id, relative_key)
            encountered.add(relative_key)
            self.store.restart_partition(partition_id, scan_id)
        try:
            first = next(iterator)
        except StopIteration:
            first = None
        if first is None and not errors and not file_failures and not force:
            return

        if partition_id is None:
            partition_id = self.store.upsert_partition(root_id, relative_key)
            encountered.add(relative_key)
            self.store.restart_partition(partition_id, scan_id)
        batch: list[_Candidate] = []
        if first is not None:
            batch.append(first)
            if len(batch) >= self.options.checkpoint_size:
                self._process_batch(
                    source_system,
                    root_id,
                    partition_id,
                    scan_id,
                    batch,
                )
                batch.clear()
        for candidate in iterator:
            batch.append(candidate)
            if len(batch) >= self.options.checkpoint_size:
                self._process_batch(
                    source_system,
                    root_id,
                    partition_id,
                    scan_id,
                    batch,
                )
                batch.clear()
        if batch:
            self._process_batch(
                source_system,
                root_id,
                partition_id,
                scan_id,
                batch,
            )
        for failure in file_failures:
            self.store.record_file_error(
                partition_id,
                scan_id,
                source_system=source_system,
                relative_path=failure.relative_path.as_posix(),
                code=failure.code,
                detail=failure.detail,
            )
        if errors:
            self.store.fail_partition(
                partition_id,
                "; ".join(errors),
                "PARTITION_ENUMERATION_ERROR",
            )
        else:
            self.store.finalize_partition(partition_id, scan_id)
        self.store.sync_root_statistics(root_id, scan_id)

    def _process_batch(
        self,
        source_system: str,
        root_id: int,
        partition_id: str,
        scan_id: str,
        batch: Sequence[_Candidate],
    ) -> None:
        file_ids = self.store.checkpoint_files(
            partition_id,
            scan_id,
            tuple(candidate.indexed_file for candidate in batch),
            count_progress=False,
        )
        matched = 0
        for candidate, file_id in zip(batch, file_ids):
            matches = self.matcher.match(candidate.relative_path)
            if not matches:
                continue
            persisted = self.store.file_record(file_id)
            if (
                persisted["validation_status"] != "not_inspected"
                and self.store.matches_for(file_id)
            ):
                matched += 1
                continue
            primary = matches[0]
            try:
                inspected = inspect_asset(
                    candidate.absolute_path,
                    primary.product_id,
                    "confirmed",
                    source_system=source_system,
                    sku=primary.sku,
                )
            except Exception as error:
                # This is the per-file inspection boundary: malformed decoder
                # inputs and read/hash failures must not abort sibling files.
                # Process-control exceptions inherit BaseException and escape.
                self.store.update_file_inspection(
                    file_id,
                    sha256="",
                    width=None,
                    height=None,
                    validation_status="inspection_failed",
                    reason_codes=("FILE_INSPECTION_ERROR",),
                )
                self.store.record_file_error(
                    partition_id,
                    scan_id,
                    source_system=source_system,
                    relative_path=candidate.relative_path.as_posix(),
                    code="FILE_INSPECTION_ERROR",
                    detail=str(error),
                )
                continue
            matched += 1
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
            partition_id,
            scan_id,
            len(batch),
            discovered=len(batch),
            indexed=len(batch),
            matched=matched,
        )
        self.store.sync_root_statistics(root_id, scan_id)

    def _fail_partition_scope(
        self,
        source_system: str,
        root_id: int,
        relative_directory: Path,
        detail: str,
        scan_id: str,
        mode: str,
        historical: dict[str, dict[str, object]],
        encountered: set[str],
    ) -> None:
        prefix = self._clean_parts(relative_directory)
        affected = [
            relative_partition
            for relative_partition in historical
            if self._clean_parts(Path(relative_partition))[: len(prefix)] == prefix
        ]
        if not affected:
            affected = [self._relative_partition(relative_directory).as_posix()]
        for relative_partition in sorted(affected):
            prior = historical.get(relative_partition)
            if prior is not None and self._completed_for_resume(
                prior, scan_id, mode
            ):
                encountered.add(relative_partition)
                continue
            self._process_partition(
                source_system,
                root_id,
                Path(relative_partition),
                (),
                [detail],
                [],
                scan_id,
                mode,
                historical,
                encountered,
                force=True,
            )

    @staticmethod
    def _completed_for_resume(
        prior: dict[str, object], scan_id: str, mode: str
    ) -> bool:
        return (
            mode == "resume"
            and prior["status"] == "completed"
            and prior["completed_scan_id"] == scan_id
        )

    def _relative_partition(self, relative_directory: Path) -> Path:
        parts = self._clean_parts(relative_directory)
        if not parts:
            return Path(".")
        return Path(*parts[: self.options.partition_depth])

    @staticmethod
    def _clean_parts(path: Path) -> tuple[str, ...]:
        return tuple(part for part in path.parts if part not in {"", "."})
