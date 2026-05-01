using System;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace VSRKRFont
{
	// Token: 0x02000005 RID: 5
	[HarmonyPatch(typeof(GlyphSprite), "set_character")]
	internal class GlyphSprite_Character_Patch
	{
		// Token: 0x06000009 RID: 9 RVA: 0x00002380 File Offset: 0x00000580
		private static bool Prefix(GlyphSprite __instance, char value)
		{
			Traverse traverse = Traverse.Create(__instance);
			Animator value2 = traverse.Field<Animator>("animator").Value;
			SpriteRenderer value3 = traverse.Field<SpriteRenderer>("spriteRenderer").Value;
			Image value4 = traverse.Field<Image>("image").Value;
			Sprite sprite;
			if (FontPlugin.KoreanGlyphs.TryGetValue((int)value, out sprite))
			{
				traverse.Field("_character").SetValue(value);
				if (value2 != null)
				{
					value2.enabled = false;
				}
				if (__instance.uiMode && value4 != null)
				{
					value4.sprite = sprite;
				}
				else if (value3 != null)
				{
					value3.sprite = sprite;
				}
				return false;
			}
			if (value2 != null && !value2.enabled)
			{
				value2.enabled = true;
			}
			return true;
		}
	}
}
