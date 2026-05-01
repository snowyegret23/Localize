import struct
import os
from PIL import Image
import xml.etree.ElementTree as ET

def make_font(filename):
    with open(filename, "rb") as f, open(f"results/{filename}", "wb") as f_out:
        clean_name = os.path.splitext(os.path.basename(filename))[0]
        print(f"Making {filename}...")

        fnt_xml = os.path.join("wokrspace", filename)
        fnt_png = os.path.join("wokrspace", f"{clean_name}_0.png")

        if not os.path.exists(fnt_xml):
            print(f"Error: {fnt_xml} not found.")
            return
        if not os.path.exists(fnt_png):
            print(f"Error: {fnt_png} not found.")
            return

        tree = ET.parse(fnt_xml)
        root = tree.getroot()

        common = root.find('common')
        scaleW = float(common.get('scaleW'))
        scaleH = float(common.get('scaleH'))
        lineHeight = float(common.get('lineHeight'))
        base = float(common.get('base'))



        chars = root.find('chars')
        char_elements = chars.findall('char')

        glyph_data = []

        for char_elem in char_elements:
            glyph = int(char_elem.get('id'))

            x = int(char_elem.get('x'))
            y = int(char_elem.get('y'))
            width = int(char_elem.get('width'))
            height = int(char_elem.get('height'))
            xoffset = int(char_elem.get('xoffset'))
            yoffset = int(char_elem.get('yoffset'))
            xadvance = int(char_elem.get('xadvance'))

            uv_left = x / scaleW
            uv_top = y / scaleH
            uv_right = (x + width) / scaleW
            uv_bottom = (y + height) / scaleH

            glyph_width_px = float(width)
            glyph_height_px = float(height)
            xoff = float(xoffset)
            yoff = float(yoffset) - 8
            xadv = float(xadvance)

            if glyph > 0:
                glyph_data.append(
                    (glyph, uv_left, uv_top, uv_right, uv_bottom, xoff, yoff, glyph_width_px, glyph_height_px, xadv)
                )

        magic = f.read(6)
        major = f.read(1)
        minor = f.read(4)
        f_out.write(magic)
        f_out.write(major)
        f_out.write(minor)
        # print(f"magic: {magic}, major: {struct.unpack('<B', major)[0]}, minor: {struct.unpack('<I', minor)[0]}")

        fontname_len = f.read(4)
        fontname_length = struct.unpack("<I", fontname_len)[0]
        fontname = f.read(fontname_length)
        fonttype_len = f.read(4)
        fonttype_length = struct.unpack("<I", fonttype_len)[0]
        fonttype = f.read(fonttype_length)
        f_out.write(fontname_len)
        f_out.write(fontname)
        f_out.write(fonttype_len)
        f_out.write(fonttype)
        # print(f"fontname: {fontname.decode('utf-8')}, fonttype: {fonttype.decode('utf-8')}")

        original_kerning_count = struct.unpack("<I", f.read(4))[0] # 항상 0으로 하는 게 편할 듯
        # print(f"kerning_count: {original_kerning_count}")
        if original_kerning_count > 0:
            kerning_data = f.read(original_kerning_count * 8)

        kerning_count = b"\x00\x00\x00\x00"
        f_out.write(kerning_count)
        # print(f"kerning_count: {struct.unpack('<I', kerning_count)[0]}")

        line_h = f.read(4)
        asc = f.read(4)
        dsc = f.read(4)
        font_size = f.read(4)
        # f_out.write(struct.pack("<f", lineHeight))
        f_out.write(line_h)
        # new_asc = base
        # new_dsc = lineHeight - base
        # f_out.write(struct.pack("<f", new_asc))
        # f_out.write(struct.pack("<f", new_dsc))
        f_out.write(asc)
        f_out.write(dsc)
        f_out.write(font_size)
        # print(f"line_h: {struct.unpack('<f', line_h)[0]}, asc: {struct.unpack('<f', asc)[0]}, dsc: {struct.unpack('<f', dsc)[0]}, font_size: {struct.unpack('<I', font_size)[0]}")

        original_tex_width = struct.unpack("<I", f.read(4))[0]
        original_tex_height = struct.unpack("<I", f.read(4))[0]
        original_image_size = original_tex_width * original_tex_height
        original_image_data = f.read(original_image_size)

        texture = Image.open(fnt_png)
        if texture.mode != "L":
            texture = texture.convert("L")
        tex_width, tex_height = texture.size
        texture_data = texture.tobytes()
        f_out.write(struct.pack("<I", tex_width))
        f_out.write(struct.pack("<I", tex_height))
        f_out.write(texture_data)
        # print(f"tex_width: {tex_width}, tex_height: {tex_height}, image_size: {len(texture_data)}")



        unk_1 = f.read(2)
        unk_2 = f.read(2)
        tex_width_2 = f.read(4)
        glyph_count = f.read(4)
        f_out.write(unk_1)
        f_out.write(unk_2)
        f_out.write(struct.pack("<I", tex_width))
        f_out.write(struct.pack("<I", len(glyph_data)))
        # print(f"unk_1: {unk_1}, unk_2: {unk_2}, tex_width_2: {tex_width}, glyph_count: {len(glyph_data)}")

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

        for glyph in glyph_data:
            f_out.write(struct.pack("<I", glyph[0]))
            f_out.write(struct.pack("<f", glyph[1]))
            f_out.write(struct.pack("<f", glyph[2]))
            f_out.write(struct.pack("<f", glyph[3]))
            f_out.write(struct.pack("<f", glyph[4]))
            f_out.write(struct.pack("<f", glyph[5]))
            f_out.write(struct.pack("<f", glyph[6]))
            f_out.write(struct.pack("<f", glyph[7]))
            f_out.write(struct.pack("<f", glyph[8]))
            f_out.write(struct.pack("<f", glyph[9]))
            # print(f"glyph: {glyph[0]}, uv_left: {glyph[1]}, uv_top: {glyph[2]}, uv_right: {glyph[3]}, uv_bottom: {glyph[4]}, xoff: {glyph[5]}, yoff: {glyph[6]}, glyph_width_px: {glyph[7]}, glyph_height_px: {glyph[8]}, xadv: {glyph[9]}")

        end_unk_1 = f.read(4)
        end_unk_2 = f.read(4)
        end_unk_3 = f.read(4)
        f_out.write(end_unk_1)
        f_out.write(end_unk_2)
        f_out.write(end_unk_3)
        # print(f"end_unk_1: {end_unk_1}, end_unk_2: {end_unk_2}, end_unk_3: {end_unk_3}")


if __name__ == "__main__":
    os.makedirs("results", exist_ok=True)
    os.makedirs("wokrspace", exist_ok=True)
    for filename in os.listdir("."):
        if filename.endswith(".fnt"):
            make_font(filename)
