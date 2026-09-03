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
SLIDER_DIMENSIONS = {
    "fpt-slider-01-male-desktop.webp": (1920, 650),
    "fpt-slider-01-male-mobile.webp": (1080, 1350),
    "fpt-slider-02-female-desktop.webp": (1920, 650),
    "fpt-slider-02-female-mobile.webp": (1080, 1350),
    "fpt-slider-03-group-desktop.webp": (1920, 650),
    "fpt-slider-03-group-mobile.webp": (1080, 1350),
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


def _webp_dimensions(data: bytes, path: Path) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise RuntimeError(f"invalid WebP header: {path}")

    offset = 12
    while offset + 8 <= len(data):
        fourcc = data[offset:offset + 4]
        chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(data):
            raise RuntimeError(f"invalid WebP chunk: {path}")
        chunk = data[payload_start:payload_end]

        if fourcc == b"VP8X":
            if len(chunk) < 10:
                raise RuntimeError(f"invalid VP8X chunk: {path}")
            width = 1 + int.from_bytes(chunk[4:7], "little")
            height = 1 + int.from_bytes(chunk[7:10], "little")
            return width, height

        if fourcc == b"VP8L":
            if len(chunk) < 5 or chunk[0] != 0x2F:
                raise RuntimeError(f"invalid VP8L chunk: {path}")
            width = 1 + (chunk[1] | ((chunk[2] & 0x3F) << 8))
            height = 1 + (
                ((chunk[2] & 0xC0) >> 6)
                | (chunk[3] << 2)
                | ((chunk[4] & 0x0F) << 10)
            )
            return width, height

        if fourcc == b"VP8 ":
            if len(chunk) < 10 or chunk[3:6] != b"\x9d\x01\x2a":
                raise RuntimeError(f"invalid VP8 frame header: {path}")
            width = int.from_bytes(chunk[6:8], "little") & 0x3FFF
            height = int.from_bytes(chunk[8:10], "little") & 0x3FFF
            return width, height

        offset = payload_end + (chunk_size & 1)

    raise RuntimeError(f"WebP dimensions not found: {path}")


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

    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _webp_dimensions(data, path)

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

    for name, expected in SLIDER_DIMENSIONS.items():
        path = ASSET_DIR / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"missing/empty slider asset: {path.relative_to(ROOT)}")
        actual = dimensions(path)
        if actual != expected:
            raise SystemExit(
                f"slider resolution regression: {name}={actual[0]}x{actual[1]}; "
                f"expected={expected[0]}x{expected[1]}"
            )
        print(f"[fpt-ui-assets] {name}: {actual[0]}x{actual[1]}, {path.stat().st_size} bytes")

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

    print("[fpt-ui-assets] RESOLUTION + MANIFEST + RESPONSIVE SLIDER PASS")


if __name__ == "__main__":
    main()
