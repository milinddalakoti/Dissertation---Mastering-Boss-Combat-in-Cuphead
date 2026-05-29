import socket
import json
import threading
import time
import csv
import os
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

    def start(self):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
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
            except Exception as e:
                if self.running:
                    print(f"[-] Error accepting connections: {e}")

    def _handle_client(self, client_socket):
        buffer = ""
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
            self.latest_state.update(message)
            print(f"[STATE UPDATE] {message}")

            # Check for episode end to trigger automatic restart
            self._check_episode_end_and_restart(message)
        except json.JSONDecodeError:
            print(f"[!] Failed to parse JSON: {message_str}")

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
                print(f"[INPUT] Move Left")
            elif action == "move_right":
                self.keyboard.press(Key.right)
                print(f"[INPUT] Move Right")
            elif action == "jump":
                self.keyboard.press(KeyCode.from_char('z'))  # Z key for jump
                print(f"[INPUT] Jump")
            elif action == "shoot":
                self.keyboard.press(KeyCode.from_char('x'))  # X key for shoot
                print(f"[INPUT] Shoot")
            elif action == "dash":
                self.keyboard.press(KeyCode.from_char('c'))  # C key for dash
                print(f"[INPUT] Dash")
            else:
                print(f"[INPUT WARN] Unknown action: {action}")

        except Exception as e:
            print(f"[INPUT ERROR] Failed to send input '{action}': {e}")

    def _release_key(self, action):
        """Release a specific key"""
        try:
            key_map = {
                "move_left": Key.left,
                "move_right": Key.right,
                "jump": KeyCode.from_char('z'),
                "shoot": KeyCode.from_char('x'),
                "dash": KeyCode.from_char('c')
            }

            if action in key_map:
                self.keyboard.release(key_map[action])
                print(f"[INPUT RELEASE] {action}")
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
            if self.command_socket is None or not self.command_socket.fileno() >= 0:
                # Create a new connection for sending commands
                self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.command_socket.connect((self.host, 5001))  # Different port for commands
                print(f"[+] Command socket connected to {self.host}:5001")

            # Send command as JSON with newline delimiter
            command_json = json.dumps({"command": "restart_level"}) + "\n"
            self.command_socket.send(command_json.encode('utf-8'))
            print(f"[COMMAND SENT] Restart level command")
            return True
        except Exception as e:
            print(f"[-] Failed to send restart command: {e}")
            self.command_socket = None
            return False

    def _check_episode_end_and_restart(self, message):
        """Check if the message indicates episode end and trigger restart"""
        try:
            # Debug: Print the message we're checking
            print(f"[DEBUG] Checking episode end: {message}")

            # Boss death with win = True
            if message.get("event") == "boss_dead" and message.get("win") == True:
                print(f"[EPISODE END] Boss defeated! Scheduling restart...")
                threading.Timer(2.0, self.send_restart_command).start()  # Restart after 2 seconds

            # Player death with win = False
            elif message.get("event") == "player_dead" and message.get("win") == False:
                print(f"[EPISODE END] Player died! Scheduling restart...")
                threading.Timer(2.0, self.send_restart_command).start()  # Restart after 2 seconds
            else:
                # More detailed debug for troubleshooting
                event = message.get("event")
                win = message.get("win")
                print(f"[DEBUG] Not triggering restart - event: {event}, win: {win}")
        except Exception as e:
            print(f"[ERROR] Failed to check episode end: {e}")
            import traceback
            traceback.print_exc()

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