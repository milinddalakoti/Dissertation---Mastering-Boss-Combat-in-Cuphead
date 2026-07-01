# Cuphead RL Dissertation Project

## Project Overview
This project implements a reinforcement learning framework to train an AI agent to master boss combat in Cuphead, with Goopy Le Grande (Slime boss) as the primary target. The architecture uses a Python-led approach for maximum accessibility and flexibility via a plugin based approach

## File Structure

```
Dissertation/
├── CupheadPlugin/                     # C# plugin (game state extraction only)
│   ├── Plugin.cs                      # Simplified BepInEx/Harmony plugin with TCP reconnection
│   └── CupheadPlugin.csproj           # Project configuration
├── GameFiles/                         # Cuphead game installation
├── PythonRL/                          # Python RL components (main logic)
│   ├── environment_server.py          # Main server: state reception + input control
│   ├── ppo_training.py                # PPO training loop with convergence tracking
│   ├── live_display.py                # Live training dashboard
│   ├── REQUIREMENTS.txt               # Dependencies (pynput>=1.7.6, gymnasium, stable-baselines3, torch)
│   └── runs_data/                     # Training outputs (auto-created)
│       ├── csv_logs/                  # Episode CSV logs (CUPHEAD_DATE_TIME_EPISODES.csv)
│       └── training_logs/             # Training logs (CUPHEAD_DATE_TIME_EPISODES.log + RESTART_DEBUG_*.log)
├── PDD_Dalakoti_Milind_25230406.pdf   # Dissertation document
├── global.json                        # .NET configuration
└── README.md                          # This file
```

## Build & Run Commands

### Build C# Plugin
```bash
dotnet build CupheadPlugin/CupheadPlugin.csproj --configuration Debug --output CupheadPlugin/bin/Debug/net35
cp CupheadPlugin/bin/Debug/net35/CupheadPlugin.dll GameFiles/BepInEx/plugins/CupheadPlugin/
```
**Note**: Always rebuild and copy DLL after any Plugin.cs changes. The plugin includes automatic TCP reconnection when the connection drops during level restarts.

### Install Python Dependencies
```bash
cd PythonRL
pip install -r REQUIREMENTS.txt
# Requires: pynput>=1.7.6, numpy, gymnasium, stable-baselines3, torch
```

### Run Training
```bash
cd PythonRL
python ppo_training.py    # Starts environment_server internally
# Then launch Cuphead and navigate to Goopy Le Grande
```

