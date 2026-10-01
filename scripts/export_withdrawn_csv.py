#!/usr/bin/env python3
"""Export OGM withdrawal entries with metadata titles and codes to CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import NamedTuple


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METADATA_DIRECTORY = REPOSITORY_ROOT / "metadata-aardvark"
DEFAULT_WITHDRAWN_FILE = REPOSITORY_ROOT / "withdrawn.json"


class Record(NamedTuple):
    title: str
    code: str


def iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a valid date; use YYYY-MM-DD"
        ) from error


def load_records(directory: Path, wanted_ids: set[str]) -> dict[str, Record]:
    if not directory.is_dir():
        raise ValueError(f"metadata directory does not exist: {directory}")

    records: dict[str, Record] = {}
    for path in directory.rglob("*.json"):
        record_id = path.stem
        if record_id not in wanted_ids:
            continue
        if record_id in records:
            raise ValueError(f"duplicate record ID below {directory}: {record_id}")

        try:
            with path.open(encoding="utf-8") as stream:
                metadata = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read metadata record {path}: {error}") from error

        if not isinstance(metadata, dict):
            raise ValueError(f"metadata record is not a JSON object: {path}")

        title = metadata.get("dct_title_s", "")
        if not isinstance(title, str):
            raise ValueError(f"dct_title_s is not a string in {path}")

        relative_parts = path.relative_to(directory).parts
        # Normal records use Category/Code/ID.json. Top-level category records,
        # such as Collections/ID.json, have no separate code value.
        code = relative_parts[-2] if len(relative_parts) >= 3 else ""
        records[record_id] = Record(title=title, code=code)

    return records


def load_withdrawals(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError as error:
        raise ValueError(f"withdrawal file does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read withdrawal file {path}: {error}") from error

    if not isinstance(data, dict) or not isinstance(data.get("withdrawn"), list):
        raise ValueError(f"{path} does not contain a withdrawn array")

    withdrawals: list[dict[str, str]] = []
    for index, entry in enumerate(data["withdrawn"]):
        if not isinstance(entry, dict):
            raise ValueError(f"withdrawn entry {index} is not an object")
        record_id = entry.get("id")
        withdrawal_date = entry.get("date")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"withdrawn entry {index} has an invalid id")
        if not isinstance(withdrawal_date, str):
            raise ValueError(f"withdrawn entry {index} has an invalid date")
        try:
            date.fromisoformat(withdrawal_date)
        except ValueError as error:
            raise ValueError(
                f"withdrawn entry {index} has an invalid date: {withdrawal_date!r}"
            ) from error
        withdrawals.append({"id": record_id, "date": withdrawal_date})

    return withdrawals


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a CSV report of withdrawn IDs, titles, and codes."
    )
    parser.add_argument("output", type=Path, help="CSV file to create")
    parser.add_argument(
        "--metadata-directory",
        type=Path,
        default=DEFAULT_METADATA_DIRECTORY,
        help=f"metadata used to resolve titles and codes (default: {DEFAULT_METADATA_DIRECTORY})",
    )
    parser.add_argument(
        "--withdrawn-file",
        type=Path,
        default=DEFAULT_WITHDRAWN_FILE,
        help=f"withdrawal log (default: {DEFAULT_WITHDRAWN_FILE})",
    )
    parser.add_argument(
        "--date",
        dest="withdrawal_date",
        type=iso_date,
        help="include only withdrawals with this YYYY-MM-DD date",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        withdrawals = load_withdrawals(arguments.withdrawn_file.resolve())
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if arguments.withdrawal_date:
        withdrawals = [
            entry
            for entry in withdrawals
            if entry["date"] == arguments.withdrawal_date
        ]

    try:
        records = load_records(
            arguments.metadata_directory.resolve(),
            {entry["id"] for entry in withdrawals},
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    output = arguments.output.resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=("id", "title", "code", "withdrawal_date")
            )
            writer.writeheader()
            for entry in sorted(withdrawals, key=lambda item: item["id"]):
                record = records.get(entry["id"], Record(title="", code=""))
                writer.writerow(
                    {
                        "id": entry["id"],
                        "title": record.title,
                        "code": record.code,
                        "withdrawal_date": entry["date"],
                    }
                )
    except OSError as error:
        print(f"error: could not write {output}: {error}", file=sys.stderr)
        return 1

    unresolved = sum(entry["id"] not in records for entry in withdrawals)
    print(f"Wrote {len(withdrawals)} withdrawal rows to {output}.")
    if unresolved:
        print(
            f"Warning: {unresolved} IDs were not found in the metadata directory; "
            "their title and code columns are blank.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
