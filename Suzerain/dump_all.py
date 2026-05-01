"""
Full dump of all Suzerain translatable text from:
  1. articy:draft UABEA JSON (dialogues, menus, actors)
  2. EntityTextAssets bundle via UnityPy (bills, codex, news, etc.)
Output: dump.csv with "key","src","dst" (UTF-8, CRLF, full-quote)
"""
import json
import os
import csv
import re

BASE = os.path.dirname(__file__)
GAME_AA = os.path.join(
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "GOG Galaxy", "Games", "Suzerain", "Suzerain_Data", "StreamingAssets",
    "aa", "StandaloneWindows64")
ARTICY_JSON = os.path.join(BASE, "text_uabea",
    "Suzerain-CAB-4baef2dda5f38ed8b34b5cc0e775ce26-3360783889458989589.json")
ENTITY_BUNDLE = os.path.join(GAME_AA,
    "defaultlocalgroup_assets_assets_database_entitytextassets.asset_7658ed792f0c4924d06b11829a6c36d1.bundle")
SCENE_BUNDLES = [
    os.path.join(GAME_AA, "scenes_scenes_assets_scenes_mainmenu.unity_55bf1b8ebce7f13d6de1e2036ae24392.bundle"),
    os.path.join(GAME_AA, "scenes_scenes_assets_scenes_sordland.unity_89bbe5bad3a8370ed4a20618bcdf25b8.bundle"),
    os.path.join(GAME_AA, "scenes_scenes_assets_scenes_rizia.unity_423f7dad200b5af581cbc9310a195394.bundle"),
]
OUTPUT = os.path.join(BASE, "dump.csv")

# JSON keys that contain translatable text in EntityTextAssets
LOCALIZABLE_KEYS = {
    "Text", "MenuText", "Title", "Subtitle", "Description", "Dialogue",
    "BillName", "BillDescription", "HubBillName", "HubBillDescription",
    "Name", "CharacterName", "CharacterTitle",
    "PanelTitle", "PanelSubtitle", "PageName",
    "CharacterInfoTitle_1", "CharacterInfoTitle_2",
    "CharacterInfoSubtitle_1", "CharacterInfoSubtitle_2",
    "DisplayText", "HeaderText", "BodyText", "ButtonText",
    "OptionText", "TooltipText", "Label",
    "HubTitle", "HubDescription",
}

def esc(s: str) -> str:
    return s.replace('"', '""')

def is_localizable_value(value: str) -> bool:
    if not value or len(value) < 2:
        return False
    if value.startswith("0x"):
        return False
    if value in ("true", "false", "null"):
        return False
    if all(c in "0123456789.-" for c in value):
        return False
    if "/" in value and " " not in value:
        return False
    return True

