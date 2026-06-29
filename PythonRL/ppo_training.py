"""
PPO Training Script for Cuphead RL Environment
"""
import os
import time
import json
import threading
from datetime import datetime
import logging
import csv
import numpy as np
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
from environment_server import CupheadEnvironmentServer
from live_display import LiveDisplay, LiveDisplayCallback, integrate_with_env

# Setup logging
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ppo_training.log")
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_path, mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('PPOTraining')

class EpisodeCountCallback(BaseCallback):
    """Callback to stop after N episodes."""
    def __init__(self, max_episodes=30):
        super().__init__()
        self.max_episodes = max_episodes
        self.episode_count = 0

    def _on_step(self) -> bool:
        if self.n_calls % 100 == 0:
            print(f"Step {self.n_calls}...")
        return True

class CupheadGymEnv(gym.Env):
    """Gym environment wrapper for Cuphead."""
    metadata = {'render.modes': ['human']}

    def __init__(self, host='127.0.0.1', port=5000, display: LiveDisplay = None):
        super().__init__()
        self.server = CupheadEnvironmentServer(host, port)
        self.display = display  # Optional live display
        self.last_action = 0  # Track last action for display

        # Action space - matching the 8 actions in original
        self.action_space = spaces.Discrete(8)

        # Observation space - Cuphead uses 0-3 HP for players, boss has 1200 HP
        self.observation_space = spaces.Box(
            low=np.array([-1000, -1000, 0, 0, -1000, -1000, 0], dtype=np.float32),
            high=np.array([1000, 1000, 1200, 100, 1000, 1000, 3], dtype=np.float32),
            dtype=np.float32
        )

        # Episode tracking
        self.episode_start_time = None
        self.last_boss_hp = 1200
        self.steps = 0
        self.max_steps = 18000
        self.run_number = 0
        self.damage_events = []
        self.total_damage = 0.0
        self.final_score = 0.0
        self.death_time = None
        self.actions_this_episode = []
        self.fight_active = False  # Track whether fight has actually started (not just level loaded)

        # Reward shaping
        self.win_reward = 100
        self.death_penalty = -10
        self.hit_reward = 0.5
        self.survival_reward = 0.01

        # CSV logging
        self.csv_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training_runs.csv")

        # Track player health at episode end
        self.player_end_health = 3  # Will be updated in step()

        # Initialize CSV file with headers on creation
        if not os.path.exists(self.csv_log_path):
            with open(self.csv_log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['run_number','actions_sequence','score','boss_damage_total',
                    'fight_duration_seconds','first_damage_time','second_damage_time','death_time_seconds',
                    'player_end_health'])  # Added player_end_health column

        # Disable random actions when used with Gym
        self.server._gym_mode = True

    def reset(self, seed=None, options=None):
        """Reset environment - wait for level_loaded (server already started)."""
        # Increment run number for NEW episode
        # This happens when transitioning to a new episode (called after previous episode ends)
        self.run_number += 1

        # Reset tracking
        self.steps = 0
        self.episode_start_time = time.time()
        self.damage_events = []
        self.total_damage = 0.0
        self.final_score = 0.0
        self.death_time = None
        self.actions_this_episode = []
        self.fight_active = False  # Will be set to True when we receive first non-NO_EVENT state
        self.player_end_health = 3  # Track player health at episode end for CSV logging

        # First call starts server, subsequent calls just wait
        if not self.server.running:
            self.server.start()
            logger.info("Server started, waiting for level_loaded event...")
            while self.server.running:
                state = self.server.get_state()
                if state.get('event') == 'level_loaded':
                    logger.info("Level loaded - starting training")
                    break
                time.sleep(0.1)

        # Reset boss HP tracking for new episode
        self.last_boss_hp = 1200

        # Clear ALL stale state that could trigger premature restart
        # Use thread-safe clear method
        self.server.clear_state_for_new_episode()

        return self._get_observation(), {}

    def step(self, action):
        """Execute one step with configurable timing between actions."""
        self.steps += 1

        # Check for terminal event from previous state BEFORE we get new state
        # This handles the race condition where player_dead event arrives between steps
        with self.server._state_lock:
            previous_event = self.server.latest_state.get('event', '')
            if previous_event == 'player_dead':
                logger.info(f"EPISODE: Detected pending player_dead event - ending episode now. Steps: {self.steps}")
                self.death_time = time.time() - self.episode_start_time if self.episode_start_time else 0
                self._log_episode()
                threading.Timer(2.0, self.server.send_restart_command).start()
                self.server.latest_state['event'] = ''
                self.server.latest_state['win'] = None
                return self._get_observation(), 0.0, True, False, {'raw_state': self.server.latest_state}
            elif previous_event == 'boss_dead':
                win_val = self.server.latest_state.get('win')
                if win_val is True or win_val == True or win_val == 'true' or win_val == 'True':
                    logger.info(f"EPISODE: Detected pending boss_dead event - ending episode now. Steps: {self.steps}")
                self.death_time = None
                self._log_episode()
                threading.Timer(2.0, self.server.send_restart_command).start()
                self.server.latest_state['event'] = ''
                self.server.latest_state['win'] = None
                return self._get_observation(), 0.0, True, False, {'raw_state': self.server.latest_state}

        # Detect fight start - when we see actual player/boss activity
        # (level_loaded event fires, but fight_active only becomes True when we see
        # real combat - boss has taken damage OR player has moved position)
        state = self.server.get_state()
        if not self.fight_active:
            boss_hp = state.get('boss_hp', 1200)
            level_time = state.get('level_time', 0)
            boss_positions = state.get('boss_positions', [{}])
            player_positions = state.get('player_positions', [{}])
            boss_phase = state.get('boss_phase', 'Unknown')
            
            # Fight starts when: boss has taken damage OR level_time > 2s (past intro) OR player has moved OR boss phase is known
            player_has_moved = False
            if player_positions:
                for p in player_positions:
                    x = p.get('x', 0)
                    y = p.get('y', 0)
                    if x != 0 or y != 0:
                        player_has_moved = True
                        break
            boss_has_damage = boss_hp < 1200
            # Also consider fight active if we see a real boss phase (not "Unknown" or "Generic")
            boss_phase_active = boss_phase not in ('Unknown', 'Generic')
            if boss_has_damage or level_time > 2.0 or player_has_moved or boss_phase_active:
                self.fight_active = True
                logger.info(f"[FIGHT ACTIVE] Fight started - boss_hp: {boss_hp}, level_time: {level_time:.2f}s, phase: {boss_phase}")

        # Update live display before action
        if self.display:
            self.display.set_previous_action(self.last_action)

        action_map = ["move_left", "move_right", "aim_up", "duck_crouch",
                      "jump", "shoot", "dash", "directional_aim"]
        self.actions_this_episode.append(action_map[action])
        self.last_action = action  # Store for next step's 'previous'

        self.server.send_input(action_map[action], 1.0)
        time.sleep(0.1)  # Hold time - how long key is pressed
        self.server.send_input(action_map[action], 0.0)
        time.sleep(0.2)  # Wait between actions - prevents input spam/glitch

        state = self.server.get_state()

        # Track player health for CSV logging at episode end
        player_positions = state.get('player_positions', [{}])
        for player in player_positions:
            if player:
                health = player.get('health')
                try:
                    if health is not None:
                        self.player_end_health = int(health)
                        if health != 3:  # Log if health changed from full
                            logger.info(f"[HEALTH DEBUG] Step {self.steps}: player health = {health}")
                except (ValueError, TypeError):
                    pass
                break

        # Update live display after state change
        if self.display:
            self.display.update_game_state(state)
            self.display.set_current_action(action)
            self.display.update_score(self.final_score, self.total_damage)
            self.display.update_episode(self.run_number, self.steps)

        reward = self._calculate_reward(state)
        self.final_score += reward
        done = self._check_done(state)

        return self._get_observation(), reward, done, False, {'raw_state': state}

    def _get_observation(self):
        state = self.server.get_state()
        boss_positions = state.get('boss_positions', [{}])
        boss_x = boss_positions[0].get('x', 0) if boss_positions else 0
        boss_y = boss_positions[0].get('y', 0) if boss_positions else 0
        boss_hp = state.get('boss_hp', 1200)
        boss_hp_pct = state.get('boss_hp_pct', 100.0)

        player_positions = state.get('player_positions', [{}])
        player_x = player_positions[0].get('x', 0) if player_positions else 0
        player_y = player_positions[0].get('y', 0) if player_positions else 0

        # Check if ANY player is dead (check all players, not just index 0)
        player_is_dead = 0.0
        if player_positions:
            for player in player_positions:
                is_dead_raw = player.get('is_dead', False)
                if isinstance(is_dead_raw, str):
                    is_dead = is_dead_raw.lower() == 'true'
                else:
                    is_dead = bool(is_dead_raw)
                if is_dead:
                    player_is_dead = 1.0
                    break

        return np.array([
            boss_x / 500.0, boss_y / 500.0, boss_hp / 1200.0, boss_hp_pct / 100.0,
            player_x / 500.0, player_y / 500.0, player_is_dead
        ], dtype=np.float32)

    def _calculate_reward(self, state):
        reward = 0
        current_boss_hp = state.get('boss_hp', 1200)
        if self.last_boss_hp > current_boss_hp:
            damage = self.last_boss_hp - current_boss_hp
            self.total_damage += damage
            if self.episode_start_time:
                self.damage_events.append(time.time() - self.episode_start_time)
            reward += self.hit_reward
        self.last_boss_hp = current_boss_hp
        reward += self.survival_reward
        return reward

    def _check_done(self, state):
        event = state.get('event', '')
        # Normalize event name for robust matching (strip whitespace, handle case variations)
        event = event.strip().lower() if isinstance(event, str) else ''

        # Debug logging on terminal events only (reduced noise)
        if self.fight_active and event in ('player_dead', 'boss_dead'):
            logger.info(f"[DEBUG] Step {self.steps}: event='{event}', raw_win={state.get('win')}, player_positions={state.get('player_positions')}")

        # Player death event from Plugin (explicit) - process regardless of fight_active
        # ONLY listen to the explicit player_dead event to avoid false positives
        if event == 'player_dead':
            logger.info(f"EPISODE: Player dead event received - logging and restarting. Steps this episode: {self.steps}")
            self.death_time = time.time() - self.episode_start_time if self.episode_start_time else 0
            self._log_episode()
            threading.Timer(2.0, self.server.send_restart_command).start()
            return True

        # Boss death event (explicit win condition) - check both boolean and string forms
        win_value = state.get('win', False)
        # Normalize win value for comparison (handle boolean and string forms)
        is_win = win_value is True or (isinstance(win_value, str) and win_value.lower() == 'true')
        if event == 'boss_dead' and is_win:
            logger.info("EPISODE: Boss dead - logging and restarting")
            self.death_time = None
            self._log_episode()
            threading.Timer(2.0, self.server.send_restart_command).start()
            return True

        if self.steps >= self.max_steps:
            logger.info("EPISODE: Max steps - logging")
            self._log_episode()
            return True

        return False

    def _log_episode(self):
        if self.episode_start_time is None:
            return
        fight_duration = time.time() - self.episode_start_time
        first_damage = self.damage_events[0] if len(self.damage_events) > 0 else ""
        second_damage = self.damage_events[1] if len(self.damage_events) > 1 else ""
        actions_str = ", ".join(self.actions_this_episode)

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.csv_log_path), exist_ok=True)

            with open(self.csv_log_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                row = [self.run_number, actions_str, round(self.final_score, 2),
                    round(self.total_damage, 2), round(fight_duration, 2),
                    round(first_damage, 2) if first_damage else "",
                    round(second_damage, 2) if second_damage else "",
                    round(self.death_time, 2) if self.death_time else "",
                    self.player_end_health]  # Player health at episode end
                writer.writerow(row)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            logger.info(f"[CSV] Logged to training_runs.csv - Run #{self.run_number}")
        except Exception as e:
            logger.error(f"CSV error: {e}")
            print(f"[CSV ERROR] {e}")

    def close(self):
        self.server.stop()

def train_ppo(max_episodes=10, save_path='ppo_cuphead_model', enable_display=True):
    """Train PPO agent for exactly N episodes (complete boss fights)."""
    print("=" * 60)
    print("PPO TRAINING - Cuphead RL")
    print("=" * 60)
    print(f"Target: {max_episodes} episodes | Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Create live display
    display = LiveDisplay(update_interval=0.1) if enable_display else None
    if display:
        display.start()

    # Create environment with display
    env = CupheadGymEnv(display=display)

    # Start server and wait for level_loaded BEFORE creating PPO model
    env.server.start()

    # Attach display to server for automatic state updates
    if display:
        from live_display import attach_display_to_server
        attach_display_to_server(env.server, display)

    print("\n" + "=" * 60)
    print("WAITING FOR CUPHEAD CONNECTION")
    print("=" * 60)
    print("Launch Cuphead and navigate to Goopy Le Grande")
    print("=" * 60 + "\n")

    # Wait for first level_loaded (blocking)
    while True:
        state = env.server.get_state()
        if state.get('event') == 'level_loaded':
            print("Level loaded! Starting PPO training...\n")
            break
        # Silent wait for level_loaded - live display shows status

    # PPO model
    model = PPO(
        'MlpPolicy', env,
        learning_rate=3e-4, n_steps=2048, batch_size=64,
        n_epochs=10, gamma=0.99, gae_lambda=0.95, clip_range=0.2,
        verbose=1
    )

    print(f"Training {max_episodes} episodes...\n")
    start_time = time.time()

    # Train for N episodes using manual loop
    episodes_completed = 0
    obs, _ = env.reset()  # This increments run_number to 1
    time.sleep(2)  # Wait 2 seconds before first input stream

    while episodes_completed < max_episodes:
        action, _ = model.predict(obs, deterministic=False)
        obs, reward, done, truncated, _ = env.step(int(action))

        # Predict and set next action for display (what will happen next step)
        if hasattr(env, 'display') and env.display:
            next_a, _ = model.predict(obs, deterministic=False)
            env.display.set_next_action(int(next_a))

        if done or truncated:
            episodes_completed += 1
            logger.info(f"Episode {episodes_completed}/{max_episodes} complete")

            # Wait for level_loaded after restart (with timeout to prevent infinite loop)
            logger.info("Waiting for level_loaded after restart...")
            wait_start = time.time()
            max_wait_time = 15.0  # Maximum 15 seconds to wait for level_loaded
            while time.time() - wait_start < max_wait_time:
                state = env.server.get_state()
                if state.get('event') == 'level_loaded':
                    logger.info("Level loaded - resuming")
                    break
                # Also check if event indicates something went wrong
                if state.get('event') in ('player_dead', 'boss_dead'):
                    logger.info("Level already in terminal state - continuing")
                    break
                time.sleep(0.1)
            else:
                logger.warning("Timeout waiting for level_loaded - forcing continue")
            time.sleep(2)  # Wait 2 seconds after level load before next input stream
            obs, _ = env.reset()  # This will increment run_number for next episode

    print(f"\nTraining completed {episodes_completed} episodes in {time.time() - start_time:.1f}s")

    if display:
        display.stop()

    return model, env

if __name__ == "__main__":
    model, env = train_ppo()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        env.close()