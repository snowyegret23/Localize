using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using HarmonyLib;
using Il2Cpp;
using MelonLoader;
using UnityEngine;
using UnityEngine.InputSystem;
using Il2CppInterop.Runtime;
using Il2CppPixelCrushers.DialogueSystem;
using Il2CppTMPro;

[assembly: MelonInfo(typeof(SuzerainLocMod.Mod), "SuzerainLocMod", "0.0.1", "Snowyegret")]
[assembly: MelonGame("Torpor Games", "Suzerain")]

namespace SuzerainLocMod;

public class Mod : MelonMod
{
    static readonly string BasePath = Path.Combine(
        Path.GetDirectoryName(Environment.ProcessPath)!, "Mods", "SuzerainLocData");
    static readonly string TranslationPath = Path.Combine(BasePath, "translation.csv");
    static readonly string ConfigPath = Path.Combine(BasePath, "google_sheet.json");

    // key -> translated text (for dialogue, UI, entity — all types)
    internal static readonly Dictionary<string, string> Overrides = new(StringComparer.Ordinal);

    // Entity overrides grouped: "PrefixDataJson.0xID" -> [(fieldName, dst)]
    internal static readonly Dictionary<string, List<(string field, string dst)>>
        EntityOverrides = new(StringComparer.Ordinal);

    static MelonLogger.Instance Log = null!;

    public override void OnInitializeMelon()
    {
        Log = LoggerInstance;
        Directory.CreateDirectory(BasePath);
        LoadTranslation();
        Log.Msg($"Data path: {BasePath}");
        Log.Msg($"Overrides: {Overrides.Count}, Entity groups: {EntityOverrides.Count}");
        Log.Msg("F6=Reload translation");

        PatchHarmony(typeof(Field), nameof(Field.LookupLocalizedValue),
            typeof(Hooks), "Postfix_LookupLocalizedValue", isPostfix: true);
        PatchHarmony(typeof(StaticUIText), nameof(StaticUIText.SetLocalizableTexts),
            typeof(Hooks), "Postfix_StaticUIText", isPostfix: true);
        PatchHarmony(typeof(ConversationView), nameof(ConversationView.StartSubtitle),
            typeof(Hooks), "Prefix_StartSubtitle");
        PatchHarmony(typeof(ConversationView), nameof(ConversationView.StartResponses),
            typeof(Hooks), "Prefix_StartResponses");
        PatchHarmony(typeof(EntityDataManager), "LoadDataFromTextAssets",
            typeof(Hooks), "Postfix_LoadData", isPostfix: true);
        PatchHarmony(typeof(KeywordsManager), nameof(KeywordsManager.SetupKeywords),
            typeof(Hooks), "Postfix_SetupKeywords", isPostfix: true);
        PatchHarmony(typeof(Il2Cpp.CodexEntryPage), nameof(Il2Cpp.CodexEntryPage.SetLocalizableTexts),
            typeof(Hooks), "Postfix_CodexSetLocalizableTexts", isPostfix: true);
    }

    void PatchHarmony(Type targetType, string methodName, Type patchType, string patchMethod, bool isPostfix = false)
    {
        try
        {
            var target = AccessTools.Method(targetType, methodName);
            if (target == null) { Log.Warning($"Harmony: {targetType.Name}.{methodName} not found"); return; }
            var method = AccessTools.Method(patchType, patchMethod);
            if (isPostfix)
                HarmonyInstance.Patch(target, postfix: new HarmonyMethod(method));
            else
                HarmonyInstance.Patch(target, prefix: new HarmonyMethod(method));
            Log.Msg($"Harmony: {targetType.Name}.{methodName} OK");
        }
        catch (Exception ex) { Log.Error($"Harmony {methodName}: {ex.Message}"); }
    }

