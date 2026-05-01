import argparse
import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

import pefile
from fontTools.ttLib import TTFont


RT_RCDATA = 10
DYNAFONT_EN = "DYNAFONT_EN"
DYNAFONT_JP = "DYNAFONT_JP"
FONT_SECTION_NAME = b".fbfont\x00"
FONT_SECTION_FLAGS = 0x40000040
IMAGE_FILE_HEADER_SIZE = 20
SECTION_HEADER_SIZE = 40


@dataclass(frozen=True)
class ResourceRef:
    name: str
    entry_offset: int
    data_rva: int
    size: int
    codepage: int
    reserved: int


@dataclass(frozen=True)
class PayloadPlacement:
    section_name: str
    data_rva: int
    raw_offset: int
    raw_size: int
    virtual_size: int
    created: bool



def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)



def load_font_info(font_path: Path) -> tuple[str, dict[int, str]]:
    font = TTFont(font_path)
    family = "<unknown>"
    for record in font["name"].names:
        if record.nameID != 1:
            continue
        try:
            text = record.toUnicode().strip()
        except Exception:
            text = record.string.decode(errors="ignore").strip()
        if text:
            family = text
            break
    cmap = font.getBestCmap() or {}
    return family, cmap



def require_hangul(font_path: Path) -> None:
    family, cmap = load_font_info(font_path)
    has_ga = 0xAC00 in cmap
    has_han = 0xD55C in cmap
    if not has_ga or not has_han:
        raise SystemExit(f"{font_path} does not expose Hangul glyphs in cmap; family={family}")
    print(f"font family: {family}")
    print(f"hangul glyphs: U+AC00={has_ga}, U+D55C={has_han}")



def section_name(section: pefile.SectionStructure) -> str:
    return section.Name.rstrip(b"\x00").decode(errors="ignore")



def file_header_offset(pe: pefile.PE) -> int:
    return pe.DOS_HEADER.e_lfanew + 4



def optional_header_offset(pe: pefile.PE) -> int:
    return file_header_offset(pe) + IMAGE_FILE_HEADER_SIZE



def section_table_offset(pe: pefile.PE) -> int:
    return optional_header_offset(pe) + pe.FILE_HEADER.SizeOfOptionalHeader



def first_section_raw_offset(pe: pefile.PE) -> int:
    return min(section.PointerToRawData for section in pe.sections)



def write_u16(blob: bytearray, offset: int, value: int) -> None:
    blob[offset : offset + 2] = struct.pack("<H", value)



def write_u32(blob: bytearray, offset: int, value: int) -> None:
    blob[offset : offset + 4] = struct.pack("<I", value)



def find_resource(pe: pefile.PE, resource_name: str) -> ResourceRef:
    if not hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        raise SystemExit("PE has no resource directory")

    for type_entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if type_entry.struct.Id != RT_RCDATA:
            continue
        for name_entry in type_entry.directory.entries:
            if str(name_entry.name) != resource_name:
                continue
            lang_entry = name_entry.directory.entries[0]
            data_entry = lang_entry.data.struct
            return ResourceRef(
                name=resource_name,
                entry_offset=data_entry.get_file_offset(),
                data_rva=data_entry.OffsetToData,
                size=data_entry.Size,
                codepage=data_entry.CodePage,
                reserved=data_entry.Reserved,
            )
    raise SystemExit(f"resource not found: {resource_name}")



def find_font_section(pe: pefile.PE):
    for section in pe.sections:
        if section_name(section) == FONT_SECTION_NAME.rstrip(b"\x00").decode(errors="ignore"):
            return section
    return None



def last_section(pe: pefile.PE) -> pefile.SectionStructure:
    return max(pe.sections, key=lambda section: section.VirtualAddress)



def ensure_blob_size(blob: bytearray, size: int) -> None:
    if len(blob) < size:
        blob.extend(b"\x00" * (size - len(blob)))



