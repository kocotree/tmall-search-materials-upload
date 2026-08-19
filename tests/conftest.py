from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_machine_runtime_config(monkeypatch, tmp_path):
    """Keep every test away from the workstation's real machine config."""

    user_data_root = tmp_path / "machine-runtime"
    config = user_data_root / "config" / "runtime.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("TMALL_USER_DATA_ROOT", str(user_data_root))
