#!/usr/bin/env python3
"""
Volition CLI Tool
Usage:
  ./volition email abe-01 "Check the logs"
  ./volition chat "Hello everyone"
  ./volition summon @abe-01 "Emergency meeting!"
  ./volition listen general
  ./volition read --abe abe-01 (Drains the inbox)
"""

import sys
import json
import argparse
import redis
import time
import os
from datetime import datetime

# CONFIG
REDIS_HOST = os.environ.get("REDIS_HOST") # Adjust to your Redis LXC IP
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "volition")

def get_redis():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)

def cmd_email(args):
    r = get_redis()
    target = args.target
    msg = {
        "from": "Human-Abe",
        "timestamp": datetime.utcnow().isoformat(),
        "event": "NewInboxMessage",
        "content": args.message
    }
    key = f"inbox:{target}"
    r.lpush(key, json.dumps(msg))
    print(f"✅ Email sent to {target} (pushed to {key})")

def cmd_chat(args):
    r = get_redis()
    entry = {
        "from": "Human-Abe",
        "content": args.message,
        "timestamp": datetime.utcnow().isoformat()
    }
    r.xadd("chat:general", entry)
    print("✅ Message posted to Town Square (chat:general)")

def cmd_summon(args):
    r = get_redis()
    entry = {
        "from": "Human-Abe",
        "content": args.message,
        "tags": args.tags,
        "timestamp": datetime.utcnow().isoformat(),
        "type": "summon"
    }
    r.xadd("chat:synchronous", entry)
    print("🚨 SUMMON SENT to chat:synchronous! They should wake up shortly.")

def cmd_listen(args):
    r = get_redis()
    stream = "chat:general"
    if args.channel == "sync": stream = "chat:synchronous"
    if args.channel == "actions": stream = "volition:action_log"

    print(f"👂 Listening to {stream}... (Ctrl+C to stop)")
    last_id = "$"
    while True:
        try:
            resp = r.xread({stream: last_id}, count=1, block=5000)
            if resp:
                for _, messages in resp:
                    for msg_id, data in messages:
                        last_id = msg_id
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] {data}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

def cmd_read(args):
    r = get_redis()
    target = args.abe or os.environ.get("ABE_NAME", "Human-Abe")
    key = f"inbox:{target}"
    print(f"📬 Checking {key} (WARNING: Destructive Read - popping messages)...")
    
    count = 0
    while True:
        data = r.lpop(key)
        if not data:
            if count == 0:
                print("📭 Inbox empty.")
            else:
                print(f"--- End of Inbox ({count} messages read) ---")
            break
        
        count += 1
        try:
            msg = json.loads(data)
            ts = msg.get('timestamp', '??')
            sender = msg.get('from', 'unknown')
            content = msg.get('content', '')
            print(f"[{ts}] FROM: {sender}")
            print(f"CONTENT: {content}")
            print("-" * 40)
        except:
            print(f"RAW: {data}")

def main():
    parser = argparse.ArgumentParser(description="Volition Human Interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Email
    p_email = subparsers.add_parser("email", help="Send direct message to an Abe")
    p_email.add_argument("target", help="e.g., abe-01")
    p_email.add_argument("message", help="The message content")
    p_email.set_defaults(func=cmd_email)

    # Chat
    p_chat = subparsers.add_parser("chat", help="Post to public chat")
    p_chat.add_argument("message", help="The message content")
    p_chat.set_defaults(func=cmd_chat)

    # Summon
    p_summon = subparsers.add_parser("summon", help="Trigger high-priority sync chat")
    p_summon.add_argument("tags", help="e.g., @abe-01 or @all")
    p_summon.add_argument("message", help="The alert message")
    p_summon.set_defaults(func=cmd_summon)

    # Listen
    p_listen = subparsers.add_parser("listen", help="Tail a stream")
    p_listen.add_argument("channel", choices=["general", "sync", "actions"], default="general")
    p_listen.set_defaults(func=cmd_listen)

    # Read
    p_read = subparsers.add_parser("read", help="Drain messages from an inbox")
    p_read.add_argument("--abe", help="Inbox to read (default: $ABE_NAME or Human-Abe)")
    p_read.set_defaults(func=cmd_read)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()