# ── Extract EntityTextAssets via UnityPy ──────────────────────────
# UnityPy TextAsset name -> runtime EntityTextAssets property name
TEXTASSET_TO_RUNTIME = {
    "BillData": "BillsDataJson",
    "DecisionData": "DecisionsDataJson",
    "ConversationData": "ConversationsDataJson",
    "NarrationData": "NarrationsDataJson",
    "CodexEntryData": "CodexEntriesDataJson",
    "CodexCategoryData": "CodexCategoryDataJson",
    "CodexTopicData": "CodexTopicDataJson",
    "JournalEntryData": "JournalEntriesDataJson",
    "NewsData": "NewsDataJson",
    "ReportData": "ReportsDataJson",
    "PolicyData": "PoliciesDataJson",
    "SituationData": "SituationsDataJson",
    "NewsCategoryData": "NewsCategoryDataJson",
    "CharacterCustomizationPanelData": "CharacterCustomizationPanelsDataJson",
    "CharacterCustomizationTypeData": "CharacterCustomizationTypeDataJson",
    "CharacterCustomizationOptionData": "CharacterCustomizationOptionDataJson",
    "ConnectionPositionData": "ConnectionPositionDataJson",
    "FactionData": "FactionDataJson",
    "CompositionData": "CompositionDataJson",
    "AdvisorsPageData": "AdvisorsPageDataJson",
    "FactionsPageData": "FactionsPageDataJson",
    "CompositionPageData": "CompositionPageDataJson",
    "ConnectionData": "ConnectionDataJson",
    "ConnectionsPanelData": "ConnectionsPanelDataJson",
    "PagedDecisionPanelData": "PagedDecisionPanelsDataJson",
    "MultipleChoicePageData": "MultipleChoicePageDataJson",
    "CarouselChoicePageData": "CarouselChoicePageDataJson",
    "MultipleChoiceOptionData": "MultipleChoiceOptionDataJson",
    "CarouselChoiceOptionData": "CarouselChoiceOptionDataJson",
    "OneTimeDecreesPanelData": "OneTimeDecreesPanelDataJson",
    "ReusableDecreesPanelData": "ReusableDecreesPanelDataJson",
    "DecreeData": "DecreesDataJson",
    "DecreeApprovalData": "DecreeApprovalDataJson",
    "DecreeApprovalCharacterData": "DecreeApprovalCharacterDataJson",
    "ReminderPanelData": "ReminderPanelDataJson",
    "ReminderPanelSegmentData": "ReminderPanelSegmentDataJson",
    "CityTokenData": "CityTokensDataJson",
    "CountryTokenData": "CountryTokensDataJson",
    "EventTokenData": "EventTokensDataJson",
    "TokenStatusEffectData": "TokenStatusEffectsDataJson",
    "ConditionalTokenStatusEffectGroupData": "ConditionalTokenStatusEffectGroupDataJson",
    "TurnData": "TurnsDataJson",
    "StepData": "StepDataJson",
    "TimelineData": "TimelineDataJson",
    "TimelineElementData": "TimelineElementDataJson",
    "TooltipData": "TooltipDataJson",
    "MapSegmentData": "MapSegmentDataJson",
    "MapLayerData": "MapLayerDataJson",
    "StoryPackData": "StoryPackDataJson",
    "AppBundleData": "AppBundleDataJson",
    "CollectionItemData": "CollectionItemDataJson",
    "DLCCollectionItemData": "DLCCollectionItemDataJson",
    "ConditionalAchievementData": "ConditionalAchievementDataJson",
    "CharacterDetailsPanelData": "CharacterDetailsPanelDataJson",
    "CharacterDetailsSectionData": "CharacterDetailsSectionDataJson",
    "CountryDetailsPanelData": "CountryDetailsPanelDataJson",
    "CountryDetailsDemographicData": "CountryDetailsDemographicDataJson",
    "HUDPanelData": "HUDPanelDataJson",
    "HUDStatData": "HUDStatDataJson",
    "HUDTextStatData": "HUDTextStatDataJson",
    "HUDPeriodicStatModifierData": "HUDPeriodicStatModifierDataJson",
    "SummaryData": "SummaryDataJson",
    "SummarySegmentData": "SummarySegmentDataJson",
    "CompassData": "CompassDataJson",
    "CompassConfigurationData": "CompassConfigurationDataJson",
    "CompassTitleData": "CompassTitleDataJson",
    "CompassCharacterGroupData": "CompassCharacterGroupDataJson",
    "CompassCharacterData": "CompassCharacterDataJson",
    "AnalyticsEventData": "AnalyticsEventDataJson",
    "WarProductionPanelData": "WarProductionPanelDataJson",
    "TutorialPanelData": "TutorialPanelDataJson",
    "TutorialPageData": "TutorialPageDataJson",
    "OperationData": "OperationDataJson",
    "GraphPanelData": "GraphPanelDataJson",
    "ArchetypeData": "ArchetypesDataJson",
    "WarFragmentData": "WarFragmentDataJson",
    "ConditionalInstructionData": "ConditionalInstructionDataJson",
}

def extract_entity_text_assets() -> list[tuple[str, str, str]]:
    import UnityPy
    rows = []
    if not os.path.exists(ENTITY_BUNDLE):
        print(f"  Bundle not found: {ENTITY_BUNDLE}")
        return rows

    print(f"  Loading bundle...")
    env = UnityPy.load(ENTITY_BUNDLE)

    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            name = data.m_Name  # e.g. "BillData", "ConversationData"
            text = data.m_Script
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            if not text:
                continue
            # Use runtime property name as category (for mod key matching)
            category = TEXTASSET_TO_RUNTIME.get(name, name + "Json")
            print(f"    TextAsset: {name} -> {category} ({len(text)} chars)")
            try:
                parsed = json.loads(text)
                extract_from_parsed(parsed, category, rows)
            except json.JSONDecodeError:
                print(f"    WARNING: {name} is not valid JSON, skipping")

    return rows

