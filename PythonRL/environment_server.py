import socket
import json
import threading
import time
import csv
import os
import random
import logging
from pynput.keyboard import Key, Controller

class CupheadEnvironmentServer:
    def __init__(self, host='127.0.0.1', port=5000):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.running = False
        self.latest_state = {}
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
        self.action_interval = 0.3    # seconds between each action press/release
        self.press_hold = 0.15        # how long to hold each key (seconds)
        self.burst_duration = 8.0     # how long the random actions last after activation

        # Setup logging to file
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cuphead_rl.log")
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] %(message)s',
            datefmt='%H:%M:%S',
            handlers=[
                logging.FileHandler(log_path, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('CupheadRL')
        self.logger.info(f"Log file created at: {log_path}")

    def start(self):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        self.running = True
        self.logger.info(f"Python RL Environment Server listening on {self.host}:{self.port}")

        # Run server in a thread so it doesn't block
        thread = threading.Thread(target=self._accept_connections)
        thread.daemon = True
        thread.start()

    def _accept_connections(self):
        while self.running:
            try:
                client_socket, addr = self.server_socket.accept()
                self.logger.info(f"Connected to Cuphead Engine at {addr}")
                self._handle_client(client_socket)
            except Exception as e:
                if self.running:
                    self.logger.error(f"Error accepting connections: {e}")

    def _handle_client(self, client_socket):
        buffer = ""
        while self.running:
            try:
                data = client_socket.recv(1024).decode('utf-8')
                if not data:
                    self.logger.warning("Cuphead Engine disconnected. Waiting for reconnect...")
                    break

                buffer += data
                # Process complete JSON lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        self._process_message(line)

            except ConnectionResetError:
                self.logger.warning("Cuphead Engine connection reset. Waiting for reconnect...")
                break
            except Exception as e:
                self.logger.error(f"Client handler error: {e}")
                break

        client_socket.close()

    def _process_message(self, message_str):
        try:
            message = json.loads(message_str)
            self.latest_state.update(message)
            self.logger.info(f"STATE UPDATE: {json.dumps(message)}")

            # Check for episode end to trigger automatic restart
            self._check_episode_end_and_restart(message)

            # ---- NEW: Detect fight start/restart and manage random actions ----
            event_type = message.get("event", "NO_EVENT")
            self.logger.info(f"Processing message - Event: '{event_type}', Full message: {json.dumps(message)}")

            # Handle level loaded events (fight start or restart)
            if event_type == "level_loaded":
                self.logger.info(f"LEVEL LOADED: Level: {message.get('level', 'Unknown')}")
                # Reset state to allow new burst on each level load/restart
                self._state_received = False

                # If we're not already active, start the delayed action loop
                if not self._random_active:
                    self.logger.info(f"FIGHT START DETECTED: Received level_loaded event - starting delayed random action burst.")
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
                            self.logger.info(f"RANDOM THREAD: Random action thread started - THREAD ID: {self._random_thread.ident if self._random_thread else 'None'}")
                else:
                    self.logger.info(f"FIGHT RESTART: Level reloaded while actions were active - continuing current burst")

            # Ignore our own connection confirmation message
            elif event_type == "connected":
                self.logger.info("Ignoring our own connection confirmation message")

            # Handle any other genuine game state update as fallback
            elif not self._state_received and event_type != "NO_EVENT" and event_type != "connected":
                self._state_received = True
                self.logger.info(f"FIGHT START DETECTED: Received game state update - starting delayed random action burst.")
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
                        self.logger.info(f"RANDOM THREAD: Random action thread started - THREAD ID: {self._random_thread.ident if self._random_thread else 'None'}")
            else:
                # Log what we're receiving for debugging
                if event_type != "NO_EVENT":
                    self.logger.info(f"Received event: {event_type} - waiting for fight start")
                else:
                    self.logger.info(f"Message has no event field: {json.dumps(message)}")
            # If a burst is already running we let it continue; when it ends,
            # the next state update will start a new burst (keeps actions going
            # throughout the fight).
        except json.JSONDecodeError:
            self.logger.warning(f"Failed to parse JSON: {message_str}")
        except Exception as e:
            self.logger.error(f"Unexpected error in _process_message: {e}")
            import traceback
            traceback.print_exc()

    def get_state(self):
        return self.latest_state

    def send_input(self, action, value=1.0):
        """Send input to the game using pynput"""
        try:
            if value <= 0.5:
                # Release key if value is low
                self._release_key(action)
                return

            # Press key based on action
            if action == "move_left":
                self.keyboard.press(Key.left)
                self.logger.info("[INPUT] Move Left")
            elif action == "move_right":
                self.keyboard.press(Key.right)
                self.logger.info("[INPUT] Move Right")
            elif action == "aim_up":
                self.keyboard.press(Key.up)
                self.logger.info("[INPUT] Aim Up")
            elif action == "duck_crouch":
                self.keyboard.press(Key.down)
                self.logger.info("[INPUT] Duck/Crouch")
            elif action == "jump":
                self.keyboard.press(KeyCode.from_char('z'))  # Z key for jump
                self.logger.info("[INPUT] Jump")
            elif action == "shoot":
                self.keyboard.press(KeyCode.from_char('x'))  # X key for shoot
                self.logger.info("[INPUT] Shoot")
            elif action == "dash":
                self.keyboard.press(Key.shift)  # Left Shift key for dash
                self.logger.info("[INPUT] Dash")
            elif action == "directional_aim":
                self.keyboard.press(KeyCode.from_char('c'))  # C key for directional aim
                self.logger.info("[INPUT] Directional Aim")
            else:
                self.logger.warning(f"[INPUT WARN] Unknown action: {action}")

        except Exception as e:
            self.logger.error(f"[INPUT ERROR] Failed to send input '{action}': {e}")

    def _release_key(self, action):
        """Release a specific key"""
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
                self.logger.info(f"[INPUT RELEASE] {action}")
        except Exception as e:
            self.logger.error(f"[INPUT ERROR] Failed to release key '{action}': {e}")

    def send_inputs(self, actions_dict):
        """Send multiple inputs based on a dictionary"""
        for action, value in actions_dict.items():
            self.send_input(action, value)

    # RESTART FUNCTIONALITY
    def send_restart_command(self):
        """Send a restart command to the game via TCP on port 5001"""
        try:
            if self.command_socket is None:
                # Create a new connection for sending commands
                self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.command_socket.connect((self.host, 5001))  # Different port for commands
                self.logger.info(f"Command socket connected to {self.host}:5001")
            elif not self._is_socket_connected(self.command_socket):
                # Socket exists but is not connected, recreate it
                try:
                    self.command_socket.close()
                except:
                    pass
                self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.command_socket.connect((self.host, 5001))  # Different port for commands
                self.logger.info(f"Command socket reconnected to {self.host}:5001")

            # Send command as JSON with newline delimiter
            command_json = json.dumps({"command": "restart_level"}) + "\n"
            sent = self.command_socket.send(command_json.encode('utf-8'))
            if sent:
                self.logger.info(f"[COMMAND SENT] Restart level command ({sent} bytes)")
                return True
            else:
                self.logger.error("Failed to send restart command: zero bytes sent")
                self.command_socket = None
                return False
        except Exception as e:
            self.logger.error(f"Failed to send restart command: {e}")
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
            # Debug: Print the message we're checking
            self.logger.info(f"Checking episode end: {json.dumps(message)}")

            # Boss death with win = True
            if message.get("event") == "boss_dead" and message.get("win") == True:
                self.logger.info("EPISODE END: Boss defeated! Scheduling restart...")
                threading.Timer(2.0, self.send_restart_command).start()  # Restart after 2 seconds

            # Player death with win = False
            elif message.get("event") == "player_dead" and message.get("win") == False:
                self.logger.info("EPISODE END: Player died! Scheduling restart...")
                threading.Timer(2.0, self.send_restart_command).start()  # Restart after 2 seconds
            else:
                # More detailed debug for troubleshooting
                event = message.get("event")
                win = message.get("win")
                self.logger.info(f"Not triggering restart - event: {event}, win: {win}")
        except Exception as e:
            self.logger.error(f"Failed to check episode end: {e}")
            import traceback
            traceback.print_exc()

    # CSV PLAYBACK FUNCTIONALITY
    def load_csv_playback(self, file_path):
        """Load CSV data for playback"""
        try:
            if not os.path.exists(file_path):
                self.logger.error(f"CSV file not found: {file_path}")
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
                        self.logger.warning(f"Skipping invalid row in CSV: {row} - {e}")

            # Sort by time offset to ensure proper sequence
            self.csv_data.sort(key=lambda x: x['time_offset'])
            self.logger.info(f"PLAYBACK: Loaded {len(self.csv_data)} commands from {file_path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load CSV playback: {e}")
            return False

    def start_playback(self, loop=False):
        """Start playing back the loaded CSV data"""
        if not self.csv_data:
            self.logger.error("No CSV data loaded for playback")
            return False

        if self.playback_active:
            self.logger.warning("Playback already active")
            return False

        self.playback_active = True
        self.playback_paused = False
        self.csv_index = 0
        self.playback_start_time = time.time()
        self.loop = loop

        self.playback_thread = threading.Thread(target=self._playback_worker)
        self.playback_thread.daemon = True
        self.playback_thread.start()
        self.logger.info(f"PLAYBACK: Started playback (loop={loop})")
        return True

    def stop_playback(self):
        """Stop the current playback"""
        self.playback_active = False
        if self.playback_thread:
            self.playback_thread.join(timeout=1.0)
        self.logger.info("PLAYBACK: Stopped playback")

    def pause_playback(self):
        """Pause the current playback"""
        self.playback_paused = True
        self.logger.info("PLAYBACK: Playback paused")

    def resume_playback(self):
        """Resume the paused playback"""
        self.playback_paused = False
        self.logger.info("PLAYBACK: Playback resumed")

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
                self.logger.info("PLAYBACK: Looping playback")
                # Continue the loop (will go back to start of while)
            else:
                self.playback_active = False
                self.logger.info("PLAYBACK: Playback completed")

        except Exception as e:
            self.logger.error(f"PLAYBACK ERROR: Playback worker error: {e}")
        finally:
            self.playback_active = False

    # RANDOM BOSS FUNCTIONALITY
    def _delayed_random_action_loop(self, duration):
        """
        Waits 3 seconds for the fight to actually start, then runs the random action loop.
        """
        self.logger.info("RANDOM LOOP: Waiting 3 seconds for fight to start...")
        time.sleep(3.0)  # Wait for fight to actually begin after level load

        if not self.running:
            return

        self.logger.info("RANDOM LOOP: Starting random action loop after 3-second delay")
        self._random_action_loop(duration)

    # ------------------------------------------------------------------
    # Random action loop – runs in its own daemon thread
    # ------------------------------------------------------------------
    def _random_action_loop(self, duration):
        """
        Repeatedly pick a random action, press it for `self.press_hold` seconds,
        release, then wait the remainder of `self.action_interval` before the next.
        Runs for `duration` seconds or until the server is stopped.
        """
        end_time = time.time() + duration
        actions = ["move_left", "move_right", "jump", "shoot", "dash"]
        action_count = 0

        self.logger.info(f"RANDOM LOOP: Starting random action loop for {duration} seconds")

        while time.time() < end_time and self.running:
            action = random.choice(actions)
            action_count += 1
            # Press
            self.logger.info(f"RANDOM LOOP: Action #{action_count}: Pressing {action}")
            self.send_input(action, 1.0)
            self.logger.info(f"RANDOM INPUT: Press {action}")
            time.sleep(self.press_hold)
            # Release
            self.logger.info(f"RANDOM LOOP: Action #{action_count}: Releasing {action}")
            self.send_input(action, 0.0)
            self.logger.info(f"RANDOM INPUT: Release {action}")
            # Wait the rest of the interval
            time.sleep(max(0.0, self.action_interval - self.press_hold))

        # Clean up flag
        with self._random_lock:
            self._random_active = False
            self.logger.info(f"RANDOM ACTION: Burst finished after {action_count} actions.")

    # ------------------------------------------------------------------
    # Optional: expose a method to stop random actions early
    # ------------------------------------------------------------------
    def stop_random_actions(self):
        """Force-stop any ongoing random-action burst."""
        with self._random_lock:
            if self._random_active:
                self._random_active = False
                self.logger.info("RANDOM ACTION: Stopped by user request.")

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
    try:
        # Keep main thread alive
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.stop()