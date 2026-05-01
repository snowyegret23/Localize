using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace VSRKRFont
{
	// Token: 0x02000006 RID: 6
	public static class SpriteReplacer
	{
		// Token: 0x0600000B RID: 11 RVA: 0x0000244C File Offset: 0x0000064C
		public static Sprite TryReplaceSprite(Sprite originalSprite)
		{
			if (originalSprite == null || string.IsNullOrEmpty(originalSprite.name))
			{
				return originalSprite;
			}
			string name = originalSprite.name;
			Sprite sprite;
			if (SpriteReplacer.ReplacedSpritesCache.TryGetValue(name, out sprite))
			{
				return sprite;
			}
			if (SpriteReplacer.NonExistentFilesCache.Contains(name))
			{
				return originalSprite;
			}
			string text = Path.Combine(Path.Combine(FontPlugin.PluginPath, "Sprite"), name + ".png");
			if (File.Exists(text))
			{
				try
				{
					byte[] array = File.ReadAllBytes(text);
					Texture2D texture2D = new Texture2D(2, 2, 4, false);
					ImageConversion.LoadImage(texture2D, array);
					float pixelsPerUnit = originalSprite.pixelsPerUnit;
					float width = originalSprite.rect.width;
					float num = (float)texture2D.width / width;
					float num2 = pixelsPerUnit * num;
					Sprite sprite2 = Sprite.Create(texture2D, new Rect(0f, 0f, (float)texture2D.width, (float)texture2D.height), originalSprite.pivot, num2, 0U, 0);
					sprite2.name = name;
					SpriteReplacer.ReplacedSpritesCache[name] = sprite2;
					Debug.Log("Sprite '" + name + "' was successfully replaced with high-resolution version.");
					return sprite2;
				}
				catch (Exception ex)
				{
					Debug.LogError("Failed to load or create sprite for '" + name + "': " + ex.Message);
					SpriteReplacer.NonExistentFilesCache.Add(name);
					return originalSprite;
				}
			}
			SpriteReplacer.NonExistentFilesCache.Add(name);
			return originalSprite;
		}

		// Token: 0x04000006 RID: 6
		private static readonly Dictionary<string, Sprite> ReplacedSpritesCache = new Dictionary<string, Sprite>();

		// Token: 0x04000007 RID: 7
		private static readonly HashSet<string> NonExistentFilesCache = new HashSet<string>();
	}
}