def extract_from_parsed(obj, category: str, rows: list, item_id: str = "", path: str = ""):
    """Recursively walk JSON and extract localizable key-value pairs.
    item_id tracks the entity's Id field for unique keying."""
    if isinstance(obj, dict):
        current_id = item_id
        if "Id" in obj and isinstance(obj["Id"], str):
            current_id = obj["Id"]
        for k, v in obj.items():
            if isinstance(v, str) and k in LOCALIZABLE_KEYS and is_localizable_value(v):
                if current_id:
                    key = f"{category}.{current_id}.{k}"
                else:
                    key = f"{category}.{k}"
                rows.append((key, "entity", v))
            elif k == "Keywords" and isinstance(v, list) and current_id:
                # Dump each keyword for translation (used for codex hyperlinks)
                for idx, kw in enumerate(v):
                    if isinstance(kw, str) and len(kw) >= 2:
                        rows.append((f"{category}.{current_id}.Keywords.{idx}", "keyword", kw))
            elif isinstance(v, (dict, list)):
                extract_from_parsed(v, category, rows, current_id, k)
    elif isinstance(obj, list):
        for item in obj:
            extract_from_parsed(item, category, rows, item_id, path)

# ── Extract scene UI text (locaId / m_text from MonoBehaviours) ───
def extract_scene_ui_text() -> list[tuple[str, str, str]]:
    import UnityPy
    rows = []
    seen_texts: set[str] = set()
    for bundle_path in SCENE_BUNDLES:
        if not os.path.exists(bundle_path):
            print(f"  Scene bundle not found: {bundle_path}")
            continue
        scene_name = os.path.basename(bundle_path).split("_")[4].replace(".unity", "")
        print(f"  Loading scene: {scene_name}...")
        env = UnityPy.load(bundle_path)
        count = 0
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                tree = obj.read_typetree()
            except:
                continue
            loca = tree.get("locaId", "")
            if loca and isinstance(loca, str) and len(loca) >= 2:
                if loca not in seen_texts:
                    rows.append((f"ui.{scene_name}.{loca}", "ui", loca))
                    seen_texts.add(loca)
                    count += 1
        print(f"    {count} locaId entries")
    return rows

# ── Extract articy dialogues ──────────────────────────────────────
def extract_articy() -> list[tuple[str, str, str]]:
    rows = []
    if not os.path.exists(ARTICY_JSON):
        print(f"  Articy JSON not found: {ARTICY_JSON}")
        return rows

    print(f"  Loading articy JSON...")
    with open(ARTICY_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  Loaded.")

    # Actors
    for actor in data.get("actors", {}).get("Array", []):
        fields = {f["title"]: f["value"] for f in actor.get("fields", {}).get("Array", [])}
        aid = str(actor.get("id", ""))
        name = fields.get("Name", "")
        desc = fields.get("Description", "")
        if name:
            rows.append((f"actor.{aid}.Name", "actor", name))
        if desc:
            rows.append((f"actor.{aid}.Description", "actor", desc))

    # Conversations + DialogueEntries
    for conv in data.get("conversations", {}).get("Array", []):
        conv_fields = {f["title"]: f["value"] for f in conv.get("fields", {}).get("Array", [])}
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

            key_base = articy_id if articy_id else f"conv.{conv_id}.e.{entry_id}"

            if en:
                rows.append((f"{key_base}.en", "dialogue", en))
            if menu_en and menu_en != en:
                rows.append((f"{key_base}.menu_en", "menu", menu_en))

    return rows

# ── Main ──────────────────────────────────────────────────────────
def main():
    all_rows: list[tuple[str, str, str]] = []
    seen_keys: set[str] = set()

    def add_rows(rows):
        for key, typ, src in rows:
            if key not in seen_keys:
                all_rows.append((key, typ, src))
                seen_keys.add(key)

    print("=== Articy dialogues ===")
    add_rows(extract_articy())

    print("=== EntityTextAssets (UnityPy) ===")
    add_rows(extract_entity_text_assets())

    print("=== Scene UI text (UnityPy) ===")
    add_rows(extract_scene_ui_text())

    # Write CSV
    print(f"\nWriting {len(all_rows)} entries to {OUTPUT} ...")
    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        f.write('"key","src","dst","mt"\r\n')
        for key, typ, src in all_rows:
            f.write(f'"{esc(key)}","{esc(src)}","",""\r\n')

    # Stats
    types: dict[str, int] = {}
    for _, typ, _ in all_rows:
        types[typ] = types.get(typ, 0) + 1
    print("Done.")
    for t, c in sorted(types.items()):
        print(f"  {t}: {c}")
    print(f"  TOTAL: {len(all_rows)}")

if __name__ == "__main__":
    main()
