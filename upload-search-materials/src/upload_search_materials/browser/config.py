from pathlib import Path

import yaml


REQUIRED_SELECTORS = {
    "store_name",
    "human_check",
    "export_basic",
    "export_search",
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
}


class SelectorConfigError(ValueError):
    pass


def load_selectors(path: Path) -> dict[str, str]:
    values = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(values, dict):
        raise SelectorConfigError("选择器配置必须是键值映射")
    missing = sorted(key for key in REQUIRED_SELECTORS if not str(values.get(key, "")).strip())
    if missing:
        raise SelectorConfigError("缺少选择器: " + ", ".join(missing))
    return {str(key): str(value).strip() for key, value in values.items()}