def place_font_payload(pe: pefile.PE, blob: bytearray, payload: bytes) -> PayloadPlacement:
    file_alignment = pe.OPTIONAL_HEADER.FileAlignment
    section_alignment = pe.OPTIONAL_HEADER.SectionAlignment
    existing = find_font_section(pe)

    if existing is not None:
        if existing is not last_section(pe):
            raise SystemExit("existing .fbfont section is not the last section; cannot safely resize it")

        raw_offset = existing.PointerToRawData
        raw_size = align_up(len(payload), file_alignment)
        virtual_size = len(payload)
        ensure_blob_size(blob, raw_offset + raw_size)
        blob[raw_offset : raw_offset + len(payload)] = payload
        if raw_size > len(payload):
            blob[raw_offset + len(payload) : raw_offset + raw_size] = b"\x00" * (raw_size - len(payload))

        header_off = existing.get_file_offset()
        write_u32(blob, header_off + 8, virtual_size)
        write_u32(blob, header_off + 16, raw_size)
        write_u32(
            blob,
            optional_header_offset(pe) + 56,
            align_up(existing.VirtualAddress + virtual_size, section_alignment),
        )
        return PayloadPlacement(
            section_name=section_name(existing),
            data_rva=existing.VirtualAddress,
            raw_offset=raw_offset,
            raw_size=raw_size,
            virtual_size=virtual_size,
            created=False,
        )

    new_header_off = section_table_offset(pe) + pe.FILE_HEADER.NumberOfSections * SECTION_HEADER_SIZE
    if new_header_off + SECTION_HEADER_SIZE > first_section_raw_offset(pe):
        raise SystemExit("no room left in PE headers for an extra section")

    tail = last_section(pe)
    file_alignment = pe.OPTIONAL_HEADER.FileAlignment
    section_alignment = pe.OPTIONAL_HEADER.SectionAlignment
    raw_offset = align_up(tail.PointerToRawData + tail.SizeOfRawData, file_alignment)
    raw_size = align_up(len(payload), file_alignment)
    virtual_size = len(payload)
    data_rva = align_up(tail.VirtualAddress + max(tail.Misc_VirtualSize, tail.SizeOfRawData), section_alignment)
    ensure_blob_size(blob, raw_offset + raw_size)
    blob[raw_offset : raw_offset + len(payload)] = payload
    if raw_size > len(payload):
        blob[raw_offset + len(payload) : raw_offset + raw_size] = b"\x00" * (raw_size - len(payload))

    header = struct.pack(
        "<8sIIIIIIHHI",
        FONT_SECTION_NAME,
        virtual_size,
        data_rva,
        raw_size,
        raw_offset,
        0,
        0,
        0,
        0,
        FONT_SECTION_FLAGS,
    )
    blob[new_header_off : new_header_off + SECTION_HEADER_SIZE] = header
    write_u16(blob, file_header_offset(pe) + 2, pe.FILE_HEADER.NumberOfSections + 1)
    write_u32(blob, optional_header_offset(pe) + 56, align_up(data_rva + virtual_size, section_alignment))

    return PayloadPlacement(
        section_name=FONT_SECTION_NAME.rstrip(b"\x00").decode(errors="ignore"),
        data_rva=data_rva,
        raw_offset=raw_offset,
        raw_size=raw_size,
        virtual_size=virtual_size,
        created=True,
    )



def write_resource_entry(blob: bytearray, resource: ResourceRef, data_rva: int, size: int) -> None:
    blob[resource.entry_offset : resource.entry_offset + 16] = struct.pack(
        "<IIII", data_rva, size, resource.codepage, resource.reserved
    )



def update_checksum(blob: bytearray) -> int:
    pe = pefile.PE(data=bytes(blob), fast_load=False)
    checksum = pe.generate_checksum()
    checksum_off = pe.OPTIONAL_HEADER.get_file_offset() + 0x40
    write_u32(blob, checksum_off, checksum)
    return checksum



def patch_exe(exe_path: Path, font_path: Path, out_path: Path, link_english: bool) -> tuple[PayloadPlacement, int]:
    blob = bytearray(exe_path.read_bytes())
    pe = pefile.PE(data=bytes(blob), fast_load=False)
    font_bytes = font_path.read_bytes()

    en_ref = find_resource(pe, DYNAFONT_EN)
    jp_ref = find_resource(pe, DYNAFONT_JP)
    placement = place_font_payload(pe, blob, font_bytes)

    write_resource_entry(blob, jp_ref, placement.data_rva, len(font_bytes))
    if link_english:
        write_resource_entry(blob, en_ref, placement.data_rva, len(font_bytes))

    checksum = update_checksum(blob)
    out_path.write_bytes(blob)
    return placement, checksum



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace Full Bore's embedded dynafont resource with a Hangul-capable TTF/OTF."
    )
    parser.add_argument("--exe", type=Path, required=True, help="Input FullBore.exe")
    parser.add_argument("--font", type=Path, required=True, help="Replacement TTF/OTF/TTC path")
    parser.add_argument("--out", type=Path, required=True, help="Output patched EXE")
    parser.add_argument(
        "--no-link-en",
        action="store_true",
        help="Only replace DYNAFONT_JP. Leave DYNAFONT_EN pointing at its original resource.",
    )
    args = parser.parse_args()

    exe_path = args.exe.resolve()
    font_path = args.font.resolve()
    out_path = args.out.resolve()
    if not exe_path.exists():
        raise SystemExit(f"EXE not found: {exe_path}")
    if not font_path.exists():
        raise SystemExit(f"font not found: {font_path}")

    require_hangul(font_path)
    placement, checksum = patch_exe(exe_path, font_path, out_path, not args.no_link_en)
    payload = font_path.read_bytes()

    print(
        f"font payload section: {placement.section_name} RVA 0x{placement.data_rva:08x} "
        f"raw_size={placement.raw_size} virtual_size={placement.virtual_size} created={placement.created}"
    )
    if args.no_link_en:
        print(f"patched resource: {DYNAFONT_JP} -> RVA 0x{placement.data_rva:08x} size {len(payload)}")
        print(f"left unchanged: {DYNAFONT_EN}")
    else:
        print(f"patched resources: {DYNAFONT_EN}, {DYNAFONT_JP} -> RVA 0x{placement.data_rva:08x} size {len(payload)}")
    print(f"font sha256: {hashlib.sha256(payload).hexdigest()}")
    print(f"checksum: 0x{checksum:08x}")
    print(f"wrote: {out_path}")


if __name__ == "__main__":
    main()
