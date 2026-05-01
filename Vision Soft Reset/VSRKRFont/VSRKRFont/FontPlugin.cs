using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace VSRKRFont
{
	// Token: 0x02000002 RID: 2
	[BepInPlugin("com.snowyegret.vsrkrfont", "VSR Korean Font", "1.0.0")]
	public class FontPlugin : BaseUnityPlugin
	{
		// Token: 0x06000001 RID: 1 RVA: 0x00002098 File Offset: 0x00000298
		private void Awake()
		{
			FontPlugin.PluginPath = Path.GetDirectoryName(base.Info.Location);
			this.LoadGlyphs();
			new Harmony("com.snowyegret.vsrkrfont.patch").PatchAll();
			base.Logger.LogInfo("VSR Korean Font Mod loaded and patched.");
			base.Logger.LogInfo("Sprite replacer functionality is also active.");
			base.Logger.LogInfo("VSR Korean Font Mod - made by snowyegret");
		}

		// Token: 0x06000002 RID: 2 RVA: 0x00002100 File Offset: 0x00000300
		private void LoadGlyphs()
		{
			string text = Path.Combine(FontPlugin.PluginPath, "HangulGlyph");
			if (!Directory.Exists(text))
			{
				base.Logger.LogError("Glyph directory not found at: " + text);
				return;
			}
			string[] files = Directory.GetFiles(text, "*.png");
			base.Logger.LogInfo(string.Format("Found {0} glyph files. Starting parallel loading...", files.Length));
			ConcurrentBag<GlyphLoadData> loadedData = new ConcurrentBag<GlyphLoadData>();
			Parallel.ForEach<string>(files, delegate(string text2)
			{
				try
				{
					int num;
					if (int.TryParse(Path.GetFileNameWithoutExtension(text2), out num))
					{
						byte[] array = File.ReadAllBytes(text2);
						loadedData.Add(new GlyphLoadData
						{
							CharacterCode = num,
							PngData = array
						});
					}
				}
				catch (Exception ex)
				{
					this.Logger.LogError("[VSRKRFont ERROR] Failed to load glyph '" + Path.GetFileName(text2) + "': " + ex.Message);
				}
			});
			foreach (GlyphLoadData glyphLoadData in loadedData)
			{
				Texture2D texture2D = new Texture2D(2, 2, 4, false);
				texture2D.filterMode = 0;
				texture2D.wrapMode = 1;
				if (ImageConversion.LoadImage(texture2D, glyphLoadData.PngData))
				{
					Sprite sprite = Sprite.Create(texture2D, new Rect(0f, 0f, (float)texture2D.width, (float)texture2D.height), new Vector2(0.5f, 0.5f), 16f);
					sprite.name = glyphLoadData.CharacterCode.ToString();
					FontPlugin.KoreanGlyphs[glyphLoadData.CharacterCode] = sprite;
				}
			}
			base.Logger.LogInfo(string.Format("{0} Korean glyphs loaded.", FontPlugin.KoreanGlyphs.Count));
		}

		// Token: 0x04000001 RID: 1
		public static Dictionary<int, Sprite> KoreanGlyphs = new Dictionary<int, Sprite>();

		// Token: 0x04000002 RID: 2
		public static string PluginPath;
	}
}
