# Cuphead RL Dissertation Project

## Project Overview
This project implements a reinforcement learning framework to train an AI agent to master boss combat in Cuphead, with Goopy Le Grande (Slime boss) as the primary target. The architecture uses a Python-led approach for maximum accessibility and leverages your existing Python expertise.

## 📁 File Structure

```
Dissertation/
├── CupheadPlugin/                     # C# plugin (game state extraction only)
│   ├── Plugin.cs                      # Simplified BepInEx/Harmony plugin
│   └── CupheadPlugin.csproj           # Project configuration
├── GameFiles/                         # Cuphead game installation
├── PythonRL/                          # Python RL components (main logic)
│   ├── environment_server.py          # Main server: state reception + input control
│   ├── test_commands_pynput.py        # Testing script for pynput input
│   ├── test_commands.py               # Legacy TCP command tester (reference)
│   └── REQUIREMENTS.txt               # pynput dependency (>=1.7.6)
├── Planning documents/                # Reorganized planning references
│   ├── implementation_plan.md
│   ├── IMPLEMENTATION_SUMMARY.md
│   ├── SUMMARY OF WHAT ALL I HAVE DONE.txt
│   └── TESTING_GUIDE.md
├── PDD_Dalakoti_Milind_25230406.pdf   # Dissertation document
├── global.json                        # .NET configuration
└── README.md                          # This file
```

## 🔧 How to Use

### 1. Installation
```bash
cd PythonRL
pip install -r REQUIREMENTS.txt
```

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

### 4. RL Agent Integration
Use `environment_server.py` to:
- Get state: `server.get_state()` → boss HP, phase, events
- Send actions: `server.send_input("jump", 1.0)` → press Z key
- Release actions: `server.send_input("jump", 0.0)` → release Z key

## 🎯 Current Status

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

## 🚀 Next Steps

1. **Install pynput**: `pip install -r REQUIREMENTS.txt`
2. **Test inputs**: `python test_commands_pynput.py`
3. **Verify end-to-end**: Launch game, run server, test inputs, observe movement
4. **Implement RL agent**: Process state → select action → send inputs → repeat
5. **Begin training**: Start PPO episodes to learn Goopy Le Grande fight patterns

## 📖 Documentation

- `FINAL_SUMMARY.md`: Complete technical summary
- `INSTALL_AND_TEST.md`: Detailed installation and testing guide
- `TESTING_GUIDE.md`: Original testing procedures
- `IMPLEMENTATION_SUMMARY.md`: High-level implementation overview
- Progress tracking: `.claude/projects/.../memory/progress_log.md`

## 💡 Notes

- The C# plugin (`Plugin.cs`) is intentionally minimal - focused purely on reliable state extraction
- All complex logic (state processing, decision making, input control) resides in Python
- This approach leverages your Python expertise while maintaining game compatibility
- Ready for PPO, DQN, or any other RL algorithm implementation

---
*Ready for RL agent development and training!*
*Last updated: 2026-05-25*