    public override void OnUpdate()
    {
        var kb = Keyboard.current;
        if (kb == null) return;
        if (kb.f6Key.wasPressedThisFrame)
        {
            LoadTranslation();
            Log.Msg($"Reloaded: {Overrides.Count} overrides, {EntityOverrides.Count} entity groups");
            // Re-patch entity data objects + keywords
            try { PatchLoadedEntityData(); }
            catch (Exception ex) { Log.Error($"Re-patch: {ex.Message}"); }
            try { PatchKeywords(); }
            catch (Exception ex) { Log.Error($"Keywords: {ex.Message}"); }
            // Refresh UI text
            try { RefreshStaticUIText(); }
            catch (Exception ex) { Log.Error($"UI refresh: {ex.Message}"); }
        }
    }

    // ── Entity data patching (post-parse, object-level) ──────────────
    // After EntityDataManager.LoadDataFromTextAssets parses JSON into objects,
    // iterate all entity lists and set text properties directly by articy Id.
    internal static void PatchLoadedEntityData()
    {
        if (EntityOverrides.Count == 0) return;
        int total = 0;

        // EntityDataManager has static properties like BillsData, CodexEntriesData, etc.
        // Each returns a List<*Data> where each item has Id and *Properties sub-objects.
        foreach (var prop in typeof(EntityDataManager).GetProperties(
            BindingFlags.Public | BindingFlags.Static))
        {
            if (!prop.Name.EndsWith("Data") || prop.Name == "AllBillsData") continue;
            if (!prop.PropertyType.IsGenericType) continue;

            // Get the list property name -> derive the dump prefix
            // e.g. "CodexEntriesData" -> "CodexEntriesDataJson"
            string dumpPrefix = prop.Name + "Json";

            try
            {
                var listObj = prop.GetValue(null);
                if (listObj == null) continue;

                // Get Count and indexer via reflection
                var countProp = listObj.GetType().GetProperty("Count");
                if (countProp == null) continue;
                int count = (int)countProp.GetValue(listObj)!;
                var indexer = listObj.GetType().GetProperty("Item");
                if (indexer == null) continue;

                for (int i = 0; i < count; i++)
                {
                    var entity = indexer.GetValue(listObj, new object[] { i });
                    if (entity == null) continue;

                    // Get entity Id
                    string id = GetStringProp(entity, "Id");
                    if (string.IsNullOrEmpty(id)) continue;

                    string groupKey = $"{dumpPrefix}.{id}";
                    if (!EntityOverrides.TryGetValue(groupKey, out var fieldOverrides)) continue;

                    // Apply each field override
                    foreach (var (field, dst) in fieldOverrides)
                    {
                        // For Description fields, insert keyword links into the translated text
                        string finalVal = dst;
                        if (field is "Description" or "BillDescription" or "HubBillDescription" or "HubDescription")
                            finalVal = InsertLinksIntoText(dst);
                        if (TrySetNestedField(entity, field, finalVal))
                            total++;
                    }
                }
            }
            catch { }
        }

        if (total > 0) Log.Msg($"Entity: {total} patched");
    }

    static string GetStringProp(object obj, string name)
    {
        try
        {
            var p = obj.GetType().GetProperty(name);
            return p?.GetValue(obj) as string ?? "";
        }
        catch { return ""; }
    }

    // Try to set a string property on the entity or any of its nested *Properties objects
    static bool TrySetNestedField(object entity, string fieldName, string value)
    {
        // Try direct property first
        try
        {
            var p = entity.GetType().GetProperty(fieldName);
            if (p != null && p.PropertyType == typeof(string) && p.SetMethod != null)
            {
                p.SetValue(entity, value);
                return true;
            }
        }
        catch { }

        // Search nested *Properties objects
        foreach (var prop in entity.GetType().GetProperties())
        {
            if (!prop.Name.EndsWith("Properties")) continue;
            try
            {
                var nested = prop.GetValue(entity);
                if (nested == null) continue;
                var fp = nested.GetType().GetProperty(fieldName);
                if (fp != null && fp.PropertyType == typeof(string) && fp.SetMethod != null)
                {
                    fp.SetValue(nested, value);
                    return true;
                }
            }
            catch { }
        }
        return false;
    }

