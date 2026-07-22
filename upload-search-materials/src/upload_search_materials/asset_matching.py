"""Deterministic in-memory matching from indexed paths to products."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence
import unicodedata

from .io_tables import ProductValidationReport
from .models import ProductRecord


_SEPARATORS = re.compile(r"[\s\-—_+·]+")
_BRAND_PREFIX = "kk树"


def normalize_match_text(value: str) -> str:
    """Canonicalize product and directory text for conservative name matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if normalized.startswith(_BRAND_PREFIX):
        normalized = normalized[len(_BRAND_PREFIX) :]
    return _SEPARATORS.sub("", normalized)


@dataclass(frozen=True)
class PathMatch:
    product_id: str
    sku: str
    product_title: str
    match_type: str
    match_status: str
    reason_codes: tuple[str, ...]


class ProductPathMatcher:
    """Match file paths against a validation-filtered product snapshot."""

    def __init__(self, products: tuple[ProductRecord, ...]) -> None:
        self._products = products

    @classmethod
    def from_products(
        cls,
        records: Sequence[ProductRecord],
        validation_report: ProductValidationReport,
    ) -> ProductPathMatcher:
        eligible = []
        blocked_rows = validation_report.reason_codes_by_row
        for fallback_row, record in enumerate(records, 2):
            source_row = int(record.raw.get("_source_row", fallback_row))
            if source_row not in blocked_rows:
                eligible.append(record)
        return cls(tuple(eligible))

    def match(self, relative_path: Path) -> tuple[PathMatch, ...]:
        directory_components = relative_path.parts[:-1]

        id_matches = [
            product
            for product in self._products
            if product.product_id and product.product_id in directory_components
        ]
        if id_matches:
            return self._build_matches(
                id_matches,
                match_type="exact_product_id",
                match_status="matched_unlicensed",
            )

        folded_components = {component.casefold() for component in directory_components}
        sku_matches = [
            product
            for product in self._products
            if product.sku and product.sku.casefold() in folded_components
        ]
        if sku_matches:
            return self._build_matches(
                sku_matches,
                match_type="exact_sku",
                match_status="matched_unlicensed",
            )

        normalized_components = tuple(
            normalize_match_text(component) for component in directory_components
        )
        name_matches = []
        for product in self._products:
            title = normalize_match_text(product.title)
            if len(title) < 4:
                continue
            if any(
                title in component or (len(component) >= 4 and component in title)
                for component in normalized_components
            ):
                name_matches.append(product)
        return self._build_matches(
            name_matches,
            match_type="name_candidate",
            match_status="needs_manual_confirmation",
        )

    @staticmethod
    def _build_matches(
        products: Sequence[ProductRecord],
        *,
        match_type: str,
        match_status: str,
    ) -> tuple[PathMatch, ...]:
        by_key: dict[tuple[str, str], PathMatch] = {}
        for product in products:
            key = (product.product_id, match_type)
            by_key.setdefault(
                key,
                PathMatch(
                    product_id=product.product_id,
                    sku=product.sku,
                    product_title=product.title,
                    match_type=match_type,
                    match_status=match_status,
                    reason_codes=(),
                ),
            )
        return tuple(by_key[key] for key in sorted(by_key))
