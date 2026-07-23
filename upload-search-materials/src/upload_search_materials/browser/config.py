from pathlib import Path

import yaml


REQUIRED_SELECTORS = {
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


class SelectorConfigError(ValueError):
    pass


def load_selectors(path: Path) -> dict[str, str]:
    values = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(values, dict):
        raise SelectorConfigError("选择器配置必须是键值映射")
    missing = sorted(key for key in REQUIRED_SELECTORS if not str(values.get(key, "")).strip())
    promotion_selector = str(
        values.get("export_promotion") or values.get("export_search") or ""
    ).strip()
    if not promotion_selector:
        missing.append("export_promotion/export_search")
    if missing:
        raise SelectorConfigError("缺少选择器: " + ", ".join(missing))
    normalized = {str(key): str(value).strip() for key, value in values.items()}
    normalized["export_promotion"] = promotion_selector
    normalized["export_search"] = promotion_selector
    return normalized
