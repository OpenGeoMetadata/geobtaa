#!/usr/bin/env python3
"""Add records missing from a new metadata export to withdrawn.json.

Record IDs are derived from JSON filenames, without the .json extension. This
matches the identifier convention used by the OGM withdrawal file.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OLD_DIRECTORY = REPOSITORY_ROOT / "metadata-aardvark"
DEFAULT_WITHDRAWN_FILE = REPOSITORY_ROOT / "withdrawn.json"
EXPECTED_SCHEMA = "https://opengeometadata.org/schema/ogm-withdrawals-1.0.json"


def iso_date(value: str) -> str:
    """Return a validated ISO 8601 calendar date."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a valid date; use YYYY-MM-DD"
        ) from error


def record_paths(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        raise ValueError(f"metadata directory does not exist: {directory}")

    paths: dict[str, Path] = {}
    for path in directory.rglob("*.json"):
        record_id = path.stem
        if record_id in paths:
            raise ValueError(f"duplicate record ID found below {directory}: {record_id}")
        paths[record_id] = path
    return paths


def load_withdrawn(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError as error:
        raise ValueError(f"withdrawal file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {path}: {error}") from error

    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if data.get("$schema") != EXPECTED_SCHEMA:
        raise ValueError(f"{path} does not use the expected OGM withdrawal schema")
    if data.get("ogm_version") != "1.0":
        raise ValueError(f"{path} does not use OGM withdrawal version 1.0")

    entries = data.get("withdrawn")
    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain a 'withdrawn' array")

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"withdrawn entry {index} is not an object")
        record_id = entry.get("id")
        entry_date = entry.get("date")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"withdrawn entry {index} has an invalid id")
        if record_id in seen:
            raise ValueError(f"withdrawn.json contains duplicate id: {record_id}")
        seen.add(record_id)
        if not isinstance(entry_date, str):
            raise ValueError(f"withdrawn entry {index} has an invalid date")
        try:
            date.fromisoformat(entry_date)
        except ValueError as error:
            raise ValueError(
                f"withdrawn entry {index} has an invalid date: {entry_date!r}"
            ) from error

    return data


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write valid JSON beside the destination, then atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_csv_report(
    path: Path,
    record_ids: list[str],
    old_directory: Path,
    old_paths: dict[str, Path],
    withdrawal_date: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("id", "title", "code", "withdrawal_date")
        )
        writer.writeheader()
        for record_id in record_ids:
            metadata_path = old_paths[record_id]
            try:
                with metadata_path.open(encoding="utf-8") as metadata_stream:
                    metadata = json.load(metadata_stream)
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"could not read metadata record {metadata_path}: {error}"
                ) from error
            if not isinstance(metadata, dict):
                raise ValueError(f"metadata record is not a JSON object: {metadata_path}")
            title = metadata.get("dct_title_s", "")
            if not isinstance(title, str):
                raise ValueError(f"dct_title_s is not a string in {metadata_path}")

            relative_parts = metadata_path.relative_to(old_directory).parts
            code = relative_parts[-2] if len(relative_parts) >= 3 else ""
            writer.writerow(
                {
                    "id": record_id,
                    "title": title,
                    "code": code,
                    "withdrawal_date": withdrawal_date,
                }
            )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find record IDs in the current metadata directory that are absent "
            "from a new export and add them to an OGM withdrawn.json file."
        )
    )
    parser.add_argument("new_directory", type=Path, help="new metadata export")
    parser.add_argument(
        "--old-directory",
        type=Path,
        default=DEFAULT_OLD_DIRECTORY,
        help=f"current metadata directory (default: {DEFAULT_OLD_DIRECTORY})",
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
        default=date.today().isoformat(),
        help="withdrawal date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="update withdrawn.json; without this flag the command is a dry run",
    )
    parser.add_argument(
        "--csv-report",
        type=Path,
        help="write candidate new withdrawals to a CSV with titles and codes",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        old_directory = arguments.old_directory.resolve()
        old_paths = record_paths(old_directory)
        new_paths = record_paths(arguments.new_directory.resolve())
        data = load_withdrawn(arguments.withdrawn_file.resolve())
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    old_ids = set(old_paths)
    new_ids = set(new_paths)
    existing_ids = {entry["id"] for entry in data["withdrawn"]}
    retired_ids = old_ids - new_ids
    additions = sorted(retired_ids - existing_ids)

    print(f"Current records:            {len(old_ids):>8}")
    print(f"New export records:         {len(new_ids):>8}")
    print(f"Retired records detected:   {len(retired_ids):>8}")
    print(f"Already logged:             {len(retired_ids & existing_ids):>8}")
    print(f"New withdrawal entries:     {len(additions):>8}")

    if arguments.csv_report:
        try:
            write_csv_report(
                arguments.csv_report.resolve(),
                additions,
                old_directory,
                old_paths,
                arguments.withdrawal_date,
            )
        except (OSError, ValueError) as error:
            print(f"error: could not write CSV report: {error}", file=sys.stderr)
            return 1
        print(f"CSV preview:                {arguments.csv_report.resolve()}")

    if not additions:
        print("No changes needed.")
        return 0

    if not arguments.write:
        if arguments.csv_report:
            print("Dry run only; withdrawn.json was not changed. Review the CSV, then rerun.")
        else:
            print("Dry run only; rerun with --write after reviewing these IDs:")
            for record_id in additions:
                print(record_id)
        return 0

    data["withdrawn"].extend(
        {"id": record_id, "date": arguments.withdrawal_date}
        for record_id in additions
    )
    data["withdrawn"].sort(key=lambda entry: entry["id"])
    try:
        atomic_write(arguments.withdrawn_file.resolve(), data)
    except OSError as error:
        print(f"error: could not write withdrawal file: {error}", file=sys.stderr)
        return 1

    print(
        f"Added {len(additions)} entries dated {arguments.withdrawal_date} to "
        f"{arguments.withdrawn_file}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
