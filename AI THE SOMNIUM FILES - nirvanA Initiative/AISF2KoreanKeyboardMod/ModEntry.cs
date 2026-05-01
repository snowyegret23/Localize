using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Reflection;
using System.Diagnostics;
using HarmonyLib;
using Il2Cpp;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Il2CppGame;
using MelonLoader;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Events;
using UnityEngine.UI;

[assembly: MelonInfo(typeof(AISF2KoreanKeyboardMod.ModEntry), "AISF2 Korean Keyboard", "1.0.3", "Snowyegret")]
[assembly: MelonGame("SpikeChunsoft", "AI_TheSomniumFiles2")]

namespace AISF2KoreanKeyboardMod;

public sealed class ModEntry : MelonMod
{
    internal const string ShiftButtonName = "key_shift_custom";
    internal const string SpriteFolderName = "AISF2KoreanKeyboardMod_Sprite";
    internal const string KeymapConfigFileName = "AISF2KoreanKeyboardMod_Keymap.json";
    internal static readonly bool EnableSoftShiftButton = true;
    internal static readonly float ShiftButtonScale = 0.9f;
    internal static readonly int ShiftPlacementRefreshFrames = 120;
    internal static readonly int RepeatInputGraceFrames = 8;
    internal static readonly int ShiftMoveDirectionGraceFrames = 12;
    internal static readonly float ShiftNavPressThreshold = 0.4f;
    internal static readonly float ShiftNavReleaseThreshold = 0.2f;
    internal static readonly Dictionary<long, Stack<ClickContext>> ClickContexts = new();
    internal static readonly Dictionary<long, KeyboardShiftState> ShiftStates = new();
    internal static readonly Dictionary<long, KeyboardInput> KnownOwners = new();
    internal static readonly HashSet<long> ShiftButtonIds = new();
    internal static readonly Dictionary<long, KeyboardInput._InputSub_d__38> InputSubCoroutines = new();
    internal static readonly HashSet<long> ShiftInjectedIntoButtonList = new();
    internal static readonly Dictionary<string, Sprite?> SpriteCache = new(StringComparer.OrdinalIgnoreCase);
    internal static readonly object DebugSync = new();
    internal static readonly HashSet<string> DebugOnceKeys = new(StringComparer.Ordinal);
    internal static readonly Dictionary<long, string> InputSubSnapshots = new();
    internal static readonly Dictionary<long, string> InputSnapshots = new();
    internal static readonly Dictionary<long, RepeatInputSample> RepeatInputSamples = new();
    internal static bool MissingSpriteDirWarned;
    internal static bool DebugEnabled = false;
    internal static int DebugBudget = 0;

    internal static readonly Dictionary<char, string> KorMap = new()
    {
        ['q'] = "ㅂ", ['w'] = "ㅈ", ['e'] = "ㄷ", ['r'] = "ㄱ", ['t'] = "ㅅ",
        ['y'] = "ㅛ", ['u'] = "ㅕ", ['i'] = "ㅑ", ['o'] = "ㅐ", ['p'] = "ㅔ",
        ['a'] = "ㅁ", ['s'] = "ㄴ", ['d'] = "ㅇ", ['f'] = "ㄹ", ['g'] = "ㅎ",
        ['h'] = "ㅗ", ['j'] = "ㅓ", ['k'] = "ㅏ", ['l'] = "ㅣ",
        ['z'] = "ㅋ", ['x'] = "ㅌ", ['c'] = "ㅊ", ['v'] = "ㅍ", ['b'] = "ㅠ",
        ['n'] = "ㅜ", ['m'] = "ㅡ"
    };

    internal static readonly Dictionary<char, char> ShiftJamoMap = new()
    {
        ['ㅂ'] = 'ㅃ',
        ['ㅈ'] = 'ㅉ',
        ['ㄷ'] = 'ㄸ',
        ['ㄱ'] = 'ㄲ',
        ['ㅅ'] = 'ㅆ',
        ['ㅐ'] = 'ㅒ',
        ['ㅔ'] = 'ㅖ'
    };
    internal static KeyboardMappingConfig KeymapConfig = new();
    /// <summary>Decoded alias map: primary answer → set of accepted alias texts.</summary>
    internal static readonly Dictionary<string, HashSet<string>> AnswerToAliases = new(StringComparer.Ordinal);
    /// <summary>Reverse alias map used when ctx.answer is blank. Ambiguous aliases are excluded.</summary>
    internal static readonly Dictionary<string, string> AliasToPrimaryAnswer = new(StringComparer.Ordinal);
    internal static readonly HashSet<string> AmbiguousAliases = new(StringComparer.Ordinal);
    internal static readonly HashSet<string> MissingLimitedProfileWarnings = new(StringComparer.Ordinal);

    public override void OnInitializeMelon()
    {
        HarmonyInstance.PatchAll(typeof(ModEntry).Assembly);
        LoadKeyboardMappingConfig();
        MelonLogger.Msg("AISF2 Korean keyboard mod initialized");
    }

    public override void OnUpdate()
    {
        if (KnownOwners.Count == 0)
        {
            return;
        }

        var refreshPlacement = Time.frameCount % 2 == 0;
        var refreshEnsure = Time.frameCount % 60 == 0;
        var dead = new List<long>();
        foreach (var pair in KnownOwners)
        {
            var owner = pair.Value;
            if (owner == null)
            {
                dead.Add(pair.Key);
                continue;
            }

            try
            {
                var state = GetShiftState(owner, createIfMissing: false);
                if (state == null)
                {
                    continue;
                }

                if (refreshPlacement && state.PlacementRefreshFramesRemaining > 0)
                {
                    RefreshShiftButtonPlacement(owner);
                    state.PlacementRefreshFramesRemaining--;
                }

                UpdateMoveDirectionCache(owner, state);
                UpdateShiftNavEdgeState(owner, state);
                TryDriveShiftNavigation(owner, state);

                if (!refreshEnsure)
                {
                    continue;
                }

                var needsShift = EnableSoftShiftButton && state.ShiftButton == null;
                if (needsShift || !state.SpritesApplied)
                {
                    EnsureShiftButton(owner);
                }
            }
            catch
            {
                dead.Add(pair.Key);
            }
        }

        foreach (var id in dead)
        {
            KnownOwners.Remove(id);
            ShiftStates.Remove(id);
            InputSubCoroutines.Remove(id);
            ShiftInjectedIntoButtonList.Remove(id);
        }
    }

    internal static long GetId(Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase obj)
    {
        return obj == null ? 0 : obj.Pointer.ToInt64();
    }

    internal static void RememberRepeatInputResult(RepeatInput? repeatInput, int value)
    {
        if (repeatInput == null)
        {
            return;
        }

        var id = GetId(repeatInput);
        if (id == 0)
        {
            return;
        }

        RepeatInputSamples[id] = new RepeatInputSample
        {
            Frame = Time.frameCount,
            Value = value
        };
    }

    internal static string GetMappedToken(KeyboardInput owner, string keyName, Transform? sourceTransform = null)
    {
        if (owner == null || owner.keymap == null)
        {
            return string.Empty;
        }

        var direct = TryLookupKey(owner, keyName);
        if (!string.IsNullOrEmpty(direct))
        {
            return direct;
        }

        if (sourceTransform == null)
        {
            return string.Empty;
        }

        var current = sourceTransform;
        for (var i = 0; i < 8 && current != null; i++)
        {
            var token = TryLookupKey(owner, current.name ?? string.Empty);
            if (!string.IsNullOrEmpty(token))
            {
                return token;
            }

            current = current.parent;
        }

        return string.Empty;
    }

    private static string NormalizeConfigToken(string? token)
    {
        return string.IsNullOrWhiteSpace(token) ? string.Empty : token.Trim().ToLowerInvariant();
    }

    [Conditional("AISF2_DEBUG")]
    internal static void DebugLog(string key, string message, bool once = true)
    {
        try
        {
            lock (DebugSync)
            {
                if (once && !string.IsNullOrEmpty(key) && !DebugOnceKeys.Add(key))
                {
                    return;
                }

                var line = $"[{DateTime.Now:HH:mm:ss.fff}] [frame={Time.frameCount}] {message}";
                File.AppendAllText(GetDebugLogPath(), line + System.Environment.NewLine, Encoding.UTF8);
            }
        }
        catch
        {
        }
    }

    [Conditional("AISF2_DEBUG")]
    internal static void DebugException(string key, string label, Exception ex, bool once = false)
    {
        var msg = $"{label}: {ex.GetType().Name}: {ex.Message}";
        if (!string.IsNullOrEmpty(ex.StackTrace))
        {
            msg += System.Environment.NewLine + ex.StackTrace;
        }

        DebugLog(key, msg, once);
    }

    [Conditional("AISF2_DEBUG")]
    private static void InitializeDebugLog()
    {
        try
        {
            lock (DebugSync)
            {
                File.WriteAllText(
                    GetDebugLogPath(),
                    $"=== AISF2KoreanKeyboardMod trace start {DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} ==={System.Environment.NewLine}",
                    Encoding.UTF8);
                DebugOnceKeys.Clear();
                InputSubSnapshots.Clear();
                InputSnapshots.Clear();
            }
        }
        catch
        {
        }
    }

    internal static string GetDebugLogPath()
    {
        return Path.Combine(Path.GetDirectoryName(typeof(ModEntry).Assembly.Location) ?? ".", "AISF2KoreanKeyboardMod_trace.log");
    }

    internal static string DescribeMethod(MethodBase? method)
    {
        if (method == null)
        {
            return "<method:null>";
        }

        var parameters = method.GetParameters();
        var sb = new StringBuilder();
        sb.Append(method.DeclaringType?.FullName ?? "<type:null>");
        sb.Append("::");
        sb.Append(method.Name);
        sb.Append('(');
        for (var i = 0; i < parameters.Length; i++)
        {
            if (i > 0)
            {
                sb.Append(", ");
            }

            sb.Append(parameters[i].ParameterType.Name);
            sb.Append(' ');
            sb.Append(parameters[i].Name);
        }

        sb.Append(") -> ");
        sb.Append(method is MethodInfo methodInfo ? methodInfo.ReturnType.Name : "Void");
        return sb.ToString();
    }

    internal static string DescribeValue(object? value, KeyboardInput? owner = null)
    {
        if (value == null)
        {
            return "<null>";
        }

        return value switch
        {
            Button button => $"Button({DescribeButton(button, owner)})",
            ButtonEx buttonEx => $"ButtonEx(id={GetId(buttonEx)} name={buttonEx.name})",
            KeyboardInput keyboardInput => $"KeyboardInput({DescribeOwner(keyboardInput)})",
            GameObject gameObject => $"GameObject({GetTransformPath(gameObject.transform)})",
            Transform transform => $"Transform({GetTransformPath(transform)})",
            string text => $"\"{text}\"",
            _ => value.ToString() ?? value.GetType().FullName ?? value.GetType().Name
        };
    }

    internal static string DescribeArgs(object[]? args, KeyboardInput? owner = null)
    {
        if (args == null || args.Length == 0)
        {
            return "[]";
        }

        var parts = new string[args.Length];
        for (var i = 0; i < args.Length; i++)
        {
            parts[i] = $"{i}={DescribeValue(args[i], owner)}";
        }

        return "[" + string.Join(", ", parts) + "]";
    }

    [Conditional("AISF2_DEBUG")]
    internal static void LogTypeMethods(Type type, string label)
    {
        try
        {
            var methods = type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            Array.Sort(methods, (left, right) => string.CompareOrdinal(left.Name, right.Name));
            var sb = new StringBuilder();
            sb.Append(label);
            sb.Append(" methods=");
            var first = true;
            foreach (var method in methods)
            {
                if (!first)
                {
                    sb.Append(" | ");
                }

                sb.Append(DescribeMethod(method));
                first = false;
            }

            DebugLog($"type.methods.{label}", sb.ToString(), once: true);
        }
        catch (Exception ex)
        {
            DebugException($"type.methods.ex.{label}", $"Failed to enumerate methods for {label}", ex, once: true);
        }
    }

    internal static string DescribeButton(Button? button, KeyboardInput? owner = null)
    {
        if (button == null)
        {
            return "<button:null>";
        }

        var path = GetTransformPath(button.transform);
        var token = owner != null ? GetMappedToken(owner, button.name ?? string.Empty, button.transform) : string.Empty;
        var buttonEx = button.GetComponent<ButtonEx>();
        var animator = button.GetComponent<Animator>();
        var image = ResolveButtonImage(button);

        return string.Join(
            " ",
            $"id={GetId(button)}",
            $"name={button.name}",
            $"path={path}",
            $"activeSelf={button.gameObject.activeSelf}",
            $"activeInHierarchy={button.gameObject.activeInHierarchy}",
            $"enabled={button.enabled}",
            $"interactable={button.interactable}",
            $"token='{token}'",
            $"customShift={IsCustomShiftButton(button)}",
            $"buttonEx={(buttonEx != null ? $"yes visible={buttonEx.visible} interactable={buttonEx.interactable}" : "no")}",
            $"animator={(animator != null ? "yes" : "no")}",
            $"image={(image != null ? GetTransformPath(image.transform) : "<null>")}");
    }

    internal static string DescribeOwner(KeyboardInput? owner)
    {
        if (owner == null)
        {
            return "<owner:null>";
        }

        var cursor = ReadMemberValue(owner, "cursor") as Button;
        var last = ReadMemberValue(owner, "last") as Button;
        var ok = ReadMemberValue(owner, "ok") as Button;
        var exit = ReadMemberValue(owner, "exit");
        var close = ReadMemberValue(owner, "close");
        var task = ReadMemberValue(owner, "task");

        return string.Join(
            " ",
            $"ownerId={GetId(owner)}",
            $"root={GetTransformPath(owner._root?.transform)}",
            $"display={GetTransformPath(owner.display?.transform)}",
            $"cursor={DescribeButton(cursor)}",
            $"last={DescribeButton(last)}",
            $"ok={DescribeButton(ok)}",
            $"exit={exit ?? "<null>"}",
            $"close={close ?? "<null>"}",
            $"task={(task != null ? task.GetType().Name : "<null>")}",
            $"active={owner.gameObject.activeInHierarchy}");
    }

    internal static string DescribeContext(KeyboardInput.__c__DisplayClass38_0? ctx, Button? focus = null)
    {
        if (ctx == null)
        {
            return "<ctx:null>";
        }

        var owner = ctx.__4__this;
        return string.Join(
            " ",
            $"input='{ctx.input ?? string.Empty}'",
            $"answer='{ctx.answer ?? string.Empty}'",
            $"length={ctx.length}",
            $"count={ctx.count}",
            $"focus={DescribeButton(focus, owner)}",
            $"okButton={DescribeButton(ctx.okButton, owner)}",
            DescribeOwner(owner));
    }

    internal static string DescribeButtons(IEnumerable<Button> buttons, KeyboardInput? owner, int limit = 64)
    {
        var sb = new StringBuilder();
        var index = 0;
        foreach (var button in buttons)
        {
            if (button == null)
            {
                continue;
            }

            if (index >= limit)
            {
                sb.Append(" ...");
                break;
            }

            if (sb.Length > 0)
            {
                sb.Append(" | ");
            }

            sb.Append(index);
            sb.Append(':');
            sb.Append(button.name);
            if (owner != null)
            {
                sb.Append('(');
                sb.Append(GetMappedToken(owner, button.name ?? string.Empty, button.transform));
                sb.Append(')');
            }
            if (IsCustomShiftButton(button))
            {
                sb.Append("[shift]");
            }

            index++;
        }

        return sb.ToString();
    }

    internal static object? ReadMemberValue(object? target, string name)
    {
        if (target == null || string.IsNullOrEmpty(name))
        {
            return null;
        }

