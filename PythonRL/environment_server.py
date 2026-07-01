import socket
import json
import threading
import time
import csv
import os
import random
import logging
from pynput.keyboard import Key, Controller, KeyCode

# Setup logging to file
logger = logging.getLogger('CupheadEnv')

class CupheadEnvironmentServer:
    def __init__(self, host='127.0.0.1', port=5000):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.running = False
        self.latest_state = {}
        self._state_lock = threading.Lock()  # Thread-safe state access
        self.keyboard = Controller()  # For pynput keyboard control
        self.command_socket = None  # For sending commands to the game (port 5001)
        self.playback_thread = None
        self.playback_active = False
        self.playback_paused = False
        self.csv_data = []
        self.csv_index = 0
        self.playback_start_time = 0

        # Random boss functionality
        self._random_active = False
        self._random_lock = threading.Lock()
        self._random_thread = None
        self._state_received = False  # Track if we've seen genuine game state

        # Gym mode - disable random actions when used with PPO/Gym
        self._gym_mode = False
        self._fight_active = False   # Track if fight has actually started (not just level loaded)
        self.action_interval = 0.5    # seconds between each action press/release (increased for stability)
        self.press_hold = 0.2       # how long to hold each key (seconds) - increased for stability
        self.burst_duration = 8.0     # how long the random actions last after activation
        self._last_burst_end = 0    # track burst end time for debouncing

        # Phase jump configuration - set to "Main", "BigSlime", "Tombstone", or None for normal flow
        self.auto_phase_jump = None   # Automatically jump to this phase on level load
        self.auto_phase_set_health = False  # If True, set boss HP to phase threshold when jumping

    def start(self):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        # Set timeout on accept to allow checking self.running periodically
        self.server_socket.settimeout(1.0)
        self.running = True
        print(f"[*] Python RL Environment Server listening on {self.host}:{self.port}")

        # Run server in a thread so it doesn't block
        thread = threading.Thread(target=self._accept_connections)
        thread.daemon = True
        thread.start()

    def _accept_connections(self):
        while self.running:
            try:
                client_socket, addr = self.server_socket.accept()
                print(f"[+] Connected to Cuphead Engine at {addr}")
                self._handle_client(client_socket)
            except socket.timeout:
                # Timeout is expected - just continue loop to check self.running
                continue
            except Exception as e:
                if self.running:
                    print(f"[-] Error accepting connections: {e}")

    def _handle_client(self, client_socket):
        buffer = ""
        # Set timeout on client socket to allow checking self.running
        client_socket.settimeout(1.0)
        while self.running:
            try:
                data = client_socket.recv(1024).decode('utf-8')
                if not data:
                    print("[-] Cuphead Engine disconnected. Waiting for reconnect...")
                    break

                buffer += data
                # Process complete JSON lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        self._process_message(line)

            except socket.timeout:
                # Timeout is expected - just continue loop to check self.running
                continue
            except ConnectionResetError:
                print("[-] Cuphead Engine connection reset. Waiting for reconnect...")
                break
            except Exception as e:
                print(f"[-] Client handler error: {e}")
                break

        client_socket.close()

    def _process_message(self, message_str):
        try:
            message = json.loads(message_str)
            with self._state_lock:
                # Preserve terminal events (player_dead, boss_dead) - don't let state_update overwrite them
                existing_event = self.latest_state.get('event', '')
                is_terminal_event = existing_event in ('player_dead', 'boss_dead')
                
                self.latest_state.update(message)
                
                # If we had a terminal event, preserve it and don't let state_update overwrite
                if is_terminal_event:
                    # Keep the terminal event, but update other fields like player_positions
                    original_event = existing_event
                    original_win = self.latest_state.get('win', '')
                    self.latest_state['event'] = original_event
                    self.latest_state['win'] = original_win

            # Check for episode end to trigger automatic restart (only in random mode, NOT gym/PPO mode)
            # In PPO mode, ppo_training.py handles restarts via _check_done
            if not self._gym_mode:
                self._check_episode_end_and_restart(message)

            # ---- Detect fight start/restart and manage random actions ----
            event_type = message.get("event", "NO_EVENT")

            # Handle level loaded events (fight start or restart)
            if event_type == "level_loaded":
                self._state_received = False
                self._fight_active = False

                # If we're not already active, start the delayed action loop
                if not self._random_active and not self._gym_mode:
                    logger.info(f"FIGHT START DETECTED: Received level_loaded event - starting delayed random action burst.")
                    with self._random_lock:
                        if not self._random_active:
                            self._random_active = True
                            # Start the random action loop with a 3-second delay
                            self._random_thread = threading.Thread(
                                target=self._delayed_random_action_loop,
                                args=(self.burst_duration,),
                                daemon=True,
                            )
                            self._random_thread.start()
                            logger.info(f"RANDOM THREAD: Random action thread started - THREAD ID: {self._random_thread.ident if self._random_thread else 'None'}")
                else:
                    logger.info(f"FIGHT RESTART: Level reloaded while actions were active - continuing current burst")

            # Ignore our own connection confirmation message
            elif event_type == "connected":
                pass  # Ignore silently

            # Handle state_update - mark fight as active and start random actions if needed
            elif event_type == "state_update":
                # Check if this is actual gameplay (not just level load)
                boss_positions = message.get('boss_positions', [{}])
                level_time = message.get('level_time', 0)
                boss_phase = message.get('boss_phase', 'Unknown')
                # If we have real gameplay state, mark fight as active
                # Fight starts when: boss has position, level_time > 0.1s, or boss phase is known (not Unknown/Generic)
                boss_phase_active = boss_phase not in ('Unknown', 'Generic')
                if (boss_positions and len(boss_positions) > 0 and boss_positions[0].get('x', 0) != 0) or level_time > 0.1 or boss_phase_active:
                    self._fight_active = True
                    if not self._state_received and not self._gym_mode:
                        self._state_received = True
                        logger.info(f"Fight start detected via state: {event_type}")
            else:
                # Silent for NO_EVENT
                pass
        except json.JSONDecodeError:
            print(f"[!] Failed to parse JSON: {message_str}")
        except Exception as e:
            print(f"[!] Unexpected error in _process_message: {e}")
            import traceback
            print(traceback.format_exc())

    def get_state(self):
        with self._state_lock:
            return dict(self.latest_state)  # Return a copy to avoid external modifications

    def is_connected(self):
        """Check if we have an active connection to Cuphead."""
        try:
            # Check if we have any state data (indicates connection is alive)
            if self.latest_state.get('event') in ('level_loaded', 'state_update', 'boss_hit', 'player_dead', 'boss_dead', 'connected'):
                return True
            return False
        except:
            return False

    def wait_for_connection(self, timeout=30.0):
        """Wait for Cuphead to reconnect after a disconnect."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.is_connected():
                return True
            time.sleep(0.5)
        return False

    def send_input(self, action, value=1.0):
        """Send input to the game using pynput"""
        try:
            if value <= 0.5:
                # Release key if value is low
                self._release_key(action)
                return

            # Press key based on action - silent for live display compatibility
            if action == "move_left":
                self.keyboard.press(Key.left)
            elif action == "move_right":
                self.keyboard.press(Key.right)
            elif action == "aim_up":
                self.keyboard.press(Key.up)
            elif action == "duck_crouch":
                self.keyboard.press(Key.down)
            elif action == "jump":
                self.keyboard.press(KeyCode.from_char('z'))
            elif action == "shoot":
                self.keyboard.press(KeyCode.from_char('x'))
            elif action == "dash":
                self.keyboard.press(Key.shift)
            elif action == "directional_aim":
                self.keyboard.press(KeyCode.from_char('c'))
            else:
                pass  # Unknown action - silent

        except Exception as e:
            print(f"[INPUT ERROR] Failed to send input '{action}': {e}")

    def _release_key(self, action):
        """Release a specific key - silent for live display compatibility"""
        try:
            key_map = {
                "move_left": Key.left,
                "move_right": Key.right,
                "aim_up": Key.up,
                "duck_crouch": Key.down,
                "jump": KeyCode.from_char('z'),
                "shoot": KeyCode.from_char('x'),
                "dash": Key.shift,
                "directional_aim": KeyCode.from_char('c')
            }

            if action in key_map:
                self.keyboard.release(key_map[action])
        except Exception as e:
            print(f"[INPUT ERROR] Failed to release key '{action}': {e}")

    def send_inputs(self, actions_dict):
        """Send multiple inputs based on a dictionary"""
        for action, value in actions_dict.items():
            self.send_input(action, value)

    # RESTART FUNCTIONALITY
    def send_restart_command(self):
        """Send a restart command to the game via TCP on port 5001"""
        try:
            # Create socket with timeout to prevent indefinite blocking
            if self.command_socket is None:
                self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.command_socket.settimeout(5.0)  # 5 second timeout for connect/send
                self.command_socket.connect((self.host, 5001))
                print(f"[+] Command socket connected to {self.host}:5001")
            elif not self._is_socket_connected(self.command_socket):
                try:
                    self.command_socket.close()
                except:
                    pass
                self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.command_socket.settimeout(5.0)
                self.command_socket.connect((self.host, 5001))
                print(f"[+] Command socket reconnected to {self.host}:5001")

            # Send command as JSON with newline delimiter
            command_json = json.dumps({"command": "restart_level"}) + "\n"
            sent = self.command_socket.send(command_json.encode('utf-8'))
            if sent:
                print(f"[COMMAND SENT] Restart level command ({sent} bytes)")
                return True
            else:
                print(f"[-] Failed to send restart command: zero bytes sent")
                self.command_socket = None
                return False
        except socket.timeout:
            print(f"[-] Restart command timed out after 5s")
            self.command_socket = None
            return False
        except Exception as e:
            print(f"[-] Failed to send restart command: {e}")
            self.command_socket = None
            return False

    def _is_socket_connected(self, sock):
        """Check if socket is still connected"""
        try:
            # This will raise an exception if socket is not connected
            sock.getpeername()
            return True
        except:
            return False

    def _check_episode_end_and_restart(self, message):
        """Check if the message indicates episode end and trigger restart"""
        try:
            event = message.get("event")
            win = message.get("win")
            # Only act on explicit death events, never on level_loaded or other events
            # Normalize win value for comparison (handle boolean and string forms)
            is_win = win is True or (isinstance(win, str) and win.lower() == 'true')
            is_loss = win is False or (isinstance(win, str) and win.lower() == 'false')

            if event == "boss_dead" and is_win:
                print(f"[RESTART] Boss dead - scheduling restart")
                threading.Timer(2.0, self.send_restart_command).start()
            elif event == "player_dead" and is_loss:
                print(f"[RESTART] Player dead - scheduling restart")
                threading.Timer(2.0, self.send_restart_command).start()
            # Ignore all other events including level_loaded
        except Exception as e:
            print(f"[ERROR] Failed to check episode end: {e}")
            import traceback
            traceback.print_exc()

    def clear_state_for_new_episode(self):
        """Thread-safe state clearing for episode transitions"""
        with self._state_lock:
            self.latest_state['event'] = ''
            self.latest_state['win'] = None
            self.latest_state['player_positions'] = [
                {'player_id': 1, 'is_dead': False, 'x': 0, 'y': 0, 'health': 3},
                {'player_id': 2, 'is_dead': False, 'x': 0, 'y': 0, 'health': 3}
            ]
            self.latest_state['boss_positions'] = [{}]
            self.latest_state['boss_hp'] = 1200
            self.latest_state['boss_hp_pct'] = 100.0
            self.latest_state['boss_phase'] = 'Unknown'

    # PHASE JUMP FUNCTIONALITY
    def send_phase_jump_command(self, phase_name, set_health=False):
        """Send a phase jump command to the plugin to skip to a specific boss phase.

        Args:
            phase_name: One of "Main", "BigSlime", "Tombstone"
            set_health: If True, set boss HP to phase threshold value
        """
        try:
            if self.command_socket is None or not self._is_socket_connected(self.command_socket):
                # Create a new connection for sending commands
                self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.command_socket.settimeout(5.0)
                self.command_socket.connect((self.host, 5001))
                print(f"[+] Command socket connected to {self.host}:5001 for phase jump")

            # Send command as JSON with newline delimiter
            command_json = json.dumps({"command": "phase_jump", "phase": phase_name, "set_health": set_health}) + "\n"
            sent = self.command_socket.send(command_json.encode('utf-8'))
            if sent:
                print(f"[COMMAND SENT] Phase jump to {phase_name} (set_health={set_health}) ({sent} bytes)")
                return True
            else:
                print(f"[-] Failed to send phase jump command: zero bytes sent")
                return False
        except socket.timeout:
            print(f"[-] Phase jump command timed out after 5s")
            self.command_socket = None
            return False
        except Exception as e:
            print(f"[-] Failed to send phase jump command: {e}")
            self.command_socket = None
            return False

    # CSV PLAYBACK FUNCTIONALITY
    def load_csv_playback(self, file_path):
        """Load CSV data for playback"""
        try:
            if not os.path.exists(file_path):
                print(f"[ERROR] CSV file not found: {file_path}")
                return False

            self.csv_data = []
            with open(file_path, 'r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    try:
                        time_offset = float(row['time_offset'])
                        action = row['action']
                        value = float(row['value'])
                        self.csv_data.append({
                            'time_offset': time_offset,
                            'action': action,
                            'value': value
                        })
                    except (ValueError, KeyError) as e:
                        print(f"[WARNING] Skipping invalid row in CSV: {row} - {e}")

            # Sort by time offset to ensure proper sequence
            self.csv_data.sort(key=lambda x: x['time_offset'])
            print(f"[PLAYBACK] Loaded {len(self.csv_data)} commands from {file_path}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to load CSV playback: {e}")
            return False

    def start_playback(self, loop=False):
        """Start playing back the loaded CSV data"""
        if not self.csv_data:
            print(f"[ERROR] No CSV data loaded for playback")
            return False

        if self.playback_active:
            print(f"[WARNING] Playback already active")
            return False

        self.playback_active = True
        self.playback_paused = False
        self.csv_index = 0
        self.playback_start_time = time.time()
        self.loop = loop

        self.playback_thread = threading.Thread(target=self._playback_worker)
        self.playback_thread.daemon = True
        self.playback_thread.start()
        print(f"[PLAYBACK] Started playback (loop={loop})")
        return True

    def stop_playback(self):
        """Stop the current playback"""
        self.playback_active = False
        if self.playback_thread:
            self.playback_thread.join(timeout=1.0)
        print(f"[PLAYBACK] Stopped playback")

    def pause_playback(self):
        """Pause the current playback"""
        self.playback_paused = True
        print(f"[PLAYBACK] Playback paused")

    def resume_playback(self):
        """Resume the paused playback"""
        self.playback_paused = False
        print(f"[PLAYBACK] Playback resumed")

    def _playback_worker(self):
        """Worker thread for CSV playback"""
        try:
            while self.playback_active and self.csv_index < len(self.csv_data):
                # Check if paused
                if self.playback_paused:
                    time.sleep(0.1)
                    continue

                # Get current command
                command = self.csv_data[self.csv_index]
                current_time = time.time() - self.playback_start_time

                # Wait until it's time for this command
                if command['time_offset'] > current_time:
                    sleep_time = command['time_offset'] - current_time
                    time.sleep(min(sleep_time, 0.1))  # Sleep in small chunks to check for pause/stop
                    continue

                # Execute the command
                self.send_input(command['action'], command['value'])
                self.csv_index += 1

                # Small delay to prevent overwhelming
                time.sleep(0.01)

            # Handle looping
            if self.playback_active and self.loop:
                self.csv_index = 0
                self.playback_start_time = time.time()
                print(f"[PLAYBACK] Looping playback")
                # Continue the loop (will go back to start of while)
            else:
                self.playback_active = False
                print(f"[PLAYBACK] Playback completed")

        except Exception as e:
            print(f"[PLAYBACK ERROR] Playback worker error: {e}")
        finally:
            self.playback_active = False

    # RANDOM BOSS FUNCTIONALITY
    def _delayed_random_action_loop(self, duration):
        """
        Waits 3 seconds for the fight to actually start, then runs the random action loop.
        Silent for live display compatibility.
        """
        time.sleep(3.0)  # Wait for fight to actually begin after level load

        if not self.running:
            return

        self._random_action_loop(duration)

    # ------------------------------------------------------------------
    # Random action loop – runs in its own daemon thread
    # ------------------------------------------------------------------
    def _random_action_loop(self, duration):
        """
        Repeatedly pick a random action, press it for `self.press_hold` seconds,
        release, then wait the remainder of `self.action_interval` before the next.
        Runs for `duration` seconds or until the server is stopped.
        GUARDRAIL: Stops immediately on player death to prevent menu navigation.
        """
        end_time = time.time() + duration
        actions = ["move_left", "move_right", "jump", "shoot", "dash"]
        action_count = 0

        while time.time() < end_time and self.running:
            # CRITICAL FIX: Check if we should stop (guardrail check)
            with self._random_lock:
                if not self._random_active:
                    return
                # GUARDRAIL: Stop on player death (was detected in previous state update)
                state = dict(self.latest_state)  # Thread-safe copy
                if state.get('event') == 'player_dead':
                    print(f"[RANDOM ACTION] Player died - stopping random actions to prevent menu navigation")
                    return

            action = random.choice(actions)
            action_count += 1
            self.send_input(action, 1.0)
            time.sleep(self.press_hold)
            self.send_input(action, 0.0)
            time.sleep(max(0.0, self.action_interval - self.press_hold))

        # Clean up flag
        with self._random_lock:
            self._random_active = False

    # ------------------------------------------------------------------
    # Optional: expose a method to stop random actions early
    # ------------------------------------------------------------------
    def stop_random_actions(self):
        """Force-stop any ongoing random-action burst."""
        with self._random_lock:
            if self._random_active:
                self._random_active = False
                print("[RANDOM ACTION] Stopped by user request.")

    def stop(self):
        self.running = False
        self.stop_playback()  # Stop any active playback
        if self.command_socket:
            try:
                self.command_socket.close()
            except:
                pass
        self.server_socket.close()

# Helper class for KeyCode (since pynput doesn't have KeyCode by default in older versions)
try:
    from pynput.keyboard import KeyCode
except ImportError:
    # Fallback for older pynput versions - create a simple KeyCode class
    class KeyCode:
        @staticmethod
        def from_char(char):
            return char

if __name__ == "__main__":
    # Simple standalone test
    server = CupheadEnvironmentServer()
    server.start()
    print("Press Ctrl+C to stop the server.")
    print("To auto-jump to a phase, set server.auto_phase_jump before server.start()")
    try:
        # Keep main thread alive
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.stop()