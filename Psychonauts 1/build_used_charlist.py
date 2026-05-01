"""Scan exported_lub/*.csv + speaker.csv (in current working directory) and
write CharList_used.txt + overwrite CharList_3864.txt with the slim version.

Designed to run from the game folder (or any folder that holds the same
exported_lub/, speaker.csv, CharList_3864.txt layout).

Reads each CSV as raw UTF-8 text rather than parsing columns, so any character
the translator placed in any column (including stray notes in E/F/...) gets
captured. CSV delimiters/headers are ASCII and don't affect the result.
"""
import sys
from pathlib import Path

CWD = Path.cwd()

CSV_FOLDER = CWD / "exported_lub"
SPEAKER_FILE = CWD / "speaker.csv"
OUT = CWD / "CharList_used.txt"
OLD = CWD / "CharList_3864.txt"

chars: set = set()

# ASCII printable is added automatically by create_korean_font, but include it
# here too so the printed totals reflect what the font will actually contain.
for cp in range(0x20, 0x7F):
    chars.add(chr(cp))


def feed_file(path: Path):
    try:
        # errors='replace' so a stray bad byte never aborts the whole scan
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.read()
    except Exception as e:
        print(f"[warn] {path.name}: {e}")
        return False
    for c in data:
        if ord(c) > 0x20:
            chars.add(c)
    return True


files_seen = []

if CSV_FOLDER.exists():
    for p in sorted(CSV_FOLDER.glob("*.csv")):
        if p.name.endswith(".bak"):
            continue
        if feed_file(p):
            files_seen.append(p.name)
else:
    print(f"[warn] {CSV_FOLDER} not found - no translated CSVs scanned")

if SPEAKER_FILE.exists():
    if feed_file(SPEAKER_FILE):
        files_seen.append(SPEAKER_FILE.name)

# Write slim CharList
sorted_chars = sorted(chars)
text = "".join(sorted_chars)
OUT.write_text(text, encoding="utf-8")

# Stats
hangul = sum(1 for c in chars if 0xAC00 <= ord(c) <= 0xD7A3)
ascii_n = sum(1 for c in chars if 0x20 < ord(c) < 0x7F)
others = len(chars) - hangul - ascii_n - 1  # -1 for the space

print(f"Scanned {len(files_seen)} files in {CWD}")
print(f"Unique chars (ord > 32): {len(chars) - 1}")
print(f"  ASCII printable: {ascii_n}")
print(f"  Hangul         : {hangul}")
print(f"  Other          : {others}")

if OLD.exists():
    old_text = OLD.read_text(encoding="utf-8", errors="replace")
    old_chars = sum(1 for c in old_text if ord(c) > 0x20)
    if old_chars > 0:
        ratio = 100 * (len(chars) - 1) / old_chars
        print(f"Old CharList: {old_chars} chars  ->  New: {len(chars) - 1} chars  ({ratio:.0f}% of old)")

# Overwrite CharList_3864.txt so the existing build pipeline picks it up
# without any further changes.
OLD.write_text(text, encoding="utf-8")
print(f"Wrote {OUT.name} and overwrote {OLD.name}")
