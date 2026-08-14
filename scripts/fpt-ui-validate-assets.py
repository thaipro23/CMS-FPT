#!/usr/bin/env python3
"""Validate vendored FPT UI assets without third-party Python dependencies."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "fpt_indigo_ui" / "assets"

MINIMUMS = {
    "fpt-polytechnic-logo.png": (300, 100),
    "fpt-polytechnic-logo-white.png": (400, 140),
    "fpt-students.png": (1000, 600),
    "fpt-campus-primary.jpg": (1200, 650),
    "fpt-campus-secondary.jpg": (900, 550),
}
LOGO_NAMES = {
    "fpt-polytechnic-logo.png",
    "fpt-polytechnic-logo-white.png",
}
PHOTO_NAMES = set(MINIMUMS) - LOGO_NAMES
JPEG_SOF = {
    0xC0, 0xC1, 0xC2, 0xC3,
    0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB,
    0xCD, 0xCE, 0xCF,
}


def dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise RuntimeError(f"invalid PNG header: {path}")
        return struct.unpack(">II", data[16:24])

    if data.startswith(b"\xff\xd8"):
        offset = 2
        size = len(data)
        while offset < size:
            while offset < size and data[offset] == 0xFF:
                offset += 1
            if offset >= size:
                break
            marker = data[offset]
            offset += 1
            if marker in {0x01, *range(0xD0, 0xD9)}:
                continue
            if offset + 2 > size:
                break
            segment_len = struct.unpack(">H", data[offset:offset + 2])[0]
            if segment_len < 2 or offset + segment_len > size:
                raise RuntimeError(f"invalid JPEG segment: {path}")
            if marker in JPEG_SOF:
                if segment_len < 7:
                    raise RuntimeError(f"invalid JPEG SOF: {path}")
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                return width, height
            offset += segment_len
        raise RuntimeError(f"JPEG dimensions not found: {path}")

    raise RuntimeError(f"unsupported/incorrect image encoding: {path}")


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def main() -> None:
    measured: dict[str, tuple[int, int]] = {}
    for name, (min_width, min_height) in MINIMUMS.items():
        path = ASSET_DIR / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"missing/empty asset: {path.relative_to(ROOT)}")
        width, height = dimensions(path)
        measured[name] = (width, height)
        if width < min_width or height < min_height:
            raise SystemExit(
                f"asset resolution regression: {name}={width}x{height}; "
                f"minimum={min_width}x{min_height}"
            )
        print(f"[fpt-ui-assets] {name}: {width}x{height}, {path.stat().st_size} bytes")

    # The two logos are intentionally distinct files: a colour logo for light
    # headers and FPT-provided white artwork for navy/dark surfaces.
    colour_logo = ASSET_DIR / "fpt-polytechnic-logo.png"
    white_logo = ASSET_DIR / "fpt-polytechnic-logo-white.png"
    if digest(colour_logo) == digest(white_logo):
        raise SystemExit("colour and white FPT logo assets must not be identical")

    manifest_path = ASSET_DIR / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("missing FPT asset manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise SystemExit("unsupported FPT asset manifest schema")

    records = manifest.get("assets")
    if not isinstance(records, list) or len(records) != len(PHOTO_NAMES):
        raise SystemExit("FPT asset manifest must describe exactly the three photographic assets")

    indexed = {record.get("target"): record for record in records if isinstance(record, dict)}
    if set(indexed) != PHOTO_NAMES:
        raise SystemExit(f"FPT asset manifest target mismatch: {sorted(indexed)}")

    for name in sorted(PHOTO_NAMES):
        record = indexed[name]
        path = ASSET_DIR / name
        width, height = measured[name]
        if record.get("width") != width or record.get("height") != height:
            raise SystemExit(f"manifest dimension mismatch: {name}")
        if record.get("bytes") != path.stat().st_size:
            raise SystemExit(f"manifest byte-size mismatch: {name}")
        if record.get("sha256") != digest(path):
            raise SystemExit(f"manifest sha256 mismatch: {name}")
        source = str(record.get("source", ""))
        article = str(record.get("article", ""))
        if urlparse(source).hostname != "caodang.fpt.edu.vn":
            raise SystemExit(f"non-official asset source in manifest: {name}")
        if urlparse(article).hostname != "caodang.fpt.edu.vn":
            raise SystemExit(f"non-official source article in manifest: {name}")

    print("[fpt-ui-assets] RESOLUTION + MANIFEST FIDELITY PASS")


if __name__ == "__main__":
    main()
