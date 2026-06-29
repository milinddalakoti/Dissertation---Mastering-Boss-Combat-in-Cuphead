using BepInEx;
using BepInEx.Logging;
using HarmonyLib;
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace CupheadPlugin
{
    [BepInPlugin("com.milind.cupheadplugin", "CupheadRL Plugin", "1.0.0")]
    public class Plugin : BaseUnityPlugin
    {
        internal static ManualLogSource Log;
        internal static string logPath = Path.Combine(
            Paths.BepInExRootPath, "cuphead_debug.log");

        // Networking for sending state TO Python (existing only)
        private static TcpClient _client;
        private static NetworkStream _stream;

        // Networking for receiving commands (restart + phase jump) FROM Python
        private static TcpListener _commandListener;
        private static Thread _commandListenerThread;
        private static bool _commandListenerRunning = false;

        // Reference to current SlimeLevel for phase control
        private static SlimeLevel _currentSlimeLevel;

        private void Awake()
        {
            Log = base.Logger;
            WriteLog("=== CupheadRL Plugin Loaded ===");

            // Connect to Python Server (for sending state ONLY)
            ConnectToServer();

            // Start unified listener for restart and phase jump commands FROM Python
            StartCommandListener();

            var harmony = new Harmony("com.milind.cupheadplugin");

            // Base level patches - KEEP ESSENTIAL ONES
            TryPatch(harmony, typeof(Level), "zHack_OnWin", typeof(LevelWinPatch), "Postfix");
            TryPatch(harmony, typeof(Level.Timeline), "DealDamage", typeof(TimelineDealDamagePatch), "Postfix");

            // Slime-specific patches
            TryPatch(harmony, typeof(SlimeLevel), "OnStateChanged", typeof(SlimeStateChangedPatch), "Postfix");

            // Patch SlimeLevel.Start to get a reference to the level instance
            TryPatch(harmony, typeof(SlimeLevel), "PartialInit", typeof(SlimeLevelPartialInitPatch), "Postfix");

            // Try catching player death
            TryPatch(harmony, typeof(AbstractPlayerController), "OnDeath", typeof(PlayerDeathPatch), "Postfix");

            // WIDE NET: Catch the most common Cuphead damage/death classes just in case
            TryPatch(harmony, AccessTools.TypeByName("DamageReceiver"), "TakeDamage", typeof(UniversalDamagePatch), "Postfix");
            TryPatch(harmony, AccessTools.TypeByName("PlayerStatsManager"), "TakeDamage", typeof(UniversalPlayerDamagePatch), "Postfix");

            WriteLog("Harmony Patching phase complete.");

            // Try to patch Level.Awake for level loaded detection
            TryPatchLevelAwake(harmony);

            // Patch Level.Update for continuous state updates (boss/player positions, phases)
            TryPatchLevelUpdate(harmony);
        }

        // Patch for detecting level awake (when level/scene loads)
        private void TryPatchLevelAwake(Harmony harmony)
        {
            try
            {
                var original = AccessTools.Method(typeof(Level), "Awake");
                if (original != null)
                {
                    var postfix = AccessTools.Method(typeof(LevelAwakePatch), "Postfix");
                    harmony.Patch(original, postfix: new HarmonyMethod(postfix));
                    WriteLog("[OK] Patched Level.Awake");
                }
                else
                {
                    WriteLog("[WARN] Level.Awake method not found");
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[ERROR] Failed to patch Level.Awake: {ex.Message}");
            }
        }

        // Patch Level.Update for continuous state updates (boss/player positions, phases)
        private void TryPatchLevelUpdate(Harmony harmony)
        {
            try
            {
                var original = AccessTools.Method(typeof(Level), "Update");
                if (original != null)
                {
                    var postfix = AccessTools.Method(typeof(LevelUpdatePatch), "Postfix");
                    harmony.Patch(original, postfix: new HarmonyMethod(postfix));
                    WriteLog("[OK] Patched Level.Update for position tracking");
                }
                else
                {
                    WriteLog("[WARN] Level.Update method not found");
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[ERROR] Failed to patch Level.Update: {ex.Message}");
            }
        }

        // Patch class for detecting when a level loads
        public static class LevelAwakePatch
        {
            public static void Postfix(Level __instance)
            {
                // Send a state update indicating level has loaded
                try
                {
                    string levelName = "Unknown";
                    try
                    {
                        levelName = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
                    }
                    catch { }

                    Plugin.WriteLog($"[LEVEL LOADED] Level: {levelName}");
                    Plugin.SendState($"{{\"event\": \"level_loaded\", \"level\": \"{levelName}\"}}");
                }
                catch (Exception ex)
                {
                    Plugin.WriteLog($"[LEVEL LOADED ERROR] Failed to send level loaded state: {ex.Message}");
                }
            }
        }

        // Patch to capture SlimeLevel instance reference for phase control
        public static class SlimeLevelPartialInitPatch
        {
            public static void Postfix(SlimeLevel __instance)
            {
                _currentSlimeLevel = __instance;
                Plugin.WriteLog($"[SLIME LEVEL] Captured SlimeLevel instance for phase control");
            }
        }

        private void OnApplicationQuit()
        {
            StopCommandListener();
            if (_stream != null && _stream.CanWrite)
            {
                try
                {
                    _stream.Close();
                }
                catch { }
            }
            if (_client != null && _client.Connected)
            {
                try
                {
                    _client.Close();
                }
                catch { }
            }
        }

        private void ConnectToServer()
        {
            try
            {
                _client = new TcpClient("127.0.0.1", 5000);
                _stream = _client.GetStream();
                WriteLog("[TCP] Successfully connected to Python RL Server.");
                SendState("{\"event\": \"connected\"}");
            }
            catch (Exception ex)
            {
                WriteLog($"[TCP] Failed to connect to server: {ex.Message}. (Is environment_server.py running?)");
            }
        }

        public static void SendState(string jsonMessage)
        {
            string payload = jsonMessage + "\n";
            try
            {
                if (_stream != null && _stream.CanWrite)
                {
                    byte[] data = Encoding.UTF8.GetBytes(payload);
                    _stream.Write(data, 0, data.Length);
                    _stream.Flush();
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[TCP ERROR] Failed to send state: {ex.Message}");
            }
        }

        // Method to close connection cleanly
        static private void CloseConnection()
        {
            if (_stream != null)
            {
                try { _stream.Close(); } catch { }
            }
            if (_client != null)
            {
                try { _client.Close(); } catch { }
            }
        }

        // Combined command listener (restart + phase jump) - unified on port 5001
        private void StartCommandListener()
        {
            try
            {
                _commandListener = new TcpListener(IPAddress.Loopback, 5001);
                _commandListener.Start();
                _commandListenerRunning = true;
                _commandListenerThread = new Thread(ListenForCommands);
                _commandListenerThread.IsBackground = true;
                _commandListenerThread.Start();
                WriteLog("[COMMAND LISTENER] Unified listener started on port 5001 for restart and phase jump commands");
            }
            catch (Exception ex)
            {
                WriteLog($"[COMMAND LISTENER ERROR] Failed to start listener: {ex.Message}");
            }
        }

        private void StopCommandListener()
        {
            _commandListenerRunning = false;
            if (_commandListenerThread != null && _commandListenerThread.IsAlive)
            {
                _commandListenerThread.Join(1000);
            }
            if (_commandListener != null)
            {
                _commandListener.Stop();
            }
        }

        private void ListenForCommands()
        {
            WriteLog("[COMMAND LISTENER] Command listener thread started");
            while (_commandListenerRunning)
            {
                try
                {
                    if (_commandListener.Pending())
                    {
                        TcpClient client = _commandListener.AcceptTcpClient();
                        Thread clientThread = new Thread(() => HandleCommandClient(client));
                        clientThread.IsBackground = true;
                        clientThread.Start();
                    }
                    else
                    {
                        Thread.Sleep(10);
                    }
                }
                catch (Exception ex)
                {
                    if (_commandListenerRunning)
                    {
                        WriteLog($"[COMMAND LISTENER ERROR] Listener error: {ex.Message}");
                    }
                }
            }
            WriteLog("[COMMAND LISTENER] Command listener thread stopped");
        }

        private void HandleCommandClient(TcpClient client)
        {
            NetworkStream stream = client.GetStream();
            byte[] buffer = new byte[1024];
            string messageData = "";

            try
            {
                WriteLog("[COMMAND LISTENER] Command client connected");
                while (_commandListenerRunning && client.Connected)
                {
                    int bytesRead = stream.Read(buffer, 0, buffer.Length);
                    if (bytesRead == 0)
                    {
                        break;
                    }

                    messageData += Encoding.UTF8.GetString(buffer, 0, bytesRead);

                    while (messageData.Contains("\n"))
                    {
                        int newlinePos = messageData.IndexOf("\n");
                        string message = messageData.Substring(0, newlinePos);
                        messageData = messageData.Substring(newlinePos + 1);

                        if (!string.IsNullOrEmpty(message))
                        {
                            ProcessCommand(message.Trim());
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[COMMAND LISTENER ERROR] Client handler error: {ex.Message}");
            }
            finally
            {
                client.Close();
                WriteLog("[COMMAND LISTENER] Command client disconnected");
            }
        }

        private void ProcessCommand(string jsonCommand)
        {
            try
            {
                Plugin.WriteLog($"[COMMAND RECEIVED] Raw: {jsonCommand}");

                // Handle restart_level command (existing)
                if (jsonCommand.Contains("restart_level"))
                {
                    RestartLevel();
                    return;
                }

                // Handle phase_jump command
                if (jsonCommand.Contains("phase_jump"))
                {
                    // Parse target phase - format: {"command": "phase_jump", "phase": "BigSlime", "set_health": true}
                    string phaseName = "";
                    bool setHealth = false;

                    // Extract phase name using simple string parsing
                    int phaseIndex = jsonCommand.IndexOf("\"phase\"");
                    if (phaseIndex != -1)
                    {
                        int valueStart = jsonCommand.IndexOf("\"", phaseIndex + 7);
                        int valueEnd = jsonCommand.IndexOf("\"", valueStart + 1);
                        if (valueStart != -1 && valueEnd != -1)
                        {
                            phaseName = jsonCommand.Substring(valueStart + 1, valueEnd - valueStart - 1);
                        }
                    }

                    // Extract set_health parameter
                    int setHealthIdx = jsonCommand.IndexOf("\"set_health\"");
                    if (setHealthIdx != -1)
                    {
                        // JSON format: "set_health":true (or with spaces)
                        // Start after "set_health": (10 + 2 = 12 chars minimum, but find the actual value)
                        int valueStart = jsonCommand.IndexOf(':', setHealthIdx);
                        if (valueStart != -1)
                        {
                            string healthStr = jsonCommand.Substring(valueStart + 1).Split('}')[0].Trim();
                            setHealth = healthStr == "true";
                            Plugin.WriteLog($"[PHASE JUMP DEBUG] set_health parsing: setHealthIdx={setHealthIdx}, healthStr='{healthStr}', result={setHealth}");
                        }
                    }

                    if (!string.IsNullOrEmpty(phaseName))
                    {
                        JumpToPhase(phaseName, setHealth);
                    }
                    else
                    {
                        WriteLog("[PHASE JUMP ERROR] No phase specified in command");
                    }
                    return;
                }

                WriteLog($"[COMMAND LISTENER WARN] Unknown command: {jsonCommand}");
            }
            catch (Exception ex)
            {
                WriteLog($"[COMMAND LISTENER ERROR] Failed to process command '{jsonCommand}': {ex.Message}");
            }
        }

        // Phase Jump Functionality
        private void JumpToPhase(string phaseName, bool setHealthToThreshold = false)
        {
            Plugin.WriteLog($"[PHASE JUMP] Attempting to jump to phase: {phaseName} (set_health={setHealthToThreshold})");

            try
            {
                if (_currentSlimeLevel == null)
                {
                    Plugin.WriteLog("[PHASE JUMP ERROR] No SlimeLevel instance captured. Is the boss fight active?");
                    return;
                }

                // Map phase name to States enum value
                LevelProperties.Slime.States targetState = LevelProperties.Slime.States.Main;
                if (phaseName == "BigSlime")
                    targetState = LevelProperties.Slime.States.BigSlime;
                else if (phaseName == "Tombstone")
                    targetState = LevelProperties.Slime.States.Tombstone;
                else if (phaseName == "Main" || phaseName == "Generic")
                    targetState = LevelProperties.Slime.States.Main;
                else
                {
                    Plugin.WriteLog($"[PHASE JUMP ERROR] Unknown phase: {phaseName}");
                    return;
                }

                // Get current state to check if we're already in target phase
                var props = Traverse.Create(_currentSlimeLevel).Field("properties").GetValue<LevelProperties.Slime>();
                if (props == null)
                {
                    Plugin.WriteLog("[PHASE JUMP ERROR] Could not access Slime properties");
                    return;
                }

                if (props.CurrentState.stateName == targetState)
                {
                    Plugin.WriteLog($"[PHASE JUMP] Already in target phase: {phaseName}");
                    return;
                }

                // Find the target state index and health trigger
                int targetStateIndex = -1;
                float targetHealthTrigger = 1.0f; // Default to full health
                for (int i = 0; i < 3; i++) // Slime has max 3 phases
                {
                    try
                    {
                        var stateField = Traverse.Create(props).Field("states");
                        var statesArray = stateField.GetValue<LevelProperties.Slime.State[]>();
                        if (statesArray != null && i < statesArray.Length)
                        {
                            if (statesArray[i].stateName == targetState)
                            {
                                targetStateIndex = i;
                                targetHealthTrigger = statesArray[i].healthTrigger;
                                break;
                            }
                        }
                    }
                    catch
                    {
                        // Continue to next index
                    }
                }

                if (targetStateIndex == -1)
                {
                    Plugin.WriteLog($"[PHASE JUMP ERROR] Target phase {phaseName} not found in state array");
                    return;
                }

                // Use Traverse to set stateIndex (private field)
                Traverse.Create(props).Field("stateIndex").SetValue(targetStateIndex);

                // Handle phase transition manually (avoid calling OnStateChanged which stops coroutines)
                // Note: we do NOT call OnStateChanged() because it calls StopAllCoroutines()
                // Instead we manually call the transformation methods
                try
                {
                    if (targetState == LevelProperties.Slime.States.BigSlime)
                    {
                        // Stop existing coroutines (like slimePattern_cr) that use pattern arrays
                        // This prevents IndexOutOfRangeException on empty BigSlime pattern arrays
                        _currentSlimeLevel.StopAllCoroutines();
                        Plugin.WriteLog("[PHASE JUMP] Stopped existing coroutines to prevent pattern array errors");

                        // Set health to phase threshold if requested
                        if (setHealthToThreshold && targetHealthTrigger < 1.0f)
                        {
                            float totalHealth = _currentSlimeLevel.timeline.health;
                            float targetHealth = totalHealth * targetHealthTrigger;
                            float targetDamage = totalHealth - targetHealth;
                            Plugin.WriteLog($"[PHASE JUMP DEBUG] Total health: {totalHealth}, targetHealth: {targetHealth}, targetDamage: {targetDamage}");
                            bool fieldSet = false;

                            // Try multiple approaches to set the damage value
                            // Approach 1: Try to find the backing field
                            var damageField = AccessTools.Field(typeof(Level.Timeline), "<damage>k__BackingField");
                            if (damageField != null)
                            {
                                damageField.SetValue(_currentSlimeLevel.timeline, targetDamage);
                                fieldSet = true;
                            }

                            // Approach 2: Use reflection to set private property setter
                            if (!fieldSet)
                            {
                                try
                                {
                                    var damageProperty = AccessTools.Property(typeof(Level.Timeline), "damage");
                                    var setter = damageProperty.GetSetMethod(true);
                                    if (setter != null)
                                    {
                                        setter.Invoke(_currentSlimeLevel.timeline, new object[] { targetDamage });
                                        fieldSet = true;
                                    }
                                }
                                catch (Exception ex)
                                {
                                    Plugin.WriteLog($"[PHASE JUMP WARN] Property setter failed: {ex.Message}");
                                }
                            }

                            // Approach 3: Use Traverse with backing field name
                            if (!fieldSet)
                            {
                                try
                                {
                                    Traverse.Create(_currentSlimeLevel.timeline).Field("damage").SetValue(targetDamage);
                                    fieldSet = true;
                                }
                                catch (Exception ex)
                                {
                                    Plugin.WriteLog($"[PHASE JUMP WARN] Traverse damage field failed: {ex.Message}");
                                }
                            }

                            Plugin.WriteLog($"[PHASE JUMP] Set boss HP to {targetHealth:F1} (threshold: {targetHealthTrigger:P0}), damage field set: {fieldSet}");
                        }

                        // IMPORTANT: Set reachedBigSlimeState BEFORE calling TurnBig
                        // This ensures the bigSlime entity knows the proper state during transformation
                        Traverse.Create(_currentSlimeLevel).Field("reachedBigSlimeState").SetValue(true);

                        // Get smallSlime reference
                        var smallSlime = Traverse.Create(_currentSlimeLevel).Field("smallSlime").GetValue<SlimeLevelSlime>();

                        // Set bigSlime property state BEFORE transformation (TurnBig calls bigSlime.StartJump which uses CurrentPropertyState)
                        var bigSlime = Traverse.Create(_currentSlimeLevel).Field("bigSlime").GetValue<SlimeLevelSlime>();
                        if (bigSlime != null)
                        {
                            var bigSlimePropertyStateField = Traverse.Create(bigSlime).Field("CurrentPropertyState");
                            bigSlimePropertyStateField.SetValue(props.CurrentState);
                        }

                        // Stop smallSlime coroutines before transformation to prevent crashes
                        if (smallSlime != null)
                        {
                            smallSlime.StopAllCoroutines();
                            var turnBigMethod = AccessTools.Method(typeof(SlimeLevelSlime), "TurnBig");
                            turnBigMethod?.Invoke(smallSlime, new object[] { });
                            Plugin.WriteLog("[PHASE JUMP] Called TurnBig on smallSlime for immediate transformation");
                        }
                        // Note: bigSlime's own jump_cr is started inside TurnBig via StartJump()
                        // The level's pattern coroutine is NOT used for BigSlime (empty patterns array)
                    }
                    else if (targetState == LevelProperties.Slime.States.Tombstone)
                    {
                        // Stop existing coroutines on the level and entities to prevent errors
                        _currentSlimeLevel.StopAllCoroutines();

                        // Set health to phase threshold if requested
                        if (setHealthToThreshold && targetHealthTrigger < 1.0f)
                        {
                            float totalHealth = _currentSlimeLevel.timeline.health;
                            float targetHealth = totalHealth * targetHealthTrigger;
                            float targetDamage = totalHealth - targetHealth;
                            Plugin.WriteLog($"[PHASE JUMP DEBUG] Total health: {totalHealth}, targetHealth: {targetHealth}, targetDamage: {targetDamage}");
                            bool fieldSet = false;

                            // Try multiple approaches to set the damage value
                            var damageField = AccessTools.Field(typeof(Level.Timeline), "<damage>k__BackingField");
                            if (damageField != null)
                            {
                                damageField.SetValue(_currentSlimeLevel.timeline, targetDamage);
                                fieldSet = true;
                            }
                            if (!fieldSet)
                            {
                                try
                                {
                                    var damageProperty = AccessTools.Property(typeof(Level.Timeline), "damage");
                                    var setter = damageProperty.GetSetMethod(true);
                                    if (setter != null)
                                    {
                                        setter.Invoke(_currentSlimeLevel.timeline, new object[] { targetDamage });
                                        fieldSet = true;
                                    }
                                }
                                catch (Exception ex)
                                {
                                    Plugin.WriteLog($"[PHASE JUMP WARN] Property setter failed: {ex.Message}");
                                }
                            }
                            if (!fieldSet)
                            {
                                try
                                {
                                    Traverse.Create(_currentSlimeLevel.timeline).Field("damage").SetValue(targetDamage);
                                    fieldSet = true;
                                }
                                catch (Exception ex)
                                {
                                    Plugin.WriteLog($"[PHASE JUMP WARN] Traverse damage field failed: {ex.Message}");
                                }
                            }
                            Plugin.WriteLog($"[PHASE JUMP] Set boss HP to {targetHealth:F1} (threshold: {targetHealthTrigger:P0}), damage field set: {fieldSet}");
                        }

                        // IMPORTANT: Set reachedBigSlimeState BEFORE any transformation
                        // This is required for Tombstone transition since it expects BigSlime state to be active
                        Traverse.Create(_currentSlimeLevel).Field("reachedBigSlimeState").SetValue(true);

                        // Get bigSlime reference for transformation
                        var bigSlime = Traverse.Create(_currentSlimeLevel).Field("bigSlime").GetValue<SlimeLevelSlime>();

                        // First, if we're coming from Main phase (smallSlime still active), transform to BigSlime first
                        var smallSlime = Traverse.Create(_currentSlimeLevel).Field("smallSlime").GetValue<SlimeLevelSlime>();
                        if (smallSlime != null)
                        {
                            smallSlime.StopAllCoroutines();
                            var turnBigMethod = AccessTools.Method(typeof(SlimeLevelSlime), "TurnBig");
                            turnBigMethod?.Invoke(smallSlime, new object[] { });
                            Plugin.WriteLog("[PHASE JUMP] Transformed smallSlime to bigSlime before tombstone");
                        }

                        // Stop bigSlime coroutines too before death transform
                        if (bigSlime != null)
                        {
                            bigSlime.StopAllCoroutines();
                            var deathTransformMethod = AccessTools.Method(typeof(SlimeLevelSlime), "DeathTransform");
                            deathTransformMethod?.Invoke(bigSlime, new object[] { });
                            Plugin.WriteLog("[PHASE JUMP] Called DeathTransform on bigSlime for Tombstone");
                        }
                        else
                        {
                            Plugin.WriteLog("[PHASE JUMP WARN] bigSlime reference is null, cannot perform DeathTransform");
                        }
                    }
                }
                catch (Exception ex)
                {
                    Plugin.WriteLog($"[PHASE JUMP WARN] Could not handle phase transformation: {ex.Message}");
                }

                Plugin.WriteLog($"[PHASE JUMP SUCCESS] Jumped to phase: {phaseName} (index {targetStateIndex})");

                // Send confirmation
                SendState($"{{\"event\": \"phase_jump\", \"target_phase\": \"{phaseName}\", \"success\": true}}");
            }
            catch (Exception ex)
            {
                Plugin.WriteLog($"[PHASE JUMP ERROR] Failed: {ex.Message}");
                Plugin.SendState($"{{\"event\": \"phase_jump\", \"target_phase\": \"{phaseName}\", \"success\": false, \"error\": \"{ex.Message}\"}}");
            }
        }

        private void RestartLevel()
        {
            WriteLog("[RESTART LISTENER] Restarting level...");
            try
            {
                // Try to reload the current scene using Unity's SceneManager
                // Note: This might need adjustment based on how Cuphead loads levels
                string currentSceneName = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
                UnityEngine.SceneManagement.SceneManager.LoadScene(currentSceneName);
                WriteLog("[RESTART LISTENER] Level restarted successfully");
            }
            catch (Exception ex)
            {
                WriteLog($"[RESTART LISTENER ERROR] Failed to restart level: {ex.Message}");
                // Fallback: Try to find Cuphead-specific restart method
                TryCupheadSpecificRestart();
            }
        }

        private void TryCupheadSpecificRestart()
        {
            WriteLog("[RESTART LISTENER] Trying Cuphead-specific restart methods...");
            // This would require analyzing Cuphead's internal methods via dnSpy
            // For now, we'll log that we need to implement this properly
            WriteLog("[RESTART LISTENER WARN] Cuphead-specific restart not implemented yet");
        }

        private void TryPatch(Harmony harmony, Type originalType, string originalMethod, Type patchType, string patchMethod)
        {
            try
            {
                if (originalType == null)
                {
                    WriteLog($"[WARN] Type not found for patching: {originalType?.Name}.{originalMethod}");
                    return;
                }
                var original = AccessTools.Method(originalType, originalMethod);
                if (original == null)
                {
                    WriteLog($"[WARN] Method {originalType.Name}.{originalMethod} not found.");
                    return;
                }
                var postfix = AccessTools.Method(patchType, patchMethod);
                harmony.Patch(original, postfix: new HarmonyMethod(postfix));
                WriteLog($"[OK] Patched {originalType.Name}.{originalMethod}");
            }
            catch (Exception ex)
            {
                WriteLog($"[ERROR] Failed to patch {originalType?.Name}.{originalMethod}: {ex.Message}");
            }
        }

        public static void WriteLog(string message)
        {
            string line = $"[{DateTime.Now:HH:mm:ss}] {message}";
            Log?.LogInfo(line);
            File.AppendAllText(logPath, line + Environment.NewLine);
        }
    }

    public static class SlimeStateChangedPatch
    {
        public static void Postfix(SlimeLevel __instance)
        {
            string phase = "Unknown";
            try
            {
                var props = Traverse.Create(__instance)
                    .Field("properties")
                    .GetValue<LevelProperties.Slime>();
                phase = props?.CurrentState?.stateName.ToString() ?? "Unknown";
            }
            catch { }

            float currentHp = (__instance.timeline != null) ? __instance.timeline.health - __instance.timeline.damage : 0;
            Plugin.WriteLog($"[PHASE CHANGE] â†’ {phase} | HP: {currentHp:F1}");
            Plugin.SendState($"{{\"event\": \"phase_change\", \"phase\": \"{phase}\", \"hp\": {currentHp}}}");
        }
    }

    public static class TimelineDealDamagePatch
    {
        public static void Postfix(Level.Timeline __instance, float damage)
        {
            float totalHp = __instance.health;
            float currentHp = totalHp - __instance.damage;
            float hpPct = (totalHp > 0) ? (currentHp / totalHp) * 100f : 0f;
            Plugin.WriteLog($"[BOSS HIT] Damage: {damage:F1} | HP: {currentHp:F1}/{totalHp:F1} ({hpPct:F1}%)");
            Plugin.SendState($"{{\"event\": \"boss_hit\", \"damage\": {damage}, \"hp\": {currentHp}, \"hp_pct\": {hpPct}}}");
        }
    }

    public static class LevelWinPatch
    {
        public static void Postfix()
        {
            Plugin.WriteLog("[BOSS DEAD] Episode terminal. Level Won.");
            Plugin.SendState("{\"event\": \"boss_dead\", \"terminal\": true, \"win\": true}");
        }
    }

    public static class PlayerDeathPatch
    {
        public static void Postfix()
        {
            Plugin.WriteLog("[PLAYER DEAD] AbstractPlayerController.OnDeath fired.");
            Plugin.SendState("{\"event\": \"player_dead\", \"terminal\": true, \"win\": false}");
        }
    }

    public static class UniversalDamagePatch
    {
        public static void Postfix()
        {
            Plugin.WriteLog("[WIDE NET] DamageReceiver.TakeDamage fired.");
            Plugin.SendState("{\"event\": \"wide_net_boss_hit\"}");
        }
    }

    public static class UniversalPlayerDamagePatch
    {
        public static void Postfix()
        {
            Plugin.WriteLog("[WIDE NET] PlayerStatsManager.TakeDamage fired.");
            Plugin.SendState("{\"event\": \"wide_net_player_hit\"}");
        }
    }

    // Patch for continuous state updates (boss position/phase, player position)
    public static class LevelUpdatePatch
    {
        private const float STATE_SEND_INTERVAL = 0.1f;
        private static float _lastStateSendTime = 0f;

        public static void Postfix(Level __instance)
        {
            // Throttle updates to avoid network spam
            if (Time.time - _lastStateSendTime < STATE_SEND_INTERVAL)
            {
                return;
            }
            _lastStateSendTime = Time.time;

            try
            {
                var bossPositions = GetBossPositions(__instance);
                var playerPositions = GetPlayerPositions();
                float bossHp = GetBossHealth(__instance);
                float bossHpPct = (bossHp > 0) ? (__instance.timeline.health - __instance.timeline.damage) / __instance.timeline.health * 100f : 0f;
                string bossPhase = GetBossPhase(__instance);

                Plugin.SendState($"{{\"event\": \"state_update\", \"boss_positions\": {JsonEncodePositions(bossPositions)}, \"boss_phase\": \"{bossPhase}\", \"player_positions\": {JsonEncodePlayerPositions(playerPositions)}, \"boss_hp\": {bossHp}, \"boss_hp_pct\": {bossHpPct:F1}, \"level_time\": {__instance.LevelTime:F2}}}");
            }
            catch (Exception ex)
            {
                Plugin.WriteLog($"[UPDATE ERROR] Failed to send state update: {ex.Message}");
            }
        }

        private static List<Dictionary<string, object>> GetBossPositions(Level level)
        {
            var positions = new List<Dictionary<string, object>>();

            // Slime-specific handling - get active entity position based on current phase
            if (level is SlimeLevel slimeLevel)
            {
                try
                {
                    string currentPhase = GetBossPhase(level);

                    // Determine which entity is active based on phase
                    if (currentPhase == "Main" || currentPhase == "Generic")
                    {
                        var smallSlime = Traverse.Create(slimeLevel).Field("smallSlime").GetValue<SlimeLevelSlime>();
                        if (smallSlime != null && smallSlime.gameObject.activeInHierarchy)
                        {
                            var dict = new Dictionary<string, object>();
                            dict["x"] = smallSlime.transform.position.x;
                            dict["y"] = smallSlime.transform.position.y;
                            positions.Add(dict);
                        }
                    }
                    else if (currentPhase == "BigSlime")
                    {
                        var bigSlime = Traverse.Create(slimeLevel).Field("bigSlime").GetValue<SlimeLevelSlime>();
                        if (bigSlime != null && bigSlime.gameObject.activeInHierarchy)
                        {
                            var dict = new Dictionary<string, object>();
                            dict["x"] = bigSlime.transform.position.x;
                            dict["y"] = bigSlime.transform.position.y;
                            positions.Add(dict);
                        }
                    }
                    else if (currentPhase == "Tombstone")
                    {
                        var tombStone = Traverse.Create(slimeLevel).Field("tombStone").GetValue<SlimeLevelTombstone>();
                        if (tombStone != null && tombStone.gameObject.activeInHierarchy)
                        {
                            var dict = new Dictionary<string, object>();
                            dict["x"] = tombStone.transform.position.x;
                            dict["y"] = tombStone.transform.position.y;
                            positions.Add(dict);
                        }
                    }
                }
                catch (Exception ex)
                {
                    Plugin.WriteLog($"[SLIME POSITION ERROR] {ex.Message}");
                }
            }

            // Generic fallback for other bosses
            if (positions.Count == 0)
            {
                try
                {
                    var entities = UnityEngine.Object.FindObjectsOfType<AbstractLevelEntity>();
                    foreach (var entity in entities)
                    {
                        if (entity != null && entity.gameObject.activeInHierarchy)
                        {
                            var dict = new Dictionary<string, object>();
                            dict["x"] = entity.transform.position.x;
                            dict["y"] = entity.transform.position.y;
                            positions.Add(dict);
                        }
                    }
                }
                catch (Exception ex)
                {
                    Plugin.WriteLog($"[GENERIC POSITION ERROR] {ex.Message}");
                }
            }

            return positions;
        }

        private static string GetBossPhase(Level level)
        {
            try
            {
                // Try to get phase for Slime levels
                if (level is SlimeLevel slimeLevel)
                {
                    var props = Traverse.Create(slimeLevel).Field("properties").GetValue<LevelProperties.Slime>();
                    if (props != null && props.CurrentState != null)
                    {
                        return props.CurrentState.stateName.ToString();
                    }
                }
            }
            catch (Exception ex)
            {
                Plugin.WriteLog($"[PHASE ERROR] Could not get boss phase: {ex.Message}");
            }
            return "Unknown";
        }

        private static List<Dictionary<string, object>> GetPlayerPositions()
        {
            var positions = new List<Dictionary<string, object>>();

            for (int i = 0; i < 2; i++)
            {
                var playerId = (PlayerId)i;
                var player = PlayerManager.GetPlayer(playerId);
                var dict = new Dictionary<string, object>();
                dict["player_id"] = i + 1;

                // Only report dead if player exists and is actually dead from combat
                // During level load, player is null but that's NOT death
                if (player == null)
                {
                    // Player hasn't spawned yet - NOT dead, just waiting for join animation
                    dict["is_dead"] = "false";
                    dict["x"] = 0;
                    dict["y"] = 0;
                    dict["health"] = 3;  // Full health pending spawn
                }
                else
                {
                    dict["is_dead"] = player.IsDead.ToString().ToLower();
                    if (!player.IsDead)
                    {
                        dict["x"] = player.center.x;
                        dict["y"] = player.center.y;
                        int playerHealth = 0;
                        try
                        {
                            var stats = player.stats;
                            Plugin.WriteLog($"[PLAYER HEALTH DEBUG] player={player != null}, stats={stats != null}, playerId={player?.id}");
                            if (stats != null)
                            {
                                playerHealth = stats.Health;
                                Plugin.WriteLog($"[PLAYER HEALTH DEBUG] Read health value: {playerHealth}, type=int");
                            }
                            else
                            {
                                playerHealth = 3;  // No stats yet, assume full health
                                Plugin.WriteLog($"[PLAYER HEALTH DEBUG] No stats, setting health=3");
                            }
                        }
                        catch (Exception ex)
                        {
                            playerHealth = 3;
                            Plugin.WriteLog($"[PLAYER HEALTH ERROR] Failed to get health: {ex.Message}, setting to 3");
                        }
                        Plugin.WriteLog($"[PLAYER HEALTH FINAL] Setting dict[\"health\"] = {playerHealth}");
                        dict["health"] = playerHealth;
                    }
                    else
                    {
                        dict["x"] = 0;
                        dict["y"] = 0;
                        dict["health"] = 0;
                    }
                }
                positions.Add(dict);
            }

            return positions;
        }

        private static float GetBossHealth(Level level)
        {
            try
            {
                return level.timeline != null ? level.timeline.health - level.timeline.damage : 0f;
            }
            catch
            {
                return 0f;
            }
        }

        private static string JsonEncodePositions(List<Dictionary<string, object>> positions)
        {
            var sb = new System.Text.StringBuilder();
            sb.Append("[");
            for (int i = 0; i < positions.Count; i++)
            {
                if (i > 0) sb.Append(",");
                sb.Append("{");
                sb.Append($"\"x\": {positions[i]["x"]}, \"y\": {positions[i]["y"]}");
                sb.Append("}");
            }
            sb.Append("]");
            return sb.ToString();
        }

        private static string JsonEncodePlayerPositions(List<Dictionary<string, object>> positions)
        {
            var sb = new System.Text.StringBuilder();
            sb.Append("[");
            for (int i = 0; i < positions.Count; i++)
            {
                if (i > 0) sb.Append(",");
                sb.Append("{");
                var health = positions[i].ContainsKey("health") ? positions[i]["health"] : "MISSING";
                Plugin.WriteLog($"[JSON DEBUG] Player {i} - has health key: {positions[i].ContainsKey("health")}, value: {health}");
                sb.Append($"\"player_id\": {positions[i]["player_id"]}, \"is_dead\": \"{positions[i]["is_dead"]}\", \"x\": {positions[i]["x"]}, \"y\": {positions[i]["y"]}, \"health\": {health}");
                sb.Append("}");
            }
            sb.Append("]");
            return sb.ToString();
        }
    }
}
