"""Purpose-scoped, machine-local selector profile loading."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Mapping

import yaml


SHARED_SELECTORS = frozenset({"store_name", "human_check"})
SELECTOR_PURPOSES: dict[str, frozenset[str]] = {
    "high_value_collection": frozenset(
        {
            "promotion_tab",
            "high_value_filter",
            "promotion_rows",
            "promotion_current_page",
            "promotion_first_page",
            "promotion_terminal_page",
            "promotion_next_page",
        }
    ),
    "exact_material_status": frozenset(
        {
            "product_search",
            "product_id",
            "desired_slots",
            "material_table",
            "material_rows",
            "empty_slots",
            "review_status",
        }
    ),
    "basic_export": frozenset({"export_basic"}),
    "promotion_export": frozenset({"export_promotion"}),
    "export": frozenset({"export_basic", "export_promotion"}),
    "publish": frozenset(
        {
            "product_search",
            "product_id",
            "material_table",
            "empty_slots",
            "image_text_action",
            "video_action",
            "file_input",
            "title_input",
            "description_input",
            "publish_button",
            "success_signal",
            "remote_material_id",
            "remote_material_table",
            "remote_material_fingerprint",
            "remote_material_status",
            "remote_material_slot",
            "remote_material_time",
        }
    ),
    "remote_verification": frozenset(
        {
            "product_search",
            "product_id",
            "remote_material_table",
            "remote_material_id",
            "remote_material_fingerprint",
            "remote_material_status",
            "remote_material_slot",
            "remote_material_time",
        }
    ),
}
OPTIONAL_SELECTOR_KEYS = frozenset(
    {
        "recommended_filter",
        "safe_popup_progress",
        "safe_popup_close_priority",
        "safe_popup_close",
        "material_page",
    }
)
PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "profile_name",
        "profile_version",
        "production",
        "supported_purposes",
        "material_center_url",
        "current_dom_validation",
    }
)
LEGACY_REQUIRED_SELECTORS = frozenset(
    {
        "store_name",
        "human_check",
        "export_basic",
        "product_search",
        "product_id",
        "desired_slots",
        "material_table",
        "material_rows",
        "empty_slots",
        "review_status",
        "image_text_action",
        "video_action",
        "file_input",
        "title_input",
        "description_input",
        "publish_button",
        "success_signal",
        "remote_material_id",
        "remote_material_table",
        "remote_material_fingerprint",
        "remote_material_status",
        "remote_material_slot",
        "remote_material_time",
    }
)
PLACEHOLDER_VALUES = frozenset(
    {
        "#store",
        "#human-check",
        "#export-basic",
        "#export-search",
        "#product-search",
        "#product-id",
        "#desired-slots",
        "#material-table",
        ".material-row",
        ".empty-slot",
        ".review-status",
        "#image-text-action",
        "#video-action",
        "#file-input",
        "#title-input",
        "#description-input",
        "#publish",
        "#success",
        "#remote-id",
        "#remote-table",
        "#remote-fingerprint",
        "#remote-status",
        "#remote-slot",
        "#remote-time",
        "#promotion-current-page",
        "#promotion-first-page",
        "#promotion-terminal-page",
    }
)


class SelectorConfigError(ValueError):
    """Stable, user-facing selector profile validation failure."""


@dataclass(frozen=True)
class SelectorProfile:
    path: Path
    purpose: str
    selectors: dict[str, str]
    name: str
    version: str
    sha256: str
    material_center_url: str
    legacy: bool = False


def required_selectors(purpose: str) -> frozenset[str]:
    try:
        return SHARED_SELECTORS | SELECTOR_PURPOSES[purpose]
    except KeyError as error:
        raise SelectorConfigError(
            f"SELECTOR_PURPOSE_UNSUPPORTED: {purpose}"
        ) from error


def load_selectors(
    path: Path,
    *,
    purpose: str | None = None,
    production: bool = False,
) -> dict[str, str]:
    """Load selectors with backward-compatible full-profile validation."""

    profile = load_selector_profile(
        path,
        purpose=purpose or "legacy_full",
        production=production,
    )
    return profile.selectors


def load_selector_profile(
    path: Path,
    *,
    purpose: str,
    production: bool = True,
) -> SelectorProfile:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SelectorConfigError(f"SELECTOR_PROFILE_NOT_FOUND: {path}")
    raw = path.read_bytes()
    values = yaml.safe_load(raw.decode("utf-8-sig")) or {}
    if not isinstance(values, Mapping):
        raise SelectorConfigError(
            "SELECTOR_PROFILE_SCHEMA_INVALID: profile must be a mapping"
        )

    legacy = purpose == "legacy_full"
    if legacy:
        required = set(LEGACY_REQUIRED_SELECTORS)
    else:
        required = set(required_selectors(purpose))
    normalized = {
        str(key): str(value).strip()
        for key, value in values.items()
        if key not in PROFILE_KEYS and value is not None
    }
    promotion_selector = str(
        normalized.get("export_promotion")
        or normalized.get("export_search")
        or ""
    ).strip()
    if promotion_selector:
        normalized["export_promotion"] = promotion_selector
        normalized["export_search"] = promotion_selector
    if legacy and not promotion_selector:
        required.add("export_promotion/export_search")
    missing = sorted(
        key
        for key in required
        if key == "export_promotion/export_search"
        or not str(normalized.get(key, "")).strip()
    )
    if missing:
        raise SelectorConfigError(
            "SELECTOR_FIELDS_MISSING: " + ", ".join(missing)
        )
    if not legacy and purpose == "high_value_collection":
        pagination_fields = (
            "promotion_current_page",
            "promotion_first_page",
            "promotion_terminal_page",
        )
        pagination_values = [
            normalized[field] for field in pagination_fields
        ]
        if len(set(pagination_values)) != len(pagination_values):
            raise SelectorConfigError(
                "SELECTOR_PAGINATION_FIELDS_AMBIGUOUS"
            )

    supported = values.get("supported_purposes")
    if (
        not legacy
        and supported is not None
        and (
            not isinstance(supported, list)
            or purpose not in {str(item) for item in supported}
        )
    ):
        raise SelectorConfigError(
            f"SELECTOR_PURPOSE_NOT_DECLARED: {purpose}"
        )
    if production:
        if values.get("production") is not True:
            raise SelectorConfigError("SELECTOR_PROFILE_NOT_PRODUCTION")
        placeholders = sorted(
            key
            for key, value in normalized.items()
            if value in PLACEHOLDER_VALUES
        )
        if placeholders:
            raise SelectorConfigError(
                "SELECTOR_PLACEHOLDER_REJECTED: " + ", ".join(placeholders)
            )

    return SelectorProfile(
        path=path,
        purpose=purpose,
        selectors=normalized,
        name=str(values.get("profile_name") or path.stem),
        version=str(values.get("profile_version") or "legacy"),
        sha256=hashlib.sha256(raw).hexdigest(),
        material_center_url=str(values.get("material_center_url") or ""),
        legacy=not bool(values.get("schema_version")),
    )


def discover_selector_profile(
    *,
    explicit: str | Path | None,
    environment: Mapping[str, str] | None,
    local_config: Mapping[str, object] | None,
    workspace_root: Path,
) -> Path:
    """Resolve a production profile without falling back to the example."""

    env = os.environ if environment is None else environment
    configured = (
        explicit
        or env.get("TMALL_SELECTORS_FILE")
        or (local_config or {}).get("selectors_file")
    )
    if configured:
        candidate = Path(str(configured)).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        candidate = candidate.resolve()
    else:
        candidate = (
            workspace_root
            / "upload-search-materials"
            / "config"
            / "selectors.local.yaml"
        ).resolve()
    if not candidate.is_file():
        raise SelectorConfigError(
            f"SELECTOR_PROFILE_NOT_FOUND: {candidate}"
        )
    if candidate.name == "selectors.example.yaml":
        raise SelectorConfigError("SELECTOR_EXAMPLE_NOT_PRODUCTION")
    return candidate
