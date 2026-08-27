from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any


class BatchMode(str, Enum):
    DRY_RUN = "dry-run"
    PUBLISH = "publish"


class ProductStatus(str, Enum):
    DISCOVERED = "discovered"
    EXCLUDED = "excluded"
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    UPLOADING = "uploading"
    PARTIALLY_COMPLETED = "partially_completed"
    COMPLETED = "completed"
    FAILED = "failed"


class MaterialStatus(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    SUCCESS = "success"
    FAILED = "failed"
    PUBLISH_UNCERTAIN = "publish_uncertain"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    downloaded_at: str | None = None
    original_name: str | None = None
    report_type: str = ""
    row_count: int | None = None
    schema_valid: bool | None = None
    contract_status: str = ""
    status: str = "downloaded"
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class ProductRecord:
    product_id: str | int
    sku: str = ""
    title: str = ""
    grade: str = ""
    link: str = ""
    owner: str = ""
    team: str = ""
    category: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.product_id = _text(self.product_id)
        self.sku = _text(self.sku)


@dataclass
class AssetRecord:
    product_id: str | int
    source_path: str
    original_source_path: str = ""
    original_sha256: str = ""
    asset_id: str = ""
    sku: str = ""
    asset_type: str = ""
    source_system: str = "local"
    license_status: str = "unknown"
    sha256: str = ""
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    duration: float | None = None
    validation_status: str = "pending_validation"
    reason_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.product_id = _text(self.product_id)


@dataclass
class ProductTask:
    task_id: str
    run_id: str
    product_id: str | int
    status: ProductStatus = ProductStatus.DISCOVERED
    desired_slots: int | None = None
    owner: str = ""
    reason_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.product_id = _text(self.product_id)


@dataclass
class MaterialItem:
    task_id: str
    product_id: str | int
    material_type: str
    slot_index: int
    status: MaterialStatus = MaterialStatus.PENDING_VALIDATION
    assets: list[AssetRecord] = field(default_factory=list)
    title: str = ""
    description: str = ""
    remote_material_id: str | None = None
    attempt_count: int = 0
    content_hash: str = ""
    reason_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.product_id = _text(self.product_id)


def make_run_id(started_at: str, store: str, month: int) -> str:
    parsed = datetime.fromisoformat(started_at)
    raw = f"{started_at}|{store.strip()}|{month}".encode("utf-8")
    return f"RUN-{parsed:%Y%m%d}-" + hashlib.sha256(raw).hexdigest()[:10]


def make_task_id(
    run_id: str,
    product_id: str,
    material_type: str,
    slot_index: int,
) -> str:
    raw = f"{run_id}|{product_id}|{material_type}|{slot_index}".encode("utf-8")
    return "MAT-" + hashlib.sha256(raw).hexdigest()[:16]
