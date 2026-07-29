from pathlib import Path
import tomllib


def test_interaction_ui_assets_are_declared_as_setuptools_package_data():
    project = Path(__file__).parents[1]
    configuration = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))

    package_data = configuration["tool"]["setuptools"]["package-data"]
    patterns = package_data["upload_search_materials.interaction"]
    assert "templates/*.html" in patterns
    assert "static/*" in patterns
    assert "native_folder_picker.ps1" in patterns
