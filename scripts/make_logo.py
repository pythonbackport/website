#!/usr/bin/env python3
"""
make_logo.py — turn `pythonbackportlogo.png` into a web-ready asset.

The source artwork is an opaque PNG (451x385) with a near-white background.
On the site's dark theme that white rectangle would show up as a bright
block, so this script:

  1. decodes the PNG with nothing but the standard library,
  2. converts the white backdrop into real alpha (standard
     "white to alpha" unpremultiply: a = 255 - min(r, g, b)),
  3. crops the transparent margin so the mark fills its box,
  4. writes `logo.png` (RGBA) with no external dependencies.

Usage:
  python scripts/make_logo.py            # regenerate logo.png
  python scripts/make_logo.py --check    # fail if logo.png is stale
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "pythonbackportlogo.png"
OUTPUT = ROOT / "logo.png"

# Keep a couple of pixels of breathing room around the cropped artwork.
PADDING = 4


# ---------- PNG decoding ----------------------------------------------------

def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """Undo the per-scanline PNG filters, returning raw pixel bytes."""
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev[i]
                upleft = prev[i - channels] if i >= channels else 0
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                if pa <= pb and pa <= pc:
                    pred = left
                elif pb <= pc:
                    pred = up
                else:
                    pred = upleft
                line[i] = (line[i] + pred) & 0xFF
        out += line
        prev = line
    return out


def read_png(path: Path) -> tuple[int, int, bytes]:
    """Return (width, height, RGBA bytes) for an 8-bit PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"Not a PNG file: {path}")

    pos = 8
    idat = bytearray()
    width = height = color_type = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, _depth, color_type = struct.unpack(">IIBB", payload[:10])
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        pos += 12 + length

    if color_type is None or width is None or height is None:
        raise SystemExit(f"Corrupt PNG header in {path}")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    pixels = _unfilter(zlib.decompress(bytes(idat)), width, height, channels)

    # Normalise every colour type to RGBA.
    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        r, g, b, a = 255, 255, 255, 255
        if color_type == 6:
            r, g, b, a = pixels[i * 4:i * 4 + 4]
        elif color_type == 2:
            r, g, b = pixels[i * 3:i * 3 + 3]
        elif color_type == 4:
            r, g, b, a = pixels[i * 4], pixels[i * 4 + 1], pixels[i * 4 + 2], pixels[i * 4 + 3]
        elif color_type == 0:
            r = g = b = pixels[i]
        elif color_type == 3:
            raise SystemExit("Palette PNGs are not supported")
        rgba[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return width, height, bytes(rgba)


# ---------- PNG encoding ----------------------------------------------------

def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        raw += rgba[y * stride:(y + 1) * stride]
    out = b"\x89PNG\r\n\x1a\n"
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    out += _chunk(b"IEND", b"")
    return out


# ---------- Processing ------------------------------------------------------

def _brand_colors(rgba: bytes, width: int, height: int) -> list[tuple[int, int, int]]:
    """Find the flat artwork colours (the arrow is blue, the loop is yellow).

    The mark is flat vector art on a white backdrop, so the interior of each
    shape is one solid colour. We take the most frequent non-white colour in
    each hue cluster as that shape's brand colour.
    """
    blues: dict[tuple[int, int, int], int] = {}
    yellows: dict[tuple[int, int, int], int] = {}
    for i in range(0, len(rgba), 4):
        r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
        if r > 245 and g > 245 and b > 245:
            continue  # backdrop
        key = (r, g, b)
        if b > r:
            blues[key] = blues.get(key, 0) + 1
        elif r > b:
            yellows[key] = yellows.get(key, 0) + 1
    out: list[tuple[int, int, int]] = []
    for table in (blues, yellows):
        if table:
            out.append(max(table.items(), key=lambda kv: kv[1])[0])
    return out


def matte_to_alpha(rgba: bytes, width: int, height: int,
                   colors: list[tuple[int, int, int]]) -> bytes:
    """Remove a white backdrop, recovering true per-pixel opacity.

    A pixel of flat art composited over white obeys ``C = a*F + (1-a)*255``
    where ``F`` is the shape's brand colour and ``a`` its coverage. For each
    candidate ``F`` we solve for ``a`` by least squares and keep the colour
    with the best fit, which reproduces crisp flat shapes with soft edges.
    """
    out = bytearray(rgba)
    for i in range(0, len(out), 4):
        r, g, b = out[i], out[i + 1], out[i + 2]
        best_alpha = 0.0
        best_color = None
        best_residual = None
        for f in colors:
            num = (255 - r) * (255 - f[0]) + (255 - g) * (255 - f[1]) + (255 - b) * (255 - f[2])
            den = (255 - f[0]) ** 2 + (255 - f[1]) ** 2 + (255 - f[2]) ** 2
            a = 0.0 if den == 0 else num / den
            a = min(1.0, max(0.0, a))
            residual = ((r - (a * f[0] + (1 - a) * 255)) ** 2
                        + (g - (a * f[1] + (1 - a) * 255)) ** 2
                        + (b - (a * f[2] + (1 - a) * 255)) ** 2)
            # Prefer the smallest alpha when two fits are equally plausible;
            # that keeps anti-aliased seams between shapes from blooming.
            if best_residual is None or residual < best_residual - 1.0 or (
                    residual < best_residual + 1.0 and a < best_alpha):
                best_residual = residual
                best_alpha = a
                best_color = f
        if best_color is None or best_alpha <= 0.004:
            out[i:i + 4] = b"\x00\x00\x00\x00"
            continue
        out[i] = best_color[0]
        out[i + 1] = best_color[1]
        out[i + 2] = best_color[2]
        out[i + 3] = int(round(best_alpha * 255))
    return bytes(out)



def content_bbox(rgba: bytes, width: int, height: int, threshold: int = 8) -> tuple[int, int, int, int]:
    """Bounding box of every pixel that is not (almost) fully transparent."""
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if rgba[row + x * 4 + 3] > threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return 0, 0, width, height
    return min_x, min_y, max_x + 1, max_y + 1


def crop(rgba: bytes, width: int, height: int, box: tuple[int, int, int, int]) -> tuple[int, int, bytes]:
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0, y1 - y0
    out = bytearray(cw * ch * 4)
    for y in range(ch):
        src = ((y0 + y) * width + x0) * 4
        out[y * cw * 4:(y + 1) * cw * 4] = rgba[src:src + cw * 4]
    return cw, ch, bytes(out)


def build() -> tuple[int, int, bytes]:
    """Decode, de-background and crop the source artwork."""
    width, height, rgba = read_png(SOURCE)
    colors = _brand_colors(rgba, width, height)
    if not colors:
        raise SystemExit(f"No artwork colours found in {SOURCE}")
    rgba = matte_to_alpha(rgba, width, height, colors)
    x0, y0, x1, y1 = content_bbox(rgba, width, height)
    x0 = max(0, x0 - PADDING)
    y0 = max(0, y0 - PADDING)
    x1 = min(width, x1 + PADDING)
    y1 = min(height, y1 + PADDING)
    cw, ch, cropped = crop(rgba, width, height, (x0, y0, x1, y1))
    return cw, ch, cropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build logo.png from the source artwork")
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if logo.png is missing or stale")
    args = parser.parse_args(argv)

    if not SOURCE.exists():
        raise SystemExit(f"Missing source artwork: {SOURCE}")

    cw, ch, cropped = build()

    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT.name} is missing. Run: python scripts/make_logo.py")
            return 1
        if OUTPUT.read_bytes() != encode_png(cw, ch, cropped):
            print(f"{OUTPUT.name} is stale. Run: python scripts/make_logo.py")
            return 1
        print(f"{OUTPUT.name} is up to date.")
        return 0

    OUTPUT.write_bytes(encode_png(cw, ch, cropped))
    print(f"wrote {OUTPUT.name} ({cw}x{ch}, {OUTPUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
