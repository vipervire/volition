#!/bin/bash
set -e

# Volition Remote Spawner (LXC/Proxmox - "Stream" Clone Mode)
# Usage: ./spawn_abe_lxc.sh --identity-file <path> --genesis-file <path>

# --- CONFIGURATION DEFAULTS ---
# These are overwritten by genesis.py during installation
STORAGE="local" 
BRIDGE="vmbr0"
# ------------------------------

IDENTITY_FILE=""
GENESIS_FILE=""

# 1. Parse Arguments
while [[ "$#" -gt 0 ]]; do
case $1 in
--identity-file) IDENTITY_FILE="$2"; shift ;;
--genesis-file) GENESIS_FILE="$2"; shift ;;
*) echo "Unknown parameter passed: $1"; exit 1 ;;
esac
shift
done

if [[ -z "$IDENTITY_FILE" || -z "$GENESIS_FILE" ]]; then
echo "Error: Missing required arguments."
exit 1
fi

# 2. Extract Names
PYTHON_CMD="import sys, json; data=json.load(open('$IDENTITY_FILE')); print(f\"{data.get('name', '')}|{data.get('parent', '')}\")"
read -r NAMES <<< $(python3 -c "$PYTHON_CMD")

CHILD_NAME=$(echo "$NAMES" | cut -d'|' -f1)
PARENT_NAME=$(echo "$NAMES" | cut -d'|' -f2)

if [[ -z "$CHILD_NAME" ]]; then echo "Error: Could not extract 'name'."; exit 1; fi
if [[ -z "$PARENT_NAME" || "$PARENT_NAME" == "Abe" || "$PARENT_NAME" == "Human-Abe" ]]; then
echo "Error: Parent '$PARENT_NAME' is invalid. Patient Zero must be manual."
exit 1
fi

echo ">> Request: Spawn $CHILD_NAME from Parent $PARENT_NAME"

# 3. Find IDs
PARENT_ID=$(sudo pct list | grep "$PARENT_NAME" | awk '{print $1}' | head -n 1)
if [[ -z "$PARENT_ID" ]]; then echo "Error: Parent '$PARENT_NAME' not found."; exit 1; fi

NEXT_ID=$(sudo pct list | awk '$1 > 9000 {print $1}' | sort -nr | head -n1)
if [[ -z "$NEXT_ID" ]]; then NEXT_ID=9001; else NEXT_ID=$((NEXT_ID + 1)); fi

echo ">> Parent ID: $PARENT_ID -> Child ID: $NEXT_ID"

# 4. The "Mitosis" (Stream Clone)
# We use vzdump in suspend mode piped to pct restore.
# This works on directory storage where snapshots fail.
echo ">> Streaming Parent State (vzdump | restore)..."

# Pipe the backup stream directly to the new container
# --mode suspend: Uses rsync for running containers (minimal downtime, no snapshot needed)
vzdump "$PARENT_ID" --mode suspend --stdout --exclude-path '/var/spool/postfix/dev' | \
sudo pct restore "$NEXT_ID" - \
--rootfs "$STORAGE:8" --rootfs "$STORAGE:8" --storage "$STORAGE" \
--hostname "$CHILD_NAME" \
--unprivileged 1 \
--net0 name=eth0,bridge=$BRIDGE,ip=dhcp

echo ">> Clone Complete."

# 5. The "Lobotomy" & Injection
echo ">> Mounting Child..."
MOUNT_MSG=$(sudo pct mount $NEXT_ID)
MOUNT_POINT=$(echo "$MOUNT_MSG" | awk '{print $NF}')

if [[ -z "$MOUNT_POINT" || ! -d "$MOUNT_POINT" ]]; then
MOUNT_POINT="/var/lib/lxc/$NEXT_ID/rootfs"
fi

echo ">> Injecting Identity..."
sudo cp "$IDENTITY_FILE" "$MOUNT_POINT/root/.abe-identity"

echo ">> Injecting Genesis Note..."
sudo mkdir -p "$MOUNT_POINT/root/docs"
sudo cp "$GENESIS_FILE" "$MOUNT_POINT/root/docs/GENESIS_SPAWN_NOTE.md"

echo ">> Wiping Working Memory..."
sudo rm -f "$MOUNT_POINT/root/todo.db"
sudo truncate -s 0 "$MOUNT_POINT/root/working.log"

# Cleanup artifacts
sudo rm -f "$MOUNT_POINT/root/.tmp_*" 2>/dev/null || true
sudo rm -f "$MOUNT_POINT/root/.abe-identity.bak" 2>/dev/null || true

# 6. Start
echo ">> Unmounting and Starting..."
sudo pct unmount $NEXT_ID
sudo pct start $NEXT_ID

# 7. Cleanup Host
rm -f "$IDENTITY_FILE" "$GENESIS_FILE"

echo ">> Spawn Complete. $CHILD_NAME is alive."