"""Lazy, task-local image candidates from user-confirmed folders."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import random
from typing import Any, Iterable, Mapping, Sequence

from .asset_selection import build_gallery_data
from .assets import IMAGE_EXTENSIONS, build_image_preview, inspect_asset
from .models import ProductRecord


CANDIDATE_STRATEGY_ID = "proportional_task_sample"
CANDIDATE_STRATEGY_VERSION = 2
COVERAGE_LIMIT_REASON = "FOLDER_COVERAGE_LIMIT_EXCEEDED"


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


def _iter_images(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )


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

    while remaining:
        capacity_total = sum(capacities)
        if capacity_total <= 0:
            break
        exact = [remaining * capacity / capacity_total for capacity in capacities]
        added = [min(int(value), capacities[index]) for index, value in enumerate(exact)]
        added_total = sum(added)
        if added_total:
            for index, value in enumerate(added):
                allocations[index] += value
                proportional_allocations[index] += value
                capacities[index] -= value
            remaining -= added_total
            continue
        ranked = sorted(
            (
                (exact[index] - int(exact[index]), capacities[index], -index, index)
                for index in range(len(capacities))
                if capacities[index] > 0
            ),
            reverse=True,
        )
        for _, _, _, index in ranked[:remaining]:
            allocations[index] += 1
            proportional_allocations[index] += 1
            capacities[index] -= 1
        remaining = 0
    return allocations, base_allocations, proportional_allocations


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


def _decision_folder_id(decision: Mapping[str, Any]) -> str:
    existing = str(decision.get("folder_id", "")).strip()
    if existing:
        return existing
    source_system = str(decision.get("source_system", "confirmed_folder")).strip()
    folder_path = str(decision.get("folder_path", "")).strip()
    return hashlib.sha256(
        f"{source_system}\0{Path(folder_path).resolve()}".encode("utf-8")
    ).hexdigest()[:24]


def build_confirmed_folder_gallery(
    products: Sequence[ProductRecord],
    status_rows: Iterable[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    *,
    candidate_limit: int = 100,
    page_size: int = 30,
    sampling_seed: str = "stable",
    preview_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a proportional, task-stable sample from confirmed folders."""

    if not 1 <= candidate_limit <= 100:
        raise ValueError("candidate_limit must be between 1 and 100")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    products_by_id = {str(product.product_id): product for product in products}
    status_rows = list(status_rows)

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

    records: list[_GalleryRecord] = []
    discovered = 0
    inspected = 0
    inspection_failures = 0
    per_product: list[dict[str, Any]] = []
    for product_id, folder_decisions in sorted(confirmed_by_product.items()):
        product = products_by_id[product_id]
        candidates_by_folder: list[
            tuple[dict[str, Any], list[Path], int]
        ] = []
        seen_paths: set[str] = set()
        for decision in sorted(folder_decisions, key=_folder_rank):
            folder = Path(str(decision["folder_path"]))
            discovered_paths = _iter_images(folder)
            unique_paths = []
            for path in discovered_paths:
                key = str(path.resolve()).casefold()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                unique_paths.append(path)
            discovered += len(unique_paths)
            candidates_by_folder.append(
                (decision, unique_paths, len(discovered_paths))
            )

        counts = [len(paths) for _, paths, _ in candidates_by_folder]
        sample_size = min(candidate_limit, sum(counts))
        (
            allocations,
            base_allocations,
            proportional_allocations,
        ) = _allocation_breakdown(counts, sample_size)
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
        per_folder = []
        for (
            (decision, paths, raw_discovered_count),
            allocation,
            base_allocation,
            proportional_allocation,
        ) in zip(
            candidates_by_folder,
            allocations,
            base_allocations,
            proportional_allocations,
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
            sampled_paths = _sample_paths(paths, allocation, seed=folder_seed)
            selected.extend((decision, path) for path in sampled_paths)
            zero_allocation_reason = ""
            if not paths:
                zero_allocation_reason = (
                    "EMPTY_FOLDER"
                    if raw_discovered_count == 0
                    else "FULLY_OVERLAPPED_FOLDER"
                )
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
                    "discovered_images": len(paths),
                    "base_allocation": base_allocation,
                    "proportional_allocation": proportional_allocation,
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
        valid = 0
        for decision, path in selected:
            try:
                inspected_asset = inspect_asset(
                    path,
                    product_id,
                    "confirmed",
                    source_system=str(
                        decision.get("source_system", "confirmed_folder")
                    ),
                    sku=str(product.sku),
                )
            except Exception:
                inspection_failures += 1
                continue
            inspected += 1
            if inspected_asset.validation_status == "valid":
                valid += 1
            folder_path = str(decision["folder_path"])
            preview_path = ""
            if (
                preview_dir is not None
                and inspected_asset.validation_status == "valid"
                and inspected_asset.asset_id
            ):
                preview_path = str(
                    build_image_preview(
                        path,
                        Path(preview_dir) / f"{inspected_asset.asset_id}.jpg",
                    )
                )
            records.append(
                _GalleryRecord(
                    folder_id=_decision_folder_id(decision),
                    folder_path=folder_path,
                    source_system=str(
                        decision.get("source_system", "confirmed_folder")
                    ),
                    candidate_directory=folder_path,
                    relative_path=str(path.relative_to(Path(folder_path))),
                    absolute_path=str(path.resolve()),
                    preview_path=preview_path,
                    sha256=inspected_asset.sha256,
                    width=inspected_asset.width,
                    height=inspected_asset.height,
                    size_bytes=inspected_asset.size_bytes,
                    validation_status=inspected_asset.validation_status,
                    file_reason_codes=tuple(inspected_asset.reason_codes),
                    product_id=product_id,
                    sku=str(product.sku),
                    product_title=str(product.title),
                    match_type="name_candidate",
                    match_status="confirmed",
                    match_reason_codes=("CONFIRMED_FOLDER_BINDING",),
                )
            )
        per_product.append(
            {
                "product_id": product_id,
                "confirmed_folders": len(folder_decisions),
                "nonempty_folders": nonempty_folder_count,
                "represented_folders": represented_folder_count,
                "uncovered_folders": uncovered_folder_count,
                "complete_folder_coverage": complete_folder_coverage,
                "discovered_images": sum(counts),
                "prepared_candidates": len(selected),
                "valid_candidates": valid,
                "reason_codes": (
                    []
                    if complete_folder_coverage
                    else [COVERAGE_LIMIT_REASON]
                ),
                "folder_allocations": per_folder,
            }
        )

    data = build_gallery_data(
        records,
        status_rows,
    )
    data["requirements"] = [
        item
        for item in data["requirements"]
        if str(item.get("product_id", "")) in confirmed_by_product
    ]
    data["candidate_strategy"] = CANDIDATE_STRATEGY_ID
    data["candidate_strategy_version"] = CANDIDATE_STRATEGY_VERSION
    data["candidate_limit"] = candidate_limit
    data["page_size"] = page_size
    data["sampling_seed"] = sampling_seed
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
    data["scan_summary"] = {
        "confirmed_folder_count": sum(
            len(value) for value in confirmed_by_product.values()
        ),
        "discovered_images": discovered,
        "inspected_candidates": inspected,
        "inspection_failures": inspection_failures,
        "per_product": per_product,
    }
    reason_codes = list(data.get("reason_codes", []))
    if any(
        not item["complete_folder_coverage"] for item in per_product
    ) and COVERAGE_LIMIT_REASON not in reason_codes:
        reason_codes.append(COVERAGE_LIMIT_REASON)
    data["reason_codes"] = reason_codes
    return data
