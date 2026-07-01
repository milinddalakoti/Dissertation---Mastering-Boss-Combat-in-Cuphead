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

# Module-level logger (will be configured in train_ppo())
logger = logging.getLogger('PPOTraining')

# Dedicated restart debug logger
restart_debug_logger = None
def get_restart_debug_logger():
    global restart_debug_logger
    if restart_debug_logger is None:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs_data", "training_logs")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        log_path = os.path.join(log_dir, f"RESTART_DEBUG_{timestamp}.log")
        restart_debug_logger = logging.getLogger('RestartDebug')
        restart_debug_logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(log_path, mode='w', encoding='utf-8')
        handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S'))
        restart_debug_logger.addHandler(handler)
    return restart_debug_logger

# Legacy file paths for backward compatibility
LEGACY_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training_runs.csv")

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

    # Class-level logger (initialized once)
    _logger = None
    _log_path = None

    @classmethod
    def _get_logger(cls, max_episodes):
        """Get or create logger with new file naming convention."""
        if cls._logger is None:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs_data", "training_logs")
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            log_path = os.path.join(log_dir, f"CUPHEAD_{timestamp}_{max_episodes}_episodes.log")
            cls._log_path = log_path
            logging.basicConfig(
                level=logging.INFO,
                format='[%(asctime)s] %(levelname)s: %(message)s',
                datefmt='%H:%M:%S',
                handlers=[
                    logging.FileHandler(log_path, mode='w', encoding='utf-8'),
                    logging.StreamHandler()
                ]
            )
            cls._logger = logging.getLogger('PPOTraining')
        return cls._logger

    def __init__(self, host='127.0.0.1', port=5000, display: LiveDisplay = None, max_episodes=10):
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
        self.max_episodes = max_episodes  # Store for file naming

        # Convergence tracking
        self.episode_outcomes = []  # Track (win, damage, duration) for convergence detection
        self.convergence_check_interval = 50  # Check every N episodes

        # Reward shaping
        self.win_reward = 100
        self.death_penalty = -10
        self.hit_reward = 0.5
        self.survival_reward = 0.01

        # CSV logging - use runs_data folder with naming convention
        self.csv_log_path = None  # Will be initialized on first use

        # Track player health at episode end
        self.player_end_health = 3  # Will be updated in step()

        # Disable random actions when used with Gym
        self.server._gym_mode = True

    def _initialize_csv_path(self, max_episodes):
        """Initialize CSV file path with runs_data folder and naming convention."""
        csv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs_data", "csv_logs")
        os.makedirs(csv_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        return os.path.join(csv_dir, f"CUPHEAD_{timestamp}_{max_episodes}_episodes.csv")

    def reset(self, seed=None, options=None):
        """Reset environment - wait for level_loaded (server already started)."""
        global logger

        # Initialize logger on first reset
        if CupheadGymEnv._logger is None:
            CupheadGymEnv._logger = CupheadGymEnv._get_logger(self.max_episodes)
            logger = CupheadGymEnv._logger

        # Initialize CSV path on first reset
        if self.csv_log_path is None:
            self.csv_log_path = self._initialize_csv_path(self.max_episodes)

        # Increment run number for NEW episode
        # This happens when transitioning to a new episode (called after previous episode ends)
        self.run_number += 1

        logger.info(f"[RESET] Starting reset for episode #{self.run_number}")

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

        logger.info(f"[RESET] Reset complete for episode #{self.run_number}")
        return self._get_observation(), {}

    def step(self, action):
        """Execute one step with configurable timing between actions."""
        self.steps += 1

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

        # Check for terminal event - this will log and send restart
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
        # Check latest_state for terminal events (in case they arrived since we got state)
        with self.server._state_lock:
            event = self.server.latest_state.get('event', '')
        # Normalize event name for robust matching (strip whitespace, handle case variations)
        event = event.strip().lower() if isinstance(event, str) else ''

        # Debug logging on terminal events only (reduced noise)
        if self.fight_active and event in ('player_dead', 'boss_dead'):
            logger.info(f"[DEBUG] Step {self.steps}: event='{event}', raw_win={state.get('win')}, player_positions={state.get('player_positions')}")

        # Player death event from Plugin (explicit) - process regardless of fight_active
        if event == 'player_dead':
            logger.info(f"EPISODE: Player dead event received - logging and restarting. Steps this episode: {self.steps}")
            self.death_time = time.time() - self.episode_start_time if self.episode_start_time else 0
            self._log_episode()
            # Clear the event to prevent re-processing in training loop
            with self.server._state_lock:
                self.server.latest_state['event'] = ''
                self.server.latest_state['win'] = None
            return True

        # Boss death event (explicit win condition) - check both boolean and string forms
        win_value = state.get('win', False)
        # Normalize win value for comparison (handle boolean and string forms)
        is_win = win_value is True or (isinstance(win_value, str) and win_value.lower() == 'true')
        if event == 'boss_dead' and is_win:
            logger.info("EPISODE: Boss dead - logging and restarting")
            self.death_time = None
            self._log_episode()
            # Clear the event to prevent re-processing in training loop
            with self.server._state_lock:
                self.server.latest_state['event'] = ''
                self.server.latest_state['win'] = None
            return True

        if self.steps >= self.max_steps:
            logger.info("EPISODE: Max steps - logging")
            self._log_episode()
            return True

        return False

    def _get_convergence_metrics(self, window=50):
        """Calculate convergence metrics over the last N episodes."""
        if len(self.episode_outcomes) < window:
            return None
        recent = self.episode_outcomes[-window:]
        wins = sum(1 for o in recent if o['win'])
        avg_damage = sum(o['damage'] for o in recent) / len(recent)
        avg_duration = sum(o['duration'] for o in recent) / len(recent)
        return {
            'win_rate': wins / len(recent),
            'avg_damage': avg_damage,
            'avg_duration': avg_duration,
            'episodes_since_first_win': self._get_episodes_since_first_win()
        }

    def _get_episodes_since_first_win(self):
        """Get number of episodes since first win (for plateau detection)."""
        for i in range(len(self.episode_outcomes) - 1, -1, -1):
            if self.episode_outcomes[i]['win']:
                return len(self.episode_outcomes) - 1 - i
        return len(self.episode_outcomes)  # No wins yet

    def _log_episode(self):
        if self.episode_start_time is None:
            return
        fight_duration = time.time() - self.episode_start_time
        first_damage = self.damage_events[0] if len(self.damage_events) > 0 else ""
        second_damage = self.damage_events[1] if len(self.damage_events) > 1 else ""
        actions_str = ", ".join(self.actions_this_episode)

        # Track episode outcome for convergence detection
        # win = (death_time is None and total_damage > 0) means boss was defeated
        win = self.death_time is None and self.total_damage > 0
        self.episode_outcomes.append({
            'win': win,
            'damage': self.total_damage,
            'duration': fight_duration
        })

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.csv_log_path), exist_ok=True)

            # Check if file needs headers (first write)
            write_header = not os.path.exists(self.csv_log_path)
            with open(self.csv_log_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(['run_number','actions_sequence','score','boss_damage_total',
                        'fight_duration_seconds','first_damage_time','second_damage_time','death_time_seconds',
                        'player_end_health'])
                row = [self.run_number, actions_str, round(self.final_score, 2),
                    round(self.total_damage, 2), round(fight_duration, 2),
                    round(first_damage, 2) if first_damage else "",
                    round(second_damage, 2) if second_damage else "",
                    round(self.death_time, 2) if self.death_time else "",
                    self.player_end_health]
                writer.writerow(row)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            logger.info(f"[CSV] Logged to {os.path.basename(self.csv_log_path)} - Run #{self.run_number}")
        except Exception as e:
            logger.error(f"CSV error: {e}")
            print(f"[CSV ERROR] {e}")

    def close(self):
        self.server.stop()

def migrate_legacy_files():
    """Migrate legacy training_runs.csv to runs_data folder."""
    try:
        if os.path.exists(LEGACY_CSV_PATH):
            csv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs_data", "csv_logs")
            os.makedirs(csv_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            new_path = os.path.join(csv_dir, f"CUPHEAD_{timestamp}_migrated.csv")
            # Copy legacy file to new location
            import shutil
            shutil.copy2(LEGACY_CSV_PATH, new_path)
            print(f"[MIGRATION] Moved training_runs.csv to {new_path}")
    except Exception as e:
        print(f"[MIGRATION] Could not migrate legacy files: {e}")

def train_ppo(max_episodes=1000, save_path='ppo_cuphead_model', enable_display=True):
    """Train PPO agent for exactly N episodes (complete boss fights)."""
    migrate_legacy_files()  # Migrate legacy files if they exist

    print("=" * 60)
    print("PPO TRAINING - Cuphead RL")
    print("=" * 60)
    print(f"Target: {max_episodes} episodes | Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print(f"Output folders: runs_data/csv_logs/ and runs_data/training_logs/")
    print("=" * 60)

    # Create live display
    display = LiveDisplay(update_interval=0.1) if enable_display else None
    if display:
        display.start()

    # Create environment with display
    env = CupheadGymEnv(display=display, max_episodes=max_episodes)

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

    # Initialize restart debug logger
    restart_log = get_restart_debug_logger()
    restart_log.info(f"[TRAINING START] Beginning {max_episodes} episode training run")

    # Train for N episodes using manual loop
    episodes_completed = 0
    obs, _ = env.reset()  # This increments run_number to 1
    restart_log.info(f"[TRAINING LOOP] First obs received, starting main loop")
    time.sleep(2)  # Wait 2 seconds before first input stream

    while episodes_completed < max_episodes:
        action, _ = model.predict(obs, deterministic=False)
        obs, reward, done, truncated, _ = env.step(int(action))

        # Heartbeat log every 100 steps to confirm training is alive
        if env.steps % 100 == 0:
            restart_log.info(f"[TRAINING HEARTBEAT] Episode {episodes_completed}, Step {env.steps}, Reward: {reward:.2f}, Done: {done}")

        # Predict and set next action for display (what will happen next step)
        if hasattr(env, 'display') and env.display:
            next_a, _ = model.predict(obs, deterministic=False)
            env.display.set_next_action(int(next_a))

        if done or truncated:
            episodes_completed += 1

            # Convergence detection and logging every 50 episodes
            if episodes_completed % 50 == 0:
                metrics = env._get_convergence_metrics(window=50)
                if metrics:
                    logger.info(f"[CONVERGENCE] Episodes {episodes_completed}: win_rate={metrics['win_rate']:.2%}, avg_damage={metrics['avg_damage']:.0f}, avg_duration={metrics['avg_duration']:.1f}s, episodes_since_first_win={metrics['episodes_since_first_win']}")

            # Clear terminal event FIRST (thread-safe, no logging while holding lock)
            # _check_done() already cleared event/win, but defensive clear ensures clean state
            with env.server._state_lock:
                if 'event' in env.server.latest_state:
                    env.server.latest_state['event'] = ''
                if 'win' in env.server.latest_state:
                    env.server.latest_state['win'] = None

            # Log AFTER releasing lock - prevents lock ordering deadlock with client handler
            logger.info(f"Episode {episodes_completed}/{max_episodes} complete, calling reset...")

            # Send restart command with timeout protection
            threading.Timer(2.0, env.server.send_restart_command).start()

            # Wait for level_loaded after restart (with reconnection handling)
            logger.info("[RESTART LOOP] Waiting for level_loaded after restart...")
            wait_start = time.time()
            max_wait_time = 90.0  # Extended timeout for slower restarts
            consecutive_empty_states = 0
            loop_iterations = 0
            while time.time() - wait_start < max_wait_time:
                loop_iterations += 1
                state = env.server.get_state()
                event = state.get('event', '')

                # Log every 5 seconds to show we're alive
                elapsed = time.time() - wait_start
                if int(elapsed) % 5 == 0 and int(elapsed) > 0 and loop_iterations % 50 == 1:
                    restart_log.info(f"[RESTART LOOP] Waiting... {elapsed:.0f}s elapsed, event='{event}', fight_active={env.fight_active}, connected={env.server.is_connected()}")

                # Check for reconnection using server's connection status
                if not env.server.is_connected():
                    consecutive_empty_states += 1
                    if consecutive_empty_states == 5:  # 0.5s of disconnection
                        restart_log.warning("[RESTART LOOP] Connection lost - waiting for reconnection...")
                else:
                    consecutive_empty_states = 0

                if event == 'level_loaded':
                    restart_log.info(f"[RESTART LOOP] Level loaded detected after {elapsed:.1f}s - proceeding")
                    break
                # DON'T break on terminal events - we already processed them
                time.sleep(0.1)
            else:
                restart_log.warning(f"[RESTART LOOP] Timeout after {max_wait_time}s - level_loaded never arrived")

            time.sleep(3)  # Wait 3 seconds after level load before next input stream
            restart_log.info("[RESTART LOOP] Calling env.reset() for next episode...")
            obs, _ = env.reset()  # This will increment run_number for next episode
            restart_log.info(f"[RESTART LOOP] Reset complete, obs shape: {obs.shape if hasattr(obs, 'shape') else 'N/A'}")

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