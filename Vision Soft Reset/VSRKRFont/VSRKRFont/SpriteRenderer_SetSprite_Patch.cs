using System;
using HarmonyLib;
using UnityEngine;

namespace VSRKRFont
{
	// Token: 0x02000007 RID: 7
	[HarmonyPatch(typeof(SpriteRenderer), "set_sprite")]
	internal class SpriteRenderer_SetSprite_Patch
	{
		// Token: 0x0600000D RID: 13 RVA: 0x00002082 File Offset: 0x00000282
		private static void Prefix(ref Sprite value)
		{
			if (value != null)
			{
				value = SpriteReplacer.TryReplaceSprite(value);
			}
		}
	}
}
