"""Shared durable-file contract for local workflow state."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Sequence


WINDOWS_TRANSIENT_REPLACE_ERRORS = frozenset({32, 33})
WINDOWS_ACCESS_DENIED = 5
WINDOWS_REPLACE_ATTEMPTS = 13
WINDOWS_RETRY_DELAY_SECONDS = 0.025
_replace_locks_guard = threading.Lock()
_replace_locks: dict[str, threading.Lock] = {}


class PersistenceAccessDenied(PermissionError):
    """A durable target is not safely replaceable by the current identity."""

    reason_code = "PERSISTENCE_ACCESS_DENIED"

    def __init__(self, path: Path):
        super().__init__(f"{self.reason_code}:{path.name}")
        self.path = Path(path)


def _native_path(path: Path) -> str:
    value = str(Path(path).absolute())
    if os.name != "nt" or value.startswith("\\\\?\\") or len(value) < 240:
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _replace_with_retry(
    temporary: Path,
    target: Path,
    *,
    replace_attempts: int = WINDOWS_REPLACE_ATTEMPTS,
    retry_delay_seconds: float = WINDOWS_RETRY_DELAY_SECONDS,
) -> None:
    for attempt in range(max(1, int(replace_attempts))):
        try:
            os.replace(_native_path(temporary), _native_path(target))
            return
        except OSError as error:
            winerror = getattr(error, "winerror", None)
            transient = (
                os.name == "nt"
                and (
                    winerror in WINDOWS_TRANSIENT_REPLACE_ERRORS
                    or (
                        winerror == WINDOWS_ACCESS_DENIED
                        and _writable_target_access_denied_may_be_transient(
                            target
                        )
                    )
                )
            )
            if not transient or attempt + 1 >= replace_attempts:
                if os.name == "nt" and winerror == WINDOWS_ACCESS_DENIED:
                    raise PersistenceAccessDenied(target) from error
                raise
            time.sleep(retry_delay_seconds * (attempt + 1))


def _writable_target_access_denied_may_be_transient(target: Path) -> bool:
    """Disambiguate WinError 5 without treating read-only/ACL errors as leases."""

    try:
        if not target.is_file():
            return False
        mode = target.stat().st_mode
        if not mode & stat.S_IWRITE:
            return False
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".atomic-probe-", suffix=".tmp", dir=target.parent
        )
        os.close(descriptor)
        Path(probe_name).unlink()
        return True
    except OSError:
        return False


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    replace_attempts: int = WINDOWS_REPLACE_ATTEMPTS,
    retry_delay_seconds: float = WINDOWS_RETRY_DELAY_SECONDS,
) -> None:
    """Write canonical bytes through a unique sibling and atomic replace."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".atomic-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Locking needs a stable lexical key, not filesystem resolution.  This
        # also keeps local progress writes from touching unavailable NAS path
        # components during rematch-only workflows.
        key = os.path.normcase(os.path.abspath(os.fspath(target)))
        with _replace_locks_guard:
            replace_lock = _replace_locks.setdefault(key, threading.Lock())
        with replace_lock:
            _replace_with_retry(
                temporary,
                target,
                replace_attempts=replace_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def canonical_json_bytes(
    document: Mapping[str, Any], *, sort_keys: bool = False
) -> bytes:
    """Return the UTF-8/no-BOM bytes used by JSON identity hashes."""

    return (
        json.dumps(
            dict(document),
            ensure_ascii=False,
            indent=2,
            sort_keys=sort_keys,
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_json(
    path: Path,
    document: Mapping[str, Any],
    *,
    sort_keys: bool = False,
) -> None:
    atomic_write_bytes(
        path, canonical_json_bytes(document, sort_keys=sort_keys)
    )


def read_json(path: Path) -> Any:
    """Read either UTF-8 or Windows UTF-8-SIG JSON."""

    with open(_native_path(path), encoding="utf-8-sig") as stream:
        return json.load(stream)


def atomic_write_dict_csv(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
    extrasaction: str = "ignore",
) -> None:
    """Write Excel-compatible UTF-8-SIG CSV with the same atomic contract."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".atomic-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=list(fieldnames),
                extrasaction=extrasaction,
            )
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        key = os.path.normcase(str(target.resolve()))
        with _replace_locks_guard:
            replace_lock = _replace_locks.setdefault(key, threading.Lock())
        with replace_lock:
            _replace_with_retry(temporary, target)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
