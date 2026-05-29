#!/usr/bin/env python3
"""
Test script for level restart and CSV playback functionality
Usage: python test_restart_and_playback.py
"""

import sys
import os
import time
import threading
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from environment_server import CupheadEnvironmentServer
from input_recorder import InputRecorder

def test_restart_functionality():
    """Test the level restart functionality"""
    print("Testing Level Restart Functionality")
    print("=" * 40)

    # Create environment server
    server = CupheadEnvironmentServer()
    server.start()

    print("Environment server started on port 5000")
    print("Make sure Cuphead is running and the plugin is loaded")
    print("Listen for restart commands on port 5001...")
    print()

    try:
        # Wait a bit for connections
        time.sleep(2)

        # Test sending a restart command
        print("Sending test restart command...")
        result = server.send_restart_command()
        if result:
            print("Restart command sent successfully!")
        else:
            print("Failed to send restart command")

        # Wait to see if it works
        print("Waiting 5 seconds to observe restart...")
        time.sleep(5)

    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    finally:
        server.stop()
        print("Environment server stopped")

def test_csv_playback():
    """Test CSV playback functionality"""
    print("\nTesting CSV Playback Functionality")
    print("=" * 40)

    # Create a test CSV file first
    test_csv_content = """time_offset,action,value
0.0,move_left,1.0
0.5,move_left,1.0
1.0,jump,1.0
1.5,jump,0.0
2.0,move_right,1.0
2.5,move_right,1.0
3.0,shoot,1.0
3.5,shoot,0.0
4.0,dash,1.0
4.5,dash,0.0
"""

    test_csv_path = "test_recording.csv"
    with open(test_csv_path, 'w') as f:
        f.write(test_csv_content)

    print(f"Created test CSV file: {test_csv_path}")

    # Create environment server
    server = CupheadEnvironmentServer()

    # Load the CSV
    print("Loading CSV for playback...")
    if server.load_csv_playback(test_csv_path):
        print("CSV loaded successfully!")
        print(f"Loaded {len(server.csv_data)} commands")

        # Start playback
        print("Starting playback...")
        if server.start_playback(loop=False):
            print("Playback started!")
            print("Make sure Cuphead is running and focused")
            print("Watch the character perform the recorded actions")

            # Wait for playback to complete
            time.sleep(6)  # Wait longer than the CSV duration

            # Stop playback
            server.stop_playback()
            print("Playback stopped")
        else:
            print("Failed to start playback")
    else:
        print("Failed to load CSV")

    server.stop()

    # Clean up test file
    if os.path.exists(test_csv_path):
        os.remove(test_csv_path)
        print(f"Cleaned up test file: {test_csv_path}")

def test_recorder():
    """Test the input recorder"""
    print("\nTesting Input Recorder")
    print("=" * 40)

    recorder = InputRecorder(output_dir="test_recordings")

    print("Starting recorder...")
    recorder.start_recording()

    print("Recording started. Perform some actions in the game...")
    print("Press Enter to stop recording after 5 seconds...")

    # Simulate some manual recording for demo
    time.sleep(1)
    recorder.record_input_manually("move_left", 1.0)
    time.sleep(0.5)
    recorder.record_input_manually("jump", 1.0)
    time.sleep(0.5)
    recorder.record_input_manually("jump", 0.0)
    time.sleep(0.5)
    recorder.record_input_manually("shoot", 1.0)
    time.sleep(0.5)
    recorder.record_input_manually("shoot", 0.0)

    # Wait for user to stop
    try:
        input()  # Wait for user to press Enter
    except KeyboardInterrupt:
        pass

    # Stop recording and save
    filepath = recorder.stop_recording()
    if filepath:
        print(f"Recording saved to: {filepath}")

        # Show what was recorded
        print("Recorded inputs:")
        for inp in recorder.recorded_inputs:
            print(f"  {inp}")
    else:
        print("No recording saved")

def main():
    print("Cuphead RL - Restart and Playback Test Suite")
    print("=" * 50)
    print("Make sure:")
    print("1. Cuphead is running with the plugin loaded")
    print("2. The game is at the main menu or in a boss fight")
    print("3. This test will create its own environment server")
    print()

    while True:
        print("Select test:")
        print("1. Test Level Restart Functionality")
        print("2. Test CSV Playback Functionality")
        print("3. Test Input Recorder")
        print("4. Run All Tests")
        print("5. Exit")

        choice = input("\nEnter choice (1-5): ").strip()

        if choice == "1":
            test_restart_functionality()
        elif choice == "2":
            test_csv_playback()
        elif choice == "3":
            test_recorder()
        elif choice == "4":
            test_restart_functionality()
            test_csv_playback()
            test_recorder()
        elif choice == "5":
            print("Exiting...")
            break
        else:
            print("Invalid choice, please try again")

        print("\n" + "=" * 50)

if __name__ == "__main__":
    main()