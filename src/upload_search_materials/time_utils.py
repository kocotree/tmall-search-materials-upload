"""Timezone-aware timestamp helpers for persisted workflow evidence."""

from __future__ import annotations

from datetime import datetime, timezone


def iso_timestamp(
    value: datetime | None = None, *, timespec: str = "seconds"
) -> str:
    """Return an unambiguous UTC ISO 8601 timestamp."""

    selected = value or datetime.now(timezone.utc)
    if selected.tzinfo is None:
        selected = selected.astimezone()
    return selected.astimezone(timezone.utc).isoformat(timespec=timespec)
