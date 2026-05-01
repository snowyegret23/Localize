import struct
import shutil
import json
import os
import sys

GAME_DIR = "./"
EXE_NAME = "Psychonauts_expand_section.exe"
OUTPUT_NAME = "Psychonauts.exe"
EXE_PATH = os.path.join(GAME_DIR, EXE_NAME)
OUTPUT_PATH = os.path.join(GAME_DIR, OUTPUT_NAME)
IMAGE_BASE = 4194304
RENDER_PATCH_ADDR = 4346772
RENDER_RETURN_ADDR = 4346801
CALCW_PATCH_ADDR = 4341565
CALCW_RETURN_ADDR = 4341579
CALCW_GLYPH_PATCH_ADDR = 4342032
CALCW_GLYPH_RETURN_ADDR = 4342048
PATCHC_VA = 8437760
PATCHC2_VA = 8438016
PATCHC3_VA = 8438272
PATCHD_VA = 8503296


def va_to_file_offset(va, sections):
    for sec in sections:
        if sec["vaddr"] <= va < sec["vaddr"] + sec["vsize"]:
            return sec["raddr"] + (va - sec["vaddr"])
    return None


def get_sections(data):
    sections = []
    pe_offset = struct.unpack_from("<I", data, 60)[0]
    num_sections = struct.unpack_from("<H", data, pe_offset + 6)[0]
    opt_header_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    section_offset = pe_offset + 24 + opt_header_size
    for i in range(num_sections):
        off = section_offset + i * 40
        name = data[off : off + 8].rstrip(b"\x00").decode("ascii", errors="ignore")
        virt_size = struct.unpack_from("<I", data, off + 8)[0]
        virt_addr = struct.unpack_from("<I", data, off + 12)[0] + IMAGE_BASE
        raw_size = struct.unpack_from("<I", data, off + 16)[0]
        raw_addr = struct.unpack_from("<I", data, off + 20)[0]
        sections.append(
            {
                "name": name,
                "vaddr": virt_addr,
                "vsize": virt_size,
                "raddr": raw_addr,
                "rsize": raw_size,
            }
        )
    return sections


def build_render_patch_code():
    code = bytearray()

    def here():
        return len(code)

    def patch_rel8(pos, target_label_pos):
        offset = target_label_pos - (pos + 2)
        code[pos + 1] = offset & 255

    code += b"PQVW"
    code += b"\x8bM\x10"
    code += b"\x03M\xc4"
    code += b"\x0f\xb6\x01"
    code += b"<\x80"
    jb_ascii = here()
    code += b"r\x00"
    code += b"<\xe0"
    jb_2byte = here()
    code += b"r\x00"
    code += b"<\xf0"
    jb_3byte = here()
    code += b"r\x00"
    jmp_fallback_0 = here()
    code += b"\xeb\x00"
    pos_is_2byte = here()
    patch_rel8(jb_2byte, pos_is_2byte)
    code += b"%\x1f\x00\x00\x00"
    code += b"\xc1\xe0\x06"
    code += b"\x0f\xb6q\x01"
    code += b"\x83\xe6?"
    code += b"\t\xf0"
    code += b"\xffE\xc4"
    jmp_lookup_2 = here()
    code += b"\xeb\x00"
    pos_is_3byte = here()
    patch_rel8(jb_3byte, pos_is_3byte)
    code += b"%\x0f\x00\x00\x00"
    code += b"\xc1\xe0\x0c"
    code += b"\x0f\xb6q\x01"
    code += b"\x83\xe6?"
    code += b"\xc1\xe6\x06"
    code += b"\t\xf0"
    code += b"\x0f\xb6q\x02"
    code += b"\x83\xe6?"
    code += b"\t\xf0"
    code += b"\x83E\xc4\x02"
    pos_lookup = here()
    patch_rel8(jmp_lookup_2, pos_lookup)
    code += b"\xbf" + struct.pack("<I", PATCHD_VA + 8)
    code += b"\x8b5" + struct.pack("<I", PATCHD_VA + 4)
    code += b"\x85\xf6"
    jz_fallback_1 = here()
    code += b"t\x00"
    pos_loop = here()
    code += b"9\x07"
    je_found = here()
    code += b"t\x00"
    code += b"\x83\xc7\x08"
    code += b"N"
    jnz_loop = here()
    code += b"u\x00"
    jmp_fallback_1 = here()
    code += b"\xeb\x00"
    patch_rel8(jnz_loop, pos_loop)
    pos_found = here()
    patch_rel8(je_found, pos_found)
    code += b"\x8bW\x04"
    jmp_done_1 = here()
    code += b"\xeb\x00"
    pos_is_ascii = here()
    patch_rel8(jb_ascii, pos_is_ascii)
    code += b"\x8b\x8d\xb4\xfe\xff\xff"
    code += b"\x0f\xb6T\x01\x04"
    jmp_done_2 = here()
    code += b"\xeb\x00"
    pos_fallback = here()
    patch_rel8(jmp_fallback_0, pos_fallback)
    patch_rel8(jz_fallback_1, pos_fallback)
    patch_rel8(jmp_fallback_1, pos_fallback)
    code += b"1\xd2"
    pos_done = here()
    patch_rel8(jmp_done_1, pos_done)
    patch_rel8(jmp_done_2, pos_done)
    code += b"\x89U\xa0"
    code += b"_^YX"
    code += b"h" + struct.pack("<I", RENDER_RETURN_ADDR)
    code += b"\xc3"
    return bytes(code)


