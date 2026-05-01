import struct
import csv
import sys
import os
import re

STRING_ID_RE = re.compile(b"^[A-Z]{2,4}[0-9]{3,4}[A-Z0-9]{2}$")


def is_string_id(value):
    return bool(STRING_ID_RE.match(value))


def decode_lub_text(value):
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("cp1252")


class LuaReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read(self, size):
        out = self.data[self.offset : self.offset + size]
        self.offset += size
        return out

    def read_byte(self):
        return struct.unpack("B", self.read(1))[0]

    def read_int32(self):
        return struct.unpack("<I", self.read(4))[0]

    def read_string(self):
        size = self.read_int32()
        if size == 0:
            return b""
        else:
            s = self.read(size)
            return s[:(-1)]


class LuaWriter:
    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data += b

    def write_byte(self, v):
        self.data += struct.pack("B", v)

    def write_int32(self, v):
        self.data += struct.pack("<I", v)

    def write_string(self, s):
        if not s:
            self.write_int32(0)
        else:
            final_bytes = s + b"\x00"
            self.write_int32(len(final_bytes))
            self.write(final_bytes)


class LuaFunction:
    def __init__(self):
        self.name = b""
        self.args_info = b""
        self.locals = []
        self.lines = []
        self.const_strs = []
        self.const_nums = b""
        self.children = []
        self.code = b""

    def read(self, reader, header):
        self.name = reader.read_string()
        self.args_info = reader.read(13)
        count = reader.read_int32()
        for _ in range(count):
            loc_name = reader.read_string()
            start = reader.read_int32()
            end = reader.read_int32()
            self.locals.append((loc_name, start, end))
        count = reader.read_int32()
        self.lines = reader.read(count * 4)
        count = reader.read_int32()
        for _ in range(count):
            self.const_strs.append(reader.read_string())
        count = reader.read_int32()
        self.const_nums = reader.read(count * header["sizeof_number"])
        count = reader.read_int32()
        for _ in range(count):
            child = LuaFunction()
            child.read(reader, header)
            self.children.append(child)
        count = reader.read_int32()
        self.code = reader.read(count * header["size_instruction"])

    def write(self, writer, header):
        writer.write_string(self.name)
        writer.write(self.args_info)
        writer.write_int32(len(self.locals))
        for loc in self.locals:
            writer.write_string(loc[0])
            writer.write_int32(loc[1])
            writer.write_int32(loc[2])
        writer.write_int32(len(self.lines) // 4)
        writer.write(self.lines)
        writer.write_int32(len(self.const_strs))
        for s in self.const_strs:
            writer.write_string(s)
        num_count = len(self.const_nums) // header["sizeof_number"]
        writer.write_int32(num_count)
        writer.write(self.const_nums)
        writer.write_int32(len(self.children))
        for child in self.children:
            child.write(writer, header)
        code_count = len(self.code) // header["size_instruction"]
        writer.write_int32(code_count)
        writer.write(self.code)

    def extract_strings(self, str_list, char_map):
        # Two ID shapes coexist in the game's StringTables:
        #   AAAA000AA  e.g. NICE001RA, NIZZ000TO   (4 letters + 3 digits + 2 letters)
        #   AAA0000AA  e.g. NID1001DO, NID2012DO   (3 letters + 4 digits + 2 letters)
        # The trailing speaker code can include digits (Z1-Z4, P1-P3), so keep
        # export/import on one shared matcher.
        last_id = None
        for i, s in enumerate(self.const_strs):
            if last_id is None:
                if is_string_id(s):
                    last_id = s.decode("ascii")
            else:
                if not is_string_id(s):
                    char_code = last_id[(-2):]
                    speaker = char_code
                    if char_map and char_code in char_map:
                        if char_map[char_code]["ko"]:
                            speaker = char_map[char_code]["ko"]
                        else:
                            if char_map[char_code]["en"]:
                                speaker = char_map[char_code]["en"]
                    str_list.append(
                        {"id": last_id, "speaker": speaker, "en": decode_lub_text(s)}
                    )
                    last_id = None
                else:
                    last_id = s.decode("ascii")
        for child in self.children:
            child.extract_strings(str_list, char_map)

    def replace_strings(self, translation_map):
        last_id = None
        for i in range(len(self.const_strs)):
            s = self.const_strs[i]
            if last_id is None:
                if is_string_id(s):
                    last_id = s.decode("ascii")
            else:
                if not is_string_id(s):
                    if last_id in translation_map:
                        new_text = translation_map[last_id]
                        self.const_strs[i] = new_text.encode("utf-8")
                    last_id = None
                else:
                    last_id = s.decode("ascii")
        for child in self.children:
            child.replace_strings(translation_map)


def read_header(reader):
    header = {}
    header["sig"] = reader.read(4)
    if header["sig"] != b"\x1bLua":
        raise ValueError("Invalid Signature")
    else:
        header["ver"] = reader.read_byte()
        if header["ver"] != 64:
            raise ValueError("Only Lua 4.0 supported")
        else:
            header["endian"] = reader.read_byte()
            header["size_int"] = reader.read_byte()
            header["size_size_t"] = reader.read_byte()
            header["size_instruction"] = reader.read_byte()
            header["SIZE_INSTRUCTION"] = reader.read_byte()
            header["SIZE_OP"] = reader.read_byte()
            header["SIZE_B"] = reader.read_byte()
            header["sizeof_number"] = reader.read_byte()
            header["test_number"] = reader.read(header["sizeof_number"])
            return header


def write_header(writer, header):
    writer.write(header["sig"])
    writer.write_byte(header["ver"])
    writer.write_byte(header["endian"])
    writer.write_byte(header["size_int"])
    writer.write_byte(header["size_size_t"])
    writer.write_byte(header["size_instruction"])
    writer.write_byte(header["SIZE_INSTRUCTION"])
    writer.write_byte(header["SIZE_OP"])
    writer.write_byte(header["SIZE_B"])
    writer.write_byte(header["sizeof_number"])
    writer.write(header["test_number"])


def load_character_csv(path):
    if not path or not os.path.exists(path):
        return None
    else:
        char_map = {}
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                char_map[row["original"]] = {
                    "en": row.get("en", ""),
                    "ko": row.get("ko", ""),
                }
        return char_map


def load_existing_csv(csv_path):
    if not os.path.exists(csv_path):
        return {}
    else:
        existing = {}
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[row["id"]] = {
                    "ko": row.get("ko", ""),
                    "notes": row.get("notes", ""),
                }
        return existing


def export_single(lub_path, csv_path, char_csv=None):
    if not os.path.exists(lub_path):
        print(f"[Error] LUB file not found: {lub_path}")
        return False
    else:
        char_map = load_character_csv(char_csv)
        existing = load_existing_csv(csv_path)
        with open(lub_path, "rb") as f:
            data = f.read()
        reader = LuaReader(data)
        header = read_header(reader)
        root_func = LuaFunction()
        root_func.read(reader, header)
        extracted = []
        root_func.extract_strings(extracted, char_map)
        if not extracted:
            print(f"[Skip] No strings in {lub_path}")
            return False
        else:
            os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    quoting=csv.QUOTE_ALL,
                    fieldnames=["id", "speaker", "en", "ko", "notes"],
                )
                writer.writeheader()
                for item in extracted:
                    row = {
                        "id": item["id"],
                        "speaker": item["speaker"],
                        "en": item["en"],
                        "ko": existing.get(item["id"], {}).get("ko", ""),
                        "notes": existing.get(item["id"], {}).get("notes", ""),
                    }
                    writer.writerow(row)
            print(f"[Export] {lub_path} -> {csv_path} ({len(extracted)} strings)")
            return True


