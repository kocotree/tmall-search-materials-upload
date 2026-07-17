from pathlib import Path

from ..io_tables import sha256_file
from ..models import SourceFile


def _download_report(page, selector: str, output_dir: Path, run_id: str, label: str, downloaded_at: str):
    with page.expect_download() as download_info:
        page.locator(selector).click()
    download = download_info.value
    safe_name = Path(download.suggested_filename).name
    destination = output_dir / f"{run_id}-{label}-{safe_name}"
    download.save_as(destination)
    return SourceFile(
        path=str(destination.resolve()),
        sha256=sha256_file(destination),
        downloaded_at=downloaded_at,
        original_name=safe_name,
    )


def export_reports(
    page,
    selectors: dict[str, str],
    output_dir: Path,
    *,
    run_id: str,
    downloaded_at: str,
) -> list[SourceFile]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    return [
        _download_report(page, selectors["export_basic"], target, run_id, "basic", downloaded_at),
        _download_report(page, selectors["export_search"], target, run_id, "search", downloaded_at),
    ]