def build_calcw_read_patch():
    code = bytearray()

    def here():
        return len(code)

    def patch_rel8(pos, target_label_pos):
        offset = target_label_pos - (pos + 2)
        code[pos + 1] = offset & 255

    code += b"PQVW"
    code += b"\x8bE\x08"
    code += b"\x0f\xb6\x08"
    code += b"\x89\xc8"
    code += b"<\x80"
    jb_ascii = here()
    code += b"r\x00"
    code += b"<\xe0"
    jb_2byte = here()
    code += b"r\x00"
    code += b"<\xf0"
    jb_3byte = here()
    code += b"r\x00"
    jmp_fallback_0 = here()
    code += b"\xeb\x00"
    pos_is_2byte = here()
    patch_rel8(jb_2byte, pos_is_2byte)
    code += b"%\x1f\x00\x00\x00"
    code += b"\xc1\xe0\x06"
    code += b"\x8bM\x08"
    code += b"\x0f\xb6q\x01"
    code += b"\x83\xe6?"
    code += b"\t\xf0"
    code += b"\xffE\x08"
    jmp_lookup_2 = here()
    code += b"\xeb\x00"
    pos_is_3byte = here()
    patch_rel8(jb_3byte, pos_is_3byte)
    code += b"%\x0f\x00\x00\x00"
    code += b"\xc1\xe0\x0c"
    code += b"\x8bM\x08"
    code += b"\x0f\xb6q\x01"
    code += b"\x83\xe6?"
    code += b"\xc1\xe6\x06"
    code += b"\t\xf0"
    code += b"\x0f\xb6q\x02"
    code += b"\x83\xe6?"
    code += b"\t\xf0"
    code += b"\x83E\x08\x02"
    pos_lookup = here()
    patch_rel8(jmp_lookup_2, pos_lookup)
    code += b"\xbf" + struct.pack("<I", PATCHD_VA + 8)
    code += b"\x8b5" + struct.pack("<I", PATCHD_VA + 4)
    code += b"\x85\xf6"
    jz_fallback_1 = here()
    code += b"t\x00"
    pos_loop = here()
    code += b"9\x07"
    je_found = here()
    code += b"t\x00"
    code += b"\x83\xc7\x08"
    code += b"N"
    jnz_loop = here()
    code += b"u\x00"
    jmp_fallback_1 = here()
    code += b"\xeb\x00"
    patch_rel8(jnz_loop, pos_loop)
    pos_found = here()
    patch_rel8(je_found, pos_found)
    code += b"\x8bO\x04"
    code += b"\x89\r" + struct.pack("<I", PATCHD_VA - 4)
    code += b"\xc6E\xf7\xff"
    jmp_done_1 = here()
    code += b"\xeb\x00"
    pos_is_ascii = here()
    patch_rel8(jb_ascii, pos_is_ascii)
    code += b"\x88M\xf7"
    code += b"\xc7\x05" + struct.pack("<I", PATCHD_VA - 4) + b"\x00\x00\x00\x00"
    jmp_done_2 = here()
    code += b"\xeb\x00"
    pos_fallback = here()
    patch_rel8(jmp_fallback_0, pos_fallback)
    patch_rel8(jz_fallback_1, pos_fallback)
    patch_rel8(jmp_fallback_1, pos_fallback)
    code += b"\xc6E\xf7 "
    code += b"\xc7\x05" + struct.pack("<I", PATCHD_VA - 4) + b"\x00\x00\x00\x00"
    pos_done = here()
    patch_rel8(jmp_done_1, pos_done)
    patch_rel8(jmp_done_2, pos_done)
    code += b"_^YX"
    code += b"\x0f\xb6U\xf7"
    code += b"\x85\xd2"
    code += b"h" + struct.pack("<I", CALCW_RETURN_ADDR)
    code += b"\xc3"
    return bytes(code)


def build_calcw_glyph_patch():
    code = bytearray()

    def here():
        return len(code)

    def patch_rel8(pos, target_label_pos):
        offset = target_label_pos - (pos + 2)
        code[pos + 1] = offset & 255

    code += b"\xa1" + struct.pack("<I", PATCHD_VA - 4)
    code += b"\x85\xc0"
    je_use_original = here()
    code += b"t\x00"
    code += b"\x8bM\xcc"
    code += b"\x8b\x89\x04\x01\x00\x00"
    code += b"\xc1\xe0\x04"
    code += b"\x01\xc8"
    code += b"\x89E\xd0"
    jmp_done = here()
    code += b"\xeb\x00"
    pos_use_original = here()
    patch_rel8(je_use_original, pos_use_original)
    code += b"\x0f\xb6E\xf7"
    code += b"P"
    code += b"\x8bM\xcc"
    call_target = 4341120
    call_offset = call_target - (PATCHC3_VA + len(code) + 5)
    code += b"\xe8" + struct.pack("<i", call_offset)
    code += b"\x89E\xd0"
    pos_done = here()
    patch_rel8(jmp_done, pos_done)
    code += b"h" + struct.pack("<I", CALCW_GLYPH_RETURN_ADDR)
    code += b"\xc3"
    return bytes(code)


