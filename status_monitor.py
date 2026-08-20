#!/usr/bin/env python3
"""Status monitor for VolBot — runs as a tmux pane showing lifecycle state."""
import time
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("VolBot Status Monitor")
print("=" * 40)
while True:
    try:
        state_file = ".lifecycle_state.json"
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            phases = state.get("phases", {})
            completed = sum(1 for p in phases.values()
                            if isinstance(p, dict) and p.get("status") == "completed")
            total = len(phases)
            print(f"  Phases: {completed}/{total} completed")
            active = state.get("current_phase", "N/A")
            print(f"  Current phase: {active}")
            if state.get("token_mint"):
                print(f"  Token: {state['token_mint'][:16]}...")
        else:
            print("  No active lifecycle.")
            print("  Run: ./run.sh --devnet --dry-run --full --auto")
        print(f"  Last check: {time.strftime('%H:%M:%S')}")
        print()
        time.sleep(30)
    except Exception as e:
        print(f"  Error: {e}")
        time.sleep(5)
