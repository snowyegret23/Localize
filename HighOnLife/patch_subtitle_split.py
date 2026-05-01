import argparse
import shutil
import sys
from pathlib import Path


RELATIVE_EXE = Path("Oregon") / "Binaries" / "Win64" / "Oregon-Win64-Shipping.exe"
PATCH_OFFSETS = (0xD3C49D, 0xD3C544)
INSTRUCTION_PREFIX = bytes.fromhex("C7 44 24 20")
ORIGINAL_LIMIT = 80


def format_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def build_patch_bytes(limit: int) -> bytes:
    return INSTRUCTION_PREFIX + limit.to_bytes(4, "little", signed=False)


def backup_path_for(exe_path: Path) -> Path:
    return exe_path.with_suffix(exe_path.suffix + ".subtitle_split.bak")


def default_exe_path() -> Path:
    return Path.cwd() / RELATIVE_EXE


def prompt_line_limit() -> int:
    while True:
        raw = input("line-limit을 입력하세요(원본 80): ").strip()
        if not raw:
            print("값을 입력해야 합니다.")
            continue
        try:
            value = int(raw)
        except ValueError:
            print("숫자만 입력하세요.")
            continue
        if value < 1 or value > 0x7FFFFFFF:
            print("1 이상의 정수를 입력하세요.")
            continue
        return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up and patch High On Life subtitle split limits."
    )
    parser.add_argument(
        "--exe",
        type=Path,
        default=None,
        help=(
            "Path to Oregon-Win64-Shipping.exe "
            f"(default: .\\{RELATIVE_EXE})"
        ),
    )
    parser.add_argument(
        "--line-limit",
        type=int,
        default=None,
        help="New global subtitle split limit. If omitted, ask interactively.",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore the executable from the backup created by this script.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing anything.",
    )
    return parser.parse_args()


def restore_backup(exe_path: Path, dry_run: bool) -> int:
    backup_path = backup_path_for(exe_path)
    if not backup_path.exists():
        print(f"Backup not found: {backup_path}")
        return 1

    print(f"Restore source : {backup_path}")
    print(f"Restore target : {exe_path}")
    if dry_run:
        print("Dry run: restore skipped.")
        return 0

    shutil.copy2(backup_path, exe_path)
    print("Restore complete.")
    return 0


def patch_executable(exe_path: Path, line_limit: int, dry_run: bool) -> int:
    if not exe_path.exists():
        print(f"Executable not found: {exe_path}")
        return 1
    if line_limit < 1 or line_limit > 0x7FFFFFFF:
        print(f"Invalid --line-limit value: {line_limit}")
        return 1

    backup_path = backup_path_for(exe_path)
    new_bytes = build_patch_bytes(line_limit)
    original_bytes = build_patch_bytes(ORIGINAL_LIMIT)

    data = bytearray(exe_path.read_bytes())
    changes: list[tuple[int, bytes, bytes]] = []

    for offset in PATCH_OFFSETS:
        current = bytes(data[offset : offset + len(new_bytes)])
        if len(current) != len(new_bytes):
            print(f"Offset out of range: 0x{offset:X}")
            return 1
        if current[: len(INSTRUCTION_PREFIX)] != INSTRUCTION_PREFIX:
            print(
                f"Unexpected instruction prefix at 0x{offset:X}: "
                f"{format_hex(current)}"
            )
            return 1

        if current != new_bytes:
            changes.append((offset, current, new_bytes))

    print(f"Target executable : {exe_path}")
    print(f"Backup path       : {backup_path}")
    print(f"Requested limit   : {line_limit}")
    print(f"Original limit    : {ORIGINAL_LIMIT}")

    for offset in PATCH_OFFSETS:
        current = bytes(data[offset : offset + len(new_bytes)])
        current_limit = int.from_bytes(current[len(INSTRUCTION_PREFIX) :], "little")
        state = "original" if current == original_bytes else "patched"
        if current == new_bytes:
            state = "target"
        print(
            f"0x{offset:X} : {format_hex(current)} "
            f"(limit={current_limit}, state={state})"
        )

    if not changes:
        print("No changes needed.")
        return 0

    if dry_run:
        print("Dry run: patch skipped.")
        return 0

    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)
        print("Backup created.")
    else:
        print("Backup already exists. Reusing it.")

    for offset, current, patched in changes:
        data[offset : offset + len(patched)] = patched
        print(
            f"Patched 0x{offset:X} : {format_hex(current)} -> {format_hex(patched)}"
        )

    exe_path.write_bytes(data)
    print("Patch complete.")
    return 0


def main() -> int:
    args = parse_args()
    exe_path = args.exe.resolve() if args.exe else default_exe_path().resolve()
    try:
        if args.restore:
            return restore_backup(exe_path, args.dry_run)

        line_limit = args.line_limit
        if line_limit is None:
            line_limit = prompt_line_limit()

        return patch_executable(exe_path, line_limit, args.dry_run)
    except PermissionError as exc:
        print(f"Permission error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
