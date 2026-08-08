from upload_search_materials.asset_selection import build_selection_batch


def candidate(
    asset_id,
    sha256,
    *,
    source_system="model_nas",
    source_path="",
    match_type="exact_product_id",
    match_status="matched_unlicensed",
    license_status="confirmed",
    validation_status="valid",
):
    return {
        "asset_id": asset_id,
        "product_id": "123",
        "source_system": source_system,
        "source_path": source_path or f"C:/assets/{asset_id}.jpg",
        "sha256": sha256,
        "match_type": match_type,
        "match_status": match_status,
        "license_status": license_status,
        "validation_status": validation_status,
    }


def test_selection_is_deterministic_and_deduplicates_sha256():
    values = [
        candidate("sku", "same", match_type="exact_sku"),
        candidate("id", "same", match_type="exact_product_id"),
        candidate("third", "third", source_system="xhs_taobao_nas"),
        candidate("second", "second", source_system="model_nas"),
    ]

    result = build_selection_batch(values, material_count=1)

    assert result["required_images"] == 3
    assert [item["asset_id"] for item in result["selected"]] == [
        "id",
        "second",
        "third",
    ]
    assert result["duplicate_sha256_count"] == 1
    assert result["groups"][0]["group_index"] == 1
    assert len(result["groups"][0]["assets"]) == 3


def test_change_batch_uses_stable_non_overlapping_windows():
    values = [
        candidate(str(index), f"sha-{index}", source_path=f"C:/assets/{index:02}.jpg")
        for index in range(12)
    ]

    first = build_selection_batch(values, material_count=2, batch_index=0)
    second = build_selection_batch(values, material_count=2, batch_index=1)

    assert [item["asset_id"] for item in first["selected"]] == [str(index) for index in range(6)]
    assert [item["asset_id"] for item in second["selected"]] == [
        str(index) for index in range(6, 12)
    ]
    assert not set(first["selected_sha256"]) & set(second["selected_sha256"])


def test_selection_excludes_remote_and_ineligible_candidates():
    values = [
        candidate("remote", "remote-sha"),
        candidate("unknown-license", "unknown", license_status="unknown"),
        candidate("invalid", "invalid", validation_status="blocked"),
        candidate(
            "name-pending",
            "name",
            match_type="name_candidate",
            match_status="needs_manual_confirmation",
        ),
        candidate("usable", "usable"),
    ]

    result = build_selection_batch(
        values,
        material_count=1,
        remote_sha256={"remote-sha"},
    )

    assert [item["asset_id"] for item in result["selected"]] == ["usable"]
    assert result["remote_duplicate_count"] == 1
    assert result["status"] == "insufficient_candidates"
    assert result["remote_dedupe_status"] == "checked"


def test_missing_remote_fingerprints_stays_explicitly_unchecked():
    result = build_selection_batch(
        [candidate("usable", "usable")],
        material_count=1,
        remote_sha256=None,
    )

    assert result["remote_dedupe_status"] == "not_available"
    assert "REMOTE_FINGERPRINTS_UNAVAILABLE" in result["reason_codes"]
