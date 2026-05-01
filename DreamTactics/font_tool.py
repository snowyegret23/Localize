import struct
import sys
import os
import lzham

DICT_SIZE_LOG2 = 18


def extract(input_path, output_path):
    with open(input_path, "rb") as f:
        compressed = f.read()

    dec = lzham.LZHAMDecompressor(filters={"dict_size_log2": DICT_SIZE_LOG2})
    decompressed = dec.decompress(compressed, 50000000)

    font_size = struct.unpack("<I", decompressed[:4])[0]
    font_data = decompressed[4 : 4 + font_size]

    if font_data[0:4] == b"OTTO":
        ext = ".otf"
    else:
        ext = ".ttf"

    if not output_path.endswith((".ttf", ".otf")):
        output_path = output_path + ext

    with open(output_path, "wb") as f:
        f.write(font_data)

    print(f"Extracted: {len(font_data):,} bytes -> {output_path}")


def pack(input_path, output_path):
    with open(input_path, "rb") as f:
        font_data = f.read()

    payload = struct.pack("<I", len(font_data)) + font_data
    compressed = lzham.compress(payload, filters={"dict_size_log2": DICT_SIZE_LOG2})

    with open(output_path, "wb") as f:
        f.write(compressed)

    print(
        f"Packed: {len(font_data):,} bytes -> {len(compressed):,} bytes -> {output_path}"
    )


def info(input_path):
    with open(input_path, "rb") as f:
        compressed = f.read()

    dec = lzham.LZHAMDecompressor(filters={"dict_size_log2": DICT_SIZE_LOG2})
    decompressed = dec.decompress(compressed, 50000000)

    font_size = struct.unpack("<I", decompressed[:4])[0]
    font_data = decompressed[4 : 4 + font_size]

    if font_data[0:4] == b"OTTO":
        font_type = "OpenType (OTF)"
    elif font_data[0:4] == b"\x00\x01\x00\x00":
        font_type = "TrueType (TTF)"
    else:
        font_type = "Unknown"

    print(f"File: {input_path}")
    print(f"Compressed: {len(compressed):,} bytes")
    print(f"Font Size: {font_size:,} bytes")
    print(f"Font Type: {font_type}")

    try:
        import io
        from fontTools.ttLib import TTFont

        font = TTFont(io.BytesIO(font_data))
        name_table = font["name"]
        for record in name_table.names:
            if record.nameID == 4:
                try:
                    print(f"Font Name: {record.toUnicode()}")
                    break
                except:
                    pass
        if "maxp" in font:
            print(f'Glyphs: {font["maxp"].numGlyphs}')
    except ImportError:
        pass


def main():
    if len(sys.argv) < 3:
        print("Dream Tactics Font Tool v1.0")
        print("Made by Snowyegret")
        print()
        print("Usage:")
        print("  python font_tool.py extract <compressed_file> <output.ttf/otf>")
        print("  python font_tool.py pack <input.ttf/otf> <compressed_file>")
        print("  python font_tool.py info <compressed_file>")
        print()
        print("Examples:")
        print("  python font_tool.py extract data/698288940 pretendard.otf")
        print("  python font_tool.py pack myfont.ttf data/698288940")
        print("  python font_tool.py info data/2973124184")
        return

    cmd = sys.argv[1]
    if cmd == "extract":
        if len(sys.argv) < 4:
            print("Error: extract requires input and output paths")
            return
        extract(sys.argv[2], sys.argv[3])
    elif cmd == "pack":
        if len(sys.argv) < 4:
            print("Error: pack requires input and output paths")
            return
        pack(sys.argv[2], sys.argv[3])
    elif cmd == "info":
        info(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
