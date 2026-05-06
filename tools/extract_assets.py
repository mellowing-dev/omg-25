#!/usr/bin/env python3
"""
Extract base64-embedded images and fonts from index.html into assets/,
rewrite the HTML to reference them as files, and add mobile-friendly
loading hints (lazy/async/decoding, explicit width+height).

Run from repo root: python3 tools/extract_assets.py
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
HTML = REPO / "index.html"
IMG_DIR = REPO / "assets" / "img"
FONT_DIR = REPO / "assets" / "fonts"

SOURCE_PIECES = Path(
    "/Users/melo/Library/Mobile Documents/com~apple~CloudDocs/OMG/25 OMG/OMG_25_Piezas 2"
)

# Font weight order matches the four @font-face blocks in index.html:
#   Whitney 500, Whitney 600, Whitney 900, WhitneySC 900
FONT_NAMES = [
    "whitney-500.otf",
    "whitney-600.otf",
    "whitney-900.otf",
    "whitney-sc-900.otf",
]

MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/gif": "gif",
}

# Hero/above-the-fold images that should NOT be lazy-loaded.
EAGER_HINTS = ("hero-logo", "big-logo")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_source_index() -> dict[str, str]:
    """Map sha256 -> friendly stem for every PNG/JPG in the source folder."""
    if not SOURCE_PIECES.exists():
        return {}
    idx: dict[str, str] = {}
    for p in SOURCE_PIECES.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            digest = sha256_bytes(p.read_bytes())
            idx[digest] = p.stem.replace(" ", "-")
    return idx


def decode_image(data_uri: str) -> tuple[bytes, str]:
    """Returns (binary, ext). data_uri is the full 'data:image/...;base64,XXX' string."""
    head, b64 = data_uri.split(",", 1)
    mime = head.split(":", 1)[1].split(";", 1)[0]
    ext = MIME_TO_EXT.get(mime, "bin")
    return base64.b64decode(b64), ext


def img_dims(blob: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(blob)) as im:
            return im.size
    except Exception:
        return None


def main() -> int:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    FONT_DIR.mkdir(parents=True, exist_ok=True)

    html = HTML.read_text()
    src_index = build_source_index()
    print(f"Indexed {len(src_index)} source images for hash matching.")

    # ── 1. Fonts: @font-face url(data:font/otf;base64,...) ────────────────
    font_pattern = re.compile(
        r"src:\s*url\(data:font/otf;base64,([A-Za-z0-9+/=]+)\)\s*format\('opentype'\)"
    )
    fonts = font_pattern.findall(html)
    if len(fonts) != 4:
        print(f"WARN: expected 4 fonts, found {len(fonts)}")

    for i, b64 in enumerate(fonts):
        if i >= len(FONT_NAMES):
            break
        name = FONT_NAMES[i]
        out = FONT_DIR / name
        blob = base64.b64decode(b64)
        out.write_bytes(blob)
        print(f"  font  → assets/fonts/{name}  ({len(blob):,} bytes)")
        old = f"src: url(data:font/otf;base64,{b64}) format('opentype')"
        new = f"src: url('assets/fonts/{name}') format('opentype')"
        # Only replace the first occurrence so each font file maps 1:1
        html = html.replace(old, new, 1)

    # ── 2. Images: <img ... src="data:image/...;base64,...">  ─────────────
    img_pattern = re.compile(
        r'<img\b([^>]*?)\bsrc="(data:image/[^";]+;base64,[A-Za-z0-9+/=]+)"([^>]*)>',
        re.DOTALL,
    )

    seen: dict[str, str] = {}  # sha -> filename (so duplicates share one file)
    counter = 0

    def replace_img(match: re.Match) -> str:
        nonlocal counter
        before, data_uri, after = match.group(1), match.group(2), match.group(3)
        blob, ext = decode_image(data_uri)
        digest = sha256_bytes(blob)

        if digest in seen:
            filename = seen[digest]
        else:
            counter += 1
            stem = src_index.get(digest)
            if stem:
                filename = f"{stem}.{ext}"
            else:
                filename = f"img-{counter:03d}.{ext}"
            (IMG_DIR / filename).write_bytes(blob)
            seen[digest] = filename

        # Decide eager vs lazy
        attrs = (before + after).lower()
        is_eager = any(h in attrs for h in EAGER_HINTS)

        # Build new attribute list, preserving original ordering of any non-src attrs
        rebuilt = (before + after).strip()
        # Inject loading/decoding/dims if not already present
        extras: list[str] = []
        if "loading=" not in rebuilt:
            extras.append('loading="lazy"' if not is_eager else 'loading="eager"')
        if "decoding=" not in rebuilt:
            extras.append('decoding="async"')
        if "fetchpriority=" not in rebuilt and is_eager:
            extras.append('fetchpriority="high"')

        dims = img_dims(blob)
        if dims and "width=" not in rebuilt and "height=" not in rebuilt:
            extras.append(f'width="{dims[0]}" height="{dims[1]}"')

        attr_str = (rebuilt + " " + " ".join(extras)).strip()
        return f'<img {attr_str} src="assets/img/{filename}">'

    new_html, n = img_pattern.subn(replace_img, html)
    print(f"Rewrote {n} <img> tags; wrote {counter} unique image files.")

    HTML.write_text(new_html)
    print(f"index.html: {HTML.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
