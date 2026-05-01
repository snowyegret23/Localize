using System;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace VSRKRFont
{
	// Token: 0x02000008 RID: 8
	[HarmonyPatch(typeof(Image), "set_sprite")]
	internal class Image_SetSprite_Patch
	{
		// Token: 0x0600000F RID: 15 RVA: 0x00002082 File Offset: 0x00000282
		private static void Prefix(ref Sprite value)
		{
			if (value != null)
			{
				value = SpriteReplacer.TryReplaceSprite(value);
			}
		}
	}
}
