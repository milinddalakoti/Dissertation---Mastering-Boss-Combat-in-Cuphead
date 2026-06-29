"""
Live Terminal Display Wrapper for PPO Training
Shows live updates in-place (using carriage return) with thread-safe state updates.
"""
import threading
import time
import sys
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger('LiveDisplay')


@dataclass
class DisplayState:
    """Thread-safe container for all display data."""
    boss_x: float = 0.0
    boss_y: float = 0.0
    boss_hp: int = 1200
    boss_hp_pct: float = 100.0
    boss_phase: int = 0

    player_x: float = 0.0
    player_y: float = 0.0
    player_health: int = 3  # Cuphead uses 0-3 HP

    current_action: str = "none"
    next_action: str = "none"
    previous_action: str = "none"

    episode_count: int = 0
    step_count: int = 0
    score: float = 0.0
    total_damage: float = 0.0

    last_update: float = 0.0

    # Lock for thread-safe updates
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class LiveDisplay:
    """
    Live terminal display that updates in-place using carriage returns.
    Thread-safe for updates from game state changes.
    """

    ACTION_NAMES = [
        "move_left", "move_right", "aim_up", "duck_crouch",
        "jump", "shoot", "dash", "directional_aim"
    ]

    def __init__(self, update_interval: float = 0.1, enabled: bool = True):
        self.state = DisplayState()
        self.enabled = enabled
        self.update_interval = update_interval
        self._running = False
        self._display_thread: Optional[threading.Thread] = None
        self._last_render_hash: int = 0
        self._use_color = self._detect_color_support()

        # ANSI color codes
        self._COLORS = {
            'reset': '\033[0m',
            'bold': '\033[1m',
            'cyan': '\033[36m',
            'green': '\033[32m',
            'yellow': '\033[33m',
            'magenta': '\033[35m',
            'red': '\033[31m',
            'blue': '\033[34m',
        } if self._use_color else {k: '' for k in ['reset', 'bold', 'cyan', 'green', 'yellow', 'magenta', 'red', 'blue']}

    def _detect_color_support(self) -> bool:
        """Detect if terminal supports ANSI colors."""
        try:
            if sys.platform == 'win32':
                # Windows 10+ supports ANSI colors, enable them
                import os
                if sys.stdout.isatty():
                    os.system('')  # Enable ANSI escape on Windows
                    return True
                return False
            return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
        except:
            return False

    def update_game_state(self, game_state: Dict[str, Any]):
        """Update display state from game state dictionary (thread-safe)."""
        if not self.enabled:
            return

        with self.state._lock:
            # Boss position
            boss_positions = game_state.get('boss_positions', [{}])
            if boss_positions:
                self.state.boss_x = float(boss_positions[0].get('x', 0))
                self.state.boss_y = float(boss_positions[0].get('y', 0))

            # Boss HP (handle both hp and hp_pct)
            self.state.boss_hp = game_state.get('boss_hp', self.state.boss_hp)
            self.state.boss_hp_pct = game_state.get('boss_hp_pct', self.state.boss_hp_pct)
            self.state.boss_phase = game_state.get('boss_phase', self.state.boss_phase)

            # Player position and health (use first alive player's health for primary display)
            player_positions = game_state.get('player_positions', [{}])
            if player_positions:
                # Get position from first player
                self.state.player_x = float(player_positions[0].get('x', 0))
                self.state.player_y = float(player_positions[0].get('y', 0))

                # Get health from first alive player, or from is_dead status
                # Default to 3 (Cuphead's max health for peashooter weapon)
                self.state.player_health = 3
                for i, player in enumerate(player_positions):
                    if player:
                        health = player.get('health')
                        # If health key exists and has a value, use it
                        if health is not None:
                            try:
                                h = int(health)
                                self.state.player_health = h
                                break
                            except (ValueError, TypeError):
                                pass
                        # Derive from is_dead if no health provided
                        is_dead_raw = player.get('is_dead', False)
                        if isinstance(is_dead_raw, str):
                            is_dead = is_dead_raw.lower() == 'true'
                        else:
                            is_dead = bool(is_dead_raw)
                        if not is_dead:
                            self.state.player_health = 3  # Alive player, full health
                            break  # Found alive player with no health value
                        else:
                            self.state.player_health = 0
                            break  # Found dead player

            self.state.last_update = time.time()

    def update_episode(self, episode: int, step: int):
        """Update episode and step counts (thread-safe)."""
        if not self.enabled:
            return
        with self.state._lock:
            self.state.episode_count = episode
            self.state.step_count = step

    def update_score(self, score: float, total_damage: float = 0.0):
        """Update score and damage (thread-safe)."""
        if not self.enabled:
            return
        with self.state._lock:
            self.state.score = score
            self.state.total_damage = total_damage

    def update_actions(self, current_action: Optional[int] = None,
                     next_action: Optional[int] = None,
                     previous_action: Optional[int] = None):
        """Update action information (thread-safe)."""
        if not self.enabled:
            return
        with self.state._lock:
            if current_action is not None:
                self.state.current_action = self.ACTION_NAMES[current_action] if 0 <= current_action < len(self.ACTION_NAMES) else "unknown"
            if next_action is not None:
                self.state.next_action = self.ACTION_NAMES[next_action] if 0 <= next_action < len(self.ACTION_NAMES) else "unknown"
            if previous_action is not None:
                self.state.previous_action = self.ACTION_NAMES[previous_action] if 0 <= previous_action < len(self.ACTION_NAMES) else "unknown"

    def set_current_action(self, action_idx: int):
        """Set just the current action."""
        if not self.enabled:
            return
        with self.state._lock:
            self.state.current_action = self.ACTION_NAMES[action_idx] if 0 <= action_idx < len(self.ACTION_NAMES) else "unknown"

    def set_previous_action(self, action_idx: int):
        """Set the previous action (call after action is executed)."""
        if not self.enabled:
            return
        with self.state._lock:
            self.state.previous_action = self.ACTION_NAMES[action_idx] if 0 <= action_idx < len(self.ACTION_NAMES) else "none"

    def set_next_action(self, action_idx: int):
        """Set the predicted next action."""
        if not self.enabled:
            return
        with self.state._lock:
            self.state.next_action = self.ACTION_NAMES[action_idx] if 0 <= action_idx < len(self.ACTION_NAMES) else "none"

    def start(self):
        """Start the display update thread."""
        if not self.enabled:
            return
        self._running = True
        self._display_thread = threading.Thread(target=self._display_loop, daemon=True)
        self._display_thread.start()

    def stop(self):
        """Stop the display update thread and print final state."""
        if not self.enabled:
            return
        self._running = False
        if self._display_thread:
            self._display_thread.join(timeout=1.0)
        # Print final newline
        print()

    def _display_loop(self):
        """Main display loop - runs in separate thread."""
        while self._running:
            self._render()
            time.sleep(self.update_interval)

    def _render(self):
        """Render the display state to terminal (in-place)."""
        with self.state._lock:
            # Build the display lines
            lines = self._build_lines()

            # Only update if content changed (avoid flicker)
            content = "\n".join(lines)
            content_hash = hash(content)
            if content_hash == self._last_render_hash:
                return
            self._last_render_hash = content_hash

            # Clear previous lines and write new content
            # Move cursor up to overwrite previous content
            try:
                # Clear all lines we're about to overwrite
                for _ in range(len(lines) + 1):
                    sys.stdout.write('\033[2K\033[1A')  # Clear line and move up
                # Move back down to top
                sys.stdout.write('\033[0G')  # Reset horizontal position
                
                # Write the content
                sys.stdout.write('\n'.join(lines) + '\n')
                sys.stdout.flush()
            except Exception:
                # If ANSI codes fail, just print normally
                sys.stdout.write('\n'.join(lines) + '\n')
                sys.stdout.flush()

    def _build_lines(self) -> list:
        """Build display lines from current state."""
        c = self._COLORS
        s = self.state

        lines = [
            f"{c['bold']}{c['cyan']}=== CUPHEAD PPO TRAINING DASHBOARD ==={c['reset']}",
            "",
            f"{c['yellow']}[BOSS STATE]{c['reset']}",
            f"  Position: ({s.boss_x:>8.1f}, {s.boss_y:>8.1f})",
            f"  HP: {s.boss_hp:>4}/1200 ({s.boss_hp_pct:>5.1f}%)",
            f"  Phase: {s.boss_phase}",
            "",
            f"{c['green']}[PLAYER STATE]{c['reset']}",
            f"  Position: ({s.player_x:>8.1f}, {s.player_y:>8.1f})",
            f"  Health: {s.player_health} HP",
            "",
            f"{c['magenta']}[ACTIONS]{c['reset']}",
            f"  Current:  {s.current_action}",
            f"  Next:     {s.next_action}",
            f"  Previous: {s.previous_action}",
            "",
            f"{c['blue']}[TRAINING METRICS]{c['reset']}",
            f"  Episode: {s.episode_count}",
            f"  Step:    {s.step_count}",
            f"  Score:   {s.score:+.2f}",
            f"  Damage:  {s.total_damage:.0f}",
            f"{c['cyan']}============================================{c['reset']}",
        ]
        return lines

    def _get_boss_hp_pct(self) -> float:
        """Calculate boss HP percentage."""
        return (self.state.boss_hp / 12.0) if self.state.boss_hp > 0 else 0.0


