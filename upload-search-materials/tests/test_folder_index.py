from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

from upload_search_materials.asset_index import NamedRoot
from upload_search_materials.asset_matching import PathMatch
from upload_search_materials.cli import main
from upload_search_materials.folder_index import (
    build_folder_review_data,
    build_folder_index,
    rematch_folder_index,
    snapshot_folder_candidates,
    write_folder_candidates,
)


class FolderMatcher:
    def match(self, path: Path):
        if "KQ25046" not in path.as_posix():
            return ()
        return (
            PathMatch(
                "898453469175",
                "KQ25046",
                "涂鸦艺术家分体泳衣",
                "exact_sku",
                "matched_unlicensed",
                (),
            ),
        )


def test_folder_index_never_reads_or_hashes_image_files(tmp_path, monkeypatch):
    root = tmp_path / "root"
    candidate = root / "2025" / "KQ25046"
    candidate.mkdir(parents=True)
    image = candidate / "broken.jpg"
    image.write_bytes(b"must-not-be-read")
    original = image.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("folder index must not inspect image contents")

    monkeypatch.setattr(
        "upload_search_materials.folder_index.hashlib.sha256",
        lambda value: __import__("hashlib").new("sha256", value),
    )
    database = tmp_path / "output" / "folder-index.sqlite3"
    summary = build_folder_index(
        database_path=database,
        products_sha256="a" * 64,
        roots=(NamedRoot("model_nas", root),),
        matcher=FolderMatcher(),
        checkpoint_size=1,
    )

    assert summary["folders_discovered"] == 2
    assert summary["matched_folders"] == 1
    assert image.read_bytes() == original
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    connection.close()


def test_folder_index_does_not_inherit_parent_match_into_generic_children(tmp_path):
    root = tmp_path / "root"
    (root / "KQ25046" / "KV").mkdir(parents=True)
    database = tmp_path / "output" / "folder-index.sqlite3"

    summary = build_folder_index(
        database_path=database,
        products_sha256="a" * 64,
        roots=(NamedRoot("model_nas", root),),
        matcher=FolderMatcher(),
    )

    assert summary["matched_folders"] == 1
    connection = sqlite3.connect(database)
    matched_names = [
        row[0]
        for row in connection.execute(
            "SELECT folder_name FROM folders JOIN matches USING(folder_id)"
        )
    ]
    connection.close()
    assert matched_names == ["KQ25046"]


def test_folder_index_refresh_marks_removed_directories_inactive(tmp_path):
    root = tmp_path / "root"
    removed = root / "old"
    removed.mkdir(parents=True)
    database = tmp_path / "output" / "folder-index.sqlite3"
    options = {
        "database_path": database,
        "products_sha256": "a" * 64,
        "roots": (NamedRoot("model_nas", root),),
        "matcher": FolderMatcher(),
    }
    build_folder_index(**options)
    removed.rmdir()
    (root / "new").mkdir()

    summary = build_folder_index(**options, refresh=True)

    assert summary["complete"] is True
    connection = sqlite3.connect(database)
    states = dict(connection.execute("SELECT folder_name, active FROM folders"))
    connection.close()
    assert states == {"old": 0, "new": 1}


def test_rematch_uses_stored_folder_names_without_rescanning_root(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    (root / "KQ25046").mkdir(parents=True)
    database = tmp_path / "output" / "folder-index.sqlite3"
    build_folder_index(
        database_path=database,
        products_sha256="a" * 64,
        roots=(NamedRoot("model_nas", root),),
        matcher=FolderMatcher(),
    )
    connection = sqlite3.connect(database)
    connection.execute("UPDATE folders SET folder_name='unrelated'")
    connection.commit()
    connection.close()
    root.rename(tmp_path / "root-unavailable")
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self: (_ for _ in ()).throw(
            AssertionError("rematch-only must not resolve or access a NAS root")
        ),
    )

    summary = rematch_folder_index(
        database_path=database,
        products_sha256="a" * 64,
        roots=(NamedRoot("model_nas", root),),
        matcher=FolderMatcher(),
    )

    assert summary["matched_folders"] == 0
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    connection.close()


