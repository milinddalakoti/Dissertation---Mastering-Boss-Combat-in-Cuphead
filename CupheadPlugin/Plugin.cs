using BepInEx;
using BepInEx.Logging;
using HarmonyLib;
using System;
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

        // Networking for receiving restart commands FROM Python (new - minimal)
        private static TcpListener _restartListener;
        private static Thread _restartListenerThread;
        private static bool _restartListenerRunning = false;

        private void Awake()
        {
            Log = base.Logger;
            WriteLog("=== CupheadRL Plugin Loaded ===");

            // Connect to Python Server (for sending state ONLY)
            ConnectToServer();

            // Start minimal listener for restart commands FROM Python
            StartRestartListener();

            var harmony = new Harmony("com.milind.cupheadplugin");

            // Base level patches - KEEP ESSENTIAL ONES
            TryPatch(harmony, typeof(Level), "zHack_OnWin", typeof(LevelWinPatch), "Postfix");
            TryPatch(harmony, typeof(Level.Timeline), "DealDamage", typeof(TimelineDealDamagePatch), "Postfix");

            // Slime-specific patches
            TryPatch(harmony, typeof(SlimeLevel), "OnStateChanged", typeof(SlimeStateChangedPatch), "Postfix");

            // Try catching player death
            TryPatch(harmony, typeof(AbstractPlayerController), "OnDeath", typeof(PlayerDeathPatch), "Postfix");

            // WIDE NET: Catch the most common Cuphead damage/death classes just in case
            TryPatch(harmony, AccessTools.TypeByName("DamageReceiver"), "TakeDamage", typeof(UniversalDamagePatch), "Postfix");
            TryPatch(harmony, AccessTools.TypeByName("PlayerStatsManager"), "TakeDamage", typeof(UniversalPlayerDamagePatch), "Postfix");

            WriteLog("Harmony Patching phase complete.");
        }

        private void OnApplicationQuit()
        {
            StopRestartListener();
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

        // Restart Listener Functionality (Minimal - for restart commands only)
        private void StartRestartListener()
        {
            try
            {
                _restartListener = new TcpListener(IPAddress.Loopback, 5001); // Port for restart commands
                _restartListener.Start();
                _restartListenerRunning = true;
                _restartListenerThread = new Thread(ListenForRestartCommands);
                _restartListenerThread.IsBackground = true;
                _restartListenerThread.Start();
                WriteLog("[RESTART LISTENER] Listener started on port 5001");
            }
            catch (Exception ex)
            {
                WriteLog($"[RESTART LISTENER ERROR] Failed to start listener: {ex.Message}");
            }
        }

        private void StopRestartListener()
        {
            _restartListenerRunning = false;
            if (_restartListenerThread != null && _restartListenerThread.IsAlive)
            {
                _restartListenerThread.Join(1000); // Wait up to 1 second for thread to finish
            }
            if (_restartListener != null)
            {
                _restartListener.Stop();
            }
        }

        private void ListenForRestartCommands()
        {
            WriteLog("[RESTART LISTENER] Listener thread started");
            while (_restartListenerRunning)
            {
                try
                {
                    if (_restartListener.Pending())
                    {
                        TcpClient client = _restartListener.AcceptTcpClient();
                        Thread clientThread = new Thread(() => HandleRestartCommandClient(client));
                        clientThread.IsBackground = true;
                        clientThread.Start();
                    }
                    else
                    {
                        Thread.Sleep(10); // Avoid busy waiting
                    }
                }
                catch (Exception ex)
                {
                    if (_restartListenerRunning)
                    {
                        WriteLog($"[RESTART LISTENER ERROR] Listener error: {ex.Message}");
                    }
                }
            }
            WriteLog("[RESTART LISTENER] Listener thread stopped");
        }

        private void HandleRestartCommandClient(TcpClient client)
        {
            NetworkStream stream = client.GetStream();
            byte[] buffer = new byte[1024];
            string messageData = "";

            try
            {
                WriteLog("[RESTART LISTENER] Client connected");
                while (_restartListenerRunning && client.Connected)
                {
                    int bytesRead = stream.Read(buffer, 0, buffer.Length);
                    if (bytesRead == 0)
                    {
                        break; // Client disconnected
                    }

                    messageData += Encoding.UTF8.GetString(buffer, 0, bytesRead);

                    // Process complete messages (separated by newline)
                    while (messageData.Contains("\n"))
                    {
                        int newlinePos = messageData.IndexOf("\n");
                        string message = messageData.Substring(0, newlinePos);
                        messageData = messageData.Substring(newlinePos + 1);

                        if (!string.IsNullOrEmpty(message))
                        {
                            ProcessRestartCommand(message.Trim());
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[RESTART LISTENER ERROR] Client handler error: {ex.Message}");
            }
            finally
            {
                client.Close();
                WriteLog("[RESTART LISTENER] Client disconnected");
            }
        }

        private void ProcessRestartCommand(string jsonCommand)
        {
            try
            {
                // Simple JSON parsing for restart command - handle variations in formatting
                // Expected format: {"command":"restart_level"} or {"command": "restart_level"}
                if (jsonCommand.Contains("restart_level"))
                {
                    RestartLevel();
                }
                else
                {
                    WriteLog($"[RESTART LISTENER WARN] Unknown command: {jsonCommand}");
                }
            }
            catch (Exception ex)
            {
                WriteLog($"[RESTART LISTENER ERROR] Failed to process command '{jsonCommand}': {ex.Message}");
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
            Plugin.WriteLog($"[PHASE CHANGE] → {phase} | HP: {currentHp:F1}");
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
}