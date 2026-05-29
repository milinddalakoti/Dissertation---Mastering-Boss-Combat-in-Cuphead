#!/usr/bin/env python3
"""
Test script for pynput-based input control in Cuphead RL Environment
Usage: python test_commands_pynput.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from environment_server import CupheadEnvironmentServer
import time

def test_inputs():
    """Test various input actions"""
    print("Cuphead RL Pynput Input Tester")
    print("=" * 40)
    print("Make sure:")
    print("1. Cuphead is running")
    print("2. This script will create its own environment server")
    print("3. Game is at the main menu or in a boss fight")
    print()

    # Create environment server (just for the input methods, not for networking)
    server = CupheadEnvironmentServer()

    # Test sequence
    test_actions = [
        ("move_left", 1.0),
        ("move_right", 1.0),
        ("jump", 1.0),
        ("shoot", 1.0),
        ("dash", 1.0),
    ]

    print("Testing individual actions (press/release cycle):")
    for action, value in test_actions:
        print(f"\nTesting: {action}")
        print("  Pressing key...")
        server.send_input(action, value)
        time.sleep(0.3)  # Hold key briefly
        print("  Releasing key...")
        server.send_input(action, 0.0)  # Release
        time.sleep(0.5)  # Wait between tests

    print("\n" + "=" * 40)
    print("Testing complete!")
    print("Check if the game character responded to the inputs")

def test_combined_inputs():
    """Test sending multiple inputs at once"""
    print("\nTesting combined inputs...")
    server = CupheadEnvironmentServer()

    # Test move left + jump
    print("Testing: move_left + jump")
    actions = {"move_left": 1.0, "jump": 1.0}
    server.send_inputs(actions)
    time.sleep(0.3)

    # Release
    release_actions = {"move_left": 0.0, "jump": 0.0}
    server.send_inputs(release_actions)
    time.sleep(0.5)

    # Test move right + shoot
    print("Testing: move_right + shoot")
    actions = {"move_right": 1.0, "shoot": 1.0}
    server.send_inputs(actions)
    time.sleep(0.3)

    # Release
    release_actions = {"move_right": 0.0, "shoot": 0.0}
    server.send_inputs(release_actions)
    time.sleep(0.5)

def main():
    print("Select test mode:")
    print("1. Individual action tests")
    print("2. Combined input tests")
    print("3. Both")

    choice = input("\nEnter choice (1-3): ").strip()

    if choice == "1":
        test_inputs()
    elif choice == "2":
        test_combined_inputs()
    elif choice == "3":
        test_inputs()
        test_combined_inputs()
    else:
        print("Invalid choice, running individual tests...")
        test_inputs()

if __name__ == "__main__":
    main()