def test_rematch_cli_does_not_require_the_declared_root_to_be_online(tmp_path):
    products = tmp_path / "products.csv"
    products.write_text(
        "商品ID,商品名称（查找引用）,货号（查找引用）,产品等级,链接,运营,组别,品类-公司维度划分\n"
        "898453469175,涂鸦艺术家分体泳衣,KQ25046,S级,https://example.test/item,运营,一组,泳衣\n",
        encoding="utf-8-sig",
    )
    root = tmp_path / "root"
    (root / "KQ25046").mkdir(parents=True)
    output = tmp_path / "output"
    common = [
        "--products",
        str(products),
        "--root",
        f"model_nas={root}",
        "--output",
        str(output),
    ]

    assert main(["index-folders", *common]) == 0
    root.rename(tmp_path / "root-offline")

    assert main(["index-folders", *common, "--rematch-only"]) == 0
    assert (output / "folder-candidates.csv").is_file()


def test_folder_candidates_csv_contains_folder_not_image_records(tmp_path):
    root = tmp_path / "root"
    (root / "KQ25046").mkdir(parents=True)
    database = tmp_path / "output" / "folder-index.sqlite3"
    build_folder_index(
        database_path=database,
        products_sha256="a" * 64,
        roots=(NamedRoot("model_nas", root),),
        matcher=FolderMatcher(),
    )

    output = tmp_path / "folder-candidates.csv"
    assert write_folder_candidates(database, output) == 1
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["folder_name"] == "KQ25046"
    assert row["product_id"] == "898453469175"
    assert row["match_status"] == "matched_unlicensed"


def test_task_snapshot_filters_shared_candidates_without_copying_index(tmp_path):
    shared = tmp_path / "shared-folder-candidates.csv"
    shared.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-2,buyer,Z:/b,2026/b,B,2,S2,商品2,name_candidate,needs_manual_confirmation\n"
        "F-1,model,Y:/a,2025/a,A,1,S1,商品1,exact_sku,matched_unlicensed\n"
        "F-3,buyer,Z:/c,2026/c,C,1,S1,商品1,name_candidate,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )
    task_snapshot = tmp_path / "run" / "folder-candidates.csv"

    summary = snapshot_folder_candidates(shared, task_snapshot, ["1"])

    assert summary == {
        "requested_products": 1,
        "matched_products": 1,
        "candidate_rows": 2,
    }
    with task_snapshot.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["folder_id"] for row in rows] == ["F-3", "F-1"]
    assert all(row["product_id"] == "1" for row in rows)


def test_snapshot_folder_candidates_cli_writes_task_local_csv(tmp_path):
    shared = tmp_path / "folder-candidates.csv"
    shared.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-1,model,Y:/a,2025/a,A,1,S1,商品1,exact_sku,matched_unlicensed\n"
        "F-2,buyer,Z:/b,2026/b,B,2,S2,商品2,name_candidate,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )
    output = tmp_path / "task" / "folder-candidates.csv"

    exit_code = main(
        [
            "snapshot-folder-candidates",
            "--candidates",
            str(shared),
            "--product-id",
            "2",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["product_id"] for row in rows] == ["2"]


def test_index_folders_cli_builds_lightweight_outputs(tmp_path):
    root = tmp_path / "root"
    (root / "涂鸦艺术家分体泳衣").mkdir(parents=True)
    products = tmp_path / "products.csv"
    products.write_text(
        "商品ID,商品名称（查找引用）,货号（查找引用）,产品等级,链接,运营,组别,品类-公司维度划分\n"
        "898453469175,涂鸦艺术家分体泳衣,KQ25046,B级,https://example.invalid,测试,测试组,泳衣\n",
        encoding="utf-8-sig",
    )
    output = tmp_path / "output"

    exit_code = main(
        [
            "index-folders",
            "--products",
            str(products),
            "--root",
            f"model_nas={root}",
            "--output",
            str(output),
            "--checkpoint-size",
            "1",
        ]
    )

    assert exit_code == 0
    assert (output / "folder-index.sqlite3").is_file()
    assert (output / "folder-candidates.csv").is_file()
    assert (output / "folder-scan-summary.json").is_file()


def test_folder_review_data_preserves_decisions_and_safety_contract(tmp_path):
    candidates = tmp_path / "folder-candidates.csv"
    candidates.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-2,xhs,Z:/buyer,25/buyer,达人泳衣,898453469175,KQ25046,涂鸦艺术家分体泳衣,name_candidate,needs_manual_confirmation\n"
        "F-1,model,Y:/model,2025/model,KQ25046,898453469175,KQ25046,涂鸦艺术家分体泳衣,exact_sku,matched_unlicensed\n"
        "F-3,xhs,Z:/legacy,26/legacy,分龄成长太阳镜,886506466908,KQ25029,分龄成长软软镜/稳稳镜/酷酷镜,confirmed_alias,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )

    data = build_folder_review_data(
        candidates,
        decisions=[
            {
                "folder_id": "F-2",
                "product_id": "898453469175",
                "decision": "confirmed",
            }
        ],
    )

    assert data["safety_status"] == "folders_only"
    assert [row["folder_id"] for row in data["folder_candidates"]] == ["F-1", "F-2"]
    assert data["folder_candidates"][1]["decision"] == "confirmed"
    assert "alias" not in data["folder_candidates"][1]
    assert {row["folder_id"] for row in data["folder_candidates"]} == {"F-1", "F-2"}
    assert data["folder_products"] == [
        {
            "product_id": "898453469175",
            "product_title": "涂鸦艺术家分体泳衣",
            "sku": "KQ25046",
            "candidate_count": 2,
        }
    ]


def test_folder_review_accepts_run_scoped_exact_folder_query_without_alias(tmp_path):
    candidates = tmp_path / "folder-candidates.csv"
    candidates.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-3,xhs,Z:/太阳镜,26/1/分龄成长太阳镜,分龄成长太阳镜,886506466908,KQ25029,分龄成长软软镜/稳稳镜/酷酷镜,confirmed_alias,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )

    data = build_folder_review_data(
        candidates,
        exact_folder_queries=[
            {
                "product_id": "886506466908",
                "folder_name": "分龄成长太阳镜",
            }
        ],
    )

    assert len(data["folder_candidates"]) == 1
    candidate = data["folder_candidates"][0]
    assert candidate["folder_name"] == "分龄成长太阳镜"
    assert candidate["match_type"] == "exact_folder_query"
    assert candidate["decision"] == "confirmed"
    assert "alias" not in candidate