        try
        {
            var type = target.GetType();
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field != null)
            {
                return field.GetValue(target);
            }

            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null)
            {
                return property.GetValue(target);
            }
        }
        catch
        {
        }

        return null;
    }

    internal static bool WriteMemberValue(object? target, string name, object? value)
    {
        if (target == null || string.IsNullOrEmpty(name))
        {
            return false;
        }

        try
        {
            var type = target.GetType();
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field != null)
            {
                field.SetValue(target, value);
                return true;
            }

            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null && property.CanWrite)
            {
                property.SetValue(target, value);
                return true;
            }
        }
        catch
        {
        }

        return false;
    }

    internal static string DescribeCoroutine(object? coroutine, string label)
    {
        if (coroutine == null)
        {
            return $"<{label}:null>";
        }

        var owner = ReadMemberValue(coroutine, "__4__this") as KeyboardInput;
        var state = ReadMemberValue(coroutine, "__1__state");
        var keyboard = ReadMemberValue(coroutine, "keyboard") as GameObject;
        var lastKey = ReadMemberValue(coroutine, "_lastKey_5__2");
        var buttons = ReadMemberValue(coroutine, "_buttons_5__3");
        var current = ReadMemberValue(coroutine, "__2__current");

        return string.Join(
            " ",
            $"label={label}",
            $"coroutineId={GetId(coroutine as Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase)}",
            $"state={state}",
            $"keyboard={GetTransformPath(keyboard?.transform)}",
            $"lastKey={lastKey ?? "<null>"}",
            $"buttonsNull={buttons == null}",
            $"currentNull={current == null}",
            DescribeOwner(owner));
    }

    internal static KeyboardInput? ResolveOwner(Button? button)
    {
        if (button == null)
        {
            return null;
        }

        var current = button.transform;
        for (var i = 0; i < 10 && current != null; i++)
        {
            var owner = current.GetComponent<KeyboardInput>();
            if (owner != null)
            {
                return owner;
            }

            current = current.parent;
        }

        foreach (var pair in KnownOwners)
        {
            var owner = pair.Value;
            if (owner == null)
            {
                continue;
            }

            var root = owner._root ?? owner.display ?? owner.gameObject;
            if (root != null && button.transform.IsChildOf(root.transform))
            {
                return owner;
            }
        }

        return null;
    }

    internal static bool IsBackspaceKey(string keyName, string token)
    {
        if (!string.IsNullOrEmpty(token) && token.IndexOf("back", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        return !string.IsNullOrEmpty(keyName) && keyName.IndexOf("back", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    internal static bool TryGetKoreanJamo(KeyboardInput owner, string token, bool shifted, out string jamo)
    {
        jamo = string.Empty;

        if (string.IsNullOrEmpty(token) || token.Length != 1)
        {
            return false;
        }

        var ch = token[0];
        if (ch is >= '\u3131' and <= '\u318E')
        {
            jamo = token;
            return ApplyShiftToJamo(shifted, ref jamo);
        }

        if (!TryExtractTokenLetter(token, out var tokenLetter))
        {
            return false;
        }

        var state = GetShiftState(owner, createIfMissing: false);
        if (state?.LimitedBaseMap != null && state.LimitedBaseMap.TryGetValue(tokenLetter, out var limitedMapped))
        {
            jamo = limitedMapped;
            return ApplyShiftToJamo(shifted, ref jamo);
        }

        if (!KorMap.TryGetValue(tokenLetter, out var fallbackMapped))
        {
            return false;
        }

        jamo = fallbackMapped;
        return ApplyShiftToJamo(shifted, ref jamo);
    }

    internal static bool IsSoftShiftPending(KeyboardInput owner)
    {
        var state = GetShiftState(owner, createIfMissing: false);
        return state != null && state.SoftShiftPending;
    }

    internal static bool IsPhysicalShiftHeld()
    {
        return Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift);
    }

    internal static void ToggleSoftShift(KeyboardInput owner)
    {
        var state = GetShiftState(owner, createIfMissing: true);
        if (state == null)
        {
            return;
        }

        var next = !state.SoftShiftPending;
        SetSoftShiftPending(owner, next);
        DebugLog($"shift.toggle.{Time.frameCount}", $"SoftShift toggled owner={GetId(owner)} pending={next}", once: false);
    }

    internal static bool TryToggleSoftShift(KeyboardInput owner, string reason)
    {
        var state = GetShiftState(owner, createIfMissing: true);
        if (state == null)
        {
            return false;
        }

        if (state.LastShiftToggleFrame == Time.frameCount)
        {
            DebugLog(
                $"shift.toggle.skip.{GetId(owner)}.{Time.frameCount}",
                $"Skip duplicate SoftShift toggle reason={reason} owner={GetId(owner)}",
                once: false);
            return false;
        }

        state.LastShiftToggleFrame = Time.frameCount;
        ToggleSoftShift(owner);
        return true;
    }

    internal static void SetSoftShiftPending(KeyboardInput owner, bool pending)
    {
        var state = GetShiftState(owner, createIfMissing: true);
        if (state == null)
        {
            return;
        }

        DebugLog(
            $"shift.set.{GetId(owner)}.{Time.frameCount}",
            $"SetSoftShiftPending owner={GetId(owner)} old={state.SoftShiftPending} new={pending}",
            once: false);

        state.SoftShiftPending = pending;
        UpdateShiftButtonVisual(state);
        state.SpritesApplied = false;
        ApplyKeyboardSprites(owner, state);
    }

    internal static bool TryHandleCustomShiftClick(KeyboardInput.__c__DisplayClass38_0 ctx, Button focus)
    {
        if (ctx == null || focus == null || !IsCustomShiftButton(focus))
        {
            return false;
        }

        var owner = ctx.__4__this;
        if (owner == null)
        {
            DebugLog(
                $"shift.click.noowner.{Time.frameCount}",
                $"TryHandleCustomShiftClick focus={DescribeButton(focus)} ctx={DescribeContext(ctx, focus)}",
                once: false);
            return true;
        }

        var state = GetShiftState(owner, createIfMissing: true);
        if (state != null && state.LastShiftToggleFrame == Time.frameCount)
        {
            DebugLog(
                $"shift.click.dup.{GetId(owner)}.{Time.frameCount}",
                $"TryHandleCustomShiftClick duplicate frame focus={DescribeButton(focus, owner)} ctx={DescribeContext(ctx, focus)}",
                once: false);
            return true;
        }

        DebugLog(
            $"shift.click.handle.{GetId(owner)}.{Time.frameCount}",
            $"TryHandleCustomShiftClick BEFORE focus={DescribeButton(focus, owner)} ctx={DescribeContext(ctx, focus)}",
            once: false);
        TryToggleSoftShift(owner, "custom-shift-click");
        DebugLog(
            $"shift.click.handled.{GetId(owner)}.{Time.frameCount}",
            $"TryHandleCustomShiftClick AFTER focus={DescribeButton(focus, owner)} ctx={DescribeContext(ctx, focus)}",
            once: false);
        return true;
    }

    internal static void ConsumeSoftShiftIfNeeded(KeyboardInput owner)
    {
        var state = GetShiftState(owner, createIfMissing: false);
        if (state == null || !state.SoftShiftPending)
        {
            return;
        }

        DebugLog(
            $"shift.consume.{GetId(owner)}.{Time.frameCount}",
            $"ConsumeSoftShift owner={GetId(owner)}",
            once: false);

        state.SoftShiftPending = false;
        UpdateShiftButtonVisual(state);
        state.SpritesApplied = false;
        ApplyKeyboardSprites(owner, state);
    }

    internal static void EnsureShiftButton(KeyboardInput owner)
    {
        if (owner == null)
        {
            return;
        }

        var ownerId = GetId(owner);
        if (ownerId != 0)
        {
            KnownOwners[ownerId] = owner;
        }

        DebugLog(
            $"owner.ensure.{ownerId}.{Time.frameCount}",
            $"EnsureShiftButton owner={ownerId} stateCount={ShiftStates.Count} knownOwnerCount={KnownOwners.Count}",
            once: false);

        var root = ResolveKeyboardRoot(owner);
        if (root == null)
        {
            DebugLog($"owner.root.null.{ownerId}", $"Owner={ownerId} keyboard root not found", once: true);
            return;
        }
        DebugLog($"owner.root.{ownerId}", $"Owner={ownerId} root={GetTransformPath(root.transform)}", once: true);

        var state = GetShiftState(owner, createIfMissing: true);
        if (state == null)
        {
            return;
        }

        var rootId = GetId(root);
        if (state.RootId != rootId)
        {
            DebugLog(
                $"owner.root.change.{ownerId}.{Time.frameCount}",
                $"Root changed owner={ownerId} oldRoot={state.RootId} newRoot={rootId}",
                once: false);

            if (state.ShiftButton != null)
            {
                UnityEngine.Object.Destroy(state.ShiftButton.gameObject);
            }
            state.RootId = rootId;
            state.SpritesApplied = false;
            state.ShiftButton = null;
            state.LayoutProfileInitialized = false;
            state.LayoutSignature = string.Empty;
            state.IsLimitedKeyboard = false;
            state.LimitedBaseMap = null;
            state.NavUpButton = null;
            state.NavDownButton = null;
            state.NavLeftButton = null;
            state.NavRightButton = null;
            state.PlacementRefreshFramesRemaining = ShiftPlacementRefreshFrames;
            ShiftInjectedIntoButtonList.Remove(ownerId);
        }

        if (!root.activeInHierarchy)
        {
            if (state.ShiftButton != null)
            {
                state.ShiftButton.gameObject.SetActive(false);
            }
            return;
        }

        RefreshLimitedLayoutProfile(owner, state, root);

        if (IsShiftDisabledForLayout(state.LayoutSignature))
        {
            if (state.ShiftButton == null)
            {
                state.ShiftButton = FindShiftButton(root, activeOnly: false);
            }

            if (state.ShiftButton != null)
            {
                UnityEngine.Object.Destroy(state.ShiftButton.gameObject);
                state.ShiftButton = null;
            }

            CleanupShiftButtons(root);
            if (root.transform.parent != null)
            {
                CleanupShiftButtons(root.transform.parent.gameObject);
            }

            ApplyDisabledKeys(owner, state, root);
            if (!state.SpritesApplied)
            {
                ApplyKeyboardSprites(owner, state);
            }
            return;
        }

        if (!EnableSoftShiftButton)
        {
            if (!state.SpritesApplied)
            {
                ApplyKeyboardSprites(owner, state);
            }
            return;
        }

        if (state.ShiftButton != null && !state.ShiftButton.gameObject.activeSelf)
        {
            state.ShiftButton.gameObject.SetActive(true);
        }

        if (state.ShiftButton == null)
        {
            state.ShiftButton = FindShiftButton(root, activeOnly: true);
            DebugLog(
                $"owner.shift.find.{ownerId}.{Time.frameCount}",
                $"FindShiftButton owner={ownerId} found={(state.ShiftButton != null)}",
                once: false);
        }

        if (state.ShiftButton == null)
        {
            CleanupShiftButtons(root);
            if (root.transform.parent != null)
            {
                CleanupShiftButtons(root.transform.parent.gameObject);
            }
            state.ShiftButton = CreateShiftButton(owner, root);
            state.PlacementRefreshFramesRemaining = ShiftPlacementRefreshFrames;
            DebugLog(
                $"owner.shift.create.{ownerId}.{Time.frameCount}",
                $"CreateShiftButton owner={ownerId} created={(state.ShiftButton != null)}",
                once: false);
        }

        if (state.ShiftButton != null)
        {
            BindShiftButton(state.ShiftButton, owner, state);
            RefreshShiftButtonPlacement(owner, root, state);
            SetupShiftNavigation(state.ShiftButton, owner, state, root);
        }

        ApplyDisabledKeys(owner, state, root);

        if (!state.SpritesApplied || state.AppliedShiftVisual != state.SoftShiftPending)
        {
            ApplyKeyboardSprites(owner, state);
        }
        if (state.ShiftButton != null)
        {
            UpdateShiftButtonVisual(state);
        }
    }

    internal static void ApplyRenderedInput(KeyboardInput.__c__DisplayClass38_0 ctx, string composed)
    {
        if (ctx == null)
        {
            return;
        }

        ctx.input = composed ?? string.Empty;

        var target = ctx.text;
        if (target == null)
        {
            return;
        }

        var owner = ctx.__4__this;
        var begin = owner?.textTagBegin ?? string.Empty;
        var end = owner?.textTagEnd ?? string.Empty;

        target.text = string.IsNullOrEmpty(composed) ? string.Empty : begin + composed + end;
    }

    internal static void RenderDisplayedInput(KeyboardInput.__c__DisplayClass38_0 ctx, string composed)
    {
        if (ctx == null)
        {
            return;
        }

        var target = ctx.text;
        if (target == null)
        {
            return;
        }

        var owner = ctx.__4__this;
        var begin = owner?.textTagBegin ?? string.Empty;
        var end = owner?.textTagEnd ?? string.Empty;

        target.text = string.IsNullOrEmpty(composed) ? string.Empty : begin + composed + end;
    }

    internal static void HideOrDisposeShiftButton(KeyboardInput owner, bool dispose)
    {
        if (owner == null)
        {
            return;
        }

        var ownerId = GetId(owner);
        if (ownerId == 0)
        {
            return;
        }

        DebugLog(
            $"owner.cleanup.{ownerId}.{Time.frameCount}",
            $"HideOrDispose owner={ownerId} dispose={dispose}",
            once: false);

        KnownOwners.Remove(ownerId);
        InputSubCoroutines.Remove(ownerId);
        ShiftInjectedIntoButtonList.Remove(ownerId);
        if (!ShiftStates.TryGetValue(ownerId, out var state) || state == null)
        {
            if (dispose)
            {
                ShiftStates.Remove(ownerId);
            }
            return;
        }

        state.SoftShiftPending = false;
        var shiftButton = state.ShiftButton;
        if (shiftButton != null)
        {
            var shiftId = GetId(shiftButton);
            if (shiftId != 0)
            {
                ShiftButtonIds.Remove(shiftId);
            }

            try
            {
                if (dispose)
                {
                    UnityEngine.Object.Destroy(shiftButton.gameObject);
                    state.ShiftButton = null;
                }
                else
                {
                    shiftButton.gameObject.SetActive(false);
                }
            }
            catch (Exception ex)
            {
                DebugLog(
                    $"owner.cleanup.err.{ownerId}.{Time.frameCount}",
                    $"HideOrDispose exception owner={ownerId} dispose={dispose} err={ex.Message}",
                    once: false);
            }
        }

        if (dispose)
        {
            ShiftStates.Remove(ownerId);
        }
    }

    internal static void TryInjectShiftButtonIntoButtonList(KeyboardInput owner, KeyboardShiftState state)
    {
        if (owner == null || state == null || state.ShiftButton == null)
        {
            return;
        }

        var ownerId = GetId(owner);
        if (ownerId == 0 || ShiftInjectedIntoButtonList.Contains(ownerId))
        {
            return;
        }

        if (!InputSubCoroutines.TryGetValue(ownerId, out var coroutine) || coroutine == null)
        {
            return;
        }

        try
        {
            // Wait until the coroutine has completed state 0 (button list built).
            if (coroutine.__1__state < 1)
            {
                return;
            }

            // Rebuild the button list the same way the game does: GetComponentsInChildren on
            // the keyboard root, filter out "returnbutton*", then append our shift button.
            var keyboardObj = coroutine.keyboard;
            if (keyboardObj == null)
            {
                return;
            }

            var allButtons = keyboardObj.GetComponentsInChildren<Button>(true);
            if (allButtons == null)
            {
                return;
            }

            foreach (var btn in allButtons)
            {
                if (btn == state.ShiftButton)
                {
                    ShiftInjectedIntoButtonList.Add(ownerId);
                    DebugLog(
                        $"inject.skip.present.{ownerId}.{Time.frameCount}",
                        $"Skip manual injection owner={ownerId} shift already present path={GetTransformPath(state.ShiftButton.transform)}",
                        once: false);
                    return;
                }
            }

            var newList = new Il2CppSystem.Collections.Generic.List<Button>();
            var debugButtons = new List<Button>();
            foreach (var btn in allButtons)
            {
                if (btn != null && !btn.name.StartsWith("returnbutton"))
                {
                    newList.Add(btn);
                    debugButtons.Add(btn);
                }
            }

            DebugLog(
                $"inject.pre.{ownerId}.{Time.frameCount}",
                $"InjectShift PRE owner={ownerId} shift={DescribeButton(state.ShiftButton, owner)} buttons={DescribeButtons(allButtons, owner)}",
                once: false);
            newList.Add(state.ShiftButton);
            debugButtons.Add(state.ShiftButton);

            // Assign back via Cast to satisfy the IEnumerable<Button> property type.
            coroutine._buttons_5__3 = newList.Cast<Il2CppSystem.Collections.Generic.IEnumerable<Button>>();
            ShiftInjectedIntoButtonList.Add(ownerId);

            DebugLog(
                $"inject.buttons.{ownerId}.{Time.frameCount}",
                $"Injected shift button into _buttons_5__3 owner={ownerId} totalButtons={newList.Count} final={DescribeButtons(debugButtons, owner)}",
                once: false);
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"Failed to inject shift button into button list: {ex.Message}");
            // Mark as injected to avoid spamming the error every frame.
            ShiftInjectedIntoButtonList.Add(ownerId);
        }
    }

    private static string TryLookupKey(KeyboardInput owner, string keyName)
    {
        if (string.IsNullOrEmpty(keyName))
        {
            return string.Empty;
        }

        try
        {
            return owner.keymap[keyName] ?? string.Empty;
        }
        catch
        {
            const string cloneSuffix = "(Clone)";
            if (keyName.EndsWith(cloneSuffix, StringComparison.Ordinal))
            {
                var normalized = keyName[..^cloneSuffix.Length].TrimEnd();
                try
                {
                    return owner.keymap[normalized] ?? string.Empty;
                }
                catch
                {
                }
            }

            return string.Empty;
        }
    }

    private static string GetKeymapConfigPath()
    {
        return Path.Combine(Path.GetDirectoryName(typeof(ModEntry).Assembly.Location) ?? ".", KeymapConfigFileName);
    }

    private static bool TryParseSingleJamo(string? value, out char jamo)
    {
        jamo = '\0';
        if (string.IsNullOrEmpty(value) || value.Length != 1)
        {
            return false;
        }

        jamo = value[0];
        return true;
    }

    private static string GetCurrentSceneName()
    {
        try
        {
            return SceneManager.GetActiveScene().name ?? string.Empty;
        }
        catch
        {
            return string.Empty;
        }
    }

    private static string BuildLayoutSignature(List<char> letters)
    {
        var arr = letters.ToArray();
        Array.Sort(arr);
        return new string(arr);
    }

    private static string BuildLayoutSignature(IEnumerable<char> letters)
    {
        var list = new List<char>();
        foreach (var letter in letters)
        {
            list.Add(letter);
        }

        return BuildLayoutSignature(list);
    }

    private static string ResolveNonAlphabetLayoutSignature(GameObject root)
    {
        if (root == null)
        {
            return string.Empty;
        }

        foreach (Transform child in root.transform)
        {
            if (child == null || !child.gameObject.activeSelf)
            {
                continue;
            }

            if (string.Equals(child.name, "keyboard_number", StringComparison.OrdinalIgnoreCase))
            {
                return "1234567890";
            }
        }

        return string.Empty;
    }

    private static List<char> CollectAvailableLetters(KeyboardInput owner, GameObject root)
    {
        var result = new List<char>(26);
        var seen = new HashSet<char>();
        var buttons = root.GetComponentsInChildren<Button>(true);
        foreach (var button in buttons)
        {
            if (button == null || !button.gameObject.activeInHierarchy)
            {
                continue;
            }

            var token = GetMappedToken(owner, button.name ?? string.Empty, button.transform);
            if (!TryExtractTokenLetter(token, out var letter))
            {
                continue;
            }

            if (seen.Add(letter))
            {
                result.Add(letter);
            }
        }

        return result;
    }

    private static Dictionary<char, string>? ParseLimitedLayoutMap(Dictionary<string, string>? rawMap)
    {
        if (rawMap == null)
        {
            return null;
        }

        var map = new Dictionary<char, string>();
        foreach (var pair in rawMap)
        {
            if (!TryExtractTokenLetter(pair.Key ?? string.Empty, out var letter))
            {
                continue;
            }

            if (!TryParseSingleJamo(pair.Value, out var jamo))
            {
                continue;
            }

            map[letter] = jamo.ToString();
        }

        return map.Count == 0 ? null : map;
    }

    private static string ResolveCanonicalLayoutSignature(string signature)
    {
        if (string.IsNullOrEmpty(signature))
        {
            return signature;
        }

        if (KeymapConfig.LimitedLayoutsBySignature != null && KeymapConfig.LimitedLayoutsBySignature.ContainsKey(signature))
        {
            return signature;
        }

        if (KeymapConfig.ShiftNavigationProfiles != null && KeymapConfig.ShiftNavigationProfiles.ContainsKey(signature))
        {
            return signature;
        }

        var disabledMap = KeymapConfig.DisabledKeysBySignature;
        if (disabledMap == null)
        {
            return signature;
        }

        foreach (var pair in disabledMap)
        {
            var profileSignature = pair.Key ?? string.Empty;
            if (string.IsNullOrEmpty(profileSignature))
            {
                continue;
            }

            var visibleLetters = new List<char>(profileSignature.Length);
            foreach (var ch in profileSignature)
            {
                if (ch is < 'a' or > 'z')
                {
                    continue;
                }

                visibleLetters.Add(ch);
            }

            if (pair.Value != null)
            {
                foreach (var disabledToken in pair.Value)
                {
                    if (!TryExtractTokenLetter(disabledToken ?? string.Empty, out var disabledLetter))
                    {
                        continue;
                    }

                    visibleLetters.Remove(disabledLetter);
                }
            }

            if (BuildLayoutSignature(visibleLetters) == signature)
            {
                return profileSignature;
            }
        }

        return signature;
    }

    private static void RefreshLimitedLayoutProfile(KeyboardInput owner, KeyboardShiftState state, GameObject root)
    {
        var letters = CollectAvailableLetters(owner, root);
        var signature = ResolveCanonicalLayoutSignature(BuildLayoutSignature(letters));
        if (string.IsNullOrEmpty(signature))
        {
            signature = ResolveNonAlphabetLayoutSignature(root);
        }
        if (state.LayoutProfileInitialized && state.LayoutSignature == signature)
        {
            return;
        }

        state.LayoutProfileInitialized = true;
        state.LayoutSignature = signature;
        state.IsLimitedKeyboard = letters.Count > 0 && letters.Count < KorMap.Count;
        state.LimitedBaseMap = null;

        if (!state.IsLimitedKeyboard)
        {
            return;
        }

        Dictionary<string, string>? rawMap = null;
        var sceneName = GetCurrentSceneName();
        if (!string.IsNullOrEmpty(sceneName) &&
            KeymapConfig.LimitedLayoutsByScene != null &&
            KeymapConfig.LimitedLayoutsByScene.TryGetValue(sceneName, out var sceneMap))
        {
            rawMap = sceneMap;
        }
        else if (KeymapConfig.LimitedLayoutsBySignature != null &&
                 KeymapConfig.LimitedLayoutsBySignature.TryGetValue(signature, out var sigMap))
        {
            rawMap = sigMap;
        }

        state.LimitedBaseMap = ParseLimitedLayoutMap(rawMap);
        if (state.LimitedBaseMap != null)
        {
            return;
        }

        var warnKey = $"{sceneName}|{signature}";
        if (MissingLimitedProfileWarnings.Add(warnKey))
        {
            MelonLogger.Warning(
                $"Limited keyboard profile missing: scene='{sceneName}', signature='{signature}', letters='{string.Join("", letters)}'. Edit {KeymapConfigFileName}.");
        }
    }

    private static bool IsShiftDisabledForLayout(string signature)
    {
        if (string.IsNullOrEmpty(signature) || KeymapConfig.DisabledKeysBySignature == null)
        {
            return false;
        }

        if (!KeymapConfig.DisabledKeysBySignature.TryGetValue(signature, out var disabledTokens) ||
            disabledTokens == null || disabledTokens.Count == 0)
        {
            return false;
        }

        foreach (var token in disabledTokens)
        {
            if (string.Equals(NormalizeConfigToken(token), "shift", StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    private static void WriteDefaultKeyboardMappingConfig(string path)
    {
        try
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir))
            {
                Directory.CreateDirectory(dir);
            }

            var model = new KeyboardMappingConfig
            {
                LimitedLayoutsBySignature = new Dictionary<string, Dictionary<string, string>>
                {
                    ["acefilmnorsty"] = new Dictionary<string, string>
                    {
                        ["a"] = "ㅍ",
                        ["c"] = "ㅡ",
                        ["e"] = "ㄹ",
                        ["f"] = "ㅣ",
                        ["i"] = "ㅁ",
                        ["l"] = "ㅔ",
                        ["m"] = "ㅇ",
                        ["n"] = "ㅅ",
                        ["o"] = "ㅗ",
                        ["r"] = "ㄴ",
                        ["s"] = "ㅂ",
                        ["t"] = "ㅊ",
                        ["y"] = "ㅋ"
                    },
                    ["abcfghioprtu"] = new Dictionary<string, string>
                    {
                        ["a"] = "ㅊ",
                        ["b"] = "ㅜ",
                        ["c"] = "ㅍ",
                        ["f"] = "ㅏ",
                        ["g"] = "ㅋ",
                        ["h"] = "ㅂ",
                        ["i"] = "ㅡ",
                        ["o"] = "ㄹ",
                        ["p"] = "ㅅ",
                        ["r"] = "ㅓ",
                        ["t"] = "ㅁ",
                        ["u"] = "ㄴ"
                    },
                    ["abefiklmnorsu"] = new Dictionary<string, string>
                    {
                        ["a"] = "ㅅ",
                        ["b"] = "ㅓ",
                        ["e"] = "ㅂ",
                        ["f"] = "ㅡ",
                        ["i"] = "ㄹ",
                        ["k"] = "ㅣ",
                        ["l"] = "ㅁ",
                        ["m"] = "ㄴ",
                        ["n"] = "ㅊ",
                        ["o"] = "ㅍ",
                        ["r"] = "ㅋ",
                        ["s"] = "ㅇ",
                        ["u"] = "ㅏ"
                    }
                },
                LimitedLayoutsByScene = new Dictionary<string, Dictionary<string, string>>(),
                AnswerAliases = new Dictionary<string, List<string>>()
            };

            var json = JsonSerializer.Serialize(model, new JsonSerializerOptions
            {
                WriteIndented = true
            });
            File.WriteAllText(path, json, Encoding.UTF8);
            MelonLogger.Msg($"Created default keymap config: {path}");
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"Failed to create keymap config: {ex.Message}");
        }
    }

    private static void LoadKeyboardMappingConfig()
    {
        var path = GetKeymapConfigPath();
        if (!File.Exists(path))
        {
            WriteDefaultKeyboardMappingConfig(path);
            KeymapConfig = new KeyboardMappingConfig();
            return;
        }

        try
        {
            var json = File.ReadAllText(path);
            KeymapConfig = JsonSerializer.Deserialize<KeyboardMappingConfig>(json, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                ReadCommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true
            }) ?? new KeyboardMappingConfig();

            MelonLogger.Msg($"Loaded keymap config: {path}");
        }
        catch (Exception ex)
        {
            KeymapConfig = new KeyboardMappingConfig();
            MelonLogger.Warning($"Failed to load keymap config '{path}': {ex.Message}");
        }

        BuildAnswerAliases();
    }

    private static void BuildAnswerAliases()
    {
        AnswerToAliases.Clear();
        AliasToPrimaryAnswer.Clear();
        AmbiguousAliases.Clear();
        var section = KeymapConfig.AnswerAliases;
        if (section == null || section.Count == 0)
        {
            return;
        }

        foreach (var pair in section)
        {
            var primary = pair.Key ?? string.Empty;
            if (string.IsNullOrWhiteSpace(primary))
            {
                MelonLogger.Warning("AnswerAliases: empty primary answer key");
                continue;
            }

            var aliases = new HashSet<string>(StringComparer.Ordinal);
            foreach (var aliasText in pair.Value == null ? Array.Empty<string>() : (IEnumerable<string>)pair.Value)
            {
                if (string.IsNullOrEmpty(aliasText))
                {
                    continue;
                }

                if (!string.Equals(aliasText, primary, StringComparison.Ordinal))
                {
                    aliases.Add(aliasText);
                }
            }

            if (aliases.Count > 0)
            {
                AnswerToAliases[primary] = aliases;
                foreach (var alias in aliases)
                {
                    RegisterAliasReverseLookup(alias, primary);
                }
            }
        }
    }

    private static void RegisterAliasReverseLookup(string alias, string primary)
    {
        if (string.IsNullOrEmpty(alias) || string.IsNullOrEmpty(primary))
        {
            return;
        }

        if (AmbiguousAliases.Contains(alias))
        {
            return;
        }

        if (AliasToPrimaryAnswer.TryGetValue(alias, out var existing))
        {
            if (!string.Equals(existing, primary, StringComparison.Ordinal))
            {
                AliasToPrimaryAnswer.Remove(alias);
                AmbiguousAliases.Add(alias);
                MelonLogger.Warning($"AnswerAliases: alias '{alias}' is ambiguous between '{existing}' and '{primary}'");
            }
            return;
        }

        AliasToPrimaryAnswer[alias] = primary;
    }

    private static bool TryResolveAliasAnswer(KeyboardInput.__c__DisplayClass38_0 ctx, string? input, out string answer)
    {
        answer = ctx.answer ?? string.Empty;
        if (!string.IsNullOrEmpty(answer))
        {
            return true;
        }

        var reflectedCtxAnswer = ReadMemberValue(ctx, "answer") as string;
        if (!string.IsNullOrEmpty(reflectedCtxAnswer))
        {
            answer = reflectedCtxAnswer;
            return true;
        }

        var owner = ctx.__4__this;
        var ownerId = GetId(owner);
        if (ownerId != 0 && InputSubCoroutines.TryGetValue(ownerId, out var coroutine) && coroutine != null)
        {
            answer = coroutine.answer ?? string.Empty;
            if (!string.IsNullOrEmpty(answer))
            {
                return true;
            }

            var reflectedCoroutineAnswer = ReadMemberValue(coroutine, "answer") as string;
            if (!string.IsNullOrEmpty(reflectedCoroutineAnswer))
            {
                answer = reflectedCoroutineAnswer;
                return true;
            }

            var locals = ReadMemberValue(coroutine, "__8__1");
            var localAnswer = ReadMemberValue(locals, "answer") as string;
            if (!string.IsNullOrEmpty(localAnswer))
            {
                answer = localAnswer;
                return true;
            }
        }

        if (!string.IsNullOrEmpty(input) && AliasToPrimaryAnswer.TryGetValue(input, out var aliasedPrimary))
        {
            answer = aliasedPrimary;
            return true;
        }

        answer = string.Empty;
        return false;
    }

    private static void BackfillAliasAnswer(KeyboardInput.__c__DisplayClass38_0 ctx, string answer)
    {
        if (string.IsNullOrEmpty(answer))
        {
            return;
        }

        if (string.IsNullOrEmpty(ctx.answer))
        {
            ctx.answer = answer;
            WriteMemberValue(ctx, "answer", answer);
        }

        var ownerId = GetId(ctx.__4__this);
        if (ownerId == 0 || !InputSubCoroutines.TryGetValue(ownerId, out var coroutine) || coroutine == null)
        {
            return;
        }

        if (string.IsNullOrEmpty(coroutine.answer))
        {
            coroutine.answer = answer;
            WriteMemberValue(coroutine, "answer", answer);
        }

        var locals = ReadMemberValue(coroutine, "__8__1");
        WriteMemberValue(locals, "answer", answer);
    }

    /// <summary>
    /// If the player typed an alias for the current question's answer,
    /// silently replace ctx.input with the primary answer.
    /// Called just before the game's OK-button handler runs.
    /// </summary>
    internal static bool TrySubstituteAlias(KeyboardInput.__c__DisplayClass38_0 ctx)
    {
        if (ctx == null)
        {
            return false;
        }

        var input = ctx.input;
        TryResolveAliasAnswer(ctx, input, out var answer);
        if (!string.IsNullOrEmpty(answer))
        {
            BackfillAliasAnswer(ctx, answer);
        }

        if (string.IsNullOrEmpty(answer) || string.IsNullOrEmpty(input))
        {
            return false;
        }

        if (string.Equals(input, answer, StringComparison.Ordinal))
        {
            return false;
        }

        AnswerToAliases.TryGetValue(answer, out var answerAliases);
        if (answerAliases == null)
        {
            return false;
        }

        if (!answerAliases.Contains(input))
        {
            return false;
        }

        ctx.input = answer;
        return true;
    }

    private static KeyboardShiftState? GetShiftState(KeyboardInput owner, bool createIfMissing)
    {
        var id = GetId(owner);
        if (id == 0)
        {
            return null;
        }

        if (ShiftStates.TryGetValue(id, out var existing))
        {
            return existing;
        }

        if (!createIfMissing)
        {
            return null;
        }

        var created = new KeyboardShiftState();
        ShiftStates[id] = created;
        return created;
    }

    private static bool TryExtractTokenLetter(string token, out char letter)
    {
        letter = '\0';
        if (string.IsNullOrEmpty(token) || token.Length != 1)
        {
            return false;
        }

        var c = char.ToLowerInvariant(token[0]);
        if (c is < 'a' or > 'z')
        {
            return false;
        }

        letter = c;
        return true;
    }

    private static bool ApplyShiftToJamo(bool shifted, ref string jamo)
    {
        if (!shifted)
        {
            return true;
        }

        if (string.IsNullOrEmpty(jamo) || jamo.Length != 1)
        {
            return true;
        }

        if (ShiftJamoMap.TryGetValue(jamo[0], out var mapped))
        {
            jamo = mapped.ToString();
        }

        return true;
    }

    private static Button? FindShiftButton(GameObject root, bool activeOnly)
    {
        Button? fallback = null;
        var buttons = root.GetComponentsInChildren<Button>(true);
        foreach (var button in buttons)
        {
            if (button == null)
            {
                continue;
            }

            if (IsShiftButton(button) || string.Equals(button.name, ShiftButtonName, StringComparison.Ordinal))
            {
                if (!activeOnly || button.gameObject.activeInHierarchy)
                {
                    return button;
                }

                fallback ??= button;
            }
        }

        return activeOnly ? null : fallback;
    }

    private static void CleanupShiftButtons(GameObject root)
    {
        var buttons = root.GetComponentsInChildren<Button>(true);
        foreach (var button in buttons)
        {
            if (button == null)
            {
                continue;
            }

            if (!string.Equals(button.name, ShiftButtonName, StringComparison.Ordinal))
            {
                continue;
            }

            UnityEngine.Object.Destroy(button.gameObject);
        }
    }

    private static Button? FindShiftRuntimeTemplateButton(GameObject root)
    {
        if (root == null)
        {
            return null;
        }

        var buttons = root.GetComponentsInChildren<Button>(true);
        foreach (var button in buttons)
        {
            if (button == null || !button.gameObject.activeInHierarchy || IsCustomShiftButton(button))
            {
                continue;
            }

            if (button.GetComponent<Animator>() == null)
            {
                continue;
            }

            if (button.GetComponent<ButtonEx>() == null)
            {
                continue;
            }

            return button;
        }

        return null;
    }

    private static void ConfigureShiftRuntimeComponents(GameObject shiftObject, Button shiftButton, Image? image, Button? templateButton)
    {
        if (shiftObject == null || shiftButton == null)
        {
            return;
        }

        var shiftRect = shiftObject.GetComponent<RectTransform>();
        var templateButtonEx = templateButton != null ? templateButton.GetComponent<ButtonEx>() : null;

        if (templateButton != null)
        {
            shiftObject.layer = templateButton.gameObject.layer;

            shiftButton.transition = templateButton.transition;
            shiftButton.colors = templateButton.colors;
            shiftButton.spriteState = templateButton.spriteState;
            shiftButton.animationTriggers = templateButton.animationTriggers;

            var templateImage = templateButton.GetComponent<Image>();
            if (image != null && templateImage != null)
            {
                image.type = templateImage.type;
                image.material = templateImage.material;
                image.raycastTarget = templateImage.raycastTarget;
                image.preserveAspect = templateImage.preserveAspect;
                image.color = templateImage.color;
            }

            var templateAnimator = templateButton.GetComponent<Animator>();
            if (templateAnimator != null && shiftObject.GetComponent<Animator>() == null)
            {
                var shiftAnimator = shiftObject.AddComponent<Animator>();
                if (shiftAnimator != null)
                {
                    shiftAnimator.runtimeAnimatorController = templateAnimator.runtimeAnimatorController;
                    shiftAnimator.updateMode = templateAnimator.updateMode;
                    shiftAnimator.cullingMode = templateAnimator.cullingMode;
                    shiftAnimator.applyRootMotion = templateAnimator.applyRootMotion;
                    shiftAnimator.speed = templateAnimator.speed;
                }
            }

        }

        try
        {
            var buttonEx = shiftObject.GetComponent<ButtonEx>() ?? shiftObject.AddComponent<ButtonEx>();
            if (buttonEx != null)
            {
                if (templateButtonEx != null)
                {
                    WriteMemberValue(buttonEx, "pushAction", ReadMemberValue(templateButtonEx, "pushAction"));
                    buttonEx.buttonType = templateButtonEx.buttonType;
                    buttonEx.longPress = templateButtonEx.longPress;
                    buttonEx.pushOnlyEnter = templateButtonEx.pushOnlyEnter;
                    buttonEx.AnimationEnable = templateButtonEx.AnimationEnable;
                    buttonEx.isInteractable = templateButtonEx.isInteractable;
                    buttonEx.disableInputAction = templateButtonEx.disableInputAction;
                }
                else
                {
                    buttonEx.buttonType = ButtonEx.ButtonType.Normal;
                    buttonEx.AnimationEnable = true;
                }

                if (image != null)
                {
                    buttonEx.buttonIcon = image;
                    buttonEx.buttonIconParent = image.transform;
                }

                buttonEx.parent = shiftRect;
                buttonEx.visible = true;
                buttonEx.interactable = true;
                DebugLog(
                    $"shift.buttonex.beforeinit.{GetId(buttonEx)}.{Time.frameCount}",
                    $"ConfigureShiftRuntimeComponents BEFORE Initialize {DescribeButtonExState(buttonEx)} template={(templateButtonEx != null ? DescribeButtonExState(templateButtonEx) : "<buttonEx:null>")}",
                    once: false);
                buttonEx.Initialize();
                buttonEx.OnDeviceChanged();
                buttonEx.SetInteractable(true, true);
                DebugLog(
                    $"shift.buttonex.afterinit.{GetId(buttonEx)}.{Time.frameCount}",
                    $"ConfigureShiftRuntimeComponents AFTER Initialize {DescribeButtonExState(buttonEx)}",
                    once: false);
                shiftButton.enabled = true;
            }
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"Failed to configure ButtonEx on shift button: {ex.Message}");
        }
    }

    private static GameObject? CreateShiftFocusOverlay(GameObject shiftObject, Button? templateButton)
    {
        if (shiftObject == null)
        {
            return null;
        }

        var templateHit = templateButton != null ? templateButton.transform.Find("hit") : null;
        var templateHitImage = templateHit != null ? templateHit.GetComponent<Image>() : null;

        var overlayObject = new GameObject("hit");
        var overlayRect = overlayObject.AddComponent<RectTransform>();
        var overlayImage = overlayObject.AddComponent<Image>();
        if (overlayRect == null || overlayImage == null)
        {
            return null;
        }

        overlayRect.SetParent(shiftObject.transform, worldPositionStays: false);
        overlayRect.anchorMin = Vector2.zero;
        overlayRect.anchorMax = Vector2.one;
        overlayRect.offsetMin = Vector2.zero;
        overlayRect.offsetMax = Vector2.zero;
        overlayRect.localScale = Vector3.one;
        overlayRect.localRotation = Quaternion.identity;
        overlayRect.localPosition = Vector3.zero;
        overlayRect.SetAsLastSibling();

        if (templateHitImage != null)
        {
            overlayImage.sprite = templateHitImage.sprite;
            overlayImage.overrideSprite = templateHitImage.overrideSprite;
            overlayImage.type = templateHitImage.type;
            overlayImage.material = templateHitImage.material;
            overlayImage.color = templateHitImage.color;
            overlayImage.preserveAspect = templateHitImage.preserveAspect;
            overlayImage.raycastTarget = templateHitImage.raycastTarget;
            overlayImage.pixelsPerUnitMultiplier = templateHitImage.pixelsPerUnitMultiplier;
        }
        else
        {
            overlayImage.color = new Color(0f, 1f, 1f, 1f);
            overlayImage.raycastTarget = false;
        }

        overlayObject.SetActive(false);
        return overlayObject;
    }

    private static Button? CreateShiftButton(KeyboardInput owner, GameObject root)
    {
        if (!TryResolveShiftPlacementTargets(owner, root, out var horizontalAnchorRect, out var verticalAnchorRect, out var size))
        {
            return null;
        }

        var templateButton = FindShiftRuntimeTemplateButton(root);
        var shiftObject = new GameObject(ShiftButtonName);
        if (shiftObject == null)
        {
            return null;
        }

        var shiftRect = shiftObject.AddComponent<RectTransform>();
        var image = shiftObject.AddComponent<Image>();
        var shiftButton = shiftObject.AddComponent<Button>();
        if (shiftButton == null)
        {
            return null;
        }

        var shiftId = GetId(shiftButton);
        if (shiftId != 0)
        {
            ShiftButtonIds.Add(shiftId);
        }

        if (shiftRect != null)
        {
            ApplyShiftRectPlacement(shiftRect, root, horizontalAnchorRect, verticalAnchorRect, size);
        }

        var shiftSprite = LoadSpriteFile("key_shift.png");
        if (image != null && shiftSprite != null)
        {
            image.overrideSprite = shiftSprite;
            image.sprite = shiftSprite;
        }

        shiftButton.targetGraphic = image;
        shiftButton.interactable = true;
        var navigation = shiftButton.navigation;
        navigation.mode = Navigation.Mode.Explicit;
        shiftButton.navigation = navigation;
        CreateShiftFocusOverlay(shiftObject, templateButton);
        ConfigureShiftRuntimeComponents(shiftObject, shiftButton, image, templateButton);

        DebugLog(
            $"owner.shift.created.{GetId(owner)}.{Time.frameCount}",
            $"CreateShiftButton done owner={GetId(owner)} shiftPath={GetTransformPath(shiftButton.transform)} parent={GetTransformPath(shiftButton.transform.parent)}",
            once: false);

        return shiftButton;
    }

    private static bool TryResolveShiftPlacementTargets(
        KeyboardInput owner,
        GameObject root,
        out RectTransform horizontalAnchorRect,
        out RectTransform verticalAnchorRect,
        out Vector2 size)
    {
        horizontalAnchorRect = null!;
        verticalAnchorRect = null!;
        size = Vector2.zero;

        if (owner == null || root == null)
        {
            return false;
        }

        var buttons = root.GetComponentsInChildren<Button>(true);
        if (buttons == null || buttons.Length == 0)
        {
            return false;
        }

        Button? rightmost = null;
        Button? bottommost = null;
        var rightmostX = 0f;
        var rightmostY = 0f;
        var bottommostX = 0f;
        var bottommostY = 0f;

        foreach (var button in buttons)
        {
            if (button == null || !button.gameObject.activeInHierarchy || IsCustomShiftButton(button))
            {
                continue;
            }

            if (!IsUnderKeyContainer(button.transform))
            {
                continue;
            }

            var token = GetMappedToken(owner, button.name ?? string.Empty, button.transform);

            if (!IsTypableKey(button.name ?? string.Empty, token))
            {
                continue;
            }

            if (string.Equals(token, " ", StringComparison.Ordinal))
            {
                continue;
            }

            var rect = button.GetComponent<RectTransform>();
            if (rect == null)
            {
                continue;
            }

            var pos = rect.position;
            if (rightmost == null ||
                pos.x > rightmostX + 1f ||
                (Mathf.Abs(pos.x - rightmostX) <= 8f && pos.y < rightmostY))
            {
                rightmost = button;
                rightmostX = pos.x;
                rightmostY = pos.y;
            }

            if (bottommost == null ||
                pos.y < bottommostY - 1f ||
                (Mathf.Abs(pos.y - bottommostY) <= 8f && pos.x > bottommostX))
            {
                bottommost = button;
                bottommostX = pos.x;
                bottommostY = pos.y;
            }
        }

        if (rightmost == null || bottommost == null)
        {
            return false;
        }

        horizontalAnchorRect = rightmost.GetComponent<RectTransform>();
        verticalAnchorRect = bottommost.GetComponent<RectTransform>();
        if (horizontalAnchorRect == null || verticalAnchorRect == null)
        {
            return false;
        }

        var width = horizontalAnchorRect.rect.width > 1f ? horizontalAnchorRect.rect.width : horizontalAnchorRect.sizeDelta.x;
        var height = verticalAnchorRect.rect.height > 1f ? verticalAnchorRect.rect.height : verticalAnchorRect.sizeDelta.y;
        size = new Vector2(width, height) * ShiftButtonScale;
        return size.x > 0f && size.y > 0f;
    }

    private static void ApplyShiftRectPlacement(
        RectTransform shiftRect,
        GameObject root,
        RectTransform horizontalAnchorRect,
        RectTransform verticalAnchorRect,
        Vector2 size)
    {
        if (shiftRect == null || root == null || horizontalAnchorRect == null || verticalAnchorRect == null)
        {
            return;
        }

        var targetParent = ResolveShiftTargetParent(root, horizontalAnchorRect, verticalAnchorRect);
        if (shiftRect.parent != targetParent)
        {
            shiftRect.SetParent(targetParent, worldPositionStays: false);
        }

        shiftRect.anchorMin = new Vector2(0.5f, 0.5f);
        shiftRect.anchorMax = new Vector2(0.5f, 0.5f);
        shiftRect.pivot = new Vector2(0.5f, 0.5f);
        shiftRect.sizeDelta = size;

        // Combine the last non-space key column's X with the bottom non-space key row's Y so the shift key
        // lands in the open slot regardless of keyboard layout or resolution.
        var rightmostLocal = targetParent.InverseTransformPoint(horizontalAnchorRect.position);
        var bottommostLocal = targetParent.InverseTransformPoint(verticalAnchorRect.position);
        shiftRect.localPosition = new Vector3(rightmostLocal.x, bottommostLocal.y, rightmostLocal.z);
        shiftRect.rotation = horizontalAnchorRect.rotation;
    }

    private static Transform ResolveShiftTargetParent(GameObject root, RectTransform horizontalAnchorRect, RectTransform verticalAnchorRect)
    {
        return root.transform;
    }

    internal static void RefreshShiftButtonPlacement(KeyboardInput owner)
    {
        if (owner == null)
        {
            return;
        }

        var root = ResolveKeyboardRoot(owner);
        if (root == null)
        {
            return;
        }

        var state = GetShiftState(owner, createIfMissing: false);
        if (state == null)
        {
            return;
        }

        RefreshShiftButtonPlacement(owner, root, state);
    }

    internal static void ScheduleShiftPlacementRefresh(KeyboardInput owner)
    {
        var state = GetShiftState(owner, createIfMissing: true);
        if (state == null)
        {
            return;
        }

        state.PlacementRefreshFramesRemaining = ShiftPlacementRefreshFrames;
    }

    private static void RefreshShiftButtonPlacement(KeyboardInput owner, GameObject root, KeyboardShiftState state)
    {
        if (owner == null || root == null || state == null)
        {
            return;
        }

        var shiftButton = state.ShiftButton;
        if (shiftButton == null || shiftButton.gameObject == null || !shiftButton.gameObject.activeInHierarchy)
        {
            return;
        }

        var shiftRect = shiftButton.GetComponent<RectTransform>();
        if (shiftRect == null)
        {
            return;
        }

        if (!TryResolveShiftPlacementTargets(owner, root, out var horizontalAnchorRect, out var verticalAnchorRect, out var size))
        {
            return;
        }

        ApplyShiftRectPlacement(shiftRect, root, horizontalAnchorRect, verticalAnchorRect, size);
    }

    private static Button? FindPreferredEnterButton(Button[] buttons, List<Button> explicitCandidates)
    {
        static bool IsPreferred(Button button)
        {
            if (button == null || !button.gameObject.activeInHierarchy)
            {
                return false;
            }

            if (IsCustomShiftButton(button))
            {
                return false;
            }

            if (!IsUnderKeyContainer(button.transform))
            {
                return false;
            }

            return (button.name ?? string.Empty).IndexOf("key0ok", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        foreach (var candidate in explicitCandidates)
        {
            if (IsPreferred(candidate))
            {
                return candidate;
            }
        }

        foreach (var button in buttons)
        {
            if (IsPreferred(button))
            {
                return button;
            }
        }

        return null;
    }

    private static Button? ChooseEnterButton(
        KeyboardInput owner,
        Button[] buttons,
        List<Button> explicitCandidates,
        float anchorX,
        float anchorY,
        float anchorArea)
    {
        static float RectWidth(RectTransform rect)
        {
            return rect.rect.width > 1f ? rect.rect.width : rect.sizeDelta.x;
        }

        static float RectHeight(RectTransform rect)
        {
            return rect.rect.height > 1f ? rect.rect.height : rect.sizeDelta.y;
        }

        Button? best = null;
        var bestScore = float.MinValue;

        void Consider(Button button, bool explicitMatch)
        {
            if (button == null || !button.gameObject.activeInHierarchy)
            {
                return;
            }

            if (IsCustomShiftButton(button))
            {
                return;
            }

            var token = GetMappedToken(owner, button.name ?? string.Empty, button.transform);
            if (IsBackspaceKey(button.name ?? string.Empty, token))
            {
                return;
            }
            var isTypable = IsTypableKey(button.name ?? string.Empty, token);
            var underKeyContainer = IsUnderKeyContainer(button.transform);
            var underReturnButtonContainer = HasAncestorNameContains(button.transform, "returnbutton");

            var rect = button.GetComponent<RectTransform>();
            if (rect == null)
            {
                return;
            }

            var pos = rect.position;
            var dx = pos.x - anchorX;
            if (dx < -40f)
            {
                return;
            }

            var dy = Mathf.Abs(pos.y - anchorY);
            var area = Mathf.Max(1f, RectWidth(rect) * RectHeight(rect));
            if (!explicitMatch)
            {
                var likelyEnterBySize = area >= anchorArea * 1.2f;
                var likelyEnterByName = LooksLikeEnterButtonCandidate(button, token);
                var likelyControlKey = !isTypable && underKeyContainer;
                if (!likelyEnterBySize && !likelyEnterByName && !likelyControlKey)
                {
                    return;
                }
            }

            var score = area * 0.01f + dx * 0.2f - dy;
            if (!isTypable && underKeyContainer)
            {
                score += 200f;
            }

            if (underReturnButtonContainer && !underKeyContainer)
            {
                score -= 800f;
            }

            if (explicitMatch)
            {
                score += 1000f;
            }

            if (score > bestScore)
            {
                bestScore = score;
                best = button;
            }
        }

        foreach (var candidate in explicitCandidates)
        {
            if (candidate == null)
            {
                continue;
            }

            if (!IsUnderKeyContainer(candidate.transform))
            {
                continue;
            }

            Consider(candidate, explicitMatch: true);
        }

        if (best != null)
        {
            return best;
        }

        foreach (var candidate in explicitCandidates)
        {
            if (candidate == null)
            {
                continue;
            }

            Consider(candidate, explicitMatch: true);
        }

        if (best != null)
        {
            return best;
        }

        foreach (var button in buttons)
        {
            Consider(button, explicitMatch: false);
        }

        return best;
    }

    private static bool LooksLikeEnterName(string name)
    {
        if (string.IsNullOrEmpty(name))
        {
            return false;
        }

        var lower = name.ToLowerInvariant();
        return lower.Contains("enter") ||
               lower.Contains("return") ||
               lower.Contains("input") ||
               lower.Contains("confirm") ||
               lower.Contains("submit") ||
               lower.Contains("decide") ||
               lower.Contains("ok");
    }

    private static bool LooksLikeEnterButtonCandidate(Button button, string token)
    {
        if (button == null)
        {
            return false;
        }

        if (LooksLikeEnterName(button.name ?? string.Empty))
        {
            return true;
        }

        if (!string.IsNullOrEmpty(token) && token.IndexOf("eturn", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        var current = button.transform.parent;
        for (var i = 0; i < 8 && current != null; i++)
        {
            if (LooksLikeEnterName(current.name ?? string.Empty))
            {
                return true;
            }

            current = current.parent;
        }

        return false;
    }

    private static bool IsUnderKeyContainer(Transform? transform)
    {
        if (transform == null)
        {
            return false;
        }

        var current = transform.parent;
        for (var i = 0; i < 8 && current != null; i++)
        {
            if (string.Equals(current.name, "key", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            current = current.parent;
        }

        return false;
    }

    private static bool HasAncestorNameContains(Transform? transform, string needle)
    {
        if (transform == null || string.IsNullOrEmpty(needle))
        {
            return false;
        }

        var current = transform;
        for (var i = 0; i < 10 && current != null; i++)
        {
            var name = current.name ?? string.Empty;
            if (name.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return true;
            }

            current = current.parent;
        }

        return false;
    }

    private static RectTransform? ResolveEnterRectForShiftPlacement(Button enterButton)
    {
        if (enterButton == null)
        {
            return null;
        }

        static float RectArea(RectTransform rect)
        {
            var w = rect.rect.width > 1f ? rect.rect.width : rect.sizeDelta.x;
            var h = rect.rect.height > 1f ? rect.rect.height : rect.sizeDelta.y;
            return Mathf.Max(1f, w * h);
        }

        RectTransform? fallback = enterButton.GetComponent<RectTransform>();
        RectTransform? best = fallback;
        var bestArea = fallback != null ? RectArea(fallback) : 0f;

        var current = enterButton.transform;
        for (var i = 0; i < 10 && current != null; i++)
        {
            var rect = current.GetComponent<RectTransform>();
            if (rect != null)
            {
                if (LooksLikeEnterName(current.name ?? string.Empty))
                {
                    return rect;
                }

                var area = RectArea(rect);
                if (area > bestArea * 1.25f)
                {
                    best = rect;
                    bestArea = area;
                }
            }

            current = current.parent;
        }

        return best;
    }

    private static bool IsShiftButton(Button? button)
    {
        if (button == null)
        {
            return false;
        }

        var id = GetId(button);
        return id != 0 && ShiftButtonIds.Contains(id);
    }

    internal static bool IsCustomShiftButton(Button? button)
    {
        return IsShiftButton(button) || string.Equals(button?.name, ShiftButtonName, StringComparison.Ordinal);
    }

    internal static bool ButtonsMatch(Button? a, Button? b)
    {
        var aId = GetId(a);
        var bId = GetId(b);
        return aId != 0 && aId == bId;
    }

    internal static bool IsCustomShiftButtonEx(ButtonEx? buttonEx)
    {
        if (buttonEx == null)
        {
            return false;
        }

        Button? button = null;
        try
        {
            button = buttonEx.GetComponent<Button>();
        }
        catch
        {
        }

        return IsCustomShiftButton(button);
    }

    internal static string DescribeButtonExState(ButtonEx? buttonEx)
    {
        if (buttonEx == null)
        {
            return "<buttonEx:null>";
        }

        Button? button = null;
        try
        {
            button = buttonEx.GetComponent<Button>();
        }
        catch
        {
        }

        var owner = ResolveOwner(button);
        return string.Join(
            " ",
            new[]
            {
                $"buttonExId={GetId(buttonEx)}",
                $"button={DescribeButton(button, owner)}",
                $"buttonType={buttonEx.buttonType}",
                $"disableInputAction={buttonEx.disableInputAction}",
                $"pushOnlyEnter={buttonEx.pushOnlyEnter}",
                $"animEnable={buttonEx.AnimationEnable}",
                $"longPress={buttonEx.longPress}",
                $"iconParent={GetTransformPath(buttonEx.buttonIconParent)}",
                $"parent={GetTransformPath(buttonEx.parent)}",
                $"icon={GetTransformPath(buttonEx.buttonIcon?.transform)}",
                $"spriteSwitcher={GetTransformPath(buttonEx.spriteSwitcher?.transform)}"
            });
    }

    private static bool IsShiftImage(Image? image)
    {
        if (image == null)
        {
            return false;
        }

        var current = image.transform;
        for (var i = 0; i < 6 && current != null; i++)
        {
            var button = current.GetComponent<Button>();
            if (button != null)
            {
                return IsShiftButton(button) || string.Equals(button.name, ShiftButtonName, StringComparison.Ordinal);
            }

            current = current.parent;
        }

        return false;
    }

    private static void BindShiftButton(Button shiftButton, KeyboardInput owner, KeyboardShiftState state)
    {
        EnsureShiftKeymapEntry(owner, shiftButton);
        shiftButton.onClick.RemoveAllListeners();
        shiftButton.onClick.AddListener((UnityAction)(() => TryToggleSoftShift(owner, "unity-button-onClick")));
        var buttonEx = shiftButton.GetComponent<ButtonEx>();
        if (buttonEx != null)
        {
            buttonEx.onPointerClick = (Il2CppSystem.Action)(System.Action)(() => TryToggleSoftShift(owner, "buttonEx-onPointerClick"));
        }
        DebugLog(
            $"owner.shift.bind.{GetId(owner)}.{Time.frameCount}",
            $"BindShiftButton owner={GetId(owner)} shiftPath={GetTransformPath(shiftButton.transform)} shiftId={GetId(shiftButton)}",
            once: false);

        if (!state.BaseColorInitialized)
        {
            var image = shiftButton.GetComponent<Image>();
            state.BaseColor = image != null ? image.color : Color.white;
            state.BaseColorInitialized = true;
        }

        state.ShiftFocusOverlay = shiftButton.transform.Find("hit")?.gameObject;
        if (state.ShiftFocusOverlay != null)
        {
            state.ShiftFocusOverlay.SetActive(false);
        }
    }

    private static void EnsureShiftKeymapEntry(KeyboardInput owner, Button shiftButton)
    {
        if (owner == null || shiftButton == null || owner.keymap == null || string.IsNullOrEmpty(shiftButton.name))
        {
            return;
        }

        const string shiftToken = "Shift";
        try
        {
            var existing = TryLookupKey(owner, shiftButton.name);
            if (string.Equals(existing, shiftToken, StringComparison.Ordinal))
            {
                return;
            }

            owner.keymap[shiftButton.name] = shiftToken;
            DebugLog(
                $"owner.shift.keymap.{GetId(owner)}.{Time.frameCount}",
                $"EnsureShiftKeymapEntry owner={GetId(owner)} key={shiftButton.name} token='{shiftToken}' previous='{existing}'",
                once: false);
        }
        catch (Exception ex)
        {
            DebugException(
                $"owner.shift.keymap.ex.{GetId(owner)}.{Time.frameCount}",
                $"EnsureShiftKeymapEntry owner={GetId(owner)} key={shiftButton.name}",
                ex,
                once: false);
        }
    }

    private static void SetupShiftNavigation(Button shiftButton, KeyboardInput owner, KeyboardShiftState state, GameObject root)
    {
        if (shiftButton == null || owner == null || root == null)
        {
            return;
        }

        state.NavUpButton = null;
        state.NavDownButton = null;
        state.NavLeftButton = null;
        state.NavRightButton = null;

        var profile = ResolveShiftNavigationProfile(state.LayoutSignature, state.IsLimitedKeyboard);
        if (profile == null)
        {
            return;
        }

        var buttons = root.GetComponentsInChildren<Button>(true);
        var nav = shiftButton.navigation;
        nav.mode = Navigation.Mode.Explicit;

        var upBtn = ResolveNavTarget(profile.Up, owner, state, buttons);
        var downBtn = ResolveNavTarget(profile.Down, owner, state, buttons);
        var leftBtn = ResolveNavTarget(profile.Left, owner, state, buttons);
        var rightBtn = ResolveNavTarget(profile.Right, owner, state, buttons);

        state.NavUpButton = upBtn;
        state.NavDownButton = downBtn;
        state.NavLeftButton = leftBtn;
        state.NavRightButton = rightBtn;

        nav.selectOnUp = upBtn;
        nav.selectOnDown = downBtn;
        nav.selectOnLeft = leftBtn;
        nav.selectOnRight = rightBtn;
        shiftButton.navigation = nav;

        if (upBtn != null) PatchNeighborNavigation(upBtn, shiftButton, NavDirection.Down);
        if (downBtn != null) PatchNeighborNavigation(downBtn, shiftButton, NavDirection.Up);
        if (leftBtn != null) PatchNeighborNavigation(leftBtn, shiftButton, NavDirection.Right);
        if (rightBtn != null) PatchNeighborNavigation(rightBtn, shiftButton, NavDirection.Left);

        DebugLog(
            $"shift.nav.{GetId(owner)}.{Time.frameCount}",
            $"SetupShiftNavigation sig={state.LayoutSignature} up={upBtn?.name} down={downBtn?.name} left={leftBtn?.name} right={rightBtn?.name}",
            once: false);
    }

    internal enum NavDirection { Up, Down, Left, Right }

    internal static bool TryRedirectShiftFocus(KeyboardInput.__c__DisplayClass38_0? ctx, ref Button focus)
    {
        var owner = ctx?.__4__this;
        if (owner == null)
        {
            return false;
        }

        var state = GetShiftState(owner, createIfMissing: false);
        if (state?.ShiftButton == null || !state.ShiftButton.gameObject.activeInHierarchy)
        {
            return false;
        }

        var current = owner.cursor ?? owner.last;
        if (current == null)
        {
            return false;
        }

        if (!ButtonsMatch(current, state.ShiftButton) &&
            TrySuppressShiftExitFollowThrough(owner, state, current, ref focus))
        {
            return true;
        }

        if (!ButtonsMatch(current, state.ShiftButton) &&
            TryRedirectShiftEntryByFocusPattern(ctx, state, current, ref focus))
        {
            return true;
        }

        if (!TryGetLastMoveDirection(owner, state, out var direction))
        {
            return false;
        }

        if (ButtonsMatch(current, state.ShiftButton))
        {
            var leaveTarget = GetShiftNeighbor(state, direction);
            if (leaveTarget == null || ButtonsMatch(focus, leaveTarget))
            {
                return false;
            }

            DebugLog(
                $"shift.focus.redirect.leave.{GetId(owner)}.{Time.frameCount}",
                $"Redirect focus from shift dir={direction} original={DescribeButton(focus, owner)} target={DescribeButton(leaveTarget, owner)}",
                once: false);
            focus = leaveTarget;
            return true;
        }

        if (!ButtonsMatch(current, GetShiftEntryNeighbor(state, direction)))
        {
            return false;
        }

        if (ButtonsMatch(focus, state.ShiftButton))
        {
            return false;
        }

        DebugLog(
            $"shift.focus.redirect.enter.{GetId(owner)}.{Time.frameCount}",
            $"Redirect focus into shift dir={direction} current={DescribeButton(current, owner)} original={DescribeButton(focus, owner)} shift={DescribeButton(state.ShiftButton, owner)}",
            once: false);
        ArmShiftNavHold(owner, state, direction, "enter");
        focus = state.ShiftButton;
        return true;
    }

    private static void TryDriveShiftNavigation(KeyboardInput owner, KeyboardShiftState state)
    {
        if (owner == null || state?.ShiftButton == null)
        {
            return;
        }

        if (state.LastShiftNavFrame == Time.frameCount)
        {
            return;
        }

        if (!ButtonsMatch(owner.cursor, state.ShiftButton))
        {
            return;
        }

        if (!TryConsumeShiftNavEdge(owner, state, out var direction))
        {
            return;
        }

        var target = GetShiftNeighbor(state, direction);
        if (target == null)
        {
            return;
        }

        var ownerId = GetId(owner);
        if (ownerId == 0 || !InputSubCoroutines.TryGetValue(ownerId, out var coroutine) || coroutine == null)
        {
            return;
        }

        var ctx = coroutine.__8__1;
        if (ctx == null)
        {
            return;
        }

        DebugLog(
            $"shift.focus.drive.{ownerId}.{Time.frameCount}",
            $"Drive focus from shift dir={direction} target={DescribeButton(target, owner)}",
            once: false);

        state.LastShiftNavFrame = Time.frameCount;
        ArmShiftExitSuppression(owner, state, direction, "drive", target);
        ctx.Method_Internal_Void_Button_Boolean_0(target, false);
    }

    private static bool TrySuppressShiftExitFollowThrough(
        KeyboardInput owner,
        KeyboardShiftState state,
        Button current,
        ref Button focus)
    {
        if (owner == null || state == null || current == null || focus == null)
        {
            return false;
        }

        if (!state.SuppressShiftExitUntilNeutral || state.SuppressShiftExitTarget == null)
        {
            return false;
        }

        if (!ButtonsMatch(current, state.SuppressShiftExitTarget) || ButtonsMatch(focus, current))
        {
            return false;
        }

        if (TryGetCurrentRawMoveDirection(owner, out var rawDirection) &&
            rawDirection != state.SuppressShiftExitDirection)
        {
            ClearShiftExitSuppression(owner, state, "direction-changed");
            return false;
        }

        DebugLog(
            $"shift.focus.suppress.follow.{GetId(owner)}.{Time.frameCount}",
            $"Suppress shift follow-through dir={state.SuppressShiftExitDirection} current={DescribeButton(current, owner)} original={DescribeButton(focus, owner)}",
            once: false);
        focus = current;
        return true;
    }

    private static Button? GetShiftEntryNeighbor(KeyboardShiftState state, NavDirection direction)
    {
        return direction switch
        {
            NavDirection.Up => state.NavDownButton,
            NavDirection.Down => state.NavUpButton,
            NavDirection.Left => state.NavRightButton,
            NavDirection.Right => state.NavLeftButton,
            _ => null
        };
    }

    private static Button? GetShiftNeighbor(KeyboardShiftState state, NavDirection direction)
    {
        return direction switch
        {
            NavDirection.Up => state.NavUpButton,
            NavDirection.Down => state.NavDownButton,
            NavDirection.Left => state.NavLeftButton,
            NavDirection.Right => state.NavRightButton,
            _ => null
        };
    }

    private static bool TryRedirectShiftEntryByFocusPattern(
        KeyboardInput.__c__DisplayClass38_0? ctx,
        KeyboardShiftState state,
        Button current,
        ref Button focus)
    {
        if (state.ShiftButton == null)
        {
            return false;
        }

        if (ButtonsMatch(current, state.NavLeftButton) && IsLikelyRightEdgeTarget(ctx, state, focus))
        {
            DebugLog(
                $"shift.focus.redirect.pattern.left.{GetId(ctx?.__4__this)}.{Time.frameCount}",
                $"Redirect focus into shift via focus-pattern side=left current={DescribeButton(current, ctx?.__4__this)} original={DescribeButton(focus, ctx?.__4__this)} shift={DescribeButton(state.ShiftButton, ctx?.__4__this)}",
                once: false);
            ArmShiftNavHold(ctx?.__4__this, state, NavDirection.Right, "pattern-left");
            focus = state.ShiftButton;
            return true;
        }

        if (ButtonsMatch(current, state.NavRightButton) && IsLikelyLeftEdgeTarget(state, focus))
        {
            DebugLog(
                $"shift.focus.redirect.pattern.right.{GetId(ctx?.__4__this)}.{Time.frameCount}",
                $"Redirect focus into shift via focus-pattern side=right current={DescribeButton(current, ctx?.__4__this)} original={DescribeButton(focus, ctx?.__4__this)} shift={DescribeButton(state.ShiftButton, ctx?.__4__this)}",
                once: false);
            ArmShiftNavHold(ctx?.__4__this, state, NavDirection.Left, "pattern-right");
            focus = state.ShiftButton;
            return true;
        }

        if (ButtonsMatch(current, state.NavUpButton) && ButtonsMatch(focus, state.NavDownButton))
        {
            DebugLog(
                $"shift.focus.redirect.pattern.up.{GetId(ctx?.__4__this)}.{Time.frameCount}",
                $"Redirect focus into shift via focus-pattern side=up current={DescribeButton(current, ctx?.__4__this)} original={DescribeButton(focus, ctx?.__4__this)} shift={DescribeButton(state.ShiftButton, ctx?.__4__this)}",
                once: false);
            ArmShiftNavHold(ctx?.__4__this, state, NavDirection.Down, "pattern-up");
            focus = state.ShiftButton;
            return true;
        }

        if (ButtonsMatch(current, state.NavDownButton) && ButtonsMatch(focus, state.NavUpButton))
        {
            DebugLog(
                $"shift.focus.redirect.pattern.down.{GetId(ctx?.__4__this)}.{Time.frameCount}",
                $"Redirect focus into shift via focus-pattern side=down current={DescribeButton(current, ctx?.__4__this)} original={DescribeButton(focus, ctx?.__4__this)} shift={DescribeButton(state.ShiftButton, ctx?.__4__this)}",
                once: false);
            ArmShiftNavHold(ctx?.__4__this, state, NavDirection.Up, "pattern-down");
            focus = state.ShiftButton;
            return true;
        }

        return false;
    }

    private static void ArmShiftNavHold(KeyboardInput? owner, KeyboardShiftState state, NavDirection direction, string reason)
    {
        if (state == null)
        {
            return;
        }

        state.HasPendingShiftNavEdge = false;
        state.ShiftNavPendingDirection = direction;
        state.ShiftNavHoldLeft = direction == NavDirection.Left;
        state.ShiftNavHoldRight = direction == NavDirection.Right;
        state.ShiftNavHoldUp = direction == NavDirection.Up;
        state.ShiftNavHoldDown = direction == NavDirection.Down;
        DebugLog(
            $"shift.focus.edge.arm.{GetId(owner)}.{Time.frameCount}",
            $"Arm shift nav hold reason={reason} dir={direction}",
            once: false);
    }

    private static void UpdateShiftNavEdgeState(KeyboardInput owner, KeyboardShiftState state)
    {
        if (owner == null || state == null)
        {
            return;
        }

        var horizontal = GetRepeatInputRawValue(owner.horiontal);
        var vertical = GetRepeatInputRawValue(owner.vertical);

        if (state.SuppressShiftExitUntilNeutral)
        {
            if (Mathf.Abs(horizontal) < ShiftNavReleaseThreshold && Mathf.Abs(vertical) < ShiftNavReleaseThreshold)
            {
                state.SuppressShiftExitNeutralFrames++;
                if (state.SuppressShiftExitNeutralFrames >= 2)
                {
                    ClearShiftExitSuppression(owner, state, "neutral");
                }
            }
            else
            {
                state.SuppressShiftExitNeutralFrames = 0;
                if (TryGetCurrentRawMoveDirection(owner, out var rawDirection) &&
                    rawDirection != state.SuppressShiftExitDirection)
                {
                    ClearShiftExitSuppression(owner, state, "changed-direction");
                }
            }
        }

        if (horizontal <= ShiftNavReleaseThreshold)
        {
            state.ShiftNavHoldRight = false;
        }

        if (horizontal >= -ShiftNavReleaseThreshold)
        {
            state.ShiftNavHoldLeft = false;
        }

        if (vertical <= ShiftNavReleaseThreshold)
        {
            state.ShiftNavHoldUp = false;
        }

        if (vertical >= -ShiftNavReleaseThreshold)
        {
            state.ShiftNavHoldDown = false;
        }

        if (horizontal >= ShiftNavPressThreshold && !state.ShiftNavHoldRight)
        {
            state.ShiftNavHoldRight = true;
            QueueShiftNavEdge(owner, state, NavDirection.Right, "raw-right");
            return;
        }

        if (horizontal <= -ShiftNavPressThreshold && !state.ShiftNavHoldLeft)
        {
            state.ShiftNavHoldLeft = true;
            QueueShiftNavEdge(owner, state, NavDirection.Left, "raw-left");
            return;
        }

        if (vertical >= ShiftNavPressThreshold && !state.ShiftNavHoldUp)
        {
            state.ShiftNavHoldUp = true;
            QueueShiftNavEdge(owner, state, NavDirection.Up, "raw-up");
            return;
        }

        if (vertical <= -ShiftNavPressThreshold && !state.ShiftNavHoldDown)
        {
            state.ShiftNavHoldDown = true;
            QueueShiftNavEdge(owner, state, NavDirection.Down, "raw-down");
        }
    }

    private static void QueueShiftNavEdge(KeyboardInput owner, KeyboardShiftState state, NavDirection direction, string reason)
    {
        if (state == null)
        {
            return;
        }

        state.HasPendingShiftNavEdge = true;
        state.ShiftNavPendingDirection = direction;
        DebugLog(
            $"shift.focus.edge.queue.{GetId(owner)}.{Time.frameCount}",
            $"Queue shift nav edge reason={reason} dir={direction} cursor={DescribeButton(owner.cursor, owner)}",
            once: false);
    }

    private static bool TryConsumeShiftNavEdge(KeyboardInput owner, KeyboardShiftState state, out NavDirection direction)
    {
        direction = default;
        if (state == null || !state.HasPendingShiftNavEdge)
        {
            return false;
        }

        state.HasPendingShiftNavEdge = false;
        direction = state.ShiftNavPendingDirection;
        DebugLog(
            $"shift.focus.edge.consume.{GetId(owner)}.{Time.frameCount}",
            $"Consume shift nav edge dir={direction}",
            once: false);
        return true;
    }

    private static void ArmShiftExitSuppression(KeyboardInput? owner, KeyboardShiftState state, NavDirection direction, string reason, Button? target)
    {
        if (state == null)
        {
            return;
        }

        state.SuppressShiftExitDirection = direction;
        state.SuppressShiftExitUntilNeutral = true;
        state.SuppressShiftExitNeutralFrames = 0;
        state.SuppressShiftExitTarget = target;
        DebugLog(
            $"shift.focus.suppress.arm.{GetId(owner)}.{Time.frameCount}",
            $"Arm shift exit suppression reason={reason} dir={direction} target={DescribeButton(target, owner)}",
            once: false);
    }

    private static void ClearShiftExitSuppression(KeyboardInput? owner, KeyboardShiftState state, string reason)
    {
        if (state == null || !state.SuppressShiftExitUntilNeutral)
        {
            return;
        }

        state.SuppressShiftExitUntilNeutral = false;
        state.SuppressShiftExitNeutralFrames = 0;
        state.SuppressShiftExitTarget = null;
        DebugLog(
            $"shift.focus.suppress.clear.{GetId(owner)}.{Time.frameCount}",
            $"Clear shift exit suppression reason={reason}",
            once: false);
    }

    private static bool IsLikelyRightEdgeTarget(KeyboardInput.__c__DisplayClass38_0? ctx, KeyboardShiftState state, Button? focus)
    {
        if (focus == null || ButtonsMatch(focus, state.ShiftButton))
        {
            return false;
        }

        return ButtonsMatch(focus, state.NavRightButton) ||
               ButtonsMatch(focus, ctx?.okButton) ||
               IsEnterLikeButton(focus);
    }

    private static bool IsLikelyLeftEdgeTarget(KeyboardShiftState state, Button? focus)
    {
        if (focus == null || ButtonsMatch(focus, state.ShiftButton))
        {
            return false;
        }

        return ButtonsMatch(focus, state.NavLeftButton) || IsSpaceLikeButton(focus);
    }

    private static bool IsEnterLikeButton(Button? button)
    {
        if (button == null)
        {
            return false;
        }

        var name = button.name ?? string.Empty;
        return name.IndexOf("ok", StringComparison.OrdinalIgnoreCase) >= 0 ||
               name.IndexOf("enter", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private static bool IsSpaceLikeButton(Button? button)
    {
        if (button == null)
        {
            return false;
        }

        return (button.name ?? string.Empty).IndexOf("space", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private static void UpdateMoveDirectionCache(KeyboardInput owner, KeyboardShiftState state)
    {
        if (owner == null || state == null)
        {
            return;
        }

        if (!TryGetCurrentMoveDirection(owner, out var direction))
        {
            return;
        }

        state.LastMoveDirection = direction;
        state.LastMoveDirectionFrame = Time.frameCount;
    }

    private static bool TryGetLastMoveDirection(KeyboardInput owner, KeyboardShiftState? state, out NavDirection direction)
    {
        if (TryGetCurrentMoveDirection(owner, out direction))
        {
            if (state != null)
            {
                state.LastMoveDirection = direction;
                state.LastMoveDirectionFrame = Time.frameCount;
            }
            return true;
        }

        if (state != null && state.LastMoveDirectionFrame >= Time.frameCount - ShiftMoveDirectionGraceFrames)
        {
            direction = state.LastMoveDirection;
            return true;
        }

        direction = default;
        return false;
    }

    private static bool TryGetCurrentMoveDirection(KeyboardInput owner, out NavDirection direction)
    {
        direction = default;
        var horizontal = GetRepeatInputValue(owner.horiontal);
        if (horizontal != 0)
        {
            direction = horizontal > 0 ? NavDirection.Right : NavDirection.Left;
            return true;
        }

        var vertical = GetRepeatInputValue(owner.vertical);
        if (vertical != 0)
        {
            direction = vertical > 0 ? NavDirection.Up : NavDirection.Down;
            return true;
        }

        return false;
    }

    private static bool TryGetCurrentRawMoveDirection(KeyboardInput owner, out NavDirection direction)
    {
        direction = default;

        var horizontal = GetRepeatInputRawValue(owner.horiontal);
        if (Mathf.Abs(horizontal) >= 0.4f)
        {
            direction = horizontal > 0f ? NavDirection.Right : NavDirection.Left;
            return true;
        }

        var vertical = GetRepeatInputRawValue(owner.vertical);
        if (Mathf.Abs(vertical) >= 0.4f)
        {
            direction = vertical > 0f ? NavDirection.Up : NavDirection.Down;
            return true;
        }

        return false;
    }

    private static int GetRepeatInputValue(RepeatInput? repeatInput)
    {
        if (repeatInput == null)
        {
            return 0;
        }

        var id = GetId(repeatInput);
        if (id != 0 && RepeatInputSamples.TryGetValue(id, out var sample) && sample.Frame >= Time.frameCount - RepeatInputGraceFrames)
        {
            return sample.Value;
        }

        var enabledObj = ReadMemberValue(repeatInput, "enable");
        if (enabledObj is bool enabled && !enabled)
        {
            return 0;
        }

        var valObj = ReadMemberValue(repeatInput, "val");
        var value = valObj switch
        {
            float f => f,
            double d => (float)d,
            _ => 0f
        };

        if (Mathf.Abs(value) < 0.25f)
        {
            return 0;
        }

        return value > 0f ? 1 : -1;
    }

    private static float GetRepeatInputRawValue(RepeatInput? repeatInput)
    {
        if (repeatInput?.axis == null)
        {
            return 0f;
        }

        try
        {
            var action = repeatInput.axis.action;
            if (action == null)
            {
                return 0f;
            }

            return action.ReadValue<float>();
        }
        catch
        {
            return 0f;
        }
    }

    private static void PatchNeighborNavigation(Button neighbor, Button shiftButton, NavDirection dirToShift)
    {
        if (neighbor == null || shiftButton == null)
        {
            return;
        }

        var nav = neighbor.navigation;
        if (nav.mode == Navigation.Mode.None)
        {
            return;
        }

        if (nav.mode != Navigation.Mode.Explicit)
        {
            nav.mode = Navigation.Mode.Explicit;
        }

        switch (dirToShift)
        {
            case NavDirection.Up: nav.selectOnUp = shiftButton; break;
            case NavDirection.Down: nav.selectOnDown = shiftButton; break;
            case NavDirection.Left: nav.selectOnLeft = shiftButton; break;
            case NavDirection.Right: nav.selectOnRight = shiftButton; break;
        }

        neighbor.navigation = nav;
    }

    private static ShiftNavigationProfile? ResolveShiftNavigationProfile(string signature, bool isLimited)
    {
        var profiles = KeymapConfig.ShiftNavigationProfiles;
        if (profiles == null || profiles.Count == 0)
        {
            return null;
        }

        if (!string.IsNullOrEmpty(signature) && profiles.TryGetValue(signature, out var sigProfile))
        {
            return sigProfile;
        }

        if (!isLimited && profiles.TryGetValue("full", out var fullProfile))
        {
            return fullProfile;
        }

        return null;
    }

    private static Button? ResolveNavTarget(string? target, KeyboardInput owner, KeyboardShiftState state, Button[] buttons)
    {
        if (string.IsNullOrEmpty(target) || string.Equals(target, "none", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        if (string.Equals(target, "space", StringComparison.OrdinalIgnoreCase))
        {
            return FindButtonBySpecialName(buttons, "space");
        }

        if (string.Equals(target, "enter", StringComparison.OrdinalIgnoreCase))
        {
            return FindButtonBySpecialName(buttons, "enter") ?? FindButtonBySpecialName(buttons, "ok");
        }

        if (string.Equals(target, "backspace", StringComparison.OrdinalIgnoreCase))
        {
            return FindButtonBySpecialName(buttons, "back");
        }

        return FindButtonByDisplayedJamo(owner, state, buttons, target);
    }

    private static Button? FindButtonBySpecialName(Button[] buttons, string needle)
    {
        foreach (var button in buttons)
        {
            if (button == null || !button.gameObject.activeInHierarchy || IsCustomShiftButton(button))
            {
                continue;
            }

            var name = button.name ?? string.Empty;
            if (name.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return button;
            }

            var current = button.transform;
            for (var i = 0; i < 4 && current != null; i++)
            {
                if ((current.name ?? string.Empty).IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return button;
                }
                current = current.parent;
            }
        }

        return null;
    }

    private static Button? FindButtonByDisplayedJamo(KeyboardInput owner, KeyboardShiftState state, Button[] buttons, string targetJamo)
    {
        foreach (var button in buttons)
        {
            if (button == null || !button.gameObject.activeInHierarchy || IsCustomShiftButton(button))
            {
                continue;
            }

            var token = GetMappedToken(owner, button.name ?? string.Empty, button.transform);
            if (string.IsNullOrEmpty(token))
            {
                continue;
            }

            if (TryExtractTokenLetter(token, out var letter))
            {
                string? displayedJamo = null;
                if (state?.LimitedBaseMap != null && state.LimitedBaseMap.TryGetValue(letter, out var limitedMapped))
                {
                    displayedJamo = limitedMapped;
                }
                else if (KorMap.TryGetValue(letter, out var fallbackMapped))
                {
                    displayedJamo = fallbackMapped;
                }

                if (displayedJamo != null && string.Equals(displayedJamo, targetJamo, StringComparison.Ordinal))
                {
                    return button;
                }
            }

            if (token.Length == 1 && token[0] is >= '\u3131' and <= '\u318E' &&
                string.Equals(token, targetJamo, StringComparison.Ordinal))
            {
                return button;
            }
        }

        return null;
    }

    private static void ApplyDisabledKeys(KeyboardInput owner, KeyboardShiftState state, GameObject root)
    {
        if (owner == null || state == null || root == null)
        {
            return;
        }

        var disabledMap = KeymapConfig.DisabledKeysBySignature;
        if (disabledMap == null || !disabledMap.TryGetValue(state.LayoutSignature, out var disabledTokens) || disabledTokens == null || disabledTokens.Count == 0)
        {
            return;
        }

        var disabledSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var tok in disabledTokens)
        {
            var normalized = NormalizeConfigToken(tok);
            if (!string.IsNullOrEmpty(normalized))
            {
                disabledSet.Add(normalized);
            }
        }

        if (disabledSet.Count == 0)
        {
            return;
        }

        var buttons = root.GetComponentsInChildren<Button>(true);
        foreach (var button in buttons)
        {
            if (button == null || !button.gameObject.activeInHierarchy || IsCustomShiftButton(button))
            {
                continue;
            }

            var token = NormalizeConfigToken(GetMappedToken(owner, button.name ?? string.Empty, button.transform));
            if (disabledSet.Contains(token))
            {
                button.gameObject.SetActive(false);
            }
        }
    }

    private static bool IsTypableKey(string keyName, string token)
    {
        if (string.IsNullOrEmpty(token) || token.Length != 1)
        {
            return false;
        }

        if (IsBackspaceKey(keyName, token))
        {
            return false;
        }

        var c = token[0];
        return c >= ' ' && !char.IsControl(c);
    }

    private static void UpdateShiftButtonVisual(KeyboardShiftState state)
    {
        var button = state.ShiftButton;
        if (button == null)
        {
            return;
        }

        var image = button.GetComponent<Image>();
        if (image == null)
        {
            return;
        }

        var owner = ResolveOwner(button);
        var isFocused = owner != null && ButtonsMatch(owner.cursor, button);
        if (state.ShiftFocusOverlay == null)
        {
            state.ShiftFocusOverlay = button.transform.Find("hit")?.gameObject;
        }

        if (state.ShiftFocusOverlay != null && state.ShiftFocusOverlay.activeSelf != isFocused)
        {
            state.ShiftFocusOverlay.SetActive(isFocused);
        }

        if (isFocused)
        {
            image.color = state.BaseColor;
            return;
        }

        image.color = state.SoftShiftPending ? Color.cyan : state.BaseColor;
    }

    internal static void RefreshShiftFocusVisual(KeyboardInput? owner)
    {
        var state = GetShiftState(owner, createIfMissing: false);
        if (state == null)
        {
            return;
        }

        UpdateShiftButtonVisual(state);
    }

    private static void ApplyKeyboardSprites(KeyboardInput owner, KeyboardShiftState state)
    {
        var root = ResolveKeyboardRoot(owner);
        if (root == null)
        {
            DebugLog(
                $"owner.apply.noroot.{GetId(owner)}.{Time.frameCount}",
                $"ApplyKeyboardSprites skipped owner={GetId(owner)} reason=no-root",
                once: false);
            return;
        }

        var shifted = state.SoftShiftPending;
        var images = root.GetComponentsInChildren<Image>(true);
        var applied = 0;
        foreach (var image in images)
        {
            if (image == null || !TryApplySpriteForImage(owner, image, log: false, shifted: shifted))
            {
                continue;
            }
            applied++;
        }

        state.SpritesApplied = true;
        state.AppliedShiftVisual = shifted;
        DebugLog($"owner.apply.count.{GetId(owner)}.{applied}", $"ApplyKeyboardSprites owner={GetId(owner)} root={GetTransformPath(root.transform)} images={images.Length} applied={applied}", once: true);
    }

    private static GameObject? ResolveKeyboardRoot(KeyboardInput owner)
    {
        GameObject? best = null;
        var bestScore = int.MinValue;

        var candidates = new[] { owner._root, owner.display, owner.gameObject };
        foreach (var candidate in candidates)
        {
            if (candidate == null)
            {
                continue;
            }

            var buttons = candidate.GetComponentsInChildren<Button>(true);
            var mappedCount = 0;
            foreach (var button in buttons)
            {
                if (button == null)
                {
                    continue;
                }

                var token = GetMappedToken(owner, button.name ?? string.Empty, button.transform);
                if (!string.IsNullOrEmpty(token))
                {
                    mappedCount++;
                }
            }

            var score = mappedCount * 1000 + buttons.Length;
            if (score > bestScore)
            {
                best = candidate;
                bestScore = score;
            }
        }

        DebugLog(
            $"owner.root.pick.{GetId(owner)}",
            $"ResolveKeyboardRoot owner={GetId(owner)} best={(best != null ? GetTransformPath(best.transform) : "<null>")} score={bestScore}",
            once: true);
        return best;
    }

    private static Sprite? LoadSpriteFile(string fileName)
    {
        if (SpriteCache.TryGetValue(fileName, out var cached))
        {
            // Unity can invalidate native objects across scene transitions.
            // If a cached sprite compares null, drop it and force a reload.
            if (cached != null)
            {
                return cached;
            }

            DebugLog(
                $"sprite.cache.stale.{fileName}.{Time.frameCount}",
                $"Cached sprite became null; reloading file={fileName}",
                once: false);
            SpriteCache.Remove(fileName);
        }

        var folder = GetSpriteFolderPath();
        if (!Directory.Exists(folder))
        {
            if (!MissingSpriteDirWarned)
            {
                MissingSpriteDirWarned = true;
                MelonLogger.Warning($"Sprite folder not found: {folder}");
            }

            SpriteCache[fileName] = null;
            return null;
        }

        var path = Path.Combine(folder, fileName);
        if (!File.Exists(path))
        {
            DebugLog($"sprite.missing.{fileName}", $"Missing sprite file: {path}", once: true);
            SpriteCache[fileName] = null;
            return null;
        }

        byte[] bytes;
        try
        {
            bytes = File.ReadAllBytes(path);
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"Failed to read sprite '{path}': {ex.Message}");
            SpriteCache[fileName] = null;
            return null;
        }

        var texture = new Texture2D(2, 2, TextureFormat.RGBA32, mipChain: false);
        if (!TryLoadImage(texture, bytes))
        {
            UnityEngine.Object.Destroy(texture);
            SpriteCache[fileName] = null;
            return null;
        }

        texture.name = $"AISF2KR_{Path.GetFileNameWithoutExtension(fileName)}";
        texture.wrapMode = TextureWrapMode.Clamp;
        texture.filterMode = FilterMode.Bilinear;

        var sprite = Sprite.Create(
            texture,
            new Rect(0f, 0f, texture.width, texture.height),
            new Vector2(0.5f, 0.5f),
            100f);

        sprite.name = Path.GetFileNameWithoutExtension(fileName);
        SpriteCache[fileName] = sprite;
        DebugLog($"sprite.loaded.{fileName}", $"Loaded sprite: {path}", once: true);
        return sprite;
    }

    private static string GetSpriteFolderPath()
    {
        return Path.Combine(Path.GetDirectoryName(typeof(ModEntry).Assembly.Location) ?? ".", SpriteFolderName);
    }

    internal static string GetTransformPath(Transform? tr)
    {
        if (tr == null)
        {
            return "<null>";
        }

        var parts = new List<string>(8);
        var cur = tr;
        var guard = 0;
        while (cur != null && guard < 20)
        {
            parts.Add(cur.name ?? "<noname>");
            cur = cur.parent;
            guard++;
        }

        parts.Reverse();
        return string.Join("/", parts);
    }

    private static Image? ResolveButtonImage(Button button)
    {
        Image? image = button.targetGraphic as Image;
        if (image == null)
        {
            image = button.GetComponent<Image>();
        }

        var buttonEx = button.GetComponent<ButtonEx>();
        if (buttonEx != null && buttonEx.buttonIcon != null)
        {
            image = buttonEx.buttonIcon;
        }

        return image;
    }

    internal static bool TryApplySpriteForImage(KeyboardInput owner, Image image, bool log, bool shifted)
    {
        if (owner == null || image == null)
        {
            return false;
        }

        var tr = image.transform;
        var name = tr != null ? tr.name : string.Empty;

        if (IsShiftImage(image) || string.Equals(name, ShiftButtonName, StringComparison.Ordinal))
        {
            var shiftSprite = LoadSpriteFile("key_shift.png");
            if (shiftSprite == null)
            {
                return false;
            }

            image.overrideSprite = shiftSprite;
            image.sprite = shiftSprite;
            return true;
        }

        var token = GetMappedToken(owner, name ?? string.Empty, tr);
        if (!TryGetKoreanJamo(owner, token, shifted: shifted, out var jamo))
        {
            if (log)
            {
                DebugLog($"img.nomap.{GetTransformPath(tr)}", $"No jamo map for image: path={GetTransformPath(tr)} name={name} token='{token}'", once: true);
            }

            return false;
        }

        var spriteName = $"key_{jamo}.png";
        var sprite = LoadSpriteFile(spriteName);
        if (sprite == null)
        {
            if (log)
            {
                DebugLog($"img.nosprite.{spriteName}", $"Sprite unresolved for image path={GetTransformPath(tr)} token={token} jamo={jamo}", once: true);
            }

            return false;
        }

        image.overrideSprite = sprite;
        image.sprite = sprite;
        if (log)
        {
            DebugLog($"img.applied.{GetTransformPath(tr)}", $"Applied image path={GetTransformPath(tr)} token={token} jamo={jamo} sprite={spriteName}", once: true);
        }

        return true;
    }

    internal static bool TryApplySpriteForButton(KeyboardInput owner, Button button, bool log, bool shifted)
    {
        if (owner == null || button == null)
        {
            return false;
        }

        var image = ResolveButtonImage(button);
        if (image == null)
        {
            if (log)
            {
                DebugLog($"btn.noimage.{GetTransformPath(button.transform)}", $"No target image: {GetTransformPath(button.transform)}", once: true);
            }

            return false;
        }

        if (IsShiftButton(button) || string.Equals(button.name, ShiftButtonName, StringComparison.Ordinal))
        {
            var shiftSprite = LoadSpriteFile("key_shift.png");
            if (shiftSprite == null)
            {
                return false;
            }

            image.overrideSprite = shiftSprite;
            image.sprite = shiftSprite;
            return true;
        }

        var token = GetMappedToken(owner, button.name ?? string.Empty, button.transform);
        if (!TryGetKoreanJamo(owner, token, shifted: shifted, out var jamo))
        {
            if (log)
            {
                DebugLog($"btn.nomap.{GetTransformPath(button.transform)}", $"No jamo map: path={GetTransformPath(button.transform)} name={button.name} token='{token}'", once: true);
            }

            return false;
        }

        var spriteName = $"key_{jamo}.png";
        var sprite = LoadSpriteFile(spriteName);
        if (sprite == null)
        {
            if (log)
            {
                DebugLog($"btn.nosprite.{spriteName}", $"Sprite unresolved for jamo={jamo} file={spriteName}", once: true);
            }

            return false;
        }

        image.overrideSprite = sprite;
        image.sprite = sprite;
        if (log)
        {
            DebugLog($"btn.applied.{GetTransformPath(button.transform)}", $"Applied sprite path={GetTransformPath(button.transform)} token={token} jamo={jamo} sprite={spriteName}", once: true);
        }

        return true;
    }

    private static bool TryLoadImage(Texture2D texture, byte[] bytes)
    {
        try
        {
            var il2Bytes = new Il2CppStructArray<byte>(bytes);
            return ImageConversion.LoadImage(texture, il2Bytes, markNonReadable: false);
        }
        catch (Exception ex)
        {
            DebugLog("sprite.loadimage.fail", $"LoadImage failed: {ex.GetType().Name} {ex.Message}", once: true);
            return false;
        }
    }

}

internal sealed class KeyboardMappingConfig
{
    public Dictionary<string, Dictionary<string, string>>? LimitedLayoutsBySignature { get; set; }
    public Dictionary<string, Dictionary<string, string>>? LimitedLayoutsByScene { get; set; }
    public Dictionary<string, ShiftNavigationProfile>? ShiftNavigationProfiles { get; set; }
    public Dictionary<string, List<string>>? DisabledKeysBySignature { get; set; }

    /// <summary>
    /// Answer aliases for keyboard input questions.
    /// Key: primary answer.
    /// Value: list of accepted aliases.
    /// When the player types an alias and presses OK, the input is silently
    /// replaced with the primary answer so the game's __Check accepts it.
    /// </summary>
    public Dictionary<string, List<string>>? AnswerAliases { get; set; }
}

internal sealed class ShiftNavigationProfile
{
    public string? Up { get; set; }
    public string? Down { get; set; }
    public string? Left { get; set; }
    public string? Right { get; set; }
}

internal sealed class KeyboardShiftState
{
    public bool SoftShiftPending { get; set; }
    public Button? ShiftButton { get; set; }
    public GameObject? ShiftFocusOverlay { get; set; }
    public Button? NavUpButton { get; set; }
    public Button? NavDownButton { get; set; }
    public Button? NavLeftButton { get; set; }
    public Button? NavRightButton { get; set; }
    public long RootId { get; set; }
    public int PlacementRefreshFramesRemaining { get; set; }
    public bool LayoutProfileInitialized { get; set; }
    public bool IsLimitedKeyboard { get; set; }
    public string LayoutSignature { get; set; } = string.Empty;
    public Dictionary<char, string>? LimitedBaseMap { get; set; }
    public Color BaseColor { get; set; } = Color.white;
    public bool BaseColorInitialized { get; set; }
    public bool SpritesApplied { get; set; }
    public bool AppliedShiftVisual { get; set; }
    public int LastShiftToggleFrame { get; set; } = -1;
    public int LastShiftNavFrame { get; set; } = -1;
    public ModEntry.NavDirection LastMoveDirection { get; set; } = ModEntry.NavDirection.Right;
    public int LastMoveDirectionFrame { get; set; } = -1;
    public bool SuppressShiftExitUntilNeutral { get; set; }
    public ModEntry.NavDirection SuppressShiftExitDirection { get; set; } = ModEntry.NavDirection.Right;
    public int SuppressShiftExitNeutralFrames { get; set; }
    public Button? SuppressShiftExitTarget { get; set; }
    public bool ShiftNavHoldLeft { get; set; }
    public bool ShiftNavHoldRight { get; set; }
    public bool ShiftNavHoldUp { get; set; }
    public bool ShiftNavHoldDown { get; set; }
    public bool HasPendingShiftNavEdge { get; set; }
    public ModEntry.NavDirection ShiftNavPendingDirection { get; set; } = ModEntry.NavDirection.Right;
}

internal sealed class RepeatInputSample
{
    public int Frame { get; set; }
    public int Value { get; set; }
}

internal sealed class ClickContext
{
    public string InputBefore { get; set; } = string.Empty;
    public string KeyName { get; set; } = string.Empty;
    public string Token { get; set; } = string.Empty;
    public bool PhysicalShift { get; set; }
    public int MaxLength { get; set; }
    public bool AliasSubstituted { get; set; }
}

[HarmonyPatch(typeof(KeyboardInput), nameof(KeyboardInput.Hide))]
internal static class KeyboardInputHidePatch
{
    [HarmonyPostfix]
    private static void Postfix(KeyboardInput __instance)
    {
        ModEntry.DebugLog(
            $"patch.hide.{ModEntry.GetId(__instance)}.{Time.frameCount}",
            $"KeyboardInput.Hide owner={ModEntry.GetId(__instance)}",
            once: false);
        ModEntry.HideOrDisposeShiftButton(__instance, dispose: false);
    }
}

[HarmonyPatch(typeof(KeyboardInput), nameof(KeyboardInput.Erase))]
internal static class KeyboardInputErasePatch
{
    [HarmonyPostfix]
    private static void Postfix(KeyboardInput __instance)
    {
        ModEntry.DebugLog(
            $"patch.erase.{ModEntry.GetId(__instance)}.{Time.frameCount}",
            $"KeyboardInput.Erase owner={ModEntry.GetId(__instance)}",
            once: false);
        ModEntry.HideOrDisposeShiftButton(__instance, dispose: false);
    }
}

[HarmonyPatch(typeof(KeyboardInput), nameof(KeyboardInput.OnDestroy))]
internal static class KeyboardInputOnDestroyPatch
{
    [HarmonyPostfix]
    private static void Postfix(KeyboardInput __instance)
    {
        ModEntry.DebugLog(
            $"patch.destroy.{ModEntry.GetId(__instance)}.{Time.frameCount}",
            $"KeyboardInput.OnDestroy owner={ModEntry.GetId(__instance)}",
            once: false);
        ModEntry.HideOrDisposeShiftButton(__instance, dispose: true);
    }
}

[HarmonyPatch(typeof(KeyboardInput), nameof(KeyboardInput.Show))]
internal static class KeyboardInputShowPatch
{
    [HarmonyPostfix]
    private static void Postfix(KeyboardInput __instance)
    {
        ModEntry.DebugLog(
            $"patch.show.{ModEntry.GetId(__instance)}.{Time.frameCount}",
            $"KeyboardInput.Show owner={ModEntry.GetId(__instance)}",
            once: false);
        ModEntry.EnsureShiftButton(__instance);
        ModEntry.ScheduleShiftPlacementRefresh(__instance);
        ModEntry.SetSoftShiftPending(__instance, false);
    }
}

[HarmonyPatch(typeof(KeyboardInput), nameof(KeyboardInput.InputSub))]
internal static class KeyboardInputInputSubPatch
{
    [HarmonyPostfix]
    private static void Postfix(KeyboardInput __instance, ref Il2CppSystem.Collections.IEnumerator __result)
    {
        if (__instance == null || __result == null)
        {
            return;
        }

        var ownerId = ModEntry.GetId(__instance);
        if (ownerId == 0)
        {
            return;
        }

        try
        {
            var coroutine = __result.TryCast<KeyboardInput._InputSub_d__38>();
            if (coroutine != null)
            {
                ModEntry.InputSubCoroutines[ownerId] = coroutine;
                ModEntry.ShiftInjectedIntoButtonList.Remove(ownerId);
                ModEntry.DebugLog(
                    $"patch.inputsub.{ownerId}.{Time.frameCount}",
                    $"Captured InputSub coroutine owner={ownerId} {ModEntry.DescribeCoroutine(coroutine, "InputSub")}",
                    once: false);
            }
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"InputSub patch failed to capture coroutine: {ex.Message}");
        }
    }
}

[HarmonyPatch(typeof(ButtonEx), nameof(ButtonEx.SetSprite))]
internal static class ButtonExSetSpritePatch
{
    [HarmonyPostfix]
    private static void Postfix(ButtonEx __instance, int no)
    {
        if (__instance == null)
        {
            return;
        }

        try
        {
            var button = __instance.GetComponent<Button>();
            if (button == null)
            {
                ModEntry.DebugLog($"setsprite.nobutton.{ModEntry.GetId(__instance)}", $"ButtonEx has no Button component: {__instance.name}", once: true);
                return;
            }

            KeyboardInput? owner = null;
            var current = button.transform;
            for (var i = 0; i < 10 && current != null; i++)
            {
                owner = current.GetComponent<KeyboardInput>();
                if (owner != null)
                {
                    break;
                }

                current = current.parent;
            }
            if (owner == null)
            {
                foreach (var pair in ModEntry.KnownOwners)
                {
                    var candidate = pair.Value;
                    if (candidate == null)
                    {
                        continue;
                    }

                    var root = candidate._root ?? candidate.display ?? candidate.gameObject;
                    if (root != null && button.transform.IsChildOf(root.transform))
                    {
                        owner = candidate;
                        break;
                    }
                }
            }

            if (owner == null)
            {
                ModEntry.DebugLog(
                    $"setsprite.noowner.{ModEntry.GetTransformPath(button.transform)}.{Time.frameCount}",
                    $"SetSprite no owner: path={ModEntry.GetTransformPath(button.transform)} no={no}",
                    once: false);
                return;
            }

            var applied = ModEntry.TryApplySpriteForButton(owner, button, log: false, shifted: ModEntry.IsSoftShiftPending(owner));
            ModEntry.DebugLog(
                $"setsprite.call.{ModEntry.GetTransformPath(button.transform)}.{Time.frameCount}",
                $"SetSprite call: owner={ModEntry.GetId(owner)} path={ModEntry.GetTransformPath(button.transform)} no={no} applied={applied}",
                once: false);
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"ButtonEx.SetSprite patch failed: {ex.Message}");
        }
    }
}

[HarmonyPatch]
internal static class KeyboardInputDisplayClassButtonBoolTracePatch
{
    private static IEnumerable<MethodBase> TargetMethods()
    {
        var method = AccessTools.Method(
            typeof(KeyboardInput.__c__DisplayClass38_0),
            nameof(KeyboardInput.__c__DisplayClass38_0.Method_Internal_Void_Button_Boolean_0),
            new[] { typeof(Button), typeof(bool) });
        if (method != null)
        {
            yield return method;
        }
    }

    [HarmonyPrefix]
    [HarmonyPriority(Priority.First)]
    private static void Prefix(KeyboardInput.__c__DisplayClass38_0 __instance, ref Button focus, bool onClick, MethodBase __originalMethod)
    {
        ModEntry.TryRedirectShiftFocus(__instance, ref focus);
    }

    [HarmonyPostfix]
    [HarmonyPriority(Priority.Last)]
    private static void Postfix(KeyboardInput.__c__DisplayClass38_0 __instance, Button focus, bool onClick, MethodBase __originalMethod)
    {
        ModEntry.RefreshShiftFocusVisual(__instance?.__4__this);
    }
}

[HarmonyPatch(typeof(RepeatInput), nameof(RepeatInput.Update))]
internal static class RepeatInputUpdateTracePatch
{
    [HarmonyPostfix]
    private static void Postfix(RepeatInput __instance, int __result)
    {
        ModEntry.RememberRepeatInputResult(__instance, __result);
    }
}

[HarmonyPatch]
internal static class KeyboardInputDisplayClassButtonExSetupPatch
{
    private static IEnumerable<MethodBase> TargetMethods()
    {
        var method0 = AccessTools.Method(
            typeof(KeyboardInput.__c__DisplayClass38_0),
            nameof(KeyboardInput.__c__DisplayClass38_0._InputSub_b__7),
            new[] { typeof(ButtonEx) });
        if (method0 != null)
        {
            yield return method0;
        }

        var method1 = AccessTools.Method(
            typeof(KeyboardInput.__c__DisplayClass38_1),
            nameof(KeyboardInput.__c__DisplayClass38_1._InputSub_b__6),
            new[] { typeof(ButtonEx) });
        if (method1 != null)
        {
            yield return method1;
        }
    }

    [HarmonyPrefix]
    private static bool Prefix(object __instance, ButtonEx x, MethodBase __originalMethod)
    {
        if (!ModEntry.IsCustomShiftButtonEx(x))
        {
            return true;
        }
        return false;
    }
}

[HarmonyPatch]
internal static class KeyboardInputDisplayClassButtonNoArgSetupPatch
{
    private static IEnumerable<MethodBase> TargetMethods()
    {
        var method = AccessTools.Method(
            typeof(KeyboardInput.__c__DisplayClass38_1),
            nameof(KeyboardInput.__c__DisplayClass38_1._InputSub_b__8),
            Type.EmptyTypes);
        if (method != null)
        {
            yield return method;
        }
    }

    [HarmonyPrefix]
    private static bool Prefix(KeyboardInput.__c__DisplayClass38_1 __instance, MethodBase __originalMethod)
    {
        var button = ModEntry.ReadMemberValue(__instance, "button") as Button;
        if (!ModEntry.IsCustomShiftButton(button))
        {
            return true;
        }
        return false;
    }
}

[HarmonyPatch(typeof(KeyboardInput.__c__DisplayClass38_0), nameof(KeyboardInput.__c__DisplayClass38_0.Method_Internal_Void_Button_0))]
internal static class KeyboardInputComposePatch
{
    [HarmonyPrefix]
    private static bool Prefix(KeyboardInput.__c__DisplayClass38_0 __instance, Button focus)
    {
        if (__instance == null)
        {
            return true;
        }

        if (focus == null)
        {
            return true;
        }

        if (ModEntry.TryHandleCustomShiftClick(__instance, focus))
        {
            return false;
        }

        var inputBefore = __instance.input ?? string.Empty;
        var aliasSubstituted = false;

        // When the OK button is pressed, try alias substitution before the game checks the answer
        var focusName = focus.name ?? string.Empty;
        if (focusName.IndexOf("ok", StringComparison.OrdinalIgnoreCase) >= 0 ||
            focusName.IndexOf("enter", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            aliasSubstituted = ModEntry.TrySubstituteAlias(__instance);
        }

        var id = ModEntry.GetId(__instance);
        if (id == 0)
        {
            return true;
        }

        var keyName = focus?.name ?? string.Empty;
        var token = ModEntry.GetMappedToken(__instance.__4__this, keyName, focus?.transform);
        var ownerId = ModEntry.GetId(__instance.__4__this);

        if (!ModEntry.ClickContexts.TryGetValue(id, out var clickStack) || clickStack == null)
        {
            clickStack = new Stack<ClickContext>();
            ModEntry.ClickContexts[id] = clickStack;
        }

        clickStack.Push(new ClickContext
        {
            InputBefore = inputBefore,
            KeyName = keyName,
            Token = token,
            PhysicalShift = ModEntry.IsPhysicalShiftHeld(),
            MaxLength = __instance.length,
            AliasSubstituted = aliasSubstituted
        });
        return true;
    }

    [HarmonyPostfix]
    private static void Postfix(KeyboardInput.__c__DisplayClass38_0 __instance)
    {
        if (__instance == null)
        {
            return;
        }

        var id = ModEntry.GetId(__instance);
        if (id == 0 ||
            !ModEntry.ClickContexts.TryGetValue(id, out var clickStack) ||
            clickStack == null ||
            clickStack.Count == 0)
        {
            return;
        }

        var ctx = clickStack.Pop();
        if (clickStack.Count == 0)
        {
            ModEntry.ClickContexts.Remove(id);
        }

        try
        {
            var owner = __instance.__4__this;
            var ownerId = ModEntry.GetId(owner);
            var useShift = ctx.PhysicalShift || ModEntry.IsSoftShiftPending(owner);

            if (ModEntry.TryGetKoreanJamo(owner, ctx.Token, useShift, out var jamo))
            {
                var rawBefore = HangulComposer.DecomposeToRaw(ctx.InputBefore ?? string.Empty);
                var composed = HangulComposer.ComposeRaw(rawBefore + jamo);

                if (ctx.MaxLength > 0 && composed.Length > ctx.MaxLength)
                {
                    composed = ctx.InputBefore ?? string.Empty;
                }

                ModEntry.ApplyRenderedInput(__instance, composed);
                ModEntry.ConsumeSoftShiftIfNeeded(owner);
                return;
            }

            if (ModEntry.IsBackspaceKey(ctx.KeyName, ctx.Token))
            {
                var rawBefore = HangulComposer.DecomposeToRaw(ctx.InputBefore ?? string.Empty);
                var rawAfter = HangulComposer.RemoveLastRawUnit(rawBefore);
                var composed = HangulComposer.ComposeRaw(rawAfter);
                ModEntry.ApplyRenderedInput(__instance, composed);
                return;
            }

            if (ctx.AliasSubstituted)
            {
                ModEntry.RenderDisplayedInput(__instance, ctx.InputBefore);
            }
        }
        catch (Exception ex)
        {
            MelonLogger.Warning($"Korean IME compose failed: {ex.Message}");
        }
    }
}

internal static class HangulComposer
{
    private static readonly char[] Choseong =
    {
        'ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ',
        'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'
    };

    private static readonly char[] Jungseong =
    {
        'ㅏ', 'ㅐ', 'ㅑ', 'ㅒ', 'ㅓ', 'ㅔ', 'ㅕ', 'ㅖ', 'ㅗ', 'ㅘ',
        'ㅙ', 'ㅚ', 'ㅛ', 'ㅜ', 'ㅝ', 'ㅞ', 'ㅟ', 'ㅠ', 'ㅡ', 'ㅢ', 'ㅣ'
    };

    private static readonly char[] Jongseong =
    {
        '\0', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ',
        'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ',
        'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'
    };

    private static readonly Dictionary<char, int> ChoIndex = BuildIndex(Choseong);
    private static readonly Dictionary<char, int> JungIndex = BuildIndex(Jungseong);
    private static readonly Dictionary<char, int> JongIndex = BuildIndex(Jongseong);

    private static readonly Dictionary<(char, char), char> DoubleInitial = new()
    {
        [('ㄱ', 'ㄱ')] = 'ㄲ',
        [('ㄷ', 'ㄷ')] = 'ㄸ',
        [('ㅂ', 'ㅂ')] = 'ㅃ',
        [('ㅅ', 'ㅅ')] = 'ㅆ',
        [('ㅈ', 'ㅈ')] = 'ㅉ'
    };

    private static readonly Dictionary<(char, char), char> DoubleFinal = new()
    {
        [('ㄱ', 'ㅅ')] = 'ㄳ',
        [('ㄴ', 'ㅈ')] = 'ㄵ',
        [('ㄴ', 'ㅎ')] = 'ㄶ',
        [('ㄹ', 'ㄱ')] = 'ㄺ',
        [('ㄹ', 'ㅁ')] = 'ㄻ',
        [('ㄹ', 'ㅂ')] = 'ㄼ',
        [('ㄹ', 'ㅅ')] = 'ㄽ',
        [('ㄹ', 'ㅌ')] = 'ㄾ',
        [('ㄹ', 'ㅍ')] = 'ㄿ',
        [('ㄹ', 'ㅎ')] = 'ㅀ',
        [('ㅂ', 'ㅅ')] = 'ㅄ'
    };

    private static readonly Dictionary<char, (char, char)> SplitFinal = new()
    {
        ['ㄳ'] = ('ㄱ', 'ㅅ'),
        ['ㄵ'] = ('ㄴ', 'ㅈ'),
        ['ㄶ'] = ('ㄴ', 'ㅎ'),
        ['ㄺ'] = ('ㄹ', 'ㄱ'),
        ['ㄻ'] = ('ㄹ', 'ㅁ'),
        ['ㄼ'] = ('ㄹ', 'ㅂ'),
        ['ㄽ'] = ('ㄹ', 'ㅅ'),
        ['ㄾ'] = ('ㄹ', 'ㅌ'),
        ['ㄿ'] = ('ㄹ', 'ㅍ'),
        ['ㅀ'] = ('ㄹ', 'ㅎ'),
        ['ㅄ'] = ('ㅂ', 'ㅅ')
    };

    private static readonly Dictionary<(char, char), char> DoubleVowel = new()
    {
        [('ㅗ', 'ㅏ')] = 'ㅘ',
        [('ㅗ', 'ㅐ')] = 'ㅙ',
        [('ㅗ', 'ㅣ')] = 'ㅚ',
        [('ㅜ', 'ㅓ')] = 'ㅝ',
        [('ㅜ', 'ㅔ')] = 'ㅞ',
        [('ㅜ', 'ㅣ')] = 'ㅟ',
        [('ㅡ', 'ㅣ')] = 'ㅢ'
    };

    private static readonly Dictionary<char, (char, char)> SplitVowel = new()
    {
        ['ㅘ'] = ('ㅗ', 'ㅏ'),
        ['ㅙ'] = ('ㅗ', 'ㅐ'),
        ['ㅚ'] = ('ㅗ', 'ㅣ'),
        ['ㅝ'] = ('ㅜ', 'ㅓ'),
        ['ㅞ'] = ('ㅜ', 'ㅔ'),
        ['ㅟ'] = ('ㅜ', 'ㅣ'),
        ['ㅢ'] = ('ㅡ', 'ㅣ')
    };

    public static string RemoveLastRawUnit(string raw)
    {
        if (string.IsNullOrEmpty(raw))
        {
            return string.Empty;
        }

        return raw[..^1];
    }

    public static string DecomposeToRaw(string source)
    {
        if (string.IsNullOrEmpty(source))
        {
            return string.Empty;
        }

        var sb = new StringBuilder(source.Length * 2);

        foreach (var ch in source)
        {
            if (ch is >= '\uAC00' and <= '\uD7A3')
            {
                var sIndex = ch - 0xAC00;
                var l = sIndex / 588;
                var v = (sIndex % 588) / 28;
                var t = sIndex % 28;

                sb.Append(Choseong[l]);
                AppendDecomposedVowel(sb, Jungseong[v]);
                if (t > 0)
                {
                    AppendDecomposedFinal(sb, Jongseong[t]);
                }
                continue;
            }

            if (SplitVowel.TryGetValue(ch, out var vowelSplit))
            {
                sb.Append(vowelSplit.Item1);
                sb.Append(vowelSplit.Item2);
                continue;
            }

            if (SplitFinal.TryGetValue(ch, out var finalSplit))
            {
                sb.Append(finalSplit.Item1);
                sb.Append(finalSplit.Item2);
                continue;
            }

            sb.Append(ch);
        }

        return sb.ToString();
    }

    public static string ComposeRaw(string raw)
    {
        if (string.IsNullOrEmpty(raw))
        {
            return string.Empty;
        }

        var sb = new StringBuilder(raw.Length);
        var i = 0;

        while (i < raw.Length)
        {
            var c = raw[i];

            if (IsConsonant(c))
            {
                var start = i;
                var cho = c;

                if (i + 2 < raw.Length && IsConsonant(raw[i + 1]) && IsVowel(raw[i + 2]) && TryCombineInitial(cho, raw[i + 1], out var cho2))
                {
                    cho = cho2;
                    i++;
                }

                var j = i + 1;
                if (j < raw.Length && IsVowel(raw[j]))
                {
                    var jung = raw[j];
                    j++;

                    if (j < raw.Length && IsVowel(raw[j]) && TryCombineVowel(jung, raw[j], out var jung2))
                    {
                        jung = jung2;
                        j++;
                    }

                    char jong = '\0';
                    if (j < raw.Length && IsConsonant(raw[j]))
                    {
                        var codaCandidate = raw[j];
                        var nextIsVowel = j + 1 < raw.Length && IsVowel(raw[j + 1]);

                        if (!nextIsVowel && IsValidFinal(codaCandidate))
                        {
                            jong = codaCandidate;
                            j++;

                            if (j < raw.Length && IsConsonant(raw[j]))
                            {
                                var nextAfterSecond = j + 1 < raw.Length && IsVowel(raw[j + 1]);
                                if (!nextAfterSecond && TryCombineFinal(jong, raw[j], out var jong2))
                                {
                                    jong = jong2;
                                    j++;
                                }
                            }
                        }
                    }

                    if (TryComposeSyllable(cho, jung, jong, out var syllable))
                    {
                        sb.Append(syllable);
                    }
                    else
                    {
                        sb.Append(raw[start]);
                        if (start + 1 <= i)
                        {
                            for (var k = start + 1; k <= i; k++)
                            {
                                sb.Append(raw[k]);
                            }
                        }
                        sb.Append(jung);
                        if (jong != '\0')
                        {
                            sb.Append(jong);
                        }
                    }

                    i = j;
                    continue;
                }

                if (i + 1 < raw.Length && IsConsonant(raw[i + 1]) && TryCombineFinal(c, raw[i + 1], out var pairConsonant))
                {
                    sb.Append(pairConsonant);
                    i += 2;
                    continue;
                }

                sb.Append(c);
                i++;
                continue;
            }

            if (IsVowel(c))
            {
                if (i + 1 < raw.Length && IsVowel(raw[i + 1]) && TryCombineVowel(c, raw[i + 1], out var vv))
                {
                    sb.Append(vv);
                    i += 2;
                    continue;
                }

                sb.Append(c);
                i++;
                continue;
            }

            sb.Append(c);
            i++;
        }

        return sb.ToString();
    }

    private static void AppendDecomposedFinal(StringBuilder sb, char jong)
    {
        if (SplitFinal.TryGetValue(jong, out var split))
        {
            sb.Append(split.Item1);
            sb.Append(split.Item2);
            return;
        }

        sb.Append(jong);
    }

    private static void AppendDecomposedVowel(StringBuilder sb, char jung)
    {
        if (SplitVowel.TryGetValue(jung, out var split))
        {
            sb.Append(split.Item1);
            sb.Append(split.Item2);
            return;
        }

        sb.Append(jung);
    }

    private static bool TryComposeSyllable(char cho, char jung, char jong, out char composed)
    {
        composed = '\0';

        if (!ChoIndex.TryGetValue(cho, out var l) || !JungIndex.TryGetValue(jung, out var v))
        {
            return false;
        }

        var t = 0;
        if (jong != '\0')
        {
            if (!JongIndex.TryGetValue(jong, out t))
            {
                return false;
            }
        }

        composed = (char)(0xAC00 + l * 588 + v * 28 + t);
        return true;
    }

    private static bool TryCombineInitial(char first, char second, out char combined)
    {
        return DoubleInitial.TryGetValue((first, second), out combined);
    }

    private static bool TryCombineFinal(char first, char second, out char combined)
    {
        return DoubleFinal.TryGetValue((first, second), out combined);
    }

    private static bool TryCombineVowel(char first, char second, out char combined)
    {
        return DoubleVowel.TryGetValue((first, second), out combined);
    }

    private static bool IsConsonant(char c)
    {
        return ChoIndex.ContainsKey(c) || c is 'ㄳ' or 'ㄵ' or 'ㄶ' or 'ㄺ' or 'ㄻ' or 'ㄼ' or 'ㄽ' or 'ㄾ' or 'ㄿ' or 'ㅀ' or 'ㅄ';
    }

    private static bool IsVowel(char c)
    {
        return JungIndex.ContainsKey(c);
    }

    private static bool IsValidFinal(char c)
    {
        return c != '\0' && JongIndex.ContainsKey(c);
    }

    private static Dictionary<char, int> BuildIndex(char[] source)
    {
        var map = new Dictionary<char, int>(source.Length);
        for (var i = 0; i < source.Length; i++)
        {
            if (source[i] != '\0')
            {
                map[source[i]] = i;
            }
        }

        return map;
    }
}
