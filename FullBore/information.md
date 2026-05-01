# FullBore Localization Notes

## Purpose

This folder keeps only the source code and batch scripts needed for the Full Bore localization toolchain. Game executables, patched executables, PyInstaller build output, extracted resources, fonts, translated JSON/BIN files, backups, and resource dumps are intentionally not kept here.

`fullbore_extract_original_fonts.py` was left out because it is an analysis/helper tool for dumping the original embedded fonts, not required for the normal localization workflow.
`fullbore_font_patch.py` was also left out because it only wraps `fullbore_dynafont_patch.py`.

## Copied Source Files

- `fullbore_text.py`: exports Full Bore text BIN files to JSON, imports JSON back to BIN, merges translated overlays, validates key/tag consistency, and can round-trip a BIN for parser checks.
- `fullbore_dynafont_patch.py`: patches `FullBore.exe` so embedded dynafont resources point to a Hangul-capable font payload. It parses the PE file, appends or reuses a `.fbfont` section, updates resource data entries, and refreshes the PE checksum.
- `export_bin.bat`: source-based wrapper for exporting a BIN file to JSON with `fullbore_text.py`.
- `import_bin.bat`: source-based wrapper for importing JSON back to a BIN file with `fullbore_text.py`.
- `fontpatch.bat`: source-based wrapper for applying the dynafont PE patch with `fullbore_dynafont_patch.py`.

The BAT files in this folder call Python source directly. The working folder's original BAT files called PyInstaller-built `.exe` tools, but those executables are build artifacts and were not copied.

## Dependencies

- `fullbore_text.py` uses only the Python standard library.
- `fullbore_dynafont_patch.py` requires `pefile` and `fontTools`.
- `fontTools` is used to verify that the replacement font exposes Hangul glyphs in its cmap, specifically U+AC00 and U+D55C.

## Text Structure

The game text files used by the BAT defaults are:

```text
text\en.bin
text\jp.bin
```

The original backup files inspected in the working folder had these counts:

```text
backup\en.bin  85,708 bytes  798 clean entries
backup\jp.bin  18,972 bytes  238 clean entries
```

The BIN format was identified from the working notes as inferred from `FullBore.exe::FUN_005d7f80`:

- key: ASCII, NUL-terminated.
- value: UTF-16LE, NUL-terminated.
- some keys have an extra NUL byte between the key terminator and the UTF-16LE value.
- files can contain extra trailing NUL words.

The exported JSON is a list of objects:

```json
[
  {
    "key": "example_key",
    "value": "Translated text",
    "gap_words_after": 1
  }
]
```

`gap_words_after` is optional and preserves extra `0x0000` separator words after a value. During import, keys must remain ASCII and values are encoded as UTF-16LE.

`fullbore_text.py` has two parse modes:

- `clean`: skips extra separator words and is intended for normal translation work.
- `exact`: follows the in-game state machine more literally, including odd empty-key artifacts.

Validation preserves control tags matching:

```text
\^[A-Za-z][A-Za-z0-9]*
```

The `validate` command checks key order/count and makes sure the control-tag sequence in translated values matches the base file.

## Font And Resource Structure

Full Bore stores its original dynafonts as named `RT_RCDATA` resources inside the Windows PE executable.

Original `backup\FullBore.exe` resource data entries:

```text
DYNAFONT_EN  entry file offset 0x005F72B0  data RVA 0x0061D7FC  size 28,168
DYNAFONT_JP  entry file offset 0x005F72C0  data RVA 0x00624604  size 4,006,716
```

Other named `RT_RCDATA` resources observed in the same executable:

```text
SHDR_DEFER
SHDR_FILLPOLY
SHDR_SPRITE
SHDR_TEXT
```

Original PE section layout inspected from `backup\FullBore.exe`:

