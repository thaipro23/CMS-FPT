#!/usr/bin/env python3
"""Fail fast when a Learning checkout cannot host the FPT header actions."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PACKAGE_KEY = "node_modules/@edx/frontend-component-header"
MINIMUM_HEADER_VERSION = "8.2.1"


def _version_tuple(version: str) -> tuple[int, int, int]:
    core = version.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid frontend-component-header version: {version!r}")
    return tuple(int(part) for part in parts)


def locked_header_version(package_lock: dict) -> str:
    version = (
        package_lock.get("packages", {}).get(PACKAGE_KEY, {}).get("version")
        or package_lock.get("dependencies", {})
        .get("@edx/frontend-component-header", {})
        .get("version")
    )
    if not isinstance(version, str) or not version:
        raise ValueError("frontend-component-header is missing from package-lock.json")
    return version


def validate_header_version(
    package_lock: dict,
    minimum: str = MINIMUM_HEADER_VERSION,
) -> str:
    actual = locked_header_version(package_lock)
    if _version_tuple(actual) < _version_tuple(minimum):
        raise ValueError(
            "Learning runtime is incompatible with the FPT header actions slot: "
            f"frontend-component-header must be >= {minimum}, found {actual}"
        )
    return actual


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {argv[0]} /path/to/package-lock.json", file=sys.stderr)
        return 2

    lock_path = Path(argv[1])
    try:
        package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        version = validate_header_version(package_lock)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[fpt-learning-guard] ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        "[fpt-learning-guard] "
        f"frontend-component-header={version} PASS (minimum={MINIMUM_HEADER_VERSION})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
