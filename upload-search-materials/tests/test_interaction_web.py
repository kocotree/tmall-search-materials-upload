import re
import subprocess
from pathlib import Path

import pytest

from upload_search_materials.interaction.web import create_app


@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path).test_client()


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json["session_id"]


def valid_production_confirmation():
    return {
        "store": "测试店铺",
        "product_ids": ["887508274682"],
        "task_ids": ["run-product-image-1"],
        "slot_ids": ["image-1"],
        "max_products": 1,
        "approval_manifest_sha256": "a" * 64,
        "final_confirmation": True,
        "notes": "仅生成交接，不在页面执行发布",
    }


def test_create_session_returns_timestamp_id(client):
    response = client.post("/api/sessions", json={})

    assert response.status_code == 201
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d{2})?", response.json["session_id"])


def test_root_is_json_service_description(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.is_json
    assert response.json["service"] == "upload-search-materials interaction API"


@pytest.mark.parametrize(
    ("data", "content_type", "status"),
    [(b"{}", None, 415), (b"{", "application/json", 400)],
)
def test_json_body_errors_are_json_with_field_errors(client, data, content_type, status):
    response = client.post("/api/sessions", data=data, content_type=content_type)

    assert response.status_code == status
    assert response.is_json
    assert response.json["field_errors"] == {}
    assert response.json["error"]


def test_submit_rejects_missing_required_store(client, session_id):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={"values": {"month": "2026-07"}},
    )

    assert response.status_code == 422
    assert "store" in response.json["field_errors"]


def test_draft_is_saved_without_handoff(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={"values": {"store": "测试店铺"}},
    )

    assert response.status_code == 200
    assert response.json["status"] == "draft"
    assert not list(tmp_path.glob(f"{session_id}/**/handoff.json"))


def test_draft_rejects_unknown_values_without_persisting_sensitive_input(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={"values": {"store": "测试店铺", "password": "secret"}},
    )

    assert response.status_code == 422
    assert "password" in response.json["field_errors"]
    assert not list(tmp_path.glob(f"{session_id}/**/input.json"))


def test_submit_rejects_unknown_values_without_persisting_sensitive_input(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation() | {"cookie": "secret"}},
    )

    assert response.status_code == 422
    assert "cookie" in response.json["field_errors"]
    assert not list(tmp_path.glob(f"{session_id}/**/input.json"))


def test_draft_after_submit_invalidates_its_handoff_and_marks_stage_draft(client, session_id, tmp_path):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )
    drafted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/draft",
        json={"values": {"store": "updated store"}},
    )

    assert submitted.status_code == 202
    assert drafted.status_code == 200
    assert client.get(f"/api/sessions/{session_id}/stages/production_confirmation/status").json == {
        "revision": 2,
        "status": "draft",
    }
    assert not list(tmp_path.glob(f"{session_id}/**/handoff.json"))
    input_path = next(tmp_path.glob(f"{session_id}/**/input.json"))
    assert '"password"' not in input_path.read_text(encoding="utf-8")


def test_submit_returns_store_handoff_identity(client, session_id):
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )

    assert response.status_code == 202
    assert response.json["revision"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", response.json["input_sha256"])


def test_production_confirmation_route_only_writes_handoff(client, session_id, monkeypatch):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(subprocess, "run", forbidden)
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )

    assert response.status_code == 202
    assert called is False


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/sessions/missing"),
        ("get", "/api/sessions/missing/stages/setup"),
        ("get", "/api/sessions/missing/stages/setup/status"),
        ("get", "/api/sessions/missing/stages/setup/recovery"),
        ("post", "/api/sessions/missing/stages/setup/draft"),
        ("post", "/api/sessions/missing/stages/setup/submit"),
        ("get", "/api/sessions/../escape"),
    ],
)
def test_unknown_sessions_are_not_found(client, method, url):
    response = getattr(client, method)(url, json={})

    assert response.status_code == 404
    assert response.json["error"] == "not found"


@pytest.mark.parametrize("stage", ["missing", "..", "../setup"])
def test_unknown_or_traversal_stage_is_rejected(client, session_id, stage):
    response = client.get(f"/api/sessions/{session_id}/stages/{stage}")

    assert response.status_code in {400, 404}
    assert response.is_json


def test_submit_rejects_stale_revision(client, session_id):
    first = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation(), "revision": 0},
    )

    assert first.status_code == 409
    assert client.get(f"/api/sessions/{session_id}/stages/production_confirmation/status").json == {
        "revision": 0,
        "status": "draft",
    }


def test_validation_requires_override_reasons_and_enum_values(client, session_id):
    overrides = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={
            "values": {
                "confirmed_product_ids": ["887508274682"],
                "overrides": [{"product_id": "887508274682", "reason": ""}],
            }
        },
    )
    decision = client.post(
        f"/api/sessions/{session_id}/stages/dry_run/submit",
        json={"values": {"decision": ["confirm"]}},
    )

    assert overrides.status_code == 422
    assert "overrides" in overrides.json["field_errors"]
    assert decision.status_code == 422
    assert "decision" in decision.json["field_errors"]


def test_stage_read_and_status_expose_schema_and_state(client, session_id):
    stage = client.get(f"/api/sessions/{session_id}/stages/setup")
    status = client.get(f"/api/sessions/{session_id}/stages/setup/status")

    assert stage.status_code == 200
    assert stage.json["stage"]["id"] == "setup"
    assert {field["name"] for field in stage.json["stage"]["fields"]} >= {"store", "month"}
    assert status.json == {"revision": 0, "status": "draft"}


def test_recovery_returns_session_store_instruction(client, session_id):
    response = client.get(f"/api/sessions/{session_id}/stages/setup/recovery")

    assert response.status_code == 200
    assert "handoff.json" in response.json["instruction"]


def test_validation_enforces_paths_lists_dates_and_boolean_confirmation(client, session_id, tmp_path):
    path = tmp_path / "readable.txt"
    path.write_text("ok", encoding="utf-8")
    response = client.post(
        f"/api/sessions/{session_id}/stages/approval/submit",
        json={
            "values": {
                "task_ids": "not a list",
                "confirmed_by": "reviewer",
                "confirmed_at": "not-a-date",
                "valid_until": "2026-07-21T12:00:00",
                "acknowledgement": False,
            }
        },
    )

    assert response.status_code == 422
    assert {"task_ids", "confirmed_at", "acknowledgement"} <= response.json["field_errors"].keys()


def test_setup_validation_requires_readable_paths_and_exactly_one_asset_source(client, session_id, tmp_path):
    readable = tmp_path / "readable.txt"
    readable.write_text("ok", encoding="utf-8")
    values = {
        "store": "测试店铺",
        "month": "2026-07",
        "product_scope": "single",
        "products_csv": str(readable),
        "rules_csv": str(readable),
        "basic_xlsx": str(readable),
        "search_xlsx": str(readable),
        "runs_root": str(tmp_path),
        "asset_root": str(tmp_path),
        "asset_manifest": str(readable),
    }

    response = client.post(f"/api/sessions/{session_id}/stages/setup/submit", json={"values": values})

    assert response.status_code == 422
    assert "asset_root" in response.json["field_errors"]


def test_source_code_never_imports_execution_modules():
    source = Path(__file__).parents[1] / "src" / "upload_search_materials" / "interaction" / "web.py"
    text = source.read_text(encoding="utf-8")

    assert "subprocess" not in text
    assert "playwright" not in text.lower()
    assert "BrowserUploader" not in text
    assert "_publish" not in text
