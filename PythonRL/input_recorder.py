#!/usr/bin/env python3
"""
Input Recorder for Cuphead RL
Records keyboard inputs sent to the game during gameplay for CSV playback
"""

import json
import threading
import time
import csv
import os
from datetime import datetime
from environment_server import CupheadEnvironmentServer


class InputRecorder:
    def __init__(self, environment_server=None, output_dir="recordings"):
        """
        Initialize the input recorder

        Args:
            environment_server: Existing CupheadEnvironmentServer instance (optional)
            output_dir: Directory to save CSV recordings
        """
        self.env_server = environment_server or CupheadEnvironmentServer()
        self.output_dir = output_dir
        self.recording = False
        self.recorded_inputs = []
        self.start_time = None
        self.record_thread = None

        # Create output directory if it doesn't exist
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def start_recording(self):
        """Start recording inputs"""
        if self.recording:
            print("[RECORDER] Already recording")
            return False

        self.recording = True
        self.recorded_inputs = []
        self.start_time = time.time()
        print(f"[RECORDER] Started recording inputs to {self.output_dir}")
        return True

    def stop_recording(self):
        """Stop recording and save to CSV file"""
        if not self.recording:
            print("[RECORDER] Not currently recording")
            return None

        self.recording = False
        if self.record_thread:
            self.record_thread.join(timeout=1.0)

        if not self.recorded_inputs:
            print("[RECORDER] No inputs recorded")
            return None

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}.csv"
        filepath = os.path.join(self.output_dir, filename)

        # Write CSV file
        try:
            with open(filepath, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['time_offset', 'action', 'value'])  # Header

                for inp in self.recorded_inputs:
                    writer.writerow([
                        inp['time_offset'],
                        inp['action'],
                        inp['value']
                    ])

            print(f"[RECORDER] Saved {len(self.recorded_inputs)} inputs to {filepath}")
            return filepath
        except Exception as e:
            print(f"[RECORDER ERROR] Failed to save CSV: {e}")
            return None

    def _record_worker(self):
        """Background thread to monitor for inputs to record"""
        last_state = {}

        while self.recording:
            try:
                # Get current state from environment server
                current_state = self.env_server.get_state()

                # We don't have direct access to what inputs were sent,
                # so we'll record based on state changes or provide a manual recording method
                # For now, this will be a placeholder - actual recording would need
                # integration with the input sending methods

                time.sleep(0.1)  # Check every 100ms

            except Exception as e:
                print(f"[RECORDER ERROR] Recording worker error: {e}")
                time.sleep(1.0)

    def record_input_manually(self, action, value):
        """Manually record an input (to be called when sending inputs)"""
        if not self.recording:
            return

        if self.start_time is None:
            self.start_time = time.time()

        time_offset = time.time() - self.start_time

        self.recorded_inputs.append({
            'time_offset': round(time_offset, 3),
            'action': action,
            'value': value
        })

        # Optional: print for debugging
        # print(f"[RECORDER] Recorded: {action}={value} at {time_offset:.3f}s")


def demo_recording():
    """Demonstrate how to use the input recorder"""
    print("Input Recorder Demo")
    print("=" * 30)

    # Create recorder
    recorder = InputRecorder()

    # Start recording
    recorder.start_recording()

    print("Recording started. Perform actions in the game...")
    print("Press Enter to stop recording and save...")

    try:
        input()  # Wait for user to press Enter
    except KeyboardInterrupt:
        pass

    # Stop recording and save
    filepath = recorder.stop_recording()
    if filepath:
        print(f"Recording saved to: {filepath}")
        print("You can now play this back using environment_server.py")


if __name__ == "__main__":
    demo_recording()