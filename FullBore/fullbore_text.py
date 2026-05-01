import argparse
import json
import re
from pathlib import Path
from typing import Iterable

CONTROL_TAG_RE = re.compile(r"\^[A-Za-z][A-Za-z0-9]*")


def load_entries(json_path: Path) -> list[dict[str, str]]:
    entries = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{json_path} JSON root must be a list")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or "key" not in entry or "value" not in entry:
            raise ValueError(f"{json_path} entry {index} must contain key/value")
        if not isinstance(entry["key"], str) or not isinstance(entry["value"], str):
            raise ValueError(f"{json_path} entry {index} key/value must be strings")
        if "gap_words_after" in entry:
            gap_words_after = entry["gap_words_after"]
            if not isinstance(gap_words_after, int) or gap_words_after < 0:
                raise ValueError(f"{json_path} entry {index} gap_words_after must be a non-negative integer")
    return entries


def extract_control_tags(text: str) -> list[str]:
    return CONTROL_TAG_RE.findall(text)


def _decode_ascii(data: bytes, start: int, end: int) -> str:
    try:
        return data[start:end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"non-ASCII key at 0x{start:x}") from exc


def _decode_utf16le(data: bytes, start: int, end: int, key: str) -> str:
    try:
        return data[start:end].decode("utf-16le")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-16LE value for key {key!r}") from exc


def parse_bin_exact(data: bytes) -> list[dict[str, str]]:
    if not data:
        return []

    size = len(data)
    entries: list[dict[str, str]] = []
    pos = 0
    key_start = 0
    key_end = data.find(b"\x00", 0)
    if key_end < 0:
        raise ValueError("missing initial key terminator")
    current_key = _decode_ascii(data, key_start, key_end)
    state = 0
    value_start = 0

    while pos < size:
        if state == 0:
            if data[pos] != 0:
                pos += 1
                continue
            if pos + 2 >= size:
                break
            if data[pos + 1] == 0:
                value_start = pos + 2
                pos += 2
            else:
                value_start = pos + 1
                pos += 1
            state = 1
            continue

        if pos + 1 >= size:
            raise ValueError(f"unterminated value scan at 0x{pos:x}")
        if data[pos] != 0 or data[pos + 1] != 0:
            pos += 2
            continue

        value = _decode_utf16le(data, value_start, pos, current_key)
        entries.append({"key": current_key, "value": value})

        next_key_start = pos + 2
        if next_key_start >= size:
            break

        key_end = data.find(b"\x00", next_key_start)
        if key_end < 0:
            raise ValueError(f"missing key terminator after 0x{next_key_start:x}")
        current_key = _decode_ascii(data, next_key_start, key_end)
        pos += 2
        state = 0

    return entries


def parse_bin_clean(data: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    size = len(data)
    pos = 0

    while pos < size:
        while pos + 1 < size and data[pos] == 0 and data[pos + 1] == 0:
            pos += 2
        if pos >= size:
            break

        key_end = data.find(b"\x00", pos)
        if key_end < 0:
            raise ValueError(f"missing key terminator at 0x{pos:x}")
        key = _decode_ascii(data, pos, key_end)

        value_start = key_end + 1
        if value_start < size and data[value_start] == 0:
            value_start += 1
        if value_start >= size:
            break

        value_end = value_start
        while value_end + 1 < size:
            if data[value_end] == 0 and data[value_end + 1] == 0:
                break
            value_end += 2
        else:
            raise ValueError(f"unterminated UTF-16 value for key {key!r}")

        value = _decode_utf16le(data, value_start, value_end, key)
        pos = value_end + 2
        gap_words_after = 0
        while pos + 1 < size and data[pos] == 0 and data[pos + 1] == 0:
            gap_words_after += 1
            pos += 2

        entry = {"key": key, "value": value}
        if gap_words_after > 0:
            entry["gap_words_after"] = gap_words_after
        entries.append(entry)

    return entries


def parse_bin(data: bytes, mode: str = "clean") -> list[dict[str, str]]:
    if mode == "exact":
        return parse_bin_exact(data)
    if mode == "clean":
        return parse_bin_clean(data)
    raise ValueError(f"unsupported parse mode: {mode}")


def build_bin(entries: Iterable[dict[str, str]]) -> bytes:
    chunks = bytearray()

    for index, entry in enumerate(entries):
        try:
            key = entry["key"]
            value = entry["value"]
        except KeyError as exc:
            raise ValueError(f"entry {index} must contain key/value") from exc

        try:
            key_bytes = key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"entry {index} key is not ASCII: {key!r}") from exc

        value_bytes = value.encode("utf-16le")
        gap_words_after = entry.get("gap_words_after", 1 if value == "" else 0)
        if not isinstance(gap_words_after, int) or gap_words_after < 0:
            raise ValueError(f"entry {index} gap_words_after must be a non-negative integer")

        chunks.extend(key_bytes)
        chunks.append(0)

        needs_pad = ((len(key_bytes) + 1) & 1) or value == "" or (value_bytes[:1] == b"\x00")
        if needs_pad:
            chunks.append(0)

        chunks.extend(value_bytes)
        chunks.extend(b"\x00\x00")
        chunks.extend(b"\x00\x00" * gap_words_after)

    return bytes(chunks)


