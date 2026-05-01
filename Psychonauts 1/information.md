# Psychonauts 1 Localization Notes

## Purpose

This folder keeps only the source code and batch scripts for the Psychonauts 1 Korean localization toolchain. Large game resources, translated CSV files, generated fonts, patched executables, packaged tool executables, PyInstaller build files, and decompiled reference files are not kept here.

## Copied Source Files

- `text_tool.py`: exports text from Psychonauts Lua bytecode string tables and imports translated text back into `.lub` files.
- `create_korean_font.py`: creates a bitmap font atlas from a TTF and packs it into the game's `.dff` font format.
- `apply_patch.py`: patches the Windows executable so the game can render UTF-8 Korean text and measure Korean glyph widths correctly.
- `build_used_charlist.py`: scans translated CSV files and speaker data to build a compact character list for font generation.
- `1_patch_exe_and_font.bat`: batch workflow for refreshing the character list, generating/packing Korean fonts, and applying the executable patch.
- `2_export_lub.bat`: batch workflow for exporting `.lub` string tables to CSV.
- `3_import_lub.bat`: batch workflow for importing translated CSV files back into `.lub` string tables.

## Game Text System

Psychonauts 1 stores localization text in compiled Lua 4.0 bytecode files with the `.lub` extension. The working localization files are expected under:

```text
WorkResource/Localization/English/*.lub
```

The text tool parses the Lua bytecode structure directly:

- Header signature: `\x1bLua`
- Supported Lua version byte: `64` (`0x40`, Lua 4.0)
- Strings are read from function constant tables.
- Nested Lua functions are traversed recursively.
- Number constants, bytecode, locals, and line tables are preserved while string constants are replaced.

String table entries are detected as an ID followed by a text string. Valid IDs match:

```text
^[A-Z]{2,4}[0-9]{3,4}[A-Z0-9]{2}$
```

Examples:

- `NICE001RA`
- `NIZZ000TO`
- `NID1001DO`

The last two characters of the ID are treated as the speaker code. `speaker.csv` maps those original speaker codes to English/Korean display names during export.

## CSV Format

Exported CSV files use this schema:

```csv
"id","speaker","en","ko","notes"
```

- `id`: original string ID from the `.lub` string table.
- `speaker`: speaker code or mapped speaker name from `speaker.csv`.
- `en`: original text decoded as UTF-8, with CP1252 fallback.
- `ko`: Korean translation. During import, this is used first.
- `notes`: translator notes. Existing notes are preserved when re-exporting.

When importing, if `ko` is blank, the tool falls back to `en`. Replacement text is encoded as UTF-8 and written back into the Lua string constants.

## Font System

Psychonauts uses `.dff` bitmap font resources. `create_korean_font.py` creates a 1024x1024 grayscale atlas and writes metadata before packing it into the game's font format.

Important details:

- Magic: `FFFD`
- Texture size: `1024 x 1024`
- Padding: `1`
- Default font size in the batch file: `24`
- ASCII printable characters are always included.
- Korean and other required characters come from `CharList_3864.txt`.
- The intermediate JSON contains `glyphs` and `unicode_map`.
- The packed `.dff` contains an ASCII map, glyph rectangles, atlas metadata, and raw grayscale image bytes.
- `glyphs[0]` is intentionally a dummy glyph: `x=0`, `y=0`, `w=4`, `h=0`, `meta=0`, `char="\0"`.
- The game appears to use glyph index `0` for fallback measurement and word wrapping. Width `4` was chosen because it gives wrapping behavior closest to the original English font.

The batch workflow generates replacements for several original game fonts:

```text
Arial_lin.dff
Arial_swz.dff
bagel_lin.dff
bagel_swz.dff
Tahoma_lin.dff
Tahoma_swz.dff
RazNotebook_lin.dff
RazNotebook_swz.dff
```

## Executable Patch System

`apply_patch.py` patches `Psychonauts_expand_section.exe` and writes `Psychonauts.exe`.

`Psychonauts_expand_section.exe` is the prepared base executable. The original executable is not kept in this source-only folder, so the byte-level original-to-expanded diff is not documented here. The important expected change is that the prepared executable has extra PE sections used as patch space:

```text
.patchc  VA 0x0080C000  size 0x10000  raw offset 0x3F5400
.patchd  VA 0x0081C000  size 0x10000  raw offset 0x405400
```

- `.patchc` stores injected x86 code.
- `.patchd` stores the generated Unicode codepoint to glyph-index lookup table.
- The last 4 bytes before `.patchd` (`0x0081BFFC`) are also used as a temporary glyph-index slot during width calculation.

The patch adds hooks for:

- Render text glyph lookup.
- Text width calculation.
- Glyph index calculation for non-ASCII characters.

The patch code decodes UTF-8 2-byte and 3-byte sequences, converts them to Unicode codepoints, then looks up glyph indices using the generated font JSON `unicode_map`. This keeps Korean rendering and width calculation aligned with the generated DFF font.

Patch layout:

```text
0x00425394 -> jump to 0x0080C000  RenderText hook
0x00423F3D -> jump to 0x0080C100  CalcWidth UTF-8 read hook
0x00424110 -> jump to 0x0080C200  CalcWidth glyph-index hook
```

Injected code:

```text
0x0080C000  render patch code, 142 bytes
0x0080C100  CalcWidth read patch code, 174 bytes
0x0080C200  CalcWidth glyph patch code, 50 bytes
0x0081BFFC  temporary glyph-index DWORD
0x0081C000  Unicode mapping table
```

At each original hook site, the script writes a 5-byte relative `JMP` (`E9 rel32`) and fills the remaining overwritten bytes with `NOP` (`90`). The injected code returns by pushing the original return address and executing `RET`.

Unicode mapping table layout:

```text
DWORD pointer_to_first_entry  ; 0x0081C008
DWORD entry_count
DWORD codepoint, DWORD glyph_index
DWORD codepoint, DWORD glyph_index
...
```

For ASCII bytes (`< 0x80`), the patch keeps the original single-byte lookup path. For UTF-8 2-byte and 3-byte sequences, it decodes the sequence to a Unicode codepoint and searches the `.patchd` mapping table. If no mapping is found, the render path falls back to glyph index `0`, and the width path falls back to a space-like placeholder.

The patch is address-specific to the prepared executable layout. The script relies on fixed virtual addresses and converts them to file offsets by reading PE sections.

## Normal Localization Workflow

1. Prepare original game resources in the working folder:

```text
WorkResource/Localization/English/*.lub
WorkResource/Fonts/*.dff
Psychonauts_expand_section.exe
```

2. Export text:

```bat
2_export_lub.bat
```

This creates `exported_lub/*.csv`.

3. Translate CSV files:

```text
exported_lub/*.csv
```

Fill the `ko` column. Keep `id` stable.

4. Import translated text:

```bat
3_import_lub.bat
```

This writes translated strings back into `WorkResource/Localization/English/*.lub`.

5. Generate Korean font resources and patch the executable:

```bat
1_patch_exe_and_font.bat
```

This refreshes the used character list, generates font atlas files, packs `.dff` fonts, and patches the executable.

## Notes And Risks

- Keep string IDs unchanged. They are the only stable key used for import.
- Do not edit `.lub` files directly unless backing them up first.
- The executable patch depends on exact addresses and the expanded-section executable. It should not be applied to an unknown build without revalidating addresses.
- The generated Korean font must contain every codepoint used by translated text, otherwise text may render as missing glyphs or width calculation may be wrong.
- `build_used_charlist.py` intentionally scans translated CSV files as raw UTF-8 text so characters in notes or speaker names can also be captured.
