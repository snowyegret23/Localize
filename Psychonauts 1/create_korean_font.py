import struct
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont

MAGIC = b"FFFD"
TEXTURE_SIZE = 1024
PADDING = 1


def load_char_list(list_path):
    chars = set()
    for i in range(32, 127):
        chars.add(chr(i))
    if list_path and os.path.exists(list_path):
        with open(list_path, "r", encoding="utf-8") as f:
            content = f.read()
            for c in content:
                if ord(c) > 32:
                    chars.add(c)
        print(f"Loaded {len(chars)} characters from {list_path}")
    else:
        print(f"Warning: {list_path} not found. Using ASCII only.")
    return sorted(list(chars))


def generate_font(
    ttf_path, char_list_path, output_json_path, output_png_path, font_size=24
):
    json_dir = os.path.dirname(output_json_path)
    if json_dir:
        os.makedirs(json_dir, exist_ok=True)
    png_dir = os.path.dirname(output_png_path)
    if png_dir:
        os.makedirs(png_dir, exist_ok=True)
    chars = load_char_list(char_list_path)
    try:
        font = ImageFont.truetype(ttf_path, font_size)
    except IOError:
        print(f"Error: Cannot open font {ttf_path}")
        return
    try:
        ascent, descent = font.getmetrics()
    except:
        ascent, descent = (int(font_size * 0.8), int(font_size * 0.2))
    line_height = ascent + descent
    print(
        f"Font Metrics - Size: {font_size}, Ascent: {ascent}, Line Height: {line_height}"
    )
    img = Image.new("L", (TEXTURE_SIZE, TEXTURE_SIZE), 0)
    draw = ImageDraw.Draw(img)
    x, y = (PADDING, PADDING)
    glyphs = []
    mapping = {}
    glyphs.append({"x": 0, "y": 0, "w": 4, "h": 0, "meta": 0, "char": "\x00"})
    for idx, char in enumerate(chars):
        bbox = font.getbbox(char)
        if bbox:
            glyph_w = bbox[2] - bbox[0] + 1
        else:
            glyph_w = font_size // 2
        if x + glyph_w + PADDING > TEXTURE_SIZE:
            x = PADDING
            y += line_height + PADDING
        if y + line_height + PADDING > TEXTURE_SIZE:
            print("Error: Texture overflow!")
            break
        else:
            try:
                full_w = int(font.getlength(char))
            except:
                full_w = glyph_w
            if full_w == 0:
                full_w = glyph_w
            if x + full_w + PADDING > TEXTURE_SIZE:
                x = PADDING
                y += line_height + PADDING
            draw.text((x, y), char, font=font, fill=255)
            glyphs.append(
                {
                    "x": x,
                    "y": y,
                    "w": full_w,
                    "h": line_height,
                    "meta": ascent,
                    "char": char,
                }
            )
            mapping[ord(char)] = len(glyphs) - 1
            x += full_w + PADDING
    img.save(output_png_path)
    font_info = {
        "header_size": 256,
        "glyph_count": len(glyphs),
        "glyphs": glyphs,
        "width": TEXTURE_SIZE,
        "height": TEXTURE_SIZE,
        "unicode_map": {str(k): v for k, v in mapping.items()},
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(font_info, f, indent=2)
    print("Generated font resources:")
    print(f"  JSON: {output_json_path}")
    print(f"  PNG : {output_png_path}")


def pack_dff(json_path, img_path, output_dff_path):
    with open(json_path, "r", encoding="utf-8") as f:
        info = json.load(f)
    img = Image.open(img_path).convert("L")
    img_data = img.tobytes()
    unicode_map = {int(k): v for k, v in info["unicode_map"].items()}
    with open(output_dff_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", info["header_size"]))
        f.write(struct.pack("<I", info["glyph_count"]))
        ascii_map_data = bytearray(256)
        for i in range(256):
            if i in unicode_map:
                glyph_idx = unicode_map[i]
                if glyph_idx < 256:
                    ascii_map_data[i] = glyph_idx
        f.write(ascii_map_data)
        for g in info["glyphs"]:
            x1 = int(g["x"])
            y1 = int(g["y"])
            x2 = int(x1 + g["w"])
            y2 = int(y1 + g["h"])
            meta = int(g.get("meta", 0))
            f.write(struct.pack("<HHHH", x1, y1, x2, y2))
            f.write(struct.pack("<I", meta))
        f.write(struct.pack("<I", 3))
        f.write(struct.pack("<II", info["width"], info["height"]))
        f.write(img_data)
    print(f"Packed DFF: {output_dff_path}")


if __name__ == "__main__":
    print("Psychonauts 1 Font Tool")
    print("Made by Snowyegret, Version 1.0")
    print()
    if len(sys.argv) < 2:
        print("Usage:")
        print(
            "  Generate: python create_korean_font.py generate <ttf> <char_list> <json_out> <png_out> [size]"
        )
        print(
            "  Pack:     python create_korean_font.py pack <json_in> <png_in> <dff_out>"
        )
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "generate":
        if len(sys.argv) < 6:
            print("Error: Missing arguments for generate.")
            print(
                "Usage: python create_korean_font.py generate <ttf> <char_list> <json_out> <png_out> [size]"
            )
            sys.exit(1)
        ttf_path = sys.argv[2]
        char_list_path = sys.argv[3]
        output_json = sys.argv[4]
        output_png = sys.argv[5]
        font_size = int(sys.argv[6]) if len(sys.argv) > 6 else 24
        generate_font(ttf_path, char_list_path, output_json, output_png, font_size)
    else:
        if cmd == "pack":
            if len(sys.argv) < 5:
                print("Error: Missing arguments for pack.")
                print(
                    "Usage: python create_korean_font.py pack <json_in> <png_in> <dff_out>"
                )
                sys.exit(1)
            pack_dff(sys.argv[2], sys.argv[3], sys.argv[4])