def export_all(lub_dir, csv_dir, char_csv=None):
    if not os.path.exists(lub_dir):
        print(f"[Error] LUB directory not found: {lub_dir}")
        return None
    else:
        count = 0
        for fname in os.listdir(lub_dir):
            if fname.lower().endswith(".lub"):
                lub_path = os.path.join(lub_dir, fname)
                csv_name = os.path.splitext(fname)[0] + ".csv"
                csv_path = os.path.join(csv_dir, csv_name)
                if export_single(lub_path, csv_path, char_csv):
                    count += 1
        print(f"[Done] Exported {count} files to {csv_dir}")


def import_single(lub_path, csv_path, out_path):
    if not os.path.exists(lub_path):
        print(f"[Error] LUB file not found: {lub_path}")
        return False
    else:
        if not os.path.exists(csv_path):
            print(f"[Skip] CSV not found: {csv_path}")
            return False
        else:
            with open(lub_path, "rb") as f:
                data = f.read()
            reader = LuaReader(data)
            header = read_header(reader)
            root_func = LuaFunction()
            root_func.read(reader, header)
            translation_map = {}
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                csv_reader = csv.DictReader(f)
                for row in csv_reader:
                    text = row.get("ko", "").strip()
                    if not text:
                        text = row.get("en", "").strip()
                    if text:
                        translation_map[row["id"]] = text
            root_func.replace_strings(translation_map)
            writer = LuaWriter()
            write_header(writer, header)
            root_func.write(writer, header)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(writer.data)
            print(f"[Import] {csv_path} -> {out_path}")
            return True