    // ── Keywords patching (codex hyperlinks) ───────────────────────────
    // After entity data is patched, add translated keywords to KeywordsManager
    // so hyperlinks work in translated text.
    // Override keys: "CodexEntriesDataJson.{Id}.Keywords.{idx}" -> translated keyword
    internal static void PatchKeywords(KeywordsManager km = null)
    {
        try
        {
            if (km == null) km = UnityEngine.Object.FindObjectOfType<KeywordsManager>();
            if (km == null) return;
            var list = km.codexEntryKeywords;
            if (list == null) return;

            // Collect keyword overrides: Id -> [(idx, dst)]
            // Key format: "CodexEntriesDataJson.{Id}.Keywords.{idx}"
            var kwOverrides = new Dictionary<string, List<(int idx, string dst)>>();
            foreach (var kv in Overrides)
            {
                if (!kv.Key.Contains(".Keywords.")) continue;
                // Parse: prefix.Id.Keywords.idx
                var parts = kv.Key.Split('.');
                if (parts.Length < 4) continue;
                string id = parts[1];
                if (int.TryParse(parts[^1], out int idx))
                {
                    if (!kwOverrides.ContainsKey(id))
                        kwOverrides[id] = new List<(int, string)>();
                    kwOverrides[id].Add((idx, kv.Value));
                }
            }
            if (kwOverrides.Count == 0) return;

            int added = 0;
            // For each existing keyword entry, if its codexEntryData.Id has an override,
            // add a new entry with the translated keyword pointing to the same data
            int originalCount = list.Count;
            for (int i = 0; i < originalCount; i++)
            {
                var entry = list[i];
                if (entry?.codexEntryData == null) continue;
                string id = entry.codexEntryData.Id;
                if (!kwOverrides.TryGetValue(id, out var overrides)) continue;

                foreach (var (idx, dst) in overrides)
                {
                    // Check if this translated keyword already exists
                    bool exists = false;
                    for (int j = 0; j < list.Count; j++)
                    {
                        if (list[j].keyword == dst) { exists = true; break; }
                    }
                    if (exists) continue;

                    // Create new keyword entry
                    var newEntry = new KeywordsManager.CodexEntryKeyword();
                    newEntry.codexEntryData = entry.codexEntryData;
                    newEntry.keyword = dst;
                    list.Add(newEntry);
                    added++;
                }
            }

            if (added > 0)
                Log.Msg($"Keywords: +{added}");
        }
        catch (Exception ex)
        {
            Log.Error($"PatchKeywords: {ex.Message}");
        }
    }

