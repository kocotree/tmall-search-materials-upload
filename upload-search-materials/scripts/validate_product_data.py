from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from upload_search_materials.io_tables import (
    SchemaError,
    read_product_csv,
    validate_product_records,
)


def _write_report(report: dict, output_path: str | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if output_path:
        Path(output_path).write_text(rendered + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Tmall product source CSV.")
    parser.add_argument("--input", required=True, help="Product CSV path")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args(argv)

    try:
        records = read_product_csv(Path(args.input))
    except SchemaError as error:
        report = {
            "input": args.input,
            "row_count": 0,
            "reason_codes_by_row": {},
            "blocking": True,
            "batch_blocking": True,
            "error_code": "DATA_SCHEMA",
            "message": str(error),
        }
        _write_report(report, args.output)
        return 2

    validation = validate_product_records(records)
    report = {
        "input": args.input,
        "row_count": validation.row_count,
        "reason_codes_by_row": validation.reason_codes_by_row,
        "blocking": validation.blocking,
        "batch_blocking": validation.batch_blocking,
    }
    _write_report(report, args.output)
    return 1 if validation.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