def build_mapping_table(mapping):
    data = bytearray()
    data += struct.pack("<I", PATCHD_VA + 8)
    data += struct.pack("<I", len(mapping))
    sorted_map = sorted([(int(k), v) for k, v in mapping.items()])
    for codepoint, glyph_idx in sorted_map:
        data += struct.pack("<II", codepoint, glyph_idx)
    return data


def apply_patch(unicode_mapping=None):
    if not os.path.exists(EXE_PATH):
        print(f"Error: {EXE_PATH} not found.")
        return
    print(f"Patching {EXE_NAME}...")
    shutil.copy(EXE_PATH, OUTPUT_PATH)
    render_patch = build_render_patch_code()
    calcw_read_patch = build_calcw_read_patch()
    calcw_glyph_patch = build_calcw_glyph_patch()
    print(f"  Render patch: {len(render_patch)} bytes")
    print(f"  CalcWidth read patch: {len(calcw_read_patch)} bytes")
    print(f"  CalcWidth glyph patch: {len(calcw_glyph_patch)} bytes")
    with open(OUTPUT_PATH, "r+b") as f:
        data = bytearray(f.read())
        sections = get_sections(data)
        render_jmp_off = va_to_file_offset(RENDER_PATCH_ADDR, sections)
        calcw_read_jmp_off = va_to_file_offset(CALCW_PATCH_ADDR, sections)
        calcw_glyph_jmp_off = va_to_file_offset(CALCW_GLYPH_PATCH_ADDR, sections)
        patchc_off = va_to_file_offset(PATCHC_VA, sections)
        patchc2_off = va_to_file_offset(PATCHC2_VA, sections)
        patchc3_off = va_to_file_offset(PATCHC3_VA, sections)
        patchd_off = va_to_file_offset(PATCHD_VA, sections)
        patchd_temp_off = va_to_file_offset(PATCHD_VA - 4, sections)
        if not all(
            [
                render_jmp_off,
                calcw_read_jmp_off,
                calcw_glyph_jmp_off,
                patchc_off,
                patchc2_off,
                patchc3_off,
                patchd_off,
            ]
        ):
            print("Error: Could not find section offsets.")
            return
        rel32 = PATCHC_VA - (RENDER_PATCH_ADDR + 5)
        jmp_instr = b"\xe9" + struct.pack("<i", rel32)
        nops = b"\x90" * (RENDER_RETURN_ADDR - RENDER_PATCH_ADDR - 5)
        f.seek(render_jmp_off)
        f.write(jmp_instr + nops)
        print(f"  [1/3] RenderText hook @ 0x{RENDER_PATCH_ADDR:08X}")
        rel32 = PATCHC2_VA - (CALCW_PATCH_ADDR + 5)
        jmp_instr = b"\xe9" + struct.pack("<i", rel32)
        nops = b"\x90" * (CALCW_RETURN_ADDR - CALCW_PATCH_ADDR - 5)
        f.seek(calcw_read_jmp_off)
        f.write(jmp_instr + nops)
        print(f"  [2/3] CalcWidth read hook @ 0x{CALCW_PATCH_ADDR:08X}")
        rel32 = PATCHC3_VA - (CALCW_GLYPH_PATCH_ADDR + 5)
        jmp_instr = b"\xe9" + struct.pack("<i", rel32)
        nops = b"\x90" * (CALCW_GLYPH_RETURN_ADDR - CALCW_GLYPH_PATCH_ADDR - 5)
        f.seek(calcw_glyph_jmp_off)
        f.write(jmp_instr + nops)
        print(f"  [3/3] CalcWidth glyph hook @ 0x{CALCW_GLYPH_PATCH_ADDR:08X}")
        f.seek(patchc_off)
        f.write(render_patch)
        f.seek(patchc2_off)
        f.write(calcw_read_patch)
        f.seek(patchc3_off)
        f.write(calcw_glyph_patch)
        f.seek(patchd_temp_off)
        f.write(b"\x00\x00\x00\x00")
        if unicode_mapping:
            mapping_data = build_mapping_table(unicode_mapping)
            f.seek(patchd_off)
            f.write(mapping_data)
            print(f"  Unicode mapping: {len(unicode_mapping)} entries")
        else:
            print("  Warning: No unicode mapping provided.")
    print(f"\nSuccess! Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    print("Psychonauts 1 EXE Patch Tool")
    print("Made by Snowyegret, Version 1.1")
    print()
    mapping = None
    if len(sys.argv) > 2 and sys.argv[1] == "--with-korean":
        json_path = sys.argv[2]
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "unicode_map" in data:
                    mapping = data["unicode_map"]
                else:
                    mapping = data
        else:
            print(f"Error: JSON file not found: {json_path}")
            sys.exit(1)
    apply_patch(mapping)
