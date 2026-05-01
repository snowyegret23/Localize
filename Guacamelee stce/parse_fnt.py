import struct
import os
from PIL import Image

def parse_font(filename):
    with open(filename, "rb") as f, open(filename + ".txt", "w", encoding="utf-8") as f_out:
        print(f"Parsing {filename}...")
        f_out.write(f"Parsing {filename}...\n")
        magic = f.read(6)
        major = struct.unpack("<B", f.read(1))[0]
        minor = struct.unpack("<I", f.read(4))[0]
        print(f"magic: {magic}, major: {major}, minor: {minor}")
        f_out.write(f"magic: {magic}, major: {major}, minor: {minor}\n")

        fontname_len = struct.unpack("<I", f.read(4))[0]
        fontname = f.read(fontname_len).decode("utf-8")
        fonttype_len = struct.unpack("<I", f.read(4))[0]
        fonttype = f.read(fonttype_len).decode("utf-8")
        print(f"fontname: {fontname}, fonttype: {fonttype}")
        f_out.write(f"fontname: {fontname}, fonttype: {fonttype}\n")

        kerning_count = struct.unpack("<I", f.read(4))[0] # 항상 0으로 하는 게 편할 듯
        print(f"kerning_count: {kerning_count}")
        f_out.write(f"kerning_count: {kerning_count}\n")
        if kerning_count > 0:
            kerning_data = f.read(kerning_count * 8)
            print(f"kerning_data: {kerning_data}")
            f_out.write(f"kerning_data: {kerning_data}\n")

        line_h = struct.unpack("<f", f.read(4))[0]
        asc = struct.unpack("<f", f.read(4))[0]
        dsc = struct.unpack("<f", f.read(4))[0]
        font_size = struct.unpack("<I", f.read(4))[0]
        print(f"line_h: {line_h}, asc: {asc}, dsc: {dsc}, font_size: {font_size}")
        f_out.write(f"line_h: {line_h}, asc: {asc}, dsc: {dsc}, font_size: {font_size}\n")

        tex_width = struct.unpack("<I", f.read(4))[0]
        tex_height = struct.unpack("<I", f.read(4))[0]
        image_size = tex_width * tex_height
        print(f"tex_width: {tex_width}, tex_height: {tex_height}, image_size: {image_size}")
        f_out.write(f"tex_width: {tex_width}, tex_height: {tex_height}, image_size: {image_size}\n")
        image_data = f.read(image_size)
        with open(f"{filename}.png", "wb") as img_file:
            img = Image.frombytes("L", (tex_width, tex_height), image_data)
            img.save(img_file, "PNG")
        unk_1 = f.read(2)
        unk_2 = f.read(2)
        tex_width_2 = struct.unpack("<I", f.read(4))[0]
        glyph_count = struct.unpack("<I", f.read(4))[0]
        print(f"unk_1: {unk_1}, unk_2: {unk_2}, tex_width_2: {tex_width_2}, glyph_count: {glyph_count}")
        f_out.write(f"unk_1: {unk_1}, unk_2: {unk_2}, tex_width_2: {tex_width_2}, glyph_count: {glyph_count}\n")

        glyph_data = []
        while True:
            if os.path.getsize(filename) - f.tell() < 40:
                break
            glyph = struct.unpack("<I", f.read(4))[0]
            glyph_unicode = chr(glyph)
            uv_left = struct.unpack("<f", f.read(4))[0]
            uv_top = struct.unpack("<f", f.read(4))[0]
            uv_right = struct.unpack("<f", f.read(4))[0]
            uv_bottom = struct.unpack("<f", f.read(4))[0]
            xoff = struct.unpack("<f", f.read(4))[0]
            yoff = struct.unpack("<f", f.read(4))[0]
            glyph_width_px = struct.unpack("<f", f.read(4))[0]
            glyph_height_px = struct.unpack("<f", f.read(4))[0]
            xadv = struct.unpack("<f", f.read(4))[0]
            glyph_data.append((glyph, uv_left, uv_top, uv_right, uv_bottom, xoff, yoff, glyph_width_px, glyph_height_px, xadv))
            print(f"glyph: {glyph}:{glyph_unicode}, uv_left: {uv_left}, uv_top: {uv_top}, uv_right: {uv_right}, uv_bottom: {uv_bottom}, xoff: {xoff}, yoff: {yoff}, glyph_width_px: {glyph_width_px}, glyph_height_px: {glyph_height_px}, xadv: {xadv}")
            f_out.write(f"glyph: {glyph}:{glyph_unicode}, uv_left: {uv_left}, uv_top: {uv_top}, uv_right: {uv_right}, uv_bottom: {uv_bottom}, xoff: {xoff}, yoff: {yoff}, glyph_width_px: {glyph_width_px}, glyph_height_px: {glyph_height_px}, xadv: {xadv}\n")

        end_unk_1 = struct.unpack("<f", f.read(4))[0]
        end_unk_2 = struct.unpack("<f", f.read(4))[0]
        end_unk_3 = struct.unpack("<f", f.read(4))[0]
        print(f"end_unk_1: {end_unk_1}, end_unk_2: {end_unk_2}, end_unk_3: {end_unk_3}")
        f_out.write(f"end_unk_1: {end_unk_1}, end_unk_2: {end_unk_2}, end_unk_3: {end_unk_3}\n")
        

if __name__ == '__main__':
    # parse_font("PatuaOne-Regular.otf.28.latin.fnt")
    parse_font("courbd.ttf.9.japanese.fnt")