    // ── Insert keyword links into text before it's set on entity data ───
    // Builds a keyword->articyId map from Overrides (Keywords entries),
    // then scans text for keywords and wraps them with <link> tags.
    static string InsertLinksIntoText(string text)
    {
        if (string.IsNullOrEmpty(text)) return text;

        // Build keyword map from ALL codex entry keywords (English + translated)
        var kwMap = new List<(string keyword, string articyId)>();
        var seen = new HashSet<string>(StringComparer.Ordinal);

        // 1. Add translated keywords from Overrides
        foreach (var kv in Overrides)
        {
            if (!kv.Key.Contains(".Keywords.")) continue;
            var parts = kv.Key.Split('.');
            if (parts.Length >= 4 && !seen.Contains(kv.Value))
            {
                kwMap.Add((kv.Value, parts[1]));
                seen.Add(kv.Value);
            }
        }

        // 2. Add original English keywords from EntityDataManager
        try
        {
            var codexData = EntityDataManager.CodexEntriesData;
            if (codexData != null)
            {
                for (int i = 0; i < codexData.Count; i++)
                {
                    var entry = codexData[i];
                    if (entry?.CodexEntryProperties == null) continue;
                    var kws = entry.CodexEntryProperties.Keywords;
                    if (kws == null) continue;
                    for (int k = 0; k < kws.Count; k++)
                    {
                        string kw = kws[k];
                        if (!string.IsNullOrEmpty(kw) && kw.Length >= 2 && !seen.Contains(kw))
                        {
                            kwMap.Add((kw, entry.Id));
                            seen.Add(kw);
                        }
                    }
                }
            }
        }
        catch { }

        if (kwMap.Count == 0) return text;

        // Sort by keyword length descending
        kwMap.Sort((a, b) => b.keyword.Length.CompareTo(a.keyword.Length));

        foreach (var (keyword, articyId) in kwMap)
        {
            if (string.IsNullOrEmpty(keyword) || keyword.Length < 2) continue;

            int searchFrom = 0;
            while (searchFrom < text.Length)
            {
                int idx = text.IndexOf(keyword, searchFrom, StringComparison.Ordinal);
                if (idx < 0) break;

                // Not inside a tag
                string before = text[..idx];
                if (before.LastIndexOf('<') > before.LastIndexOf('>'))
                { searchFrom = idx + keyword.Length; continue; }

                // Not inside an existing <link>...</link>
                int lastLinkOpen = before.LastIndexOf("<link", StringComparison.Ordinal);
                int lastLinkClose = before.LastIndexOf("</link>", StringComparison.Ordinal);
                if (lastLinkOpen >= 0 && lastLinkOpen > lastLinkClose)
                { searchFrom = idx + keyword.Length; continue; }

                // Word boundary check (ASCII only — Korean/CJK attaches particles directly)
                if (idx > 0 && IsAsciiBoundaryChar(text[idx - 1]))
                { searchFrom = idx + keyword.Length; continue; }
                int afterIdx = idx + keyword.Length;
                if (afterIdx < text.Length && IsAsciiBoundaryChar(text[afterIdx]))
                { searchFrom = afterIdx; continue; }

                string link = $"<link=\"{articyId}\"><color=#197787>{keyword}</color></link><alpha=#FF>";
                text = text[..idx] + link + text[afterIdx..];
                break; // one per keyword
            }
        }
        return text;
    }

    // ── Insert keyword links into codex text (postfix, legacy) ────────
    // After SetLocalizableTexts sets TMPro text, scan for translated keywords
    // and wrap them with <link> tags so they become clickable codex hyperlinks.
    internal static void InsertKeywordLinks(Il2Cpp.CodexEntryPage page)
    {
        if (Overrides.Count == 0) return;
        try
        {
            var km = UnityEngine.Object.FindObjectOfType<KeywordsManager>();
            if (km?.codexEntryKeywords == null) return;

            // Find description TMPro — try path first, then fallback
            Transform descTf = page.transform.Find(
                "Container/Codex Entry Container/Viewport/Content/Codex Entry Description");
            Il2CppTMPro.TextMeshProUGUI tmp = null;
            if (descTf != null)
                tmp = descTf.GetComponent<Il2CppTMPro.TextMeshProUGUI>();

            if (tmp == null || string.IsNullOrEmpty(tmp.text)) return;

            string text = tmp.text;
            bool modified = false;

            // For each keyword, if it appears in the text and isn't already inside a <link> tag, wrap it
            var keywords = km.codexEntryKeywords;
            // Sort by keyword length descending to avoid partial matches
            var sorted = new List<(string keyword, string articyId)>();
            for (int i = 0; i < keywords.Count; i++)
            {
                var kw = keywords[i];
                if (kw?.codexEntryData == null) continue;
                sorted.Add((kw.keyword, kw.codexEntryData.Id));
            }
            sorted.Sort((a, b) => b.keyword.Length.CompareTo(a.keyword.Length));

            foreach (var (keyword, articyId) in sorted)
            {
                if (string.IsNullOrEmpty(keyword) || keyword.Length < 2) continue;

                int searchFrom = 0;
                while (searchFrom < text.Length)
                {
                    int idx = text.IndexOf(keyword, searchFrom, StringComparison.Ordinal);
                    if (idx < 0) break;

                    // Check: not inside a <link>...</link> or other tag
                    string before = text[..idx];
                    int lastOpen = before.LastIndexOf('<');
                    int lastClose = before.LastIndexOf('>');
                    if (lastOpen > lastClose)
                    {
                        // Inside a tag — skip
                        searchFrom = idx + keyword.Length;
                        continue;
                    }

                    // Check: not inside an already-linked region
                    // (between <link...> and </link>)
                    int lastLinkOpen = before.LastIndexOf("<link", StringComparison.Ordinal);
                    int lastLinkClose = before.LastIndexOf("</link>", StringComparison.Ordinal);
                    if (lastLinkOpen >= 0 && lastLinkOpen > lastLinkClose)
                    {
                        searchFrom = idx + keyword.Length;
                        continue;
                    }

                    // Word boundary check for Latin text:
                    // char before keyword must not be a letter/digit
                    if (idx > 0 && char.IsLetterOrDigit(text[idx - 1]))
                    {
                        searchFrom = idx + keyword.Length;
                        continue;
                    }
                    // char after keyword must not be a letter/digit
                    int afterIdx = idx + keyword.Length;
                    if (afterIdx < text.Length && char.IsLetterOrDigit(text[afterIdx]))
                    {
                        searchFrom = afterIdx;
                        continue;
                    }

                    // Insert link tag (only first occurrence)
                    string link = $"<link=\"{articyId}\"><color=#197787>{keyword}</color></link><alpha=#FF>";
                    text = text[..idx] + link + text[afterIdx..];
                    modified = true;
                    break; // one replacement per keyword
                }
            }

            if (modified)
                tmp.text = text;
        }
        catch (Exception ex)
        {
            Log.Error($"InsertKeywordLinks: {ex.Message}");
        }
    }

