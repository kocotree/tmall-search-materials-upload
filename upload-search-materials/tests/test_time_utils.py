from datetime import datetime

from upload_search_materials.time_utils import iso_timestamp


def test_iso_timestamp_assigns_timezone_to_naive_value():
    value = iso_timestamp(datetime(2026, 7, 29, 12, 0, 0))

    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert value.endswith("+00:00")
