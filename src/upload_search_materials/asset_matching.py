"""Deterministic in-memory matching from indexed paths to products."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Sequence
import unicodedata

from .io_tables import ProductValidationReport
from .models import ProductRecord


_SEPARATORS = re.compile(r"[\s\-—_+·]+")
_BRAND_PREFIX = "kk树"
_BUSINESS_ROLE_SUFFIX = re.compile(r"(?:\((?:主|副)\))+$")
_MIN_FUZZY_TITLE_LENGTH = 6
_MIN_FUZZY_COMMON_LENGTH = 5
_FUZZY_CONTIGUOUS_COVERAGE = 0.50


def normalize_match_text(value: str) -> str:
    """Canonicalize product and directory text for conservative name matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if normalized.startswith(_BRAND_PREFIX):
        normalized = normalized[len(_BRAND_PREFIX) :]
    return _SEPARATORS.sub("", normalized)


def normalize_product_title(value: str) -> str:
    """Remove non-material main/sub listing markers from a product title."""

    return _BUSINESS_ROLE_SUFFIX.sub("", normalize_match_text(value))


def longest_common_substring_length(left: str, right: str) -> int:
    """Return the number of consecutive characters shared by both strings."""

    if not left or not right:
        return 0
    if len(left) > len(right):
        left, right = right, left
    previous = [0] * (len(left) + 1)
    longest = 0
    for right_character in right:
        current = [0]
        for index, left_character in enumerate(left, 1):
            if left_character == right_character:
                value = previous[index - 1] + 1
                current.append(value)
                longest = max(longest, value)
            else:
                current.append(0)
        previous = current
    return longest


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
        folded_tokens = {
            token
            for component in directory_components
            for token in _SEPARATORS.split(component.casefold())
            if token
        }
        sku_matches = [
            product
            for product in self._products
            if product.sku
            and (
                product.sku.casefold() in folded_components
                or product.sku.casefold() in folded_tokens
            )
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
            if any(title in component for component in normalized_components):
                name_matches.append(product)
        return self._build_matches(
            name_matches,
            match_type="name_candidate",
            match_status="needs_manual_confirmation",
        )

    def match_folder_name(self, folder_name: str) -> tuple[PathMatch, ...]:
        """Find exact and 50%-coverage candidates without reading folder files."""

        exact_identifier_matches = self.match(
            Path(folder_name) / "__folder__.jpg"
        )
        if exact_identifier_matches and exact_identifier_matches[0].match_type in {
            "exact_product_id",
            "exact_sku",
        }:
            return exact_identifier_matches

        normalized_folder = normalize_match_text(folder_name)
        strong_matches = []
        fuzzy_matches = []
        for product in self._products:
            base_title = normalize_product_title(product.title)
            if len(base_title) < _MIN_FUZZY_TITLE_LENGTH:
                continue

            if base_title in normalized_folder:
                strong_matches.append(product)
                continue

            common_length = longest_common_substring_length(
                base_title, normalized_folder
            )
            required_length = max(
                _MIN_FUZZY_COMMON_LENGTH,
                math.ceil(len(base_title) * _FUZZY_CONTIGUOUS_COVERAGE),
            )
            if common_length >= required_length:
                fuzzy_matches.append(product)

        matches = [
            *self._build_matches(
                strong_matches,
                match_type="name_candidate",
                match_status="needs_manual_confirmation",
                reason_codes=("NORMALIZED_NAME_CONTAINED",),
            ),
            *self._build_matches(
                fuzzy_matches,
                match_type="fuzzy_name_candidate",
                match_status="needs_manual_confirmation",
                reason_codes=("CONTIGUOUS_NAME_COVERAGE_AT_LEAST_50_PERCENT",),
            ),
        ]
        return tuple(
            sorted(matches, key=lambda item: (item.product_id, item.match_type))
        )

    @staticmethod
    def _build_matches(
        products: Sequence[ProductRecord],
        *,
        match_type: str,
        match_status: str,
        reason_codes: tuple[str, ...] = (),
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
                    reason_codes=reason_codes,
                ),
            )
        return tuple(by_key[key] for key in sorted(by_key))
