---
name: system-monitoring
description: System resource monitoring and process investigation
tier: both
activation: trigger
keywords: cpu, memory, disk, load, process, pid, hung, slow, performance, resource, monitor, top, htop, free, df
flash_forbidden: false
tools:
  - name: system_snapshot
    description: Take a snapshot of CPU, memory, disk, and top processes
    handler: shell
    template: "echo '=== CPU/MEM ===' && top -bn1 | head -20 && echo '=== DISK ===' && df -h && echo '=== MEM ===' && free -h"
    parameters: {}
  - name: process_search
    description: Search for running processes by name
    handler: shell
    template: "ps aux | grep -i {process_name} | grep -v grep"
    parameters:
      process_name: {type: string, description: Process name or pattern to search for, required: true}
---

## System Monitoring Guide

When investigating system health:

1. **Start broad**: Use `system_snapshot` for an overview before diving into specifics.
2. **High load**: Check which processes are consuming CPU/memory with `top -bn1 | head -30`.
3. **Disk full**: `df -h` shows filesystem usage. `du -sh /var/log/*` helps find large log files.
4. **Hung processes**: Check with `ps aux` first. Only send SIGKILL (kill -9) if SIGTERM fails.
5. **OOM events**: Check `dmesg | grep -i 'oom\|killed'` for out-of-memory kills.

For persistent monitoring, consider setting a todo with a due time rather than polling in a tight loop.
