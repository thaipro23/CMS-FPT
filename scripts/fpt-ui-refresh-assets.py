#!/usr/bin/env python3
"""Refresh the three photographic FPT UI assets from curated official sources.

This script is a maintainer tool only. Runtime and Tutor/Open edX Docker builds
remain fully offline with respect to these source URLs: the downloaded/optimized
files are committed to Git before any application image is built.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "fpt_indigo_ui" / "assets"
MANIFEST = ASSET_DIR / "manifest.json"
USER_AGENT = "Mozilla/5.0 (compatible; FPT-UI-Asset-Vendor/1.0; +https://caodang.fpt.edu.vn/)"


@dataclass(frozen=True)
class AssetSpec:
    key: str
    target: str
    role: str
    article: str
    candidates: tuple[str, ...]
    min_width: int
    min_height: int
    max_width: int
    output_format: str


SPECS = (
    AssetSpec(
        key="students",
        target="fpt-students.png",
        role="Discovery main card / student learning visual",
        article="https://caodang.fpt.edu.vn/ban-tin/uu-dai-laptop-cho-tan-sinh-vien-fpt-polytechnic-2026.html",
        candidates=(
            "https://caodang.fpt.edu.vn/wp-content/uploads/2026/05/a-1.webp",
            "https://caodang.fpt.edu.vn/wp-content/uploads/h%C3%ACnh-2-3-2-scaled.jpg",
            "https://caodang.fpt.edu.vn/wp-content/uploads/h%C3%ACnh-3-3-1-scaled.jpg",
        ),
        min_width=1000,
        min_height=600,
        max_width=1500,
        output_format="PNG",
    ),
    AssetSpec(
        key="campus_primary",
        target="fpt-campus-primary.jpg",
        role="Discovery supporting card",
        article="https://caodang.fpt.edu.vn/tin-tuc-poly/thuc-hanh-livestream-ngay-tai-phong-lab-sinh-vien-fpt-polytechnic-hoc-that-lam-that-len-song-that.html",
        candidates=(
            "https://caodang.fpt.edu.vn/wp-content/uploads/2026/04/1-e1775814263238.jpeg",
            "https://caodang.fpt.edu.vn/wp-content/uploads/2026/04/2-1.jpeg",
        ),
        min_width=1200,
        min_height=650,
        max_width=1920,
        output_format="JPEG",
    ),
    AssetSpec(
        key="campus_secondary",
        target="fpt-campus-secondary.jpg",
        role="Learner banner / Discovery supporting card",
        article="https://caodang.fpt.edu.vn/tin-tuc-poly/tim-hieu-he-thong-phong-thuc-hanh-hien-dai-cho-sinh-vien-nganh-du-lich-nha-hang-khach-san.html",
        candidates=(
            "https://caodang.fpt.edu.vn/wp-content/uploads/2025/06/FPT-Polytechnic_Ha-Noi-1.jpg",
            "https://caodang.fpt.edu.vn/wp-content/uploads/2025/07/FPT-Polytechnic_Ha-Noi-4.png",
            "https://caodang.fpt.edu.vn/wp-content/uploads/2025/07/FPT-Polytechnic_Ha-Noi-3.png",
            "https://caodang.fpt.edu.vn/wp-content/uploads/2026/03/image1-9.jpg",
        ),
        min_width=900,
        min_height=550,
        max_width=1600,
        output_format="JPEG",
    ),
)


def fetch(url: str, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": "https://caodang.fpt.edu.vn/",
                },
            )
            with urlopen(req, timeout=35) as response:
                content_type = response.headers.get("Content-Type", "")
                data = response.read()
            if not data:
                raise RuntimeError("empty response")
            if "image" not in content_type.lower() and len(data) < 50_000:
                raise RuntimeError(f"unexpected content type {content_type!r}")
            return data
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"download failed for {url}: {last_error}")


def decode_and_validate(data: bytes, spec: AssetSpec) -> tuple[Image.Image, tuple[int, int]]:
    with Image.open(BytesIO(data)) as source:
        source.load()
        source = ImageOps.exif_transpose(source)
        width, height = source.size
        if width < spec.min_width or height < spec.min_height:
            raise RuntimeError(
                f"source is too small: {width}x{height}; require at least "
                f"{spec.min_width}x{spec.min_height}"
            )
        ratio = width / height
        if ratio < 1.15 or ratio > 2.75:
            raise RuntimeError(f"unsuitable landscape ratio {ratio:.2f} ({width}x{height})")
        return source.convert("RGB"), (width, height)


def resize(image: Image.Image, max_width: int) -> Image.Image:
    if image.width <= max_width:
        return image
    height = round(image.height * max_width / image.width)
    return image.resize((max_width, height), Image.Resampling.LANCZOS)


def save(image: Image.Image, path: Path, output_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "JPEG":
        image.save(
            path,
            format="JPEG",
            quality=88,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )
    elif output_format == "PNG":
        image.save(path, format="PNG", optimize=True, compress_level=9)
    else:
        raise ValueError(f"unsupported format {output_format}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def refresh(spec: AssetSpec) -> dict[str, object]:
    errors: list[str] = []
    for url in spec.candidates:
        try:
            data = fetch(url)
            image, original_size = decode_and_validate(data, spec)
            final = resize(image, spec.max_width)
            target = ASSET_DIR / spec.target
            save(final, target, spec.output_format)
            if target.stat().st_size < 40_000:
                raise RuntimeError(f"optimized file unexpectedly small: {target.stat().st_size} bytes")
            print(
                f"[fpt-ui-assets] {spec.target}: {original_size[0]}x{original_size[1]} "
                f"-> {final.width}x{final.height}, {target.stat().st_size} bytes"
            )
            return {
                "key": spec.key,
                "target": spec.target,
                "role": spec.role,
                "article": spec.article,
                "source": url,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "width": final.width,
                "height": final.height,
                "bytes": target.stat().st_size,
                "sha256": sha256(target),
            }
        except Exception as exc:  # continue to explicit curated fallback
            errors.append(f"{url}: {exc}")
            print(f"[fpt-ui-assets] candidate rejected for {spec.target}: {url}: {exc}", file=sys.stderr)
    raise RuntimeError(
        f"no curated source passed for {spec.target}:\n  " + "\n  ".join(errors)
    )


def main() -> int:
    records = [refresh(spec) for spec in SPECS]
    manifest = {
        "schema": 1,
        "policy": "Official FPT Polytechnic editorial imagery vendored in Git; no runtime/build-time hotlinking.",
        "assets": records,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[fpt-ui-assets] manifest: {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