### Run Standalone Server
```bash
cd PythonRL
python environment_server.py    # For manual input testing
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

✅ **Restart on Boss/Player Death**: 
- environment_server.py listens for death events 
- initiates restart command for easy replayability

✅ **Integrated Random Boss Testing**: 
- Built-in random action generator for input path testing
- 3-second delay before actions start (matches fight begin time)
- Expanded action set including aim, duck/crouch, and directional controls
- Automatic restart after level completion for continuous testing

✅ **Position tracking for player and boss**: 
- Added position and phase tracking for boss fights
```{"event": "state_update", "boss_positions": [{"x": 369, "y": -247}], "boss_phase": "Main"```
- Added position tracking for player
```"player_positions": [{"player_id": 1, "is_dead": false, "x": -410, "y": -187.9969}, {"player_id": 2, "is_dead": true, "x": 0, "y": 0}]```

✅ **Phase Jump functionality**: 
- Can jump to specific boss phases (Main, BigSlime, Tombstone) on level load
- Configurable via `auto_phase_jump` and `auto_phase_set_health` in `environment_server.py`
- When `set_health=true`, boss HP is set to phase-appropriate threshold for realistic training
- Normal mode: BigSlime (76% HP), Tombstone (31% HP)

✅ **TCP Auto-Reconnection (Plugin.cs)**: 
- Automatic reconnection (5 retries, 1s delay) when TCP connection drops during scene transitions
- Connection validity checking before each state send
- Prevents training disruption during level restarts

✅ **Robust Restart Handling (ppo_training.py)**: 
- Fixed lock ordering deadlock that caused 8-hour training hangs
- Extended restart timeout to 90s with connection monitoring
- Defensive state clearing before/after episodes
- Dedicated restart debug logger (RESTART_DEBUG_*.log)

✅ **Convergence Tracking & Logging**: 
- Win rate, avg damage, avg duration logged every 50 episodes
- Episode outcome tracking for plateau detection
- Heartbeat logging every 100 steps confirms training alive
- CSV logs organized in runs_data/csv_logs/ with timestamp naming

✅ **Socket Timeout Protection (environment_server.py)**:
- 1s timeout on accept/recv allows clean thread shutdown
- 5s timeout on command socket (restart/phase jump) prevents indefinite blocking
- Periodic self.running checks enable graceful shutdown




##  Technical Details

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
  - `aim_up` ←→ Up Arrow
  - `duck_crouch` ←→ Down Arrow
  - `jump` ←→ Z key
  - `shoot` ←→ X key
  - `dash` ←→ Left Shift
  - `directional_aim` ←→ C key

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

### Phase Jump Configuration
Set in `PythonRL/environment_server.py`:
```python
server.auto_phase_jump = "BigSlime"      # Auto-jump on level load: "Main", "BigSlime", "Tombstone", or None
server.auto_phase_set_health = True      # Also set HP to phase threshold when jumping
```

**Phase Thresholds (Normal difficulty)**:
- Main: 100% HP (full health)
- BigSlime: 76% HP (~912/1200)
- Tombstone: 31% HP (~372/1200)

## Notes

- The C# plugin (`Plugin.cs`) is intentionally minimal - focused purely on reliable state extraction
- All complex logic (state processing, decision making, input control) resides in Python
---

## PPO Training Details

### Observation Space (7 normalized dimensions)
```python
[
    boss_x/500, boss_y/500,      # Boss position (-1 to 1)
    boss_hp/1200, boss_hp_pct/100, # Boss HP (0 to 1)
    player_x/500, player_y/500,  # Player position (-1 to 1)
    player_is_dead                # Binary (0=alive, 1=dead)
]
```

### Action Space (8 discrete)
0. move_left, 1. move_right, 2. aim_up, 3. duck_crouch
4. jump, 5. shoot, 6. dash, 7. directional_aim

### Reward Function
- `+100` for boss defeat (win)
- `-10` for player death
- `+0.5` per boss hit
- `+0.01` per step (survival bonus)

### Key Hyperparameters (Stable-Baselines3 PPO)
- `learning_rate=3e-4` - Learning step size
- `n_steps=2048` - Trajectory length before update
- `batch_size=64` - Mini-batch size
- `gamma=0.99` - Discount factor
- `gae_lambda=0.95` - Advantage estimation
- `clip_range=0.2` - PPO clipping (prevents large policy updates)

### Convergence Detection
Monitored every 50 episodes:
- Win rate stability (plateaus with <10% variation)
- Average damage per episode stabilization
- Episodes between wins (consistently < 50 = learned)

---

## Data Outputs (runs_data/ folder)

All training outputs are organized in:
```
PythonRL/runs_data/
├── csv_logs/          # Episode data (named: CUPHEAD_DATE_TIME_TOTAL_EPISODES.csv)
│   └── e.g., CUPHEAD_2026-06-30_14-30-45_10_episodes.csv
└── training_logs/     # Training logs (named: CUPHEAD_DATE_TIME_TOTAL_EPISODES.log)
    └── RESTART_DEBUG_*.log  # Dedicated restart debug logs
```

**File naming convention**: `CUPHEAD_DATE_TIME_TOTAL_EPISODES`
- DATE: YYYY-MM-DD format
- TIME: HH-MM-SS (24-hour)
- TOTAL EPISODES: max_episodes parameter passed to training

---

## Critical Startup Sequence

1. **Start Python server FIRST**: `python ppo_training.py` (waits for Cuphead)
2. **THEN launch Cuphead from Steam**
3. **Navigate to Goopy Le Grande (Slime) boss fight**
4. **Training auto-starts when level_loaded event is received**

### Known Limitation
Unity games freeze when losing window focus. Workaround: use windowed mode or dual-monitor setup.

---

## Common Debug Locations

- `cuphead_debug.log` in BepInEx root - plugin state/diagnostics
- `runs_data/training_logs/` - training output logs
- `runs_data/csv_logs/` - episode data (CSV format)
