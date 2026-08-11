"""Side-effect-free random browsing actions during high-value collection."""

from __future__ import annotations

import random
import re
from typing import Any, Callable, Mapping, Sequence

from .qianniu_copy import (
    QianniuCopyError,
    _empty_slot_positions,
    _find_product_row,
    _open_recommend_list,
    _open_slot_publish_form,
    _slot_cells,
)


RandomSource = random.Random | random.SystemRandom
HumanCheckWaiter = Callable[[Any, str], None]


def product_ids_from_rows(row_texts: Sequence[str]) -> tuple[str, ...]:
    """Return stable unique product ids from one collected page."""

    return tuple(
        dict.fromkeys(
            match.group(1)
            for text in row_texts
            if (match := re.search(r"商品ID\s*(\d+)", str(text)))
        )
    )


def perform_random_collection_action(
    page: Any,
    product_ids: Sequence[str],
    *,
    material_center_url: str,
    rng: RandomSource | None = None,
    wait_for_human_check: HumanCheckWaiter | None = None,
) -> dict[str, Any]:
    """Inspect a filled slot or open an empty image-text form in a new tab.

    The auxiliary page is always closed.  No field is filled and no confirm or
    publish control is clicked, so the collector's pagination tab remains
    untouched and the action cannot become a production write.
    """

    candidates = tuple(
        dict.fromkeys(str(value).strip() for value in product_ids if str(value).strip())
    )
    if not candidates:
        return {"status": "skipped", "reason_code": "RANDOM_ACTION_NO_PRODUCT"}
    source = rng or random.SystemRandom()
    auxiliary = None
    try:
        context = getattr(page, "context", None)
        if context is None or not callable(getattr(context, "new_page", None)):
            return {
                "status": "skipped",
                "reason_code": "RANDOM_ACTION_CONTEXT_UNAVAILABLE",
            }
        auxiliary = context.new_page()
        _open_recommend_list(auxiliary, material_center_url)
        if wait_for_human_check is not None:
            wait_for_human_check(auxiliary, "random_action_open")

        product_id = source.choice(candidates)
        row = _find_product_row(auxiliary, product_id)
        slots = _slot_cells(row)
        empty_positions = _empty_slot_positions(row)
        empty_set = set(empty_positions)
        filled_positions = [
            index
            for index in range(1, slots.count() + 1)
            if index not in empty_set
        ]
        actions = [
            action
            for action, positions in (
                ("view_filled_slot", filled_positions),
                ("open_empty_image_text", empty_positions),
            )
            if positions
        ]
        if not actions:
            return {
                "status": "skipped",
                "reason_code": "RANDOM_ACTION_NO_SLOT",
                "product_id": product_id,
            }

        action = source.choice(actions)
        positions = (
            filled_positions
            if action == "view_filled_slot"
            else empty_positions
        )
        position = int(source.choice(positions))
        if action == "view_filled_slot":
            slot = slots.nth(position - 1)
            slot.hover(force=True)
            slot.click(force=True)
            auxiliary.wait_for_timeout(source.randint(600, 1_200))
        else:
            _open_slot_publish_form(
                auxiliary,
                product_id,
                position,
                row=row,
            )
            auxiliary.wait_for_timeout(source.randint(600, 1_200))
        if wait_for_human_check is not None:
            wait_for_human_check(auxiliary, "random_action_after_open")
        return {
            "status": "completed",
            "action": action,
            "product_id": product_id,
            "slot_position": position,
            "read_only": True,
        }
    except QianniuCopyError as error:
        return {
            "status": "skipped",
            "reason_code": error.reason_code,
            "detail": error.detail,
        }
    except Exception as error:
        return {
            "status": "skipped",
            "reason_code": "RANDOM_ACTION_RUNTIME_FAILED",
            "detail": str(error),
        }
    finally:
        if auxiliary is not None:
            try:
                auxiliary.close(run_before_unload=False)
            except Exception:
                pass


def random_page_interval(rng: RandomSource | None = None) -> int:
    """Choose the confirmed one-or-two-page interval."""

    return int((rng or random.SystemRandom()).randint(1, 2))