def test_folder_review_treats_missing_and_historical_pending_as_confirmed(tmp_path):
    candidates = tmp_path / "folder-candidates.csv"
    candidates.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-1,model,Y:/one,one,商品,1,SKU,商品,name_candidate,needs_manual_confirmation\n"
        "F-2,buyer,Z:/two,two,商品,1,SKU,商品,name_candidate,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )

    data = build_folder_review_data(
        candidates,
        decisions=[
            {
                "folder_id": "F-2",
                "product_id": "1",
                "decision": "pending",
            }
        ],
    )

    assert [
        item["decision"] for item in data["folder_candidates"]
    ] == ["confirmed", "confirmed"]


def test_folder_review_defaults_fuzzy_candidates_to_rejected(tmp_path):
    candidates = tmp_path / "folder-candidates.csv"
    candidates.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-1,model,Y:/one,one,果立方卡片太阳镜,768088523792,KQ24027,小魔方卡片太阳镜（主）,fuzzy_name_candidate,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )

    default_data = build_folder_review_data(candidates)
    confirmed_data = build_folder_review_data(
        candidates,
        decisions=[
            {
                "folder_id": "F-1",
                "product_id": "768088523792",
                "decision": "confirmed",
            }
        ],
    )

    assert default_data["folder_candidates"][0]["decision"] == "rejected"
    assert confirmed_data["folder_candidates"][0]["decision"] == "confirmed"


def test_prepare_folder_review_cli_writes_ui_payload(tmp_path):
    candidates = tmp_path / "folder-candidates.csv"
    candidates.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-1,model,Y:/model,2025/model,KQ25046,898453469175,KQ25046,涂鸦艺术家分体泳衣,exact_sku,matched_unlicensed\n",
        encoding="utf-8-sig",
    )
    output = tmp_path / "folder-review.json"

    assert main(
        [
            "prepare-folder-review",
            "--candidates",
            str(candidates),
            "--output",
            str(output),
        ]
    ) == 0

    assert '"review_type": "folder_ownership"' in output.read_text(encoding="utf-8")


def test_prepare_folder_review_cli_accepts_exact_folder_query(tmp_path):
    candidates = tmp_path / "folder-candidates.csv"
    candidates.write_text(
        "folder_id,source_system,absolute_path,relative_path,folder_name,product_id,sku,product_title,match_type,match_status\n"
        "F-3,xhs,Z:/太阳镜,26/1/分龄成长太阳镜,分龄成长太阳镜,886506466908,KQ25029,分龄成长软软镜/稳稳镜/酷酷镜,confirmed_alias,needs_manual_confirmation\n",
        encoding="utf-8-sig",
    )
    output = tmp_path / "folder-review.json"

    assert main(
        [
            "prepare-folder-review",
            "--candidates",
            str(candidates),
            "--output",
            str(output),
            "--exact-folder",
            "886506466908=分龄成长太阳镜",
        ]
    ) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["folder_candidates"][0]["match_type"] == "exact_folder_query"
