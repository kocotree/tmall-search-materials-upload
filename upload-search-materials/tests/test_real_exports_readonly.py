from pathlib import Path

from upload_search_materials.io_tables import (
    read_basic_materials_xlsx,
    read_search_materials_xlsx,
    sha256_file,
)


DOCS = Path(__file__).parents[2] / "docs"
BASIC = DOCS / "基础素材.xlsx"
SEARCH = DOCS / "搜推素材数据经营数据_20260717_f7d80a06ea261ece5e44632121e19d40.xlsx"


def test_current_exports_parse_without_writes():
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