    // ── Refresh StaticUIText ─────────────────────────────────────────
    void RefreshStaticUIText()
    {
        var all = Resources.FindObjectsOfTypeAll(Il2CppType.Of<StaticUIText>());
        if (all == null) return;
        int count = 0;
        for (int i = 0; i < all.Count; i++)
        {
            try { all[i].Cast<StaticUIText>()?.SetLocalizableTexts(); count++; }
            catch { }
        }
        Log.Msg($"UI: {count} refreshed");
    }

    // ── Config ───────────────────────────────────────────────────────
    class SheetConfig
    {
        public string sid { get; set; } = "";
        public string gid { get; set; } = "0";
        public string tr_1 { get; set; } = "dst";
        public string tr_2 { get; set; } = "mt";
    }

    class MultiSheetConfig
    {
        public List<SheetConfig> sheets { get; set; } = new();
    }

    static List<SheetConfig> ReadConfigs()
    {
        if (!File.Exists(ConfigPath)) return new();
        try
        {
            string json = File.ReadAllText(ConfigPath, Encoding.UTF8);
            // Try multi-sheet format first
            var multi = JsonSerializer.Deserialize<MultiSheetConfig>(json);
            if (multi?.sheets?.Count > 0) return multi.sheets;
            // Fallback: single sheet format
            var single = JsonSerializer.Deserialize<SheetConfig>(json);
            if (single != null && !string.IsNullOrEmpty(single.sid))
                return new List<SheetConfig> { single };
        }
        catch { }
        return new();
    }

    // ── Load translation ─────────────────────────────────────────────
    void LoadTranslation()
    {
        Overrides.Clear();
        EntityOverrides.Clear();
        var configs = ReadConfigs();
        if (configs.Count > 0)
        {
            var http = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
            var allCsv = new StringBuilder();
            foreach (var cfg in configs)
            {
                if (string.IsNullOrEmpty(cfg.sid)) continue;
                try
                {
                    var url = $"https://docs.google.com/spreadsheets/d/{cfg.sid}/export?format=csv&gid={cfg.gid}";
                    Log.Msg($"Downloading gid={cfg.gid}...");
                    string csv = http.GetStringAsync(url).GetAwaiter().GetResult();
                    LoadCsvFromString(csv, cfg);
                    allCsv.AppendLine(csv);
                }
                catch (Exception ex) { Log.Warning($"Sheet gid={cfg.gid}: {ex.Message}"); }
            }
            if (Overrides.Count > 0)
            {
                File.WriteAllText(TranslationPath, allCsv.ToString(), new UTF8Encoding(false));
                Log.Msg($"Loaded {Overrides.Count} overrides from {configs.Count} sheet(s)");
                return;
            }
        }
        if (File.Exists(TranslationPath))
        {
            try
            {
                var cfg = configs.Count > 0 ? configs[0] : new SheetConfig();
                LoadCsvFromString(File.ReadAllText(TranslationPath, new UTF8Encoding(false)), cfg);
            }
            catch (Exception ex) { Log.Error($"CSV load: {ex.Message}"); }
        }
    }

