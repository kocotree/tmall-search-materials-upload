import re
from typing import Iterable


class SelectorInvalidError(RuntimeError):
    pass


class ProductIdentityError(RuntimeError):
    pass


def _required_text(page, selector: str, field_name: str) -> str:
    locator = page.locator(selector)
    if not locator.count():
        raise SelectorInvalidError(field_name)
    value = locator.inner_text().strip()
    if not value:
        raise SelectorInvalidError(field_name)
    return value


def supplement_material_status(
    page,
    selectors: dict[str, str],
    product_ids: Iterable[str],
    *,
    collected_at: str,
) -> list[dict[str, str]]:
    output = []
    for product_id in product_ids:
        try:
            search = page.locator(selectors["product_search"])
            search.fill(product_id)
            search.press("Enter")
            actual_product_id = _required_text(page, selectors["product_id"], "product_id")
            if actual_product_id != product_id:
                raise ProductIdentityError(f"目标商品={product_id}; 页面商品={actual_product_id}")
            desired_text = _required_text(page, selectors["desired_slots"], "desired_slots")
            match = re.search(r"(?:^|\D)(3|9)(?:\D|$)", desired_text)
            if not match:
                raise SelectorInvalidError("desired_slots")
            desired_slots = int(match.group(1))
            table = page.locator(selectors["material_table"])
            if not table.count() or not table.is_visible():
                raise SelectorInvalidError("material_table")
            material_count = page.locator(selectors["material_rows"]).count()
            empty_texts = page.locator(selectors["empty_slots"]).all_inner_texts()
            empty_indexes = [
                int(number)
                for text in empty_texts
                for number in re.findall(r"\d+", text)
            ]
            if material_count < desired_slots and not empty_indexes:
                raise SelectorInvalidError("empty_slots")
            review_states = [
                value.strip()
                for value in page.locator(selectors["review_status"]).all_inner_texts()
                if value.strip()
            ]
            if len(review_states) != material_count:
                raise SelectorInvalidError("review_status")
            output.append(
                {
                    "商品ID": product_id,
                    "目标坑位": str(desired_slots),
                    "现有素材数": str(material_count),
                    "空坑位": ";".join(str(index) for index in empty_indexes),
                    "审核状态": ";".join(review_states),
                    "审核状态完整": "true",
                    "状态": "ready_for_review",
                    "原因码": "",
                    "采集时间": collected_at,
                    "证据": f"product={product_id};rows={material_count};selector_version=runtime",
                }
            )
        except SelectorInvalidError as error:
            output.append(
                {
                    "商品ID": product_id,
                    "目标坑位": "",
                    "现有素材数": "",
                    "空坑位": "",
                    "审核状态": "",
                    "审核状态完整": "false",
                    "状态": "needs_manual_review",
                    "原因码": "SELECTOR_INVALID",
                    "采集时间": collected_at,
                    "证据": (
                        f"product={product_id};failed_field={error};"
                        "selector_version=runtime"
                    ),
                }
            )
    return output