def import_all(lub_dir, csv_dir, out_dir):
    if not os.path.exists(lub_dir):
        print(f"[Error] LUB directory not found: {lub_dir}")
        return None
    else:
        count = 0
        for fname in os.listdir(lub_dir):
            if fname.lower().endswith(".lub"):
                lub_path = os.path.join(lub_dir, fname)
                csv_name = os.path.splitext(fname)[0] + ".csv"
                csv_path = os.path.join(csv_dir, csv_name)
                out_path = os.path.join(out_dir, fname)
                if import_single(lub_path, csv_path, out_path):
                    count += 1
        print(f"[Done] Imported {count} files to {out_dir}")


def print_usage():
    print("Usage:")
    print(
        "  Export single: python text_tool.py export --single <lub> <csv> [--character-csv <char.csv>]"
    )
    print(
        "  Export all:    python text_tool.py export --all <lub_dir> <csv_dir> [--character-csv <char.csv>]"
    )
    print("  Import single: python text_tool.py import --single <lub> <csv> <out_lub>")
    print(
        "  Import all:    python text_tool.py import --all <lub_dir> <csv_dir> <out_dir>"
    )
    print()
    print("Examples:")
    print(
        "  python text_tool.py export --single English/GLOBAL_StringTable.lub dialogues/GLOBAL.csv"
    )
    print(
        "  python text_tool.py export --all English dialogues --character-csv characters.csv"
    )
    print(
        "  python text_tool.py import --single English/GLOBAL_StringTable.lub dialogues/GLOBAL.csv Korean/GLOBAL_StringTable.lub"
    )
    print("  python text_tool.py import --all English dialogues Korean")


def main():
    print("Psychonauts 1 Text Tool")
    print("Made by Snowyegret, Version 1.0")
    print()
    if len(sys.argv) < 2:
        print_usage()
        return
    mode = sys.argv[1].lower()
    char_csv = None
    args = sys.argv[2:]
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--character-csv" and i + 1 < len(args):
            char_csv = args[i + 1]
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1
    args = filtered_args
    if mode == "export":
        if len(args) < 3:
            print_usage()
            return
        sub = args[0]
        if sub == "--single":
            export_single(args[1], args[2], char_csv)
        elif sub == "--all":
            export_all(args[1], args[2], char_csv)
        else:
            print_usage()
    elif mode == "import":
        if len(args) < 4:
            print_usage()
            return
        sub = args[0]
        if sub == "--single":
            import_single(args[1], args[2], args[3])
        elif sub == "--all":
            import_all(args[1], args[2], args[3])
        else:
            print_usage()
    else:
        print_usage()


if __name__ == "__main__":
    main()