    void LoadCsvFromString(string csvContent, SheetConfig cfg)
    {
        int keyCol = -1, srcCol = -1, tr1Col = -1, tr2Col = -1;
        bool headerParsed = false;

        foreach (var fields in ParseCsvRows(csvContent, skipHeader: false))
        {
            if (!headerParsed)
            {
                for (int i = 0; i < fields.Count; i++)
                {
                    string h = fields[i].Trim();
                    if (h == "key") keyCol = i;
                    else if (h == "src") srcCol = i;
                    else if (h == cfg.tr_1) tr1Col = i;
                    else if (h == cfg.tr_2) tr2Col = i;
                }
                if (keyCol < 0) keyCol = 0;
                headerParsed = true;
                continue;
            }

            string key = keyCol < fields.Count ? fields[keyCol] : "";
            if (string.IsNullOrEmpty(key)) continue;

            string val = "";
            if (tr1Col >= 0 && tr1Col < fields.Count)
                val = NormalizeLF(fields[tr1Col]);
            if (string.IsNullOrEmpty(val) && tr2Col >= 0 && tr2Col < fields.Count)
                val = NormalizeLF(fields[tr2Col]);
            if (string.IsNullOrEmpty(val)) continue;

            Overrides[key] = val;

            // Entity keys: "PrefixDataJson.0xID.FieldName" -> group by "PrefixDataJson.0xID"
            int dot1 = key.IndexOf('.');
            int lastDot = key.LastIndexOf('.');
            if (dot1 > 0 && lastDot > dot1 && key[..dot1].EndsWith("DataJson"))
            {
                string groupKey = key[..lastDot];
                string fieldName = key[(lastDot + 1)..];
                if (!EntityOverrides.ContainsKey(groupKey))
                    EntityOverrides[groupKey] = new List<(string, string)>();
                EntityOverrides[groupKey].Add((fieldName, val));
            }
        }
    }

    // Only ASCII letters/digits count as word boundary blockers
    // (Korean/CJK particles attach directly to words — no space boundary)
    static bool IsAsciiBoundaryChar(char c) => c is (>= 'A' and <= 'Z') or (>= 'a' and <= 'z') or (>= '0' and <= '9');

    static string NormalizeLF(string s) => s.Replace("\r\n", "\n").Replace("\r", "\n");

    // ── RFC 4180 CSV parser ──────────────────────────────────────────
    internal static IEnumerable<List<string>> ParseCsvRows(string csv, bool skipHeader = true)
    {
        int i = 0, len = csv.Length;
        bool isFirst = true;
        while (i < len)
        {
            var fields = new List<string>();
            while (true)
            {
                if (i >= len) { fields.Add(""); break; }
                if (csv[i] == '"')
                {
                    i++;
                    var sb = new StringBuilder();
                    while (i < len)
                    {
                        if (csv[i] == '"')
                        {
                            if (i + 1 < len && csv[i + 1] == '"') { sb.Append('"'); i += 2; }
                            else { i++; break; }
                        }
                        else { sb.Append(csv[i]); i++; }
                    }
                    fields.Add(sb.ToString());
                }
                else
                {
                    int start = i;
                    while (i < len && csv[i] != ',' && csv[i] != '\r' && csv[i] != '\n') i++;
                    fields.Add(csv[start..i]);
                }
                if (i < len && csv[i] == ',') { i++; continue; }
                break;
            }
            if (i < len && csv[i] == '\r') i++;
            if (i < len && csv[i] == '\n') i++;
            if (isFirst && skipHeader) { isFirst = false; continue; }
            isFirst = false;
            yield return fields;
        }
    }
}