class LiveDisplayCallback:
    """
    SB3 Callback wrapper for live display integration.
    Use this in PPO training to automatically update display.
    """

    def __init__(self, display: LiveDisplay):
        self.display = display
        self._prev_action = None

    def on_step(self, action: int, reward: float, done: bool, episode: int, step: int):
        """Called after each training step."""
        self.display.update_episode(episode, step)
        self.display.set_previous_action(self._prev_action if self._prev_action is not None else 0)
        self.display.set_current_action(action)
        self.display.update_score(reward)
        self._prev_action = action


# Integration functions for environment_server.py

def attach_display_to_server(server_instance, display: LiveDisplay):
    """
    Attach display to update when server receives state.
    Call this after creating your CupheadEnvironmentServer instance.
    """
    original_process_message = server_instance._process_message

    def patched_process_message(message_str):
        result = original_process_message(message_str)
        try:
            message = __import__('json').loads(message_str)
            display.update_game_state(message)
        except:
            pass
        return result

    server_instance._process_message = patched_process_message
    return server_instance


# Integration functions for ppo_training.py

def create_training_display(update_interval: float = 0.1) -> LiveDisplay:
    """Factory function to create a training display."""
    return LiveDisplay(update_interval=update_interval)


def integrate_with_env(env, display: LiveDisplay):
    """
    Integrate LiveDisplay with CupheadGymEnv.
    Patches the step method to update display automatically.
    """
    original_step = env.step

    def patched_step(action):
        # Store previous action before stepping
        if hasattr(env, 'last_action'):
            display.set_previous_action(env.last_action)

        # Execute step
        obs, reward, done, truncated, info = original_step(action)

        # Update display
        display.set_current_action(action)
        env.last_action = action
        display.update_score(env.final_score, env.total_damage)
        display.update_episode(env.run_number, env.steps)

        return obs, reward, done, truncated, info

    env.step = patched_step
    return env


