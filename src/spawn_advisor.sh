#!/bin/bash
set -e

# Volition Spawn Advisor
# Usage: spawn_advisor.sh --host <hostname>
# Returns a JSON capacity report for the target Proxmox node.
# Advisory only -- never exits non-zero, never blocks a spawn.

# --- CONFIGURATION DEFAULTS ---
# These are overwritten by genesis.py during installation
STORAGE="local"
# ------------------------------

HOST=""

# 1. Parse Arguments
while [[ "$#" -gt 0 ]]; do
case $1 in
--host) HOST="$2"; shift ;;
*) echo "[spawn_advisor] Unknown parameter: $1" >&2; shift ;;
esac
shift
done

# Helper: append a reason string to the REASONS accumulator
REASONS=""
add_reason() {
    if [[ -z "$REASONS" ]]; then
        REASONS="\"$1\""
    else
        REASONS="$REASONS, \"$1\""
    fi
}

emit_error_json() {
    local reason="$1"
    cat <<EOF
{"host": "$HOST", "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "recommendation": "error", "reasons": ["$reason"]}
EOF
    exit 0
}

# 2. Validate --host
if [[ -z "$HOST" ]]; then
    emit_error_json "Missing required --host argument"
fi

LOCAL_HOST=$(hostname)
LOCAL_HOST_FQDN=$(hostname -f 2>/dev/null || echo "")

if [[ "$HOST" != "$LOCAL_HOST" && "$HOST" != "$LOCAL_HOST_FQDN" && "$HOST" != "local" ]]; then
    emit_error_json "Remote host checks not yet supported. Host '$HOST' does not match local node '$LOCAL_HOST'"
fi

echo "[spawn_advisor] Checking capacity on $LOCAL_HOST..." >&2

# 3. Gather Metrics

# CPU: 1-minute load average from /proc/loadavg, normalized by core count
CPU_LOAD_1M=$(awk '{print $1}' /proc/loadavg)
CPU_CORES=$(nproc)
CPU_LOAD_RATIO=$(awk "BEGIN {printf \"%.4f\", $CPU_LOAD_1M / $CPU_CORES}")

# Memory: available MB and % free from `free -m`
MEM_TOTAL_MB=$(free -m | awk '/^Mem:/ {print $2}')
MEM_AVAILABLE_MB=$(free -m | awk '/^Mem:/ {print $7}')
MEM_PERCENT_FREE=$(awk "BEGIN {printf \"%.1f\", ($MEM_AVAILABLE_MB / $MEM_TOTAL_MB) * 100}")

# Storage: available and total from pvesm status
STORAGE_TOTAL_GB="null"
STORAGE_AVAILABLE_GB="null"
STORAGE_PERCENT_FREE="null"
STORAGE_ERROR=""

if command -v pvesm &>/dev/null; then
    PVESM_LINE=$(pvesm status 2>/dev/null | awk -v pool="$STORAGE" '$1 == pool {print}' | head -n1)
    if [[ -n "$PVESM_LINE" ]]; then
        # pvesm status columns: Name Type Status Total Used Available %
        PVESM_TOTAL=$(echo "$PVESM_LINE" | awk '{print $4}')   # bytes
        PVESM_AVAIL=$(echo "$PVESM_LINE" | awk '{print $6}')   # bytes
        if [[ -n "$PVESM_TOTAL" && "$PVESM_TOTAL" -gt 0 ]]; then
            STORAGE_TOTAL_GB=$(awk "BEGIN {printf \"%.1f\", $PVESM_TOTAL / 1073741824}")
            STORAGE_AVAILABLE_GB=$(awk "BEGIN {printf \"%.1f\", $PVESM_AVAIL / 1073741824}")
            STORAGE_PERCENT_FREE=$(awk "BEGIN {printf \"%.1f\", ($PVESM_AVAIL / $PVESM_TOTAL) * 100}")
        else
            STORAGE_ERROR="Could not parse pvesm output for pool '$STORAGE'"
        fi
    else
        STORAGE_ERROR="Storage pool '$STORAGE' not found in pvesm status"
    fi
else
    STORAGE_ERROR="pvesm not available on this host"
fi

# Containers: count running and total Abe containers (ID > 9000)
ABE_RUNNING=0
ABE_TOTAL=0
NEXT_ID=9001

if command -v pct &>/dev/null; then
    PCT_OUTPUT=$(sudo pct list 2>/dev/null || pct list 2>/dev/null || true)
    if [[ -n "$PCT_OUTPUT" ]]; then
        ABE_TOTAL=$(echo "$PCT_OUTPUT" | awk '$1 > 9000 {count++} END {print count+0}')
        ABE_RUNNING=$(echo "$PCT_OUTPUT" | awk '$1 > 9000 && $2 == "running" {count++} END {print count+0}')
        LAST_ID=$(echo "$PCT_OUTPUT" | awk '$1 > 9000 {print $1}' | sort -nr | head -n1)
        if [[ -n "$LAST_ID" ]]; then
            NEXT_ID=$((LAST_ID + 1))
        fi
    fi
fi

# 4. Evaluate Thresholds
RECOMMENDATION="ok"

# CPU checks
if awk "BEGIN {exit !($CPU_LOAD_RATIO > 0.90)}"; then
    add_reason "CPU load ratio $CPU_LOAD_RATIO exceeds critical threshold 0.90"
    RECOMMENDATION="critical"
elif awk "BEGIN {exit !($CPU_LOAD_RATIO > 0.70)}"; then
    add_reason "CPU load ratio $CPU_LOAD_RATIO exceeds warning threshold 0.70"
    [[ "$RECOMMENDATION" == "ok" ]] && RECOMMENDATION="warning"
fi

# Memory checks
if awk "BEGIN {exit !($MEM_PERCENT_FREE < 10)}"; then
    add_reason "Memory $MEM_PERCENT_FREE% free is below critical threshold 10%"
    RECOMMENDATION="critical"
elif awk "BEGIN {exit !($MEM_PERCENT_FREE < 20)}"; then
    add_reason "Memory $MEM_PERCENT_FREE% free is below warning threshold 20%"
    [[ "$RECOMMENDATION" == "ok" ]] && RECOMMENDATION="warning"
fi

# Storage checks (only if we got valid data)
if [[ "$STORAGE_PERCENT_FREE" != "null" ]]; then
    if awk "BEGIN {exit !($STORAGE_PERCENT_FREE < 10)}"; then
        add_reason "Storage '$STORAGE' $STORAGE_PERCENT_FREE% free is below critical threshold 10%"
        RECOMMENDATION="critical"
    elif awk "BEGIN {exit !($STORAGE_PERCENT_FREE < 20)}"; then
        add_reason "Storage '$STORAGE' $STORAGE_PERCENT_FREE% free is below warning threshold 20%"
        [[ "$RECOMMENDATION" == "ok" ]] && RECOMMENDATION="warning"
    fi
elif [[ -n "$STORAGE_ERROR" ]]; then
    add_reason "$STORAGE_ERROR"
    [[ "$RECOMMENDATION" == "ok" ]] && RECOMMENDATION="warning"
fi

# Container count checks
if [[ "$ABE_RUNNING" -ge 15 ]]; then
    add_reason "Running Abe container count $ABE_RUNNING exceeds critical threshold 15"
    RECOMMENDATION="critical"
elif [[ "$ABE_RUNNING" -ge 8 ]]; then
    add_reason "Running Abe container count $ABE_RUNNING exceeds warning threshold 8"
    [[ "$RECOMMENDATION" == "ok" ]] && RECOMMENDATION="warning"
fi

# 5. Emit JSON
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cat <<EOF
{
  "host": "$LOCAL_HOST",
  "timestamp": "$TIMESTAMP",
  "cpu": {
    "load_1m": $CPU_LOAD_1M,
    "cores": $CPU_CORES,
    "load_ratio": $CPU_LOAD_RATIO
  },
  "memory": {
    "total_mb": $MEM_TOTAL_MB,
    "available_mb": $MEM_AVAILABLE_MB,
    "percent_free": $MEM_PERCENT_FREE
  },
  "storage": {
    "pool": "$STORAGE",
    "total_gb": $STORAGE_TOTAL_GB,
    "available_gb": $STORAGE_AVAILABLE_GB,
    "percent_free": $STORAGE_PERCENT_FREE
  },
  "containers": {
    "running": $ABE_RUNNING,
    "total": $ABE_TOTAL,
    "next_id": $NEXT_ID
  },
  "recommendation": "$RECOMMENDATION",
  "reasons": [$REASONS]
}
EOF
