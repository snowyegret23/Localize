#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    print("Pillow is required. Install with: python -m pip install pillow", file=sys.stderr)
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)


CONSONANTS = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
VOWELS = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅛㅜㅠㅡㅣ")
COMPOUND_VOWELS = list("ㅘㅙㅚㅝㅞㅟㅢ")
SHIFT_GLYPH_DEFAULT = "⇧"


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Generate Hangul key sprites from a template image and a TTF font."
    )
    parser.add_argument("--ttf", required=True, type=Path, help="Path to TTF font.")
    parser.add_argument(
        "--template",
        type=Path,
        default=script_dir / "keysample.png",
        help="Path to key template PNG. Default: tools/keysample.png",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=script_dir / "generated_keys",
        help="Output directory for generated key_*.png files.",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=0,
        help="Font size in px. 0 means auto-size from template.",
    )
    parser.add_argument(
        "--color",
        default="#222222",
        help="Text color. Example: #222222 or #222222FF",
    )
    parser.add_argument(
        "--stroke-color",
        default="#FFFFFFFF",
        help="Stroke color. Example: #FFFFFFFF",
    )
    parser.add_argument(
        "--stroke-width",
        type=int,
        default=0,
        help="Text stroke width in pixels.",
    )
    parser.add_argument(
        "--y-offset",
        type=float,
        default=0.0,
        help="Vertical offset in pixels (positive moves down).",
    )
    parser.add_argument(
        "--shift-glyph",
        default=SHIFT_GLYPH_DEFAULT,
        help="Glyph for key_shift.png",
    )
    return parser.parse_args()


def pick_font_size(ttf: Path, sample: Image.Image, glyphs: Iterable[str]) -> int:
    width, height = sample.size
    target_w = width * 0.62
    target_h = height * 0.62
    best = max(12, int(min(width, height) * 0.50))

    for size in range(12, int(min(width, height) * 0.9) + 1):
        font = ImageFont.truetype(str(ttf), size)
        max_w = 0
        max_h = 0
        draw = ImageDraw.Draw(sample)
        for glyph in glyphs:
            left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font)
            max_w = max(max_w, right - left)
            max_h = max(max_h, bottom - top)
        if max_w <= target_w and max_h <= target_h:
            best = size
        else:
            break

    return best


def draw_centered_text(
    base: Image.Image,
    glyph: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int],
    stroke_width: int,
    y_offset: float,
) -> Image.Image:
    out = base.copy()
    draw = ImageDraw.Draw(out)
    left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font, stroke_width=stroke_width)
    tw = right - left
    th = bottom - top

    x = (out.width - tw) / 2.0 - left
    y = (out.height - th) / 2.0 - top + y_offset
    draw.text(
        (x, y),
        glyph,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )
    return out


def main() -> int:
    args = parse_args()

    if not args.ttf.is_file():
        print(f"TTF not found: {args.ttf}", file=sys.stderr)
        return 2
    if not args.template.is_file():
        print(f"Template PNG not found: {args.template}", file=sys.stderr)
        return 2

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        fill = ImageColor.getcolor(args.color, "RGBA")
        stroke_fill = ImageColor.getcolor(args.stroke_color, "RGBA")
    except ValueError as exc:
        print(f"Invalid color value: {exc}", file=sys.stderr)
        return 2

    template = Image.open(args.template).convert("RGBA")
    glyphs = CONSONANTS + VOWELS + COMPOUND_VOWELS
    font_size = args.font_size if args.font_size > 0 else pick_font_size(args.ttf, template, glyphs + [args.shift_glyph])

    try:
        font = ImageFont.truetype(str(args.ttf), font_size)
    except OSError as exc:
        print(f"Failed to load font: {exc}", file=sys.stderr)
        return 2

    created = 0
    for glyph in glyphs:
        out = draw_centered_text(
            template,
            glyph,
            font,
            fill,
            stroke_fill,
            args.stroke_width,
            args.y_offset,
        )
        out_path = out_dir / f"key_{glyph}.png"
        out.save(out_path)
        created += 1

    if args.shift_glyph:
        shift_img = draw_centered_text(
            template,
            args.shift_glyph,
            font,
            fill,
            stroke_fill,
            args.stroke_width,
            args.y_offset,
        )
        shift_img.save(out_dir / "key_shift.png")
        created += 1

    print(f"Created {created} sprites in: {out_dir}")
    print(f"Font size: {font_size}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
