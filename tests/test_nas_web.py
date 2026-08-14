from pathlib import Path

from upload_search_materials.interaction.web import create_app
from upload_search_materials.nas_sources import NasSourceStatus
from upload_search_materials.runtime_config import load_runtime_config


def make_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "config").mkdir(parents=True)
    (workspace / "runs").mkdir()
    (workspace / "src" / "upload_search_materials" / "docs").mkdir(parents=True)
    (workspace / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (workspace / "pyproject.toml").write_text("[project]\nname='test'\n")
    (workspace / "config" / "nas-sources.yaml").write_text(
        """
nas_sources:
  - id: visual-department
    label: 视觉部
    host: 192.168.124.85
    share: 视觉部
    windows_path: '\\\\192.168.124.85\\视觉部'
    subpaths: []
""".strip(),
        encoding="utf-8",
    )
    return workspace


def test_nas_status_endpoint_returns_catalog_and_mount_binding(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    runtime = load_runtime_config(environ={}, start=workspace)
    monkeypatch.setattr(
        "upload_search_materials.interaction.web.check_nas_source",
        lambda source: NasSourceStatus(
            source.source_id, "ready", "", r"\\192.168.124.85\视觉部", "NAS 来源已连接且可读"
        ),
    )
    client = create_app(workspace / "runs", runtime).test_client()

    response = client.get("/api/runtime/nas-sources")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["nas_sources"][0]["source_id"] == "visual-department"
    assert payload["nas_sources"][0]["status"]["mount_path"] == r"\\192.168.124.85\视觉部"
    assert payload["nas_sources"][0]["canonical_unc"] == r"\\192.168.124.85\视觉部"
    page = client.get("/")
    page_source = page.get_data(as_text=True)
    assert "公司共享盘" in page_source
    assert 'class="nas-source-row"' in page_source
    assert "添加整个共享盘" in page_source


def test_connect_endpoint_only_launches_after_explicit_post(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    runtime = load_runtime_config(environ={}, start=workspace)
    launches = []
    monkeypatch.setattr(
        "upload_search_materials.interaction.web.check_nas_source",
        lambda source: NasSourceStatus(
            source.source_id, "not_mounted", "NAS_NOT_MOUNTED",
            r"\\192.168.124.85\视觉部", "NAS 共享尚未连接",
        ),
    )
    monkeypatch.setattr(
        "upload_search_materials.interaction.web.launch_nas_mount",
        lambda source: launches.append(source.source_id),
    )
    client = create_app(workspace / "runs", runtime).test_client()

    assert client.get("/api/runtime/nas-sources").status_code == 200
    assert launches == []
    response = client.post("/api/runtime/nas-sources/visual-department/connect", json={})

    assert response.status_code == 200
    assert response.get_json()["launched"] is True
    assert launches == ["visual-department"]


def test_browse_endpoint_returns_only_requested_directory_level(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    runtime = load_runtime_config(environ={}, start=workspace)
    calls = []
    monkeypatch.setattr(
        "upload_search_materials.interaction.web.browse_nas_folders",
        lambda source, relative_path="": calls.append((source.source_id, relative_path))
        or [{"name": "模特图", "relative_path": "素材/模特图"}],
    )
    client = create_app(workspace / "runs", runtime).test_client()

    response = client.get(
        "/api/runtime/nas-sources/visual-department/directories?relative_path=素材"
    )

    assert response.status_code == 200
    assert response.get_json()["directories"] == [
        {"name": "模特图", "relative_path": "素材/模特图"}
    ]
    assert calls == [("visual-department", "素材")]


def test_nas_ui_starts_from_share_root_and_uses_responsive_actions():
    package = Path(__file__).parents[1] / "src" / "upload_search_materials" / "interaction"
    javascript = (package / "static" / "app.js").read_text(encoding="utf-8")
    stylesheet = (package / "static" / "app.css").read_text(encoding="utf-8")

    use_source = javascript.split("function useNasSource(row) {", 1)[1].split("}", 1)[0]
    assert 'addNasDirectory(row, "");' in use_source
    assert "subpaths.length" not in use_source
    assert ".nas-source-row" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr) minmax(160px, .7fr);" in stylesheet
    assert ".nas-source-actions {" in stylesheet
    assert "white-space: nowrap;" in stylesheet
    assert "max-width: 100%;" in stylesheet


def test_nas_and_selected_image_sources_have_distinct_section_headings():
    template = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert 'class="source-section source-section-shared"' in template
    assert 'id="shared-source-heading">公司共享盘</h4>' in template
    assert "选中的目录会加入下方“本次图片源”" in template
    assert 'class="source-section source-section-selected"' in template
    assert 'id="selected-source-heading">本次图片源</h4>' in template