if __name__ == "__main__":
    # Demo/test mode
    import random

    print("Live Display Demo - Press Ctrl+C to exit")
    print("(Showing simulated updates)\n")

    display = LiveDisplay(update_interval=0.2)

    # Simulated training loop for demo
    def demo_updates(display: LiveDisplay):
        episode = 1
        step = 0
        score = 0.0
        damage = 0

        try:
            for _ in range(200):
                step += 1
                damage += random.randint(0, 50)
                score += random.uniform(-1, 2)

                # Random game state
                game_state = {
                    'boss_positions': [{'x': random.uniform(-300, 300), 'y': random.uniform(-100, 100)}],
                    'boss_hp': max(0, 1200 - damage),
                    'boss_hp_pct': max(0, 100.0 - damage / 12.0),
                    'boss_phase': min(3, damage // 300),
                    'player_positions': [{'x': random.uniform(-200, 200), 'y': random.uniform(-50, 50), 'health': max(0, 100 - damage // 100)}],
                }

                display.update_game_state(game_state)
                display.update_episode(episode, step)
                display.update_score(score, damage)
                display.update_actions(
                    current_action=random.randint(0, 7),
                    next_action=random.randint(0, 7),
                    previous_action=random.randint(0, 7)
                )
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            display.stop()

    # Initial newline to separate demo output
    print()
    display.start()
    demo_updates(display)