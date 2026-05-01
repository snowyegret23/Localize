"""
Dump all translatable text from:
  1. articy:draft UABEA JSON export (dialogue, menus, actors)
  2. Mod's EntityTextAssets dump (bills, codex, news, etc.)
Output: dump.csv with "key","src","dst" (UTF-8, CRLF, full-quote)
"""
import json
import sys
import os
import csv

BASE = os.path.dirname(__file__)
INPUT = os.path.join(BASE,
    "text_uabea",
    "Suzerain-CAB-4baef2dda5f38ed8b34b5cc0e775ce26-3360783889458989589.json")
MOD_DUMP = os.path.join(
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "GOG Galaxy", "Games", "Suzerain", "Mods", "SuzerainLocData", "dump.csv")
OUTPUT = os.path.join(BASE, "dump.csv")

def esc(s: str) -> str:
    return s.replace('"', '""')

def main():
    print(f"Loading {INPUT} ...")
    with open(INPUT, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("Loaded.")

    rows: list[tuple[str, str, str]] = []  # (key, type, src)

    # ── Actors ────────────────────────────────────────────────────
    for actor in data.get("actors", {}).get("Array", []):
        fields = {f["title"]: f["value"] for f in actor.get("fields", {}).get("Array", [])}
        aid = str(actor.get("id", ""))
        name = fields.get("Name", "")
        desc = fields.get("Description", "")
        if name:
            rows.append((f"actor.{aid}.Name", "actor", name))
        if desc:
            rows.append((f"actor.{aid}.Description", "actor", desc))

    # ── Conversations + DialogueEntries ───────────────────────────
    for conv in data.get("conversations", {}).get("Array", []):
        conv_fields = {f["title"]: f["value"] for f in conv.get("fields", {}).get("Array", [])}
        conv_title = conv_fields.get("Title", "")
        conv_desc = conv_fields.get("Description", "")
        conv_id = str(conv.get("id", ""))

        if conv_desc:
            rows.append((f"conv.{conv_id}.Description", "conv", conv_desc))

        for entry in conv.get("dialogueEntries", {}).get("Array", []):
            e_fields = {f["title"]: f["value"] for f in entry.get("fields", {}).get("Array", [])}
            articy_id = e_fields.get("Articy Id", "")
            entry_id = str(entry.get("id", ""))
            en = e_fields.get("en", "")
            menu_en = e_fields.get("Menu Text en", "")
            seq_en = e_fields.get("Sequence en", "")

            # Use articy_id as primary key, fall back to conv_id.entry_id
            key_base = articy_id if articy_id else f"conv.{conv_id}.e.{entry_id}"

            if en:
                rows.append((f"{key_base}.en", "dialogue", en))
            if menu_en and menu_en != en:
                rows.append((f"{key_base}.menu_en", "menu", menu_en))
            # Sequence often has text shown to player too
            # But it's mostly engine commands, skip unless it contains readable text

    # ── Variables (skip — not translatable) ───────────────────────
    # ── Items / Locations (empty in this export) ──────────────────

    # ── Merge mod dump (EntityTextAssets) ─────────────────────────
    if os.path.exists(MOD_DUMP):
        print(f"Merging EntityTextAssets from {MOD_DUMP} ...")
        seen_keys = {k for k, _, _ in rows}
        mod_count = 0
        with open(MOD_DUMP, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                if len(row) < 2: continue
                key, src = row[0], row[1]
                if not key or not src: continue
                if key in seen_keys: continue
                rows.append((key, "entity", src))
                seen_keys.add(key)
                mod_count += 1
        print(f"  EntityTextAssets: +{mod_count} entries")
    else:
        print(f"Mod dump not found at {MOD_DUMP}, skipping EntityTextAssets merge.")

    # ── Write CSV ─────────────────────────────────────────────────
    print(f"Writing {len(rows)} entries to {OUTPUT} ...")
    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        f.write('"key","src","dst"\r\n')
        for key, typ, src in rows:
            f.write(f'"{esc(key)}","{esc(src)}",""\r\n')

    # Stats
    types = {}
    for _, typ, _ in rows:
        types[typ] = types.get(typ, 0) + 1
    print("Done.")
    for t, c in sorted(types.items()):
        print(f"  {t}: {c}")
    print(f"  TOTAL: {len(rows)}")

if __name__ == "__main__":
    main()
