#!/usr/bin/env python3
"""
Simple test script to send commands to the Cuphead RL Plugin
Usage: python test_commands.py
"""

import socket
import json
import time

def send_command(command_dict, host='127.0.0.1', port=5001):
    """Send a JSON command to the game via TCP"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))

        # Send command as JSON with newline delimiter
        command_json = json.dumps(command_dict) + "\n"
        sock.send(command_json.encode('utf-8'))
        print(f"[SENT] {command_dict}")

        sock.close()
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send command: {e}")
        return False

def main():
    print("Cuphead RL Plugin Command Tester")
    print("=" * 40)
    print("Make sure:")
    print("1. Cuphead is running with the plugin loaded")
    print("2. Python environment_server.py is running")
    print("3. Game is at the main menu or in a boss fight")
    print()

    # Test sequence
    test_commands = [
        {"command": "restart_level"},
        {"command": "player_action", "action": "move_left", "value": 1.0},
        {"command": "player_action", "action": "jump", "value": 1.0},
        {"command": "player_action", "action": "shoot", "value": 1.0},
        {"command": "player_action", "action": "dash", "value": 1.0},
        {"command": "player_action", "action": "move_right", "value": 1.0},
    ]

    for i, command in enumerate(test_commands, 1):
        print(f"\nTest {i}/{len(test_commands)}: Sending {command}")
        success = send_command(command)
        if success:
            print("  -> Command sent successfully")
        else:
            print("  -> Failed to send command")
        time.sleep(2)  # Wait between commands

    print("\n" + "=" * 40)
    print("Testing complete!")
    print("Check cuphead_debug.log for command processing results")

if __name__ == "__main__":
    main()