```text
ImageBase 0x00400000
SectionAlignment 0x1000
FileAlignment 0x200

.text   VA 0x00001000  raw 0x00000400  raw size 0x004D3200
.rdata  VA 0x004D5000  raw 0x004D3600  raw size 0x00103200
.data   VA 0x005D9000  raw 0x005D6800  raw size 0x00020000
.rsrc   VA 0x00617000  raw 0x005F6800  raw size 0x003E8200
.reloc  VA 0x00A00000  raw 0x009DEA00  raw size 0x00059200
```

## EXE Patch Details

`fullbore_dynafont_patch.py` does not install x86 code hooks. It patches the PE resource table so the game loads a replacement font when it requests `DYNAFONT_JP`, and by default `DYNAFONT_EN` as well.

Patch steps:

1. Load the input EXE with `pefile`.
2. Locate named `RT_RCDATA` resources `DYNAFONT_EN` and `DYNAFONT_JP`.
3. Read the replacement TTF/OTF/TTC bytes.
4. Validate that the font cmap includes U+AC00 and U+D55C.
5. Place the font payload in a `.fbfont` section.
6. Rewrite the selected `IMAGE_RESOURCE_DATA_ENTRY` records as `<OffsetToData, Size, CodePage, Reserved>`.
7. Preserve the original resource `CodePage` and `Reserved` values.
8. Recalculate and write the PE checksum.

For the original executable, the new section header position is calculated as:

```text
e_lfanew                         0x00000138
file header offset               0x0000013C
optional header offset           0x00000150
section table offset             0x00000230
new section header offset        0x000002F8
first section raw offset         0x00000400
checksum field file offset       0x00000190
```

There is room before the first section raw offset for one additional section header.

If `.fbfont` already exists, the script only reuses it when it is the last section. Otherwise, it creates a new section:

```text
name   .fbfont
flags  0x40000040
```

With the inspected `Mulmaru.ttf` payload size of 1,606,948 bytes, the patched example `FullBore.mulmaru.dynafont.flex.exe` had:

```text
.fbfont VA 0x00A5A000  raw 0x00A37C00  virtual size 0x00188524  raw size 0x00188600
SizeOfImage 0x00BE3000

DYNAFONT_EN data RVA 0x00A5A000  size 1,606,948
DYNAFONT_JP data RVA 0x00A5A000  size 1,606,948
```

By default both `DYNAFONT_EN` and `DYNAFONT_JP` are linked to the replacement font. Use `--no-link-en` only if `DYNAFONT_EN` must remain unchanged.

## Normal Workflow

Prepare working copies of the original game files outside this source-only folder:

```text
FullBore.exe
text\en.bin
text\jp.bin
```

Export text:

```bat
export_bin.bat text\en.bin en.json clean
```

Or call Python directly:

```bat
python fullbore_text.py export --mode clean text\en.bin en.json
```

Translate the JSON values. Keep `key` values, entry order, and control tags intact.

Validate against the base export:

```bat
python fullbore_text.py validate en_base.json en_translated.json
```

Import translated JSON back to BIN:

```bat
import_bin.bat en_translated.json text\en.bin
```

If the same rebuilt BIN should also replace Japanese text:

```bat
import_bin.bat en_translated.json text\en.bin --also-jp
```

Patch the executable font resource:

```bat
fontpatch.bat KoreanFont.ttf FullBore.exe FullBore.korean.exe
```

Or call Python directly:

```bat
python fullbore_dynafont_patch.py --exe FullBore.exe --font KoreanFont.ttf --out FullBore.korean.exe
```

## Important Notes

- Keep JSON keys unchanged. Import requires ASCII keys and writes them back as ASCII.
- Preserve caret control tags in translated strings. The validator compares tag sequences with the base file.
- Use `clean` mode for translation work. Use `exact` only when investigating loader behavior.
- The font patcher appends a new font section instead of overwriting the original resource body, which avoids size limits from the original embedded resources.
- Repatching is supported only when an existing `.fbfont` section is the last PE section.
- The folder intentionally excludes `*.spec`, `build/`, `dist/`, `backup/`, `resource_dump/`, `original_dynafonts/`, `__pycache__/`, `*.exe`, `*.bin`, `*.json`, `*.png`, and `*.ttf`.
