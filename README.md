# Cuphead RL Dissertation Project

## Project Overview
This project implements a reinforcement learning framework to train an AI agent to master boss combat in Cuphead, with Goopy Le Grande (Slime boss) as the primary target. The architecture uses a Python-led approach for maximum accessibility via a plugin based approach

## File Structure

```
Dissertation/
├── CupheadPlugin/                     # C# plugin (game state extraction only)
│   ├── Plugin.cs                      # Simplified BepInEx/Harmony plugin
│   └── CupheadPlugin.csproj           # Project configuration
├── GameFiles/                         # Cuphead game installation
├── PythonRL/                          # Python RL components (main logic)
│   ├── environment_server.py          # Main server: state reception + input control
│   ├── test_commands_pynput.py        # Testing script for pynput input
│   ├── test_commands.py               
│   └── REQUIREMENTS.txt               # pynput dependency (>=1.7.6)
├── PDD_Dalakoti_Milind_25230406.pdf   # Dissertation document
├── global.json                        # .NET configuration
└── README.md                          # This file
```

## Setup

### 1. Installation
```bash
cd PythonRL
pip install -r REQUIREMENTS.txt
```

### Important Setup Notes
- **BepInEx Setup**: The `GameFiles/BepInEx/` folder contains the BepInEx framework with the CupheadRL plugin installed. This folder is included in the repository for setting up the modded game quickly.
- **Doorstop Files**: To enable the BepInEx modding framework, the following files in the `GameFiles/` root are also included:
  - `.doorstop_version` – indicates the Doorstop version used.
  - `doorstop_config.ini` – configuration that tells Doorstop how to load BepInEx.
  - `winhttp.dll` – Doorstop shim DLL that hijacks the game’s import to load BepInEx.
  These files are required for the plugin to load correctly and are small, non‑copyrighted support files.
- **Required DLLs**: The `CupheadPlugin/lib/` folder is intentionally empty in the repository. After cloning, you must copy the following DLLs:
  - From your Cuphead installation (`<SteamLibrary>\steamapps\common\Cuphead\Cuphead_Data\Managed\`):
    * `Assembly-CSharp.dll`
    * `UnityEngine.CoreModule.dll`
    * `UnityEngine.dll`
  - From the repository (`GameFiles/BepInEx/plugins/`):
    * `Blender.dll`  
  Place all four DLLs into `CupheadPlugin\lib\`.
  These files are large and copyrighted (except Blender.dll which is redistributable with the BepInEx plugin), so they are not included in the repository except for Blender.dll.
- **Steam Issue Fix**: If you encounter issues with Steam interfering when running the scripts, you may need to create a `steam_appid.txt` file in the game's root directory (next to `Cuphead.exe`) containing the number `222750` (Cuphead's Steam App ID). This file is NOT included in the repository to avoid potential conflicts, but you can create it locally if needed.

### 2. Testing Input Control
```bash
cd PythonRL
python test_commands_pynput.py
```
Follow the prompts to test keyboard inputs.

### 3. End-to-End Testing
1. Launch Cuphead normally (plugin should auto-load)
2. In one terminal: `cd PythonRL && python environment_server.py`
3. In another terminal: Run your test inputs or RL agent code
4. Monitor `cuphead_debug.log` for plugin status and state updates

## Current Working Mods

✅ **State Extraction Verified**: From test logs, confirmed:
- Boss HP tracking working: `[BOSS HIT] Damage: 4.0 | HP: 1196.0/1200.0 (99.7%)`
- Phase changes detected
- Player death events captured
- All Harmony patches applied successfully

✅ **Python-Led Architecture**: 
- C# plugin reduced to essential state extraction only
- Python handles decision-making and input control via pynput
- Maximized your Python expertise as requested

✅ **Input Control Framework**: 
- pynput integration for direct keyboard control
- Mapped to standard Cuphead controls (Arrow keys, Z, X, C)
- Proper press/release handling

## 📊 Technical Details

### State Extraction (C# Plugin → Python)
- **Protocol**: TCP port 5000, JSON lines
- **Data Examples**:
  - `{"event": "boss_hit", "damage": 4.0, "hp": 1196.0, "hp_pct": 99.7}`
  - `{"event": "phase_change", "phase": "BigSlime", "hp": 800.0}`
  - `{"event": "player_dead", "terminal": true, "win": false}`

### Input Control (Python → Game)
- **Method**: pynput keyboard controller (direct OS-level input)
- **Mapping**:
  - `move_left` ←→ Left Arrow
  - `move_right` ←→ Right Arrow
  - `jump` ←→ Z key
  - `shoot` ←→ X key
  - `dash` ←→ C key

### Architecture Flow
```
Game Events 
    ↓ (BepInEx/Harmony patches in Plugin.cs)
C# Plugin: Extracts state → TCP port 5000 → JSON
Python Server: environment_server.py
    ├─ Receives state: server.get_state()
    ├─ RL Agent: Processes state → selects action
    └─ Sends input: server.send_input(action, value) → pynput → Game
```

## 💡 Notes

- The C# plugin (`Plugin.cs`) is intentionally minimal - focused purely on reliable state extraction
- All complex logic (state processing, decision making, input control) resides in Python
---
