# ==========================================================
# main_smart.py
# Fire, Smoke & Gas Leak Detection System
# ==========================================================

import subprocess
import sys
import time

# ==========================================================
# START A PYTHON SCRIPT
# ==========================================================

def start_process(script_name):
    print(f"[STARTING] {script_name}")

    return subprocess.Popen(
        [sys.executable, script_name]
    )

# ==========================================================
# MAIN
# ==========================================================

def main():

    print("=" * 70)
    print("SMART HOME FIRE / SMOKE / GAS DETECTION SYSTEM")
    print("=" * 70)

    print("\nLaunching services...\n")

    publisher = start_process("real_api_publisher.py")

    # Small delay so publisher connects first
    time.sleep(2)

    detector = start_process("mqtt_detector.py")

    print("\nSystem running.")
    print("Press CTRL+C to stop.\n")

    try:

        while True:
            time.sleep(1)

    except KeyboardInterrupt:

        print("\nStopping system...\n")

        publisher.terminate()
        detector.terminate()

        publisher.wait()
        detector.wait()

        print("System stopped.")

# ==========================================================
# ENTRY
# ==========================================================

if __name__ == "__main__":
    main()
