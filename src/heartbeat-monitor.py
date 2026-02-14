#!/usr/bin/env python3
"""
Volition Heartbeat Monitor (Hardened + Governance)
Watches 'volition:heartbeat'.
1. If a Matt goes silent for > 3 minutes, triggers HIGH PRIORITY Ntfy alert.
2. ALSO notifies Matt-01 (The Steward) via inbox so he can investigate.
"""

import time
import os
import json
import requests
import redis
import sys
from datetime import datetime, timezone

# CONFIG
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "volition")
NTFY_URL = os.environ.get("NTFY_URL")

# FAIL LOUD: No default token in code. Export NTFY_TOKEN in your shell/systemd.
NTFY_TOKEN = os.environ.get("NTFY_TOKEN")
NTFY_ENABLED = bool(NTFY_TOKEN and NTFY_URL)


# TUNING
ALERT_THRESHOLD = 180        # Alert if silent for 3 minutes
CHECK_INTERVAL = 10          # How often to check for dead Matts (approx)

def send_alert(abe_name, last_seen_str):
    """Sends a high-priority panic alert to Humans."""
    if not NTFY_ENABLED:
        print("[!] NTFY disabled; skipping human alert")
        return

    print(f"[!] FLATLINE: {abe_name} (Last seen: {last_seen_str})")
    try:
        requests.post(
            NTFY_URL,
            data=f"🚑 FLATLINE DETECTED: {abe_name} has not reported in since {last_seen_str}.",
            headers={
                "Title": f"Matt Down: {abe_name}",
                "Priority": "urgent",
                "Tags": "skull,rotating_light",
                "Authorization": f"Bearer {NTFY_TOKEN}"
            },
            timeout=5
        )
    except Exception as e:
        print(f"Failed to send Ntfy alert: {e}")


def notify_steward(r, dead_abe, last_seen_str):
    """Notifies Matt-01 that a peer has died."""
    # Don't ask Matt-01 to investigate himself.
    if dead_abe == "matt-01":
        return

    msg = {
        "from": "Volition-Overseer",
        "event_type": "SystemAlert",
        "content": f"CRITICAL: Heartbeat lost for {dead_abe}. Last seen: {last_seen_str}. Investigate status immediately.",
        "priority": "high"
    }

    try:
        r.lpush("inbox:matt-01", json.dumps(msg))
        print(f"[*] Notified Matt-01 to investigate {dead_abe}.")
    except Exception as e:
        print(f"Failed to notify Matt-01: {e}")

def main():
    print(f"[*] Heartbeat Monitor Active. Connecting to {REDIS_HOST}...")
    
    # Hardened Connection
    r = redis.Redis(
        host=REDIS_HOST, 
        port=REDIS_PORT, 
        password=REDIS_PASSWORD, 
        decode_responses=True,
        socket_timeout=5,
        retry_on_timeout=True
    )
    
    # State: { "matt-01": timestamp_float }
    known_abes = {}

    # We start at '$' (only new beats).
    # If you restart this monitor, it learns about alive Matts as they beat.
    last_id = "$"
    
    while True:
        try:
            # 1. READ NEW BEATS (Block for 1s to keep loop tight but efficient)
            streams = r.xread({"volition:heartbeat": last_id}, count=100, block=1000)
            
            if streams:
                for _, messages in streams:
                    for msg_id, data in messages:
                        last_id = msg_id
                        abe = data.get("abe")
                        if abe:
                            if abe not in known_abes:
                                print(f"[+] Discovered new heartbeat: {abe}")
                            known_abes[abe] = time.time()

            # 2. CHECK FOR DEAD MATTS
            now = time.time()
            # Iterate copy to allow modification
            for abe, last_ts in list(known_abes.items()):
                delta = now - last_ts

                if delta > ALERT_THRESHOLD:
                    # FLATLINE CONFIRMED
                    last_seen_str = datetime.fromtimestamp(last_ts, timezone.utc).strftime('%H:%M:%S UTC')

                    # Action 1: Alert Humans
                    send_alert(abe, last_seen_str)

                    # Action 2: Alert Matt-01
                    notify_steward(r, abe, last_seen_str)
                    
                    # Remove from active monitoring to prevent alert spam
                    # (Will re-add automatically if they heartbeat again)
                    print(f"[-] Removing {abe} from active monitoring until next beat.")
                    del known_abes[abe]

        except KeyboardInterrupt:
            print("\nMonitor Stopped.")
            break
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()