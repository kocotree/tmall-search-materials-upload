import hashlib
import json
from pathlib import Path

from upload_search_materials.dry_run_workflow import (
    prepare_publish_run_from_authorization,
)
from upload_search_materials.cli import build_parser
from upload_search_materials.state_store import StateStore


class FakeSessionStore:
    def __init__(self, root: Path):
        self.root = root

    def _session_path(self, session_id: str) -> Path:
        return self.root / session_id

    def _stage_path(self, session_id: str, stage_id: str) -> Path:
        names = {"approval": "07-approval", "dry_run": "06-dry-run"}
        return self._session_path(session_id) / names[stage_id]

    def _read_json(self, path: Path, label: str):
        return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_publish_authorization_processor_defaults_to_workbench_dispatcher():
    args = build_parser().parse_args(
        [
            "process-publish-authorization",
            "--runs-root",
            "runs",
            "--session",
            "20260815_120000",
        ]
    )

    assert args.claimant_id == "workbench-dispatcher"


def test_publish_bridge_is_idempotent_and_does_not_reset_attempted_state(tmp_path):
    session_id = "20260803_120000"
    store = FakeSessionStore(tmp_path)
    session_path = store._session_path(session_id)
    approval_path = store._stage_path(session_id, "approval")
    dry_path = store._stage_path(session_id, "dry_run")
    media_path = session_path / "05-slots-copy" / "derived" / "one.jpg"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"approved-image")

    approval_input_path = approval_path / "input.json"
    dry_input_path = dry_path / "input.json"
    dry_result_path = dry_path / "result.json"
    dry_tasks_path = dry_path / "dry-run-tasks.json"
    write_json(approval_input_path, {"revision": 1, "values": {}})
    write_json(dry_input_path, {"revision": 1})
    write_json(dry_result_path, {"status": "completed"})
    write_json(
        dry_tasks_path,
        {
            "store": "测试店铺",
            "tasks": [
                {
                    "task_id": "task-1",
                    "status": "ready_for_review",
                    "product_id": "10001",
                    "remote_slot_position": 3,
                    "title": "批准标题",
                    "description": "批准正文",
                    "blocking_reasons": [],
                    "media": [
                        {
                            "order": 1,
                            "output_path": str(media_path),
                            "output_sha256": sha256(media_path),
                            "width": 1200,
                            "height": 1600,
                            "size_bytes": len(media_path.read_bytes()),
                        }
                    ],
                }
            ],
        },
    )
    write_json(
        approval_path / "handoff.json",
        {
            "handoff_kind": "publish_authorization",
            "revision": 1,
            "input_sha256": sha256(approval_input_path),
            "authorization": {
                "action": "approve_and_publish_exact_tasks",
                "final_confirmation": True,
                "store": "测试店铺",
                "task_ids": ["task-1"],
                "confirmed_by": "测试用户",
                "dry_run_input_sha256": sha256(dry_input_path),
                "dry_run_result_sha256": sha256(dry_result_path),
            },
        },
    )

    first = prepare_publish_run_from_authorization(store, session_id)
    assert first["existing"] is False

    run_path = Path(first["run_dir"])
    state = StateStore(run_path / "run.sqlite3")
    state.save_item(
        "task-1",
        "submitted",
        remote_material_id="REMOTE-1",
        evidence="published",
        attempt_count=1,
    )
    state.close()

    second = prepare_publish_run_from_authorization(store, session_id)
    assert second["existing"] is True
    state = StateStore(run_path / "run.sqlite3")
    assert state.item_record("task-1")["status"] == "submitted"
    assert state.item_record("task-1")["remote_material_id"] == "REMOTE-1"
    state.close()