def cmd_export(src: Path, dst: Path, mode: str) -> None:
    entries = parse_bin(src.read_bytes(), mode=mode)
    dst.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported {len(entries)} entries ({mode}): {src} -> {dst}")


def cmd_import(src: Path, dst: Path) -> None:
    entries = load_entries(src)
    data = build_bin(entries)
    dst.write_bytes(data)
    print(f"imported {len(entries)} entries: {src} -> {dst}")


def cmd_merge(base_path: Path, overlay_path: Path, out_path: Path) -> None:
    base_entries = load_entries(base_path)
    overlay_entries = load_entries(overlay_path)

    overlay_occurrence: dict[tuple[str, int], str] = {}
    overlay_count_by_key: dict[str, int] = {}
    overlay_sequence: list[tuple[str, int, dict[str, str]]] = []

    for entry in overlay_entries:
        key = entry["key"]
        value = entry["value"]
        occurrence = overlay_count_by_key.get(key, 0)
        overlay_count_by_key[key] = occurrence + 1
        overlay_occurrence[(key, occurrence)] = value
        overlay_sequence.append((key, occurrence, entry))

    merged: list[dict[str, str]] = []
    base_count_by_key: dict[str, int] = {}

    for entry in base_entries:
        key = entry["key"]
        value = entry["value"]
        occurrence = base_count_by_key.get(key, 0)
        base_count_by_key[key] = occurrence + 1
        overlay_value = overlay_occurrence.get((key, occurrence), value)
        merged_entry = dict(entry)
        merged_entry["value"] = overlay_value if overlay_value != "" else value
        merged.append(merged_entry)

    for key, occurrence, overlay_entry in overlay_sequence:
        if occurrence >= base_count_by_key.get(key, 0):
            merged.append(dict(overlay_entry))

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged {len(base_entries)} base entries with {len(overlay_entries)} overlay entries -> {out_path}")


def cmd_validate(base_path: Path, candidate_path: Path, allow_extra_keys: bool) -> None:
    base_entries = load_entries(base_path)
    candidate_entries = load_entries(candidate_path)
    issues: list[str] = []

    min_len = min(len(base_entries), len(candidate_entries))
    key_mismatch = False
    for index in range(min_len):
        base_key = base_entries[index]["key"]
        candidate_key = candidate_entries[index]["key"]
        if base_key != candidate_key:
            key_mismatch = True
            issues.append(
                f"key mismatch at index {index}: expected {base_key!r}, got {candidate_key!r}"
            )

    if len(candidate_entries) < len(base_entries):
        issues.append(
            f"candidate has fewer entries: expected {len(base_entries)}, got {len(candidate_entries)}"
        )
    elif len(candidate_entries) > len(base_entries) and not allow_extra_keys:
        issues.append(
            f"candidate has extra entries: expected {len(base_entries)}, got {len(candidate_entries)}"
        )

    if not key_mismatch:
        for index in range(min_len):
            base_value = base_entries[index]["value"]
            candidate_value = candidate_entries[index]["value"]
            base_tags = extract_control_tags(base_value)
            candidate_tags = extract_control_tags(candidate_value)
            if base_tags != candidate_tags:
                issues.append(
                    f"control-tag mismatch at index {index} key {base_entries[index]['key']!r}: "
                    f"expected {base_tags}, got {candidate_tags}"
                )

    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        raise SystemExit(1)

    print(
        f"validation OK: {candidate_path} matches {base_path}"
        + (" (extra keys allowed)" if allow_extra_keys else "")
    )


def cmd_roundtrip(src: Path) -> None:
    original = src.read_bytes()
    rebuilt = build_bin(parse_bin(original, mode="clean"))
    if original != rebuilt:
        raise SystemExit(f"round-trip mismatch: {src}")
    print(f"round-trip OK: {src}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="export BIN to JSON")
    export_parser.add_argument("src", type=Path)
    export_parser.add_argument("dst", type=Path)
    export_parser.add_argument(
        "--mode",
        choices=("clean", "exact"),
        default="clean",
        help="clean for translation work, exact to mirror the game loader",
    )

    import_parser = subparsers.add_parser("import", help="import JSON to BIN")
    import_parser.add_argument("src", type=Path)
    import_parser.add_argument("dst", type=Path)

    merge_parser = subparsers.add_parser("merge", help="merge overlay JSON onto base JSON by key")
    merge_parser.add_argument("base", type=Path)
    merge_parser.add_argument("overlay", type=Path)
    merge_parser.add_argument("dst", type=Path)

    validate_parser = subparsers.add_parser("validate", help="validate translated JSON against a base JSON")
    validate_parser.add_argument("base", type=Path)
    validate_parser.add_argument("candidate", type=Path)
    validate_parser.add_argument(
        "--allow-extra-keys",
        action="store_true",
        help="allow candidate to append entries after the base list",
    )

    roundtrip_parser = subparsers.add_parser("roundtrip", help="verify BIN parser/packer")
    roundtrip_parser.add_argument("src", type=Path)

    args = parser.parse_args()

    if args.command == "export":
        cmd_export(args.src, args.dst, args.mode)
    elif args.command == "import":
        cmd_import(args.src, args.dst)
    elif args.command == "merge":
        cmd_merge(args.base, args.overlay, args.dst)
    elif args.command == "validate":
        cmd_validate(args.base, args.candidate, args.allow_extra_keys)
    else:
        cmd_roundtrip(args.src)


if __name__ == "__main__":
    main()
