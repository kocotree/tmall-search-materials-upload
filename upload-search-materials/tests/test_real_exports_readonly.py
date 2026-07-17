from pathlib import Path
import os

import pytest

from upload_search_materials.io_tables import (
    read_basic_materials_xlsx,
    read_search_materials_xlsx,
    sha256_file,
)


def _docs_dir() -> Path:
    configured = os.environ.get("TMALL_MATERIAL_DOCS")
    if configured:
        return Path(configured)
    candidates = [Path(__file__).parents[2] / "docs"]
    parents = Path(__file__).parents
    if len(parents) > 4:
        candidates.append(parents[4] / "docs")
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


DOCS = _docs_dir()
BASIC = DOCS / "基础素材.xlsx"
SEARCH = DOCS / "搜推素材数据经营数据_20260717_f7d80a06ea261ece5e44632121e19d40.xlsx"


def test_current_exports_parse_without_writes():
    if not BASIC.is_file() or not SEARCH.is_file():
        pytest.skip("真实 docs 在当前测试沙箱中不可见；需设置 TMALL_MATERIAL_DOCS")
    before = {
        BASIC: (BASIC.stat().st_size, BASIC.stat().st_mtime_ns, sha256_file(BASIC)),
        SEARCH: (SEARCH.stat().st_size, SEARCH.stat().st_mtime_ns, sha256_file(SEARCH)),
    }

    basic = read_basic_materials_xlsx(BASIC)
    search = read_search_materials_xlsx(SEARCH)

    after = {
        BASIC: (BASIC.stat().st_size, BASIC.stat().st_mtime_ns, sha256_file(BASIC)),
        SEARCH: (SEARCH.stat().st_size, SEARCH.stat().st_mtime_ns, sha256_file(SEARCH)),
    }
    assert len(basic) == 2158
    assert len(search) == 808
    assert len({row["商品ID"] for row in search if row["商品ID"]}) == 629
    assert all(isinstance(row["商品ID"], str) for row in basic if row["商品ID"])
    assert before == after
