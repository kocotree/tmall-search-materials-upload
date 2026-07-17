from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Iterable


ALLOWED_TRANSITIONS = {
    "pending_validation": {"needs_manual_review", "ready_for_review"},
    "needs_manual_review": {"ready_for_review", "failed"},
    "ready_for_review": {"approved", "needs_manual_review"},
    "approved": {"uploading", "ready_for_review", "blocked"},
    "uploading": {"submitted", "failed", "publish_uncertain"},
    "publish_uncertain": {"submitted", "under_review", "success", "failed"},
    "submitted": {"under_review", "success", "failed"},
    "under_review": {"success", "failed"},
    "failed": {"ready_for_review"},
}

VERIFICATION_STATUSES = {"uploading", "submitted", "under_review", "publish_uncertain"}
RECOVERABLE_STATUSES = {"ready_for_review", "approved"}


class InvalidTransition(ValueError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    store TEXT NOT NULL,
                    month INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_items (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    remote_material_id TEXT,
                    evidence TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    old_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES material_items(task_id)
                );
                """
            )

    def close(self) -> None:
        self.connection.close()

    def save_run(
        self,
        *,
        run_id: str,
        store: str,
        month: int,
        mode: str,
        status: str,
        config_hash: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO runs(run_id, store, month, mode, status, config_hash)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    store=excluded.store,
                    month=excluded.month,
                    mode=excluded.mode,
                    status=excluded.status,
                    config_hash=excluded.config_hash
                """,
                (run_id, store, month, mode, status, config_hash),
            )

    def get_run(self, run_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT run_id, store, month, mode, status, config_hash FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def save_item(
        self,
        task_id: str,
        status: str,
        *,
        remote_material_id: str | None = None,
        evidence: str = "",
        attempt_count: int = 0,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO material_items(
                    task_id, status, remote_material_id, evidence, attempt_count, updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    remote_material_id=excluded.remote_material_id,
                    evidence=excluded.evidence,
                    attempt_count=excluded.attempt_count,
                    updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    status,
                    remote_material_id,
                    evidence,
                    attempt_count,
                    utc_now_iso(),
                ),
            )

    def item_status(self, task_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM material_items WHERE task_id=?",
            (task_id,),
        ).fetchone()
        return str(row["status"]) if row else None

    def record_transition(
        self,
        task_id: str,
        old_status: str,
        new_status: str,
        *,
        reason: str,
        evidence: str,
    ) -> None:
        current = self.item_status(task_id)
        if current != old_status:
            raise InvalidTransition(f"状态已变化: expected={old_status}, actual={current}")
        if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
            raise InvalidTransition(f"{old_status} -> {new_status}")
        timestamp = utc_now_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO transitions(
                    task_id, old_status, new_status, reason, evidence, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (task_id, old_status, new_status, reason, evidence, timestamp),
            )
            self.connection.execute(
                "UPDATE material_items SET status=?, evidence=?, updated_at=? WHERE task_id=?",
                (new_status, evidence, timestamp, task_id),
            )

    def transitions_for(self, task_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT old_status, new_status, reason, evidence, created_at
            FROM transitions WHERE task_id=? ORDER BY id
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recoverable_items(self, candidate_ids: Iterable[str]) -> list[str]:
        recoverable = []
        for task_id in candidate_ids:
            row = self.connection.execute(
                "SELECT status, remote_material_id, evidence FROM material_items WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                recoverable.append(task_id)
                continue
            has_remote_evidence = bool(row["remote_material_id"]) or (
                row["status"] in VERIFICATION_STATUSES and bool(row["evidence"])
            )
            if not has_remote_evidence and row["status"] in RECOVERABLE_STATUSES:
                recoverable.append(task_id)
        return recoverable

    def items_requiring_verification(self) -> list[str]:
        placeholders = ",".join("?" for _ in VERIFICATION_STATUSES)
        rows = self.connection.execute(
            f"SELECT task_id FROM material_items WHERE status IN ({placeholders}) ORDER BY task_id",
            tuple(sorted(VERIFICATION_STATUSES)),
        ).fetchall()
        return [str(row["task_id"]) for row in rows]
