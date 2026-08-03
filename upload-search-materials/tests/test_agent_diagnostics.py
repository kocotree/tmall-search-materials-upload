from upload_search_materials.agent_diagnostics import (
    read_agent_diagnostic,
    resolve_agent_diagnostic,
    write_agent_diagnostic,
    write_exception_diagnostic,
)
from upload_search_materials.interaction.session import SessionStore


def test_agent_diagnostic_preserves_retry_identity_and_user_boundary(tmp_path):
    session_id = "20260803_120000"
    session_path = tmp_path / session_id

    written = write_agent_diagnostic(
        session_path,
        session_id=session_id,
        stage_id="setup",
        revision=2,
        input_sha256="a" * 64,
        reason_codes=["LOGIN_INTERACTION_REQUIRED"],
        phase="collection_login_check",
        message="login required",
        evidence=[session_path / "login-required.json"],
    )

    assert written["owner"] == "user_via_codex"
    assert written["user_action_required"] is True
    assert "process-setup" in written["retry"]["command"]
    assert f'--session "{session_id}"' in written["retry"]["command"]
    current = read_agent_diagnostic(tmp_path, session_id)
    assert current["status"] == "open"
    assert current["revision"] == 2

    resolved = resolve_agent_diagnostic(
        session_path, stage_id="setup", revision=2
    )

    assert resolved["status"] == "resolved"


def test_processor_exception_uses_unified_codex_diagnostic(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    try:
        raise RuntimeError("PLAYWRIGHT_TARGET_CHANGED: iframe moved")
    except RuntimeError as error:
        written = write_exception_diagnostic(
            store,
            session.session_id,
            "asset_matching",
            processor="process-final-material-handoff",
            phase="validate_and_plan",
            error=error,
            handoff_kind="final_material_selection",
        )

    assert written["reason_code"] == "PLAYWRIGHT_TARGET_CHANGED"
    assert written["owner"] == "codex"
    assert written["processor"] == "process-final-material-handoff"
    assert "process-final-material-handoff" in written["retry"]["command"]
    assert "RuntimeError" in written["traceback"]
