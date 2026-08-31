"""Lazy, task-local image candidates from user-confirmed folders."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from .asset_selection import build_gallery_data
from .assets import IMAGE_EXTENSIONS, build_loaded_image_preview
from .image_compliance import default_image_policy, load_image_preflight_source
from .models import ProductRecord
from .persistence import atomic_write_json


CANDIDATE_STRATEGY_ID = "proportional_task_sample"
CANDIDATE_STRATEGY_VERSION = 5
COVERAGE_LIMIT_REASON = "FOLDER_COVERAGE_LIMIT_EXCEEDED"
NO_SIZE_ELIGIBLE_IMAGES = "NO_SIZE_ELIGIBLE_IMAGES"
PREFERRED_MIN_SIZE_BYTES = 500 * 1024
PREFERRED_MAX_SIZE_BYTES = 15 * 1024 * 1024
GALLERY_CHECKPOINT_SCHEMA_VERSION = 2
GALLERY_CHECKPOINT_POLICY_VERSION = 1
GALLERY_MAX_WORKERS = 8
GALLERY_PROGRESSIVE_PUBLISH_SIZE = 10
GALLERY_CHECKPOINT_FLUSH_ITEMS = 10
GALLERY_CHECKPOINT_FLUSH_SECONDS = 1.0


@dataclass(frozen=True)
class _GalleryRecord:
    folder_id: str
    folder_path: str
    source_system: str
    candidate_directory: str
    relative_path: str
    absolute_path: str
    preview_path: str
    sha256: str
    width: int | None
    height: int | None
    size_bytes: int | None
    validation_status: str
    file_reason_codes: tuple[str, ...]
    product_id: str
    sku: str
    product_title: str
    match_type: str
    match_status: str
    match_reason_codes: tuple[str, ...]


def extract_folder_decisions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        values = payload.get("values")
        payload = (
            values.get("folder_decisions")
            if isinstance(values, dict)
            else payload.get("folder_decisions")
        )
    if not isinstance(payload, list):
        raise ValueError(
            "folder decisions must be a JSON list or an input object "
            "containing values.folder_decisions"
        )
    return [dict(item) for item in payload if isinstance(item, dict)]


def _folder_rank(decision: Mapping[str, Any]) -> tuple[int, str, int, str]:
    path = str(decision.get("folder_path", ""))
    normalized = path.casefold()
    return (
        1 if "未修" in path else 0,
        str(decision.get("source_system", "")).casefold(),
        -len(Path(path).parts),
        normalized,
    )


def _increment_count(counts: dict[str, int] | None, key: str) -> None:
    if counts is not None:
        counts[key] = int(counts.get(key, 0)) + 1


def _iter_images(
    folder: Path,
    *,
    error_counts: dict[str, int] | None = None,
) -> list[Path]:
    """Enumerate image paths with one metadata pass and no directory resolves."""

    images: list[Path] = []
    pending = [(Path(folder), True)]
    while pending:
        current, is_root = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            if is_root:
                raise
            _increment_count(error_counts, "directory_scan_failure_count")
            continue
        try:
            with entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append((Path(entry.path), False))
                        elif (
                            Path(entry.name).suffix.casefold()
                            in IMAGE_EXTENSIONS
                            and entry.is_file(follow_symlinks=False)
                        ):
                            images.append(Path(entry.path))
                    except OSError:
                        _increment_count(
                            error_counts,
                            "entry_metadata_failure_count",
                        )
        except OSError:
            _increment_count(error_counts, "directory_scan_failure_count")
    return sorted(images, key=lambda path: str(path).casefold())


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).casefold()


def _path_is_within(path_key: str, folder_key: str) -> bool:
    try:
        return os.path.commonpath((path_key, folder_key)) == folder_key
    except ValueError:
        return False


def _enumerate_confirmed_folders(
    decisions: Sequence[Mapping[str, Any]],
    *,
    before_root: Callable[[Path], None] | None = None,
) -> tuple[list[tuple[dict[str, Any], list[Path], int]], dict[str, int]]:
    """Traverse overlapping confirmed trees once and preserve folder ownership."""

    ranked = [dict(decision) for decision in sorted(decisions, key=_folder_rank)]
    folders = [
        (
            decision,
            Path(str(decision["folder_path"])),
            _path_key(decision["folder_path"]),
        )
        for decision in ranked
    ]
    unique_folders: dict[str, Path] = {}
    for _decision, folder, key in folders:
        unique_folders.setdefault(key, folder)
    traversal_roots: list[tuple[str, Path]] = []
    for key, folder in sorted(
        unique_folders.items(),
        key=lambda item: (len(Path(item[1]).parts), item[0]),
    ):
        if any(
            _path_is_within(key, root_key)
            for root_key, _root in traversal_roots
        ):
            continue
        traversal_roots.append((key, folder))

    paths_by_folder: list[list[Path]] = [[] for _ in folders]
    raw_counts = [0 for _ in folders]
    seen_paths: set[str] = set()
    enumeration_errors = {
        "directory_scan_failure_count": 0,
        "entry_metadata_failure_count": 0,
    }
    for _root_key, root in traversal_roots:
        if before_root is not None:
            before_root(root)
        for path in _iter_images(root, error_counts=enumeration_errors):
            path_key = _path_key(path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            matching = [
                index
                for index, (_decision, _folder, folder_key) in enumerate(folders)
                if _path_is_within(path_key, folder_key)
            ]
            for index in matching:
                raw_counts[index] += 1
            if matching:
                paths_by_folder[matching[0]].append(path)

    candidates = [
        (
            decision,
            sorted(paths, key=lambda path: str(path).casefold()),
            raw_count,
        )
        for (decision, _folder, _key), paths, raw_count in zip(
            folders,
            paths_by_folder,
            raw_counts,
            strict=True,
        )
    ]
    return candidates, {
        "confirmed_folder_count": len(folders),
        "traversal_root_count": len(traversal_roots),
        "avoided_recursive_scan_count": max(
            len(folders) - len(traversal_roots),
            0,
        ),
        **enumeration_errors,
        "enumeration_failure_count": sum(enumeration_errors.values()),
    }


def _proportional_allocations(
    counts: Sequence[int],
    target: int,
) -> list[int]:
    """Allocate a bounded sample proportionally while representing each folder."""

    allocations, _, _ = _allocation_breakdown(counts, target)
    return allocations


def _allocation_breakdown(
    counts: Sequence[int],
    target: int,
) -> tuple[list[int], list[int], list[int]]:
    """Return total, coverage-base and proportional-remainder allocations."""

    allocations = [0] * len(counts)
    base_allocations = [0] * len(counts)
    proportional_allocations = [0] * len(counts)
    nonempty = [index for index, count in enumerate(counts) if count > 0]
    if target <= 0 or not nonempty:
        return allocations, base_allocations, proportional_allocations
    total = sum(counts)
    if target >= total:
        allocations = list(counts)
        for index in nonempty:
            base_allocations[index] = 1
            proportional_allocations[index] = counts[index] - 1
        return allocations, base_allocations, proportional_allocations

    if target >= len(nonempty):
        for index in nonempty:
            allocations[index] = 1
            base_allocations[index] = 1
        remaining = target - len(nonempty)
        capacities = [max(count - allocations[index], 0) for index, count in enumerate(counts)]
    else:
        remaining = target
        capacities = list(counts)

    added = _bounded_proportional_allocations(capacities, remaining)
    for index, value in enumerate(added):
        allocations[index] += value
        proportional_allocations[index] += value
    return allocations, base_allocations, proportional_allocations


def _bounded_proportional_allocations(
    capacities: Sequence[int],
    target: int,
) -> list[int]:
    """Fill a bounded target proportionally without adding coverage slots."""

    remaining_capacities = [max(int(value), 0) for value in capacities]
    allocations = [0] * len(remaining_capacities)
    remaining = min(max(int(target), 0), sum(remaining_capacities))
    while remaining:
        capacity_total = sum(remaining_capacities)
        if capacity_total <= 0:
            break
        exact = [
            remaining * capacity / capacity_total
            for capacity in remaining_capacities
        ]
        added = [
            min(int(value), remaining_capacities[index])
            for index, value in enumerate(exact)
        ]
        added_total = sum(added)
        if added_total:
            for index, value in enumerate(added):
                allocations[index] += value
                remaining_capacities[index] -= value
            remaining -= added_total
            continue
        ranked = sorted(
            (
                (
                    exact[index] - int(exact[index]),
                    remaining_capacities[index],
                    -index,
                    index,
                )
                for index in range(len(remaining_capacities))
                if remaining_capacities[index] > 0
            ),
            reverse=True,
        )
        for _, _, _, index in ranked[:remaining]:
            allocations[index] += 1
            remaining_capacities[index] -= 1
        remaining = 0
    return allocations


def _tiered_allocation_breakdown(
    preferred_counts: Sequence[int],
    fallback_below_counts: Sequence[int],
    fallback_above_counts: Sequence[int],
    target: int,
) -> dict[str, list[int]]:
    """Allocate coverage first, then preferred, small fallback, and large fallback."""

    lengths = {
        len(preferred_counts),
        len(fallback_below_counts),
        len(fallback_above_counts),
    }
    if len(lengths) != 1:
        raise ValueError("candidate tier counts must have equal lengths")
    preferred = [max(int(value), 0) for value in preferred_counts]
    fallback_below = [max(int(value), 0) for value in fallback_below_counts]
    fallback_above = [max(int(value), 0) for value in fallback_above_counts]
    hard_eligible = [
        preferred_count + below_count + above_count
        for preferred_count, below_count, above_count in zip(
            preferred,
            fallback_below,
            fallback_above,
            strict=True,
        )
    ]
    bounded_target = min(max(int(target), 0), sum(hard_eligible))
    coverage = [0] * len(hard_eligible)
    proportional = [0] * len(hard_eligible)
    preferred_allocations = [0] * len(hard_eligible)
    fallback_below_allocations = [0] * len(hard_eligible)
    fallback_above_allocations = [0] * len(hard_eligible)

    nonempty = [
        index for index, count in enumerate(hard_eligible) if count > 0
    ]
    coverage_indices = nonempty[:bounded_target]
    for index in coverage_indices:
        coverage[index] = 1
        if preferred[index] > 0:
            preferred_allocations[index] = 1
        elif fallback_below[index] > 0:
            fallback_below_allocations[index] = 1
        else:
            fallback_above_allocations[index] = 1

    remaining = bounded_target - len(coverage_indices)
    tier_specs = (
        (preferred, preferred_allocations),
        (fallback_below, fallback_below_allocations),
        (fallback_above, fallback_above_allocations),
    )
    for counts, allocations in tier_specs:
        if remaining <= 0:
            break
        capacities = [
            max(count - allocation, 0)
            for count, allocation in zip(counts, allocations, strict=True)
        ]
        added = _bounded_proportional_allocations(capacities, remaining)
        for index, value in enumerate(added):
            allocations[index] += value
            proportional[index] += value
        remaining -= sum(added)

    allocations = [
        preferred_count + below_count + above_count
        for preferred_count, below_count, above_count in zip(
            preferred_allocations,
            fallback_below_allocations,
            fallback_above_allocations,
            strict=True,
        )
    ]
    if sum(allocations) != bounded_target:
        raise ValueError("tiered candidate allocation did not fill its target")
    return {
        "allocations": allocations,
        "coverage_allocations": coverage,
        "proportional_allocations": proportional,
        "preferred_allocations": preferred_allocations,
        "fallback_below_allocations": fallback_below_allocations,
        "fallback_above_allocations": fallback_above_allocations,
    }


def _sample_paths(
    paths: Sequence[Path],
    count: int,
    *,
    seed: str,
) -> list[Path]:
    stable_paths = sorted(paths, key=lambda path: str(path).casefold())
    if count >= len(stable_paths):
        selected = stable_paths
    else:
        selected = random.Random(seed).sample(stable_paths, count)
    return sorted(selected, key=lambda path: str(path).casefold())


def _sample_and_reserve_paths(
    paths: Sequence[Path],
    count: int,
    *,
    seed: str,
) -> tuple[list[Path], list[Path]]:
    """Return the stable primary sample and a deterministic replacement queue."""

    sampled = _sample_paths(paths, count, seed=seed)
    sampled_keys = {_path_key(path) for path in sampled}
    reserves = [
        path
        for path in sorted(paths, key=lambda path: str(path).casefold())
        if _path_key(path) not in sampled_keys
    ]
    random.Random(f"{seed}\0reserve").shuffle(reserves)
    return sampled, reserves


def _decision_folder_id(decision: Mapping[str, Any]) -> str:
    existing = str(decision.get("folder_id", "")).strip()
    if existing:
        return existing
    source_system = str(decision.get("source_system", "confirmed_folder")).strip()
    folder_path = str(decision.get("folder_path", "")).strip()
    return hashlib.sha256(
        f"{source_system}\0{Path(folder_path).resolve()}".encode("utf-8")
    ).hexdigest()[:24]


@dataclass(frozen=True)
class _InspectionOutcome:
    record: _GalleryRecord
    checkpoint_key: str
    checkpoint_entry: dict[str, Any]
    checkpoint_hit: bool = False
    timings_ms: Mapping[str, float] | None = None


def _checkpoint_key(
    product_id: str,
    decision: Mapping[str, Any],
    path: Path,
) -> str:
    folder_identity = str(decision.get("folder_id", "")).strip() or _path_key(
        str(decision.get("folder_path", ""))
    )
    return hashlib.sha256(
        (
            f"{product_id}\0{decision.get('source_system', '')}\0"
            f"{folder_identity}\0"
            f"{decision.get('folder_path', '')}\0"
            f"{os.path.abspath(path)}"
        ).encode("utf-8")
    ).hexdigest()


def _record_from_checkpoint(payload: Mapping[str, Any]) -> _GalleryRecord:
    values = dict(payload)
    values["file_reason_codes"] = tuple(values.get("file_reason_codes", ()))
    values["match_reason_codes"] = tuple(
        values.get("match_reason_codes", ())
    )
    return _GalleryRecord(**values)


def _read_task_checkpoint(
    checkpoint_path: Path | None,
    identity_sha256: str,
) -> dict[str, Any]:
    empty = {
        "schema_version": GALLERY_CHECKPOINT_SCHEMA_VERSION,
        "policy_version": GALLERY_CHECKPOINT_POLICY_VERSION,
        "identity_sha256": identity_sha256,
        "entries": {},
    }
    if checkpoint_path is None or not checkpoint_path.is_file():
        return empty
    try:
        document = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if (
        not isinstance(document, dict)
        or document.get("schema_version")
        != GALLERY_CHECKPOINT_SCHEMA_VERSION
        or document.get("policy_version")
        != GALLERY_CHECKPOINT_POLICY_VERSION
        or not isinstance(document.get("entries"), dict)
    ):
        return empty
    previous_identity = str(document.get("identity_sha256", ""))
    return {
        "schema_version": GALLERY_CHECKPOINT_SCHEMA_VERSION,
        "policy_version": GALLERY_CHECKPOINT_POLICY_VERSION,
        "identity_sha256": identity_sha256,
        "entries": dict(document["entries"]),
        "_identity_changed": previous_identity != identity_sha256,
    }


def _write_task_checkpoint(
    checkpoint_path: Path | None,
    document: Mapping[str, Any],
) -> None:
    if checkpoint_path is None:
        return
    atomic_write_json(Path(checkpoint_path), document, sort_keys=True)


def _inspect_candidate_once(
    decision: Mapping[str, Any],
    path: Path,
    product: ProductRecord,
    *,
    preview_dir: Path | None,
    checkpoint_entries: Mapping[str, Any],
    source_stat: os.stat_result | None = None,
) -> _InspectionOutcome:
    product_id = str(product.product_id)
    source = Path(os.path.abspath(os.fspath(path)))
    stat = source_stat if source_stat is not None else source.stat()
    key = _checkpoint_key(product_id, decision, source)
    cached = checkpoint_entries.get(key)
    if isinstance(cached, dict):
        record_payload = cached.get("record")
        if (
            int(cached.get("size_bytes", -1)) == int(stat.st_size)
            and int(cached.get("mtime_ns", -1)) == int(stat.st_mtime_ns)
            and isinstance(record_payload, dict)
        ):
            record = _record_from_checkpoint(record_payload)
            preview_ready = not (
                preview_dir is not None
                and record.validation_status == "valid"
                and record.sha256
            ) or Path(record.preview_path).is_file()
            if preview_ready:
                return _InspectionOutcome(
                    record=record,
                    checkpoint_key=key,
                    checkpoint_entry=dict(cached),
                    checkpoint_hit=True,
                    timings_ms={"checkpoint_hit_count": 1.0},
                )

    inspection, decoded, _, load_timings = load_image_preflight_source(
        source,
        policy=default_image_policy(),
        source_stat=stat,
        resolve_source=False,
    )
    if "size_bytes" not in inspection:
        raise OSError(f"image source is not readable: {source}")
    reasons = tuple(
        "ASSET_UNREADABLE" if reason == "SOURCE_UNREADABLE" else str(reason)
        for reason in inspection.get("reason_codes", [])
    )
    fingerprint = str(inspection.get("sha256", ""))
    validation_status = "blocked" if reasons else "valid"
    preview_path = ""
    preview_started = time.perf_counter()
    try:
        if (
            preview_dir is not None
            and validation_status == "valid"
            and fingerprint
            and decoded is not None
        ):
            preview_path = str(
                build_loaded_image_preview(
                    decoded,
                    Path(preview_dir) / f"{fingerprint[:16]}.jpg",
                )
            )
    finally:
        if decoded is not None:
            decoded.close()
    preview_ms = (time.perf_counter() - preview_started) * 1000

    folder_path = str(decision["folder_path"])
    absolute_folder = Path(os.path.abspath(folder_path))
    record = _GalleryRecord(
        folder_id=_decision_folder_id(decision),
        folder_path=folder_path,
        source_system=str(
            decision.get("source_system", "confirmed_folder")
        ),
        candidate_directory=folder_path,
        relative_path=str(source.relative_to(absolute_folder)),
        absolute_path=str(source),
        preview_path=preview_path,
        sha256=fingerprint,
        width=(
            int(inspection["width"])
            if inspection.get("width") is not None
            else None
        ),
        height=(
            int(inspection["height"])
            if inspection.get("height") is not None
            else None
        ),
        size_bytes=int(inspection["size_bytes"]),
        validation_status=validation_status,
        file_reason_codes=reasons,
        product_id=product_id,
        sku=str(product.sku),
        product_title=str(product.title),
        match_type="name_candidate",
        match_status="confirmed",
        match_reason_codes=("CONFIRMED_FOLDER_BINDING",),
    )
    checkpoint_entry = {
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "record": asdict(record),
    }
    return _InspectionOutcome(
        record=record,
        checkpoint_key=key,
        checkpoint_entry=checkpoint_entry,
        timings_ms={
            "source_read_ms": float(load_timings.get("read_ms", 0.0)),
            "sha256_ms": float(load_timings.get("sha256_ms", 0.0)),
            "decode_ms": float(load_timings.get("decode_ms", 0.0)),
            "preview_ms": round(preview_ms, 3),
            "checkpoint_hit_count": 0.0,
        },
    )


def _run_concurrent_window(
    items: Sequence[tuple[int, Mapping[str, Any], Path]],
    worker: Callable[[Mapping[str, Any], Path], _InspectionOutcome],
    *,
    completion_callback: Callable[
        [int, _InspectionOutcome | Exception], None
    ] | None = None,
    ordered_batch_callback: Callable[
        [
            Sequence[tuple[int, Mapping[str, Any], Path]],
            Sequence[_InspectionOutcome | Exception],
        ],
        None,
    ]
    | None = None,
    batch_size: int | None = None,
    max_workers: int = GALLERY_MAX_WORKERS,
) -> list[_InspectionOutcome | Exception]:
    """Run one fixed-concurrency queue and publish ready ordered batches."""

    pending = list(items)
    active: dict[Future[_InspectionOutcome], int] = {}
    results: dict[int, _InspectionOutcome | Exception] = {}
    pending_position = 0
    next_batch_position = 0
    ordered_indices = [index for index, _decision, _path in items]
    if len(set(ordered_indices)) != len(ordered_indices):
        raise ValueError("gallery item indices must be unique")
    if ordered_batch_callback is not None and (
        batch_size is None or batch_size < 1
    ):
        raise ValueError("batch_size must be positive when publishing batches")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while pending_position < len(pending) or active:
            while pending_position < len(pending) and len(active) < max_workers:
                index, decision, path = pending[pending_position]
                pending_position += 1
                future = executor.submit(worker, decision, path)
                active[future] = index
            if not active:
                continue
            completed, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                index = active.pop(future)
                try:
                    results[index] = future.result()
                except Exception as error:  # merged deterministically below
                    results[index] = error
                if completion_callback is not None:
                    completion_callback(index, results[index])
            while pending_position < len(pending) and len(active) < max_workers:
                index, decision, path = pending[pending_position]
                pending_position += 1
                future = executor.submit(worker, decision, path)
                active[future] = index
            while (
                ordered_batch_callback is not None
                and next_batch_position < len(items)
            ):
                batch_end = min(
                    next_batch_position + int(batch_size or 1),
                    len(items),
                )
                batch_items = items[next_batch_position:batch_end]
                batch_indices = [
                    index for index, _decision, _path in batch_items
                ]
                if not all(index in results for index in batch_indices):
                    break
                ordered_batch_callback(
                    batch_items,
                    [results[index] for index in batch_indices],
                )
                next_batch_position = batch_end
    return [results[index] for index, _, _ in items]


def _build_gallery_document(
    records: Sequence[_GalleryRecord],
    status_rows: Sequence[Mapping[str, Any]],
    *,
    confirmed_by_product: Mapping[str, Sequence[Mapping[str, Any]]],
    products_by_id: Mapping[str, ProductRecord],
    candidate_limit: int,
    page_size: int,
    sampling_seed: str,
    discovered: int,
    size_eligible: int,
    size_below_minimum: int,
    size_exceeded: int,
    source_stat_failures: int,
    directory_scan_failures: int,
    entry_metadata_failures: int,
    planned_inspections: int,
    inspected: int,
    inspection_failures: int,
    content_duplicates: int,
    uploaded_history_duplicates: int,
    uploaded_history_checked: bool,
    final_candidates: int,
    per_product: Sequence[Mapping[str, Any]],
    gallery_complete: bool,
    performance: Mapping[str, float | int],
) -> dict[str, Any]:
    data = build_gallery_data(records, status_rows)
    data["remote_dedupe_status"] = (
        "checked" if uploaded_history_checked else "not_available"
    )
    data["requirements"] = [
        item
        for item in data["requirements"]
        if str(item.get("product_id", "")) in confirmed_by_product
    ]
    for requirement in data["requirements"]:
        product_id = str(requirement.get("product_id", ""))
        product = products_by_id.get(product_id)
        if product is None:
            continue
        requirement["product_title"] = str(product.title)
        requirement["sku"] = str(product.sku)
    requirement_products = {
        str(item.get("product_id", ""))
        for item in data["requirements"]
        if isinstance(item, dict)
    }
    for product_id in sorted(set(confirmed_by_product) - requirement_products):
        product = products_by_id[product_id]
        data["requirements"].append(
            {
                "product_id": product_id,
                "sku": str(product.sku),
                "product_title": str(product.title),
                "missing_materials": 0,
            }
        )
    data["candidate_strategy"] = CANDIDATE_STRATEGY_ID
    data["candidate_strategy_version"] = CANDIDATE_STRATEGY_VERSION
    data["candidate_limit"] = candidate_limit
    data["page_size"] = page_size
    data["sampling_seed"] = sampling_seed
    data["gallery_complete"] = gallery_complete
    image_policy = default_image_policy()
    data["candidate_size_preference"] = {
        "hard_min_size_bytes": int(image_policy["min_size_kb"]) * 1024,
        "hard_max_size_bytes": (
            int(image_policy["max_size_mb"]) * 1024 * 1024
        ),
        "preferred_min_size_bytes": PREFERRED_MIN_SIZE_BYTES,
        "preferred_max_size_bytes": PREFERRED_MAX_SIZE_BYTES,
        "fallback_order": ["below_preferred", "above_preferred"],
        "preserve_folder_coverage": True,
    }
    data["sampling_identity_sha256"] = hashlib.sha256(
        (
            f"{CANDIDATE_STRATEGY_ID}\0"
            f"{CANDIDATE_STRATEGY_VERSION}\0{sampling_seed}"
        ).encode("utf-8")
    ).hexdigest()
    candidate_count_by_product: dict[str, int] = {}
    for candidate in data["asset_candidates"]:
        product_id = str(candidate.get("product_id", ""))
        candidate_count_by_product[product_id] = (
            candidate_count_by_product.get(product_id, 0) + 1
        )
    for requirement in data["requirements"]:
        requirement["candidate_count"] = candidate_count_by_product.get(
            str(requirement.get("product_id", "")), 0
        )
    tier_totals = {
        key: sum(int(item.get(key, 0) or 0) for item in per_product)
        for key in (
            "preferred_size_count",
            "fallback_below_preferred_count",
            "fallback_above_preferred_count",
            "preferred_sampled_count",
            "fallback_below_preferred_sampled_count",
            "fallback_above_preferred_sampled_count",
            "fallback_sampled_count",
        )
    }
    data["scan_summary"] = {
        "confirmed_folder_count": sum(
            len(value) for value in confirmed_by_product.values()
        ),
        "discovered_images": discovered,
        "size_eligible_count": size_eligible,
        "size_filtered_count": (
            size_below_minimum + size_exceeded + source_stat_failures
        ),
        "size_below_minimum_count": size_below_minimum,
        "size_exceeded_count": size_exceeded,
        "source_stat_failure_count": source_stat_failures,
        "directory_scan_failure_count": directory_scan_failures,
        "entry_metadata_failure_count": entry_metadata_failures,
        "enumeration_failure_count": (
            directory_scan_failures + entry_metadata_failures
        ),
        **tier_totals,
        "inspected_candidates": inspected,
        "inspection_failures": inspection_failures,
        "discovered_path_count": discovered,
        "planned_inspection_count": planned_inspections,
        "inspected_count": inspected,
        "inspection_failure_count": inspection_failures,
        "content_duplicate_count": content_duplicates,
        "uploaded_history_duplicate_count": uploaded_history_duplicates,
        "final_candidate_count": final_candidates,
        "pending_count": max(
            planned_inspections - inspected - inspection_failures,
            0,
        ),
        "performance": {
            key: round(value, 3) if isinstance(value, float) else value
            for key, value in performance.items()
        },
        "per_product": [dict(item) for item in per_product],
    }
    reason_codes = list(data.get("reason_codes", []))
    if any(
        not bool(item.get("complete_folder_coverage", True))
        for item in per_product
    ) and COVERAGE_LIMIT_REASON not in reason_codes:
        reason_codes.append(COVERAGE_LIMIT_REASON)
    if (
        directory_scan_failures + entry_metadata_failures > 0
        and "PARTIAL_DIRECTORY_ENUMERATION" not in reason_codes
    ):
        reason_codes.append("PARTIAL_DIRECTORY_ENUMERATION")
    data["reason_codes"] = reason_codes
    return data


def build_confirmed_folder_gallery(
    products: Sequence[ProductRecord],
    status_rows: Iterable[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    *,
    candidate_limit: int = 100,
    page_size: int = 30,
    sampling_seed: str = "stable",
    preview_dir: Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    batch_callback: Callable[[dict[str, Any]], None] | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_identity_sha256: str = "",
    uploaded_source_sha256: Iterable[str] | None = None,
    uploaded_history_checked: bool | None = None,
) -> dict[str, Any]:
    """Build a coverage-first tiered sample with task-local batch resume."""

    if not 1 <= candidate_limit <= 100:
        raise ValueError("candidate_limit must be between 1 and 100")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    products_by_id = {str(product.product_id): product for product in products}
    status_rows = list(status_rows)
    checkpoint = _read_task_checkpoint(
        checkpoint_path,
        checkpoint_identity_sha256,
    )
    checkpoint_entries = checkpoint["entries"]

    confirmed_by_product: dict[str, list[dict[str, Any]]] = {}
    for source in decisions:
        decision = dict(source)
        if str(decision.get("decision", "")) != "confirmed":
            continue
        product_id = str(decision.get("product_id", "")).strip()
        folder_path = str(decision.get("folder_path", "")).strip()
        if not product_id or not folder_path:
            raise ValueError(
                "confirmed folder decisions require product_id and folder_path"
            )
        if product_id not in products_by_id:
            raise ValueError(
                f"confirmed folder product is absent from products CSV: {product_id}"
            )
        folder = Path(folder_path)
        if not folder.is_dir():
            raise ValueError(f"confirmed folder is not readable: {folder_path}")
        confirmed_by_product.setdefault(product_id, []).append(decision)

    if not confirmed_by_product:
        raise ValueError("at least one confirmed folder decision is required")

    image_policy = default_image_policy()
    minimum_size_bytes = int(image_policy["min_size_kb"]) * 1024
    maximum_size_bytes = int(image_policy["max_size_mb"]) * 1024 * 1024
    records: list[_GalleryRecord] = []
    discovered = 0
    size_eligible = 0
    size_below_minimum = 0
    size_exceeded = 0
    source_stat_failures = 0
    directory_scan_failures = 0
    entry_metadata_failures = 0
    planned_inspections = 0
    inspected = 0
    inspection_failures = 0
    content_duplicates = 0
    uploaded_history_duplicates = 0
    final_candidates = 0
    completed_inspections = 0
    completed_inspection_failures = 0
    uploaded_fingerprints = {
        str(value).strip().casefold()
        for value in (uploaded_source_sha256 or ())
        if len(str(value).strip()) == 64
    }
    history_checked = (
        uploaded_source_sha256 is not None
        if uploaded_history_checked is None
        else bool(uploaded_history_checked)
    )
    per_product: list[dict[str, Any]] = []
    performance: dict[str, float | int] = {
        "enumeration_ms": 0.0,
        "source_stat_ms": 0.0,
        "source_read_ms": 0.0,
        "sha256_ms": 0.0,
        "decode_ms": 0.0,
        "preview_ms": 0.0,
        "checkpoint_write_ms": 0.0,
        "progress_callback_ms": 0.0,
        "batch_publish_ms": 0.0,
        "source_stat_count": 0,
        "checkpoint_hit_count": 0,
        "checkpoint_write_count": 0,
        "progress_callback_count": 0,
        "batch_publish_count": 0,
        "traversal_root_count": 0,
        "avoided_recursive_scan_count": 0,
        "directory_scan_failure_count": 0,
        "entry_metadata_failure_count": 0,
        "checkpoint_write_failure_count": 0,
    }
    checkpoint_dirty_count = int(
        bool(checkpoint.pop("_identity_changed", False))
    )
    checkpoint_writes_disabled = False
    active_checkpoint_keys: set[str] = set()
    last_checkpoint_write = time.monotonic()

    def publish_progress(progress: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        started = time.perf_counter()
        progress_callback(progress)
        performance["progress_callback_ms"] = float(
            performance["progress_callback_ms"]
        ) + (time.perf_counter() - started) * 1000
        performance["progress_callback_count"] = int(
            performance["progress_callback_count"]
        ) + 1

    def flush_checkpoint(*, force: bool = False) -> None:
        nonlocal checkpoint_dirty_count, checkpoint_writes_disabled
        nonlocal last_checkpoint_write
        if checkpoint_dirty_count == 0:
            return
        if checkpoint_writes_disabled:
            checkpoint_dirty_count = 0
            return
        now = time.monotonic()
        if (
            not force
            and checkpoint_dirty_count < GALLERY_CHECKPOINT_FLUSH_ITEMS
            and now - last_checkpoint_write < GALLERY_CHECKPOINT_FLUSH_SECONDS
        ):
            return
        started = time.perf_counter()
        try:
            _write_task_checkpoint(checkpoint_path, checkpoint)
        except OSError:
            performance["checkpoint_write_ms"] = float(
                performance["checkpoint_write_ms"]
            ) + (time.perf_counter() - started) * 1000
            performance["checkpoint_write_failure_count"] = int(
                performance["checkpoint_write_failure_count"]
            ) + 1
            checkpoint_writes_disabled = True
            checkpoint_dirty_count = 0
            last_checkpoint_write = now
            return
        if checkpoint_path is not None:
            performance["checkpoint_write_ms"] = float(
                performance["checkpoint_write_ms"]
            ) + (time.perf_counter() - started) * 1000
            performance["checkpoint_write_count"] = int(
                performance["checkpoint_write_count"]
            ) + 1
        checkpoint_dirty_count = 0
        last_checkpoint_write = now

    for product_id, folder_decisions in sorted(confirmed_by_product.items()):
        product = products_by_id[product_id]
        enumeration_started = time.perf_counter()

        def before_root(folder: Path) -> None:
            publish_progress(
                {
                    "current_product": product_id,
                    "current_folder": str(folder),
                    "discovered_count": discovered,
                    "prepared_count": inspected,
                    "discovered_path_count": discovered,
                    "size_eligible_count": size_eligible,
                    "size_filtered_count": (
                        size_below_minimum
                        + size_exceeded
                        + source_stat_failures
                    ),
                    "size_below_minimum_count": size_below_minimum,
                    "size_exceeded_count": size_exceeded,
                    "source_stat_failure_count": source_stat_failures,
                    "planned_inspection_count": planned_inspections,
                    "inspected_count": inspected,
                    "inspection_failure_count": inspection_failures,
                    "content_duplicate_count": content_duplicates,
                    "uploaded_history_duplicate_count": (
                        uploaded_history_duplicates
                    ),
                    "final_candidate_count": final_candidates,
                    "pending_count": max(
                        planned_inspections - inspected - inspection_failures,
                        0,
                    ),
                }
            )

        candidates_by_folder, enumeration_stats = (
            _enumerate_confirmed_folders(
                folder_decisions,
                before_root=before_root,
            )
        )
        performance["enumeration_ms"] = float(
            performance["enumeration_ms"]
        ) + (time.perf_counter() - enumeration_started) * 1000
        performance["traversal_root_count"] = int(
            performance["traversal_root_count"]
        ) + int(enumeration_stats["traversal_root_count"])
        performance["avoided_recursive_scan_count"] = int(
            performance["avoided_recursive_scan_count"]
        ) + int(enumeration_stats["avoided_recursive_scan_count"])
        product_directory_scan_failures = int(
            enumeration_stats["directory_scan_failure_count"]
        )
        product_entry_metadata_failures = int(
            enumeration_stats["entry_metadata_failure_count"]
        )
        directory_scan_failures += product_directory_scan_failures
        entry_metadata_failures += product_entry_metadata_failures
        performance["directory_scan_failure_count"] = int(
            performance["directory_scan_failure_count"]
        ) + product_directory_scan_failures
        performance["entry_metadata_failure_count"] = int(
            performance["entry_metadata_failure_count"]
        ) + product_entry_metadata_failures
        product_discovered = sum(
            len(paths) for _decision, paths, _raw_count in candidates_by_folder
        )
        discovered += product_discovered
        source_facts: dict[
            str, tuple[os.stat_result | None, OSError | None]
        ] = {}
        filter_stats_by_folder: dict[str, dict[str, int]] = {}
        tiered_candidates_by_folder: list[
            tuple[
                dict[str, Any],
                list[Path],
                list[Path],
                list[Path],
                int,
            ]
        ] = []
        for decision, paths, raw_discovered_count in candidates_by_folder:
            preferred_paths: list[Path] = []
            fallback_below_paths: list[Path] = []
            fallback_above_paths: list[Path] = []
            folder_below_minimum = 0
            folder_size_exceeded = 0
            folder_stat_failures = 0
            for path in paths:
                source_key = _path_key(path)
                facts = source_facts.get(source_key)
                if facts is None:
                    started = time.perf_counter()
                    source_stat = None
                    source_error = None
                    try:
                        source_stat = path.stat()
                    except OSError as error:
                        source_error = error
                    performance["source_stat_ms"] = float(
                        performance["source_stat_ms"]
                    ) + (time.perf_counter() - started) * 1000
                    performance["source_stat_count"] = int(
                        performance["source_stat_count"]
                    ) + 1
                    facts = (source_stat, source_error)
                    source_facts[source_key] = facts
                source_stat, source_error = facts
                if source_error is not None or source_stat is None:
                    folder_stat_failures += 1
                elif int(source_stat.st_size) < minimum_size_bytes:
                    folder_below_minimum += 1
                elif int(source_stat.st_size) > maximum_size_bytes:
                    folder_size_exceeded += 1
                elif int(source_stat.st_size) < PREFERRED_MIN_SIZE_BYTES:
                    fallback_below_paths.append(path)
                elif int(source_stat.st_size) > PREFERRED_MAX_SIZE_BYTES:
                    fallback_above_paths.append(path)
                else:
                    preferred_paths.append(path)
            folder_id = _decision_folder_id(decision)
            folder_size_eligible = (
                len(preferred_paths)
                + len(fallback_below_paths)
                + len(fallback_above_paths)
            )
            filter_stats_by_folder[folder_id] = {
                "unique_discovered_images": len(paths),
                "size_eligible_images": folder_size_eligible,
                "preferred_size_images": len(preferred_paths),
                "fallback_below_preferred_images": len(
                    fallback_below_paths
                ),
                "fallback_above_preferred_images": len(
                    fallback_above_paths
                ),
                "size_below_minimum_images": folder_below_minimum,
                "size_exceeded_images": folder_size_exceeded,
                "source_stat_failure_images": folder_stat_failures,
            }
            size_eligible += folder_size_eligible
            size_below_minimum += folder_below_minimum
            size_exceeded += folder_size_exceeded
            source_stat_failures += folder_stat_failures
            tiered_candidates_by_folder.append(
                (
                    decision,
                    preferred_paths,
                    fallback_below_paths,
                    fallback_above_paths,
                    raw_discovered_count,
                )
            )
        preferred_counts = [
            len(preferred_paths)
            for _, preferred_paths, _, _, _ in tiered_candidates_by_folder
        ]
        fallback_below_counts = [
            len(fallback_below_paths)
            for _, _, fallback_below_paths, _, _ in tiered_candidates_by_folder
        ]
        fallback_above_counts = [
            len(fallback_above_paths)
            for _, _, _, fallback_above_paths, _ in tiered_candidates_by_folder
        ]
        counts = [
            preferred_count + below_count + above_count
            for preferred_count, below_count, above_count in zip(
                preferred_counts,
                fallback_below_counts,
                fallback_above_counts,
                strict=True,
            )
        ]
        sample_size = min(candidate_limit, sum(counts))
        allocation_breakdown = _tiered_allocation_breakdown(
            preferred_counts,
            fallback_below_counts,
            fallback_above_counts,
            sample_size,
        )
        allocations = allocation_breakdown["allocations"]
        base_allocations = allocation_breakdown["coverage_allocations"]
        proportional_allocations = allocation_breakdown[
            "proportional_allocations"
        ]
        preferred_allocations = allocation_breakdown[
            "preferred_allocations"
        ]
        fallback_below_allocations = allocation_breakdown[
            "fallback_below_allocations"
        ]
        fallback_above_allocations = allocation_breakdown[
            "fallback_above_allocations"
        ]
        nonempty_folder_count = sum(count > 0 for count in counts)
        represented_folder_count = sum(
            allocation > 0 for allocation in allocations
        )
        uncovered_folder_count = max(
            nonempty_folder_count - represented_folder_count,
            0,
        )
        complete_folder_coverage = uncovered_folder_count == 0
        selected: list[tuple[dict[str, Any], Path]] = []
        reserve_groups: dict[
            str, list[deque[tuple[dict[str, Any], Path, str]]]
        ] = {
            "preferred": [],
            "fallback_below": [],
            "fallback_above": [],
        }
        per_folder = []
        for (
            (
                decision,
                preferred_paths,
                fallback_below_paths,
                fallback_above_paths,
                raw_discovered_count,
            ),
            allocation,
            base_allocation,
            proportional_allocation,
            preferred_allocation,
            fallback_below_allocation,
            fallback_above_allocation,
        ) in zip(
            tiered_candidates_by_folder,
            allocations,
            base_allocations,
            proportional_allocations,
            preferred_allocations,
            fallback_below_allocations,
            fallback_above_allocations,
            strict=True,
        ):
            folder_path = str(decision["folder_path"])
            folder_id = _decision_folder_id(decision)
            folder_seed = hashlib.sha256(
                (
                    f"{CANDIDATE_STRATEGY_ID}\0"
                    f"{CANDIDATE_STRATEGY_VERSION}\0"
                    f"{sampling_seed}\0{product_id}\0{folder_id}"
                ).encode("utf-8")
            ).hexdigest()
            sampled_preferred, reserve_preferred = _sample_and_reserve_paths(
                preferred_paths,
                preferred_allocation,
                seed=f"{folder_seed}\0preferred",
            )
            (
                sampled_fallback_below,
                reserve_fallback_below,
            ) = _sample_and_reserve_paths(
                fallback_below_paths,
                fallback_below_allocation,
                seed=f"{folder_seed}\0fallback-below",
            )
            (
                sampled_fallback_above,
                reserve_fallback_above,
            ) = _sample_and_reserve_paths(
                fallback_above_paths,
                fallback_above_allocation,
                seed=f"{folder_seed}\0fallback-above",
            )
            sampled_paths = [
                *sampled_preferred,
                *sampled_fallback_below,
                *sampled_fallback_above,
            ]
            selected.extend((decision, path) for path in sampled_paths)
            reserve_groups["preferred"].append(
                deque(
                    (decision, path, "preferred")
                    for path in reserve_preferred
                )
            )
            reserve_groups["fallback_below"].append(
                deque(
                    (decision, path, "fallback_below")
                    for path in reserve_fallback_below
                )
            )
            reserve_groups["fallback_above"].append(
                deque(
                    (decision, path, "fallback_above")
                    for path in reserve_fallback_above
                )
            )
            filter_stats = filter_stats_by_folder[folder_id]
            zero_allocation_reason = ""
            if filter_stats["unique_discovered_images"] == 0:
                zero_allocation_reason = (
                    "EMPTY_FOLDER"
                    if raw_discovered_count == 0
                    else "FULLY_OVERLAPPED_FOLDER"
                )
            elif filter_stats["size_eligible_images"] == 0:
                zero_allocation_reason = NO_SIZE_ELIGIBLE_IMAGES
            elif allocation == 0:
                zero_allocation_reason = COVERAGE_LIMIT_REASON
            per_folder.append(
                {
                    "folder_id": folder_id,
                    "folder_path": folder_path,
                    "source_system": str(
                        decision.get("source_system", "confirmed_folder")
                    ),
                    "raw_discovered_images": raw_discovered_count,
                    "discovered_images": filter_stats[
                        "unique_discovered_images"
                    ],
                    "size_eligible_images": filter_stats[
                        "size_eligible_images"
                    ],
                    "preferred_size_images": filter_stats[
                        "preferred_size_images"
                    ],
                    "fallback_below_preferred_images": filter_stats[
                        "fallback_below_preferred_images"
                    ],
                    "fallback_above_preferred_images": filter_stats[
                        "fallback_above_preferred_images"
                    ],
                    "size_below_minimum_images": filter_stats[
                        "size_below_minimum_images"
                    ],
                    "size_exceeded_images": filter_stats[
                        "size_exceeded_images"
                    ],
                    "source_stat_failure_images": filter_stats[
                        "source_stat_failure_images"
                    ],
                    "base_allocation": base_allocation,
                    "proportional_allocation": proportional_allocation,
                    "preferred_allocation": preferred_allocation,
                    "fallback_below_preferred_allocation": (
                        fallback_below_allocation
                    ),
                    "fallback_above_preferred_allocation": (
                        fallback_above_allocation
                    ),
                    "fallback_allocation": (
                        fallback_below_allocation
                        + fallback_above_allocation
                    ),
                    "sampled_images": len(sampled_paths),
                    "zero_allocation_reason": zero_allocation_reason,
                }
            )
        random.Random(
            hashlib.sha256(
                (
                    f"{CANDIDATE_STRATEGY_ID}\0"
                    f"{CANDIDATE_STRATEGY_VERSION}\0"
                    f"{sampling_seed}\0{product_id}\0combined"
                ).encode("utf-8")
            ).hexdigest()
        ).shuffle(selected)
        reserves: deque[tuple[dict[str, Any], Path, str]] = deque()
        for tier in ("preferred", "fallback_below", "fallback_above"):
            groups = reserve_groups[tier]
            while any(groups):
                for group in groups:
                    if group:
                        reserves.append(group.popleft())
        planned_inspections += len(selected)
        product_inspected = 0
        product_failures = 0
        product_duplicates = 0
        product_uploaded_history_duplicates = 0
        product_final_candidates = 0
        valid = 0
        seen_product_sha256: set[str] = set()
        folder_summary_by_id = {
            str(item.get("folder_id", "")): item for item in per_folder
        }
        for item in per_folder:
            item["final_candidate_count"] = 0
            item["uploaded_history_duplicate_count"] = 0
            item["history_replacement_allocation"] = 0
        product_summary = {
            "product_id": product_id,
            "confirmed_folders": len(folder_decisions),
            "nonempty_folders": nonempty_folder_count,
            "represented_folders": represented_folder_count,
            "uncovered_folders": uncovered_folder_count,
            "complete_folder_coverage": complete_folder_coverage,
            "discovered_images": product_discovered,
            "size_eligible_count": sum(counts),
            "size_filtered_count": product_discovered - sum(counts),
            "directory_scan_failure_count": (
                product_directory_scan_failures
            ),
            "entry_metadata_failure_count": (
                product_entry_metadata_failures
            ),
            "enumeration_failure_count": (
                product_directory_scan_failures
                + product_entry_metadata_failures
            ),
            "preferred_size_count": sum(preferred_counts),
            "fallback_below_preferred_count": sum(
                fallback_below_counts
            ),
            "fallback_above_preferred_count": sum(
                fallback_above_counts
            ),
            "preferred_sampled_count": sum(preferred_allocations),
            "fallback_below_preferred_sampled_count": sum(
                fallback_below_allocations
            ),
            "fallback_above_preferred_sampled_count": sum(
                fallback_above_allocations
            ),
            "fallback_sampled_count": (
                sum(fallback_below_allocations)
                + sum(fallback_above_allocations)
            ),
            "prepared_candidates": len(selected),
            "planned_inspection_count": len(selected),
            "inspected_count": product_inspected,
            "inspection_failure_count": product_failures,
            "content_duplicate_count": product_duplicates,
            "uploaded_history_duplicate_count": (
                product_uploaded_history_duplicates
            ),
            "final_candidate_count": product_final_candidates,
            "valid_candidates": valid,
            "reason_codes": (
                [] if complete_folder_coverage else [COVERAGE_LIMIT_REASON]
            ),
            "folder_allocations": per_folder,
        }

        def emit_progress(current_folder: str | None = None) -> None:
            if progress_callback is None:
                return
            reported_inspections = max(inspected, completed_inspections)
            reported_failures = max(
                inspection_failures,
                completed_inspection_failures,
            )
            publish_progress(
                {
                    "current_product": product_id,
                    "current_folder": current_folder,
                    "discovered_count": discovered,
                    "prepared_count": inspected,
                    "discovered_path_count": discovered,
                    "size_eligible_count": size_eligible,
                    "size_filtered_count": (
                        size_below_minimum
                        + size_exceeded
                        + source_stat_failures
                    ),
                    "size_below_minimum_count": size_below_minimum,
                    "size_exceeded_count": size_exceeded,
                    "source_stat_failure_count": source_stat_failures,
                    "planned_inspection_count": planned_inspections,
                    "inspected_count": reported_inspections,
                    "inspection_failure_count": reported_failures,
                    "content_duplicate_count": content_duplicates,
                    "uploaded_history_duplicate_count": (
                        uploaded_history_duplicates
                    ),
                    "final_candidate_count": final_candidates,
                    "available_candidate_count": len(records),
                    "pending_count": max(
                        planned_inspections
                        - reported_inspections
                        - reported_failures,
                        0,
                    ),
                }
            )

        emit_progress()

        def worker(
            decision: Mapping[str, Any], path: Path
        ) -> _InspectionOutcome:
            source_stat, source_error = source_facts[_path_key(path)]
            if source_error is not None or source_stat is None:
                if source_error is None:
                    raise OSError(f"image source metadata is unavailable: {path}")
                raise source_error
            return _inspect_candidate_once(
                decision,
                path,
                product,
                preview_dir=preview_dir,
                checkpoint_entries=checkpoint_entries,
                source_stat=source_stat,
            )

        indexed_selected = [
            (index, decision, path)
            for index, (decision, path) in enumerate(selected)
        ]
        active_checkpoint_keys.update(
            _checkpoint_key(product_id, decision, path)
            for _index, decision, path in indexed_selected
        )
        decision_by_index = {
            index: decision for index, decision, _path in indexed_selected
        }

        def checkpoint_completion(
            index: int,
            result: _InspectionOutcome | Exception,
        ) -> None:
            nonlocal checkpoint_dirty_count
            nonlocal completed_inspections
            nonlocal completed_inspection_failures
            if isinstance(result, _InspectionOutcome) and not result.checkpoint_hit:
                checkpoint_entries[result.checkpoint_key] = (
                    result.checkpoint_entry
                )
                checkpoint_dirty_count += 1
                flush_checkpoint()
            if isinstance(result, Exception):
                completed_inspection_failures += 1
            else:
                completed_inspections += 1
            decision = decision_by_index.get(index, {})
            emit_progress(str(decision.get("folder_path", "")) or None)

        def merge_ready_batch(
            window: Sequence[tuple[int, Mapping[str, Any], Path]],
            results: Sequence[_InspectionOutcome | Exception],
        ) -> None:
            nonlocal inspected
            nonlocal inspection_failures
            nonlocal content_duplicates
            nonlocal uploaded_history_duplicates
            nonlocal final_candidates
            nonlocal product_inspected
            nonlocal product_failures
            nonlocal product_duplicates
            nonlocal product_uploaded_history_duplicates
            nonlocal product_final_candidates
            nonlocal valid
            for (_, decision, _), result in zip(
                window,
                results,
                strict=True,
            ):
                if isinstance(result, Exception):
                    inspection_failures += 1
                    product_failures += 1
                    emit_progress(str(decision["folder_path"]))
                    continue
                for key, value in (result.timings_ms or {}).items():
                    performance[key] = float(performance.get(key, 0.0)) + float(
                        value
                    )
                inspected += 1
                product_inspected += 1
                inspected_asset = result.record
                fingerprint = str(inspected_asset.sha256 or "").casefold()
                duplicate = bool(
                    fingerprint and fingerprint in seen_product_sha256
                )
                uploaded_before = bool(
                    fingerprint and fingerprint in uploaded_fingerprints
                )
                if fingerprint:
                    seen_product_sha256.add(fingerprint)
                if duplicate:
                    content_duplicates += 1
                    product_duplicates += 1
                elif uploaded_before:
                    uploaded_history_duplicates += 1
                    product_uploaded_history_duplicates += 1
                    folder_summary = folder_summary_by_id.get(
                        inspected_asset.folder_id
                    )
                    if folder_summary is not None:
                        folder_summary["uploaded_history_duplicate_count"] = (
                            int(
                                folder_summary.get(
                                    "uploaded_history_duplicate_count", 0
                                )
                            )
                            + 1
                        )
                else:
                    final_candidates += 1
                    product_final_candidates += 1
                    if inspected_asset.validation_status == "valid":
                        valid += 1
                    records.append(inspected_asset)
                    folder_summary = folder_summary_by_id.get(
                        inspected_asset.folder_id
                    )
                    if folder_summary is not None:
                        folder_summary["final_candidate_count"] = (
                            int(
                                folder_summary.get(
                                    "final_candidate_count", 0
                                )
                            )
                            + 1
                        )
                emit_progress(str(decision["folder_path"]))

            flush_checkpoint(force=True)
            product_summary.update(
                {
                    "inspected_count": product_inspected,
                    "inspection_failure_count": product_failures,
                    "content_duplicate_count": product_duplicates,
                    "uploaded_history_duplicate_count": (
                        product_uploaded_history_duplicates
                    ),
                    "final_candidate_count": product_final_candidates,
                    "valid_candidates": valid,
                }
            )
            if batch_callback is not None:
                partial = _build_gallery_document(
                    records,
                    status_rows,
                    confirmed_by_product=confirmed_by_product,
                    products_by_id=products_by_id,
                    candidate_limit=candidate_limit,
                    page_size=page_size,
                    sampling_seed=sampling_seed,
                    discovered=discovered,
                    size_eligible=size_eligible,
                    size_below_minimum=size_below_minimum,
                    size_exceeded=size_exceeded,
                    source_stat_failures=source_stat_failures,
                    directory_scan_failures=directory_scan_failures,
                    entry_metadata_failures=entry_metadata_failures,
                    planned_inspections=planned_inspections,
                    inspected=inspected,
                    inspection_failures=inspection_failures,
                    content_duplicates=content_duplicates,
                    uploaded_history_duplicates=(
                        uploaded_history_duplicates
                    ),
                    uploaded_history_checked=history_checked,
                    final_candidates=final_candidates,
                    per_product=[*per_product, product_summary],
                    gallery_complete=False,
                    performance=performance,
                )
                started = time.perf_counter()
                batch_callback(partial)
                performance["batch_publish_ms"] = float(
                    performance["batch_publish_ms"]
                ) + (time.perf_counter() - started) * 1000
                performance["batch_publish_count"] = int(
                    performance["batch_publish_count"]
                ) + 1

        _run_concurrent_window(
            indexed_selected,
            worker,
            completion_callback=checkpoint_completion,
            ordered_batch_callback=merge_ready_batch,
            batch_size=min(page_size, GALLERY_PROGRESSIVE_PUBLISH_SIZE),
        )
        next_index = len(indexed_selected)
        while product_final_candidates < sample_size and reserves:
            needed = min(
                sample_size - product_final_candidates,
                len(reserves),
            )
            replacement_batch = [reserves.popleft() for _ in range(needed)]
            indexed_replacements = []
            for decision, path, tier in replacement_batch:
                index = next_index
                next_index += 1
                indexed_replacements.append((index, decision, path))
                active_checkpoint_keys.add(
                    _checkpoint_key(product_id, decision, path)
                )
                decision_by_index[index] = decision
                folder_summary = folder_summary_by_id.get(
                    _decision_folder_id(decision)
                )
                if folder_summary is not None:
                    folder_summary["sampled_images"] = (
                        int(folder_summary.get("sampled_images", 0)) + 1
                    )
                    folder_summary["history_replacement_allocation"] = (
                        int(
                            folder_summary.get(
                                "history_replacement_allocation", 0
                            )
                        )
                        + 1
                    )
                    if tier == "preferred":
                        field = "preferred_allocation"
                        summary_field = "preferred_sampled_count"
                    elif tier == "fallback_below":
                        field = "fallback_below_preferred_allocation"
                        summary_field = (
                            "fallback_below_preferred_sampled_count"
                        )
                    else:
                        field = "fallback_above_preferred_allocation"
                        summary_field = (
                            "fallback_above_preferred_sampled_count"
                        )
                    folder_summary[field] = int(folder_summary.get(field, 0)) + 1
                    product_summary[summary_field] = int(
                        product_summary.get(summary_field, 0)
                    ) + 1
                    if tier != "preferred":
                        folder_summary["fallback_allocation"] = (
                            int(folder_summary.get("fallback_allocation", 0))
                            + 1
                        )
                        product_summary["fallback_sampled_count"] = int(
                            product_summary.get("fallback_sampled_count", 0)
                        ) + 1
            planned_inspections += len(indexed_replacements)
            product_summary["prepared_candidates"] = (
                int(product_summary.get("prepared_candidates", 0))
                + len(indexed_replacements)
            )
            product_summary["planned_inspection_count"] = int(
                product_summary.get("planned_inspection_count", 0)
            ) + len(indexed_replacements)
            product_summary["history_replacement_count"] = int(
                product_summary.get("history_replacement_count", 0)
            ) + len(indexed_replacements)
            _run_concurrent_window(
                indexed_replacements,
                worker,
                completion_callback=checkpoint_completion,
                ordered_batch_callback=merge_ready_batch,
                batch_size=min(
                    page_size,
                    GALLERY_PROGRESSIVE_PUBLISH_SIZE,
                ),
            )
        flush_checkpoint(force=True)
        per_product.append(product_summary)

    stale_checkpoint_keys = set(checkpoint_entries) - active_checkpoint_keys
    if stale_checkpoint_keys:
        for key in stale_checkpoint_keys:
            checkpoint_entries.pop(key, None)
        checkpoint_dirty_count += len(stale_checkpoint_keys)
    flush_checkpoint(force=True)

    data = _build_gallery_document(
        records,
        status_rows,
        confirmed_by_product=confirmed_by_product,
        products_by_id=products_by_id,
        candidate_limit=candidate_limit,
        page_size=page_size,
        sampling_seed=sampling_seed,
        discovered=discovered,
        size_eligible=size_eligible,
        size_below_minimum=size_below_minimum,
        size_exceeded=size_exceeded,
        source_stat_failures=source_stat_failures,
        directory_scan_failures=directory_scan_failures,
        entry_metadata_failures=entry_metadata_failures,
        planned_inspections=planned_inspections,
        inspected=inspected,
        inspection_failures=inspection_failures,
        content_duplicates=content_duplicates,
        uploaded_history_duplicates=uploaded_history_duplicates,
        uploaded_history_checked=history_checked,
        final_candidates=final_candidates,
        per_product=per_product,
        gallery_complete=True,
        performance=performance,
    )
    if inspected != (
        final_candidates
        + content_duplicates
        + uploaded_history_duplicates
    ):
        raise ValueError("gallery candidate count invariant failed")
    if planned_inspections != inspected + inspection_failures:
        raise ValueError("gallery inspection count invariant failed")
    if len(data.get("asset_candidates", [])) != final_candidates:
        raise ValueError("gallery final candidate count invariant failed")
    return data