// ── All Harmony hooks ────────────────────────────────────────────────
internal static class Hooks
{
    // Dialogue text (articy) — intercept before subtitle display
    public static void Prefix_StartSubtitle(Subtitle subtitle)
    {
        if (subtitle?.dialogueEntry == null || Mod.Overrides.Count == 0) return;
        try
        {
            string aid = Field.LookupValue(subtitle.dialogueEntry.fields, "Articy Id");
            if (string.IsNullOrEmpty(aid)) return;
            var ft = subtitle.formattedText;
            if (ft != null && !string.IsNullOrEmpty(ft.text))
            {
                if (Mod.Overrides.TryGetValue(aid + ".en", out string dst) && !string.IsNullOrEmpty(dst))
                    ft.text = dst;
            }
        }
        catch { }
    }

    // Response/choice menu text (articy)
    public static void Prefix_StartResponses(
        Subtitle subtitle,
        Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<Response> responses)
    {
        if (responses == null || Mod.Overrides.Count == 0) return;
        try
        {
            for (int i = 0; i < responses.Length; i++)
            {
                var r = responses[i];
                if (r?.destinationEntry == null || r.formattedText == null) continue;
                string aid = Field.LookupValue(r.destinationEntry.fields, "Articy Id");
                if (string.IsNullOrEmpty(aid)) continue;
                if (Mod.Overrides.TryGetValue(aid + ".menu_en", out string dst) && !string.IsNullOrEmpty(dst))
                    r.formattedText.text = dst;
            }
        }
        catch { }
    }

    // Localized UI text (LookupLocalizedValue — rarely used but covers some cases)
    public static void Postfix_LookupLocalizedValue(
        Il2CppSystem.Collections.Generic.List<Field> fields, string title, ref string __result)
    {
        if (string.IsNullOrEmpty(__result) || Mod.Overrides.Count == 0) return;
        string suffix = title switch
        {
            "Dialogue Text" => ".en",
            "Menu Text" => ".menu_en",
            _ => ""
        };
        if (string.IsNullOrEmpty(suffix)) return;
        string aid = "";
        for (int i = 0; i < fields.Count; i++)
            if (fields[i].title == "Articy Id") { aid = fields[i].value; break; }
        if (!string.IsNullOrEmpty(aid) &&
            Mod.Overrides.TryGetValue(aid + suffix, out string dst) && !string.IsNullOrEmpty(dst))
            __result = dst;
    }

    // Scene UI text (StaticUIText with locaId)
    public static void Postfix_StaticUIText(StaticUIText __instance)
    {
        if (Mod.Overrides.Count == 0) return;
        try
        {
            string locaId = __instance.locaId;
            if (string.IsNullOrEmpty(locaId)) return;
            var tmp = __instance.textMeshProUGUI;
            if (tmp == null) return;
            string scene = __instance.gameObject?.scene.name ?? "";
            if (Mod.Overrides.TryGetValue($"ui.{scene}.{locaId}", out string dst) && !string.IsNullOrEmpty(dst))
                tmp.text = dst;
            else if (Mod.Overrides.TryGetValue($"ui.mainmenu.{locaId}", out dst) && !string.IsNullOrEmpty(dst))
                tmp.text = dst;
            else if (Mod.Overrides.TryGetValue($"ui.sordland.{locaId}", out dst) && !string.IsNullOrEmpty(dst))
                tmp.text = dst;
        }
        catch { }
    }

    // Entity data — after JSON is parsed into objects, patch text properties directly
    public static void Postfix_LoadData(EntityTextAssets entityTextAssets)
    {
        Mod.PatchLoadedEntityData();
    }

    public static void Postfix_SetupKeywords(KeywordsManager __instance)
    {
        Mod.PatchKeywords(__instance);
    }

    // After CodexEntryPage.SetLocalizableTexts finishes, insert keyword links
    public static void Postfix_CodexSetLocalizableTexts(Il2Cpp.CodexEntryPage __instance)
    {
        Mod.InsertKeywordLinks(__instance);
    }
}
