# Volition — Manual Setup Guide

This document describes how to deploy Volition **without** relying on the Genesis automation, or how to understand and validate each component that Genesis configures for you. If you prefer to know exactly what runs where, this is the reference.

This guide assumes you are comfortable with Linux, systemd, Redis, SSH, and Python virtual environments.

---

## System Overview

Volition is intentionally split across multiple machines and trust domains.

**Cognitive agents (Matts)** run in isolated Proxmox LXC containers.
All coordination happens through Redis.
Heavy compute and human-facing tools live outside the containers.

Minimum components:

* Proxmox VE host
* Redis (LAN-reachable, authenticated)
* One Matt container (matt-01)
* GPU Worker (embeddings + summarization)
* Ear (social awareness)

Strongly recommended:

* Dashboard (human control plane)
* Ntfy (alerts)
* GPU machine with Ollama

---

## 1. Redis (Mandatory)

Redis is Volition’s nervous system. Nothing functions without it.

Requirements:

* Redis must listen on a LAN interface
* Authentication must be enabled
* Protected mode must be disabled

Example `/etc/redis/redis.conf` changes:

```
bind 0.0.0.0
requirepass volition
protected-mode no
```

Restart Redis:

```
systemctl restart redis-server
```

Verify from any node:

```
redis-cli -h <redis-ip> -a volition PING
```

Expected output:

```
PONG
```

---

## 2. Matt Container (Cognitive Agent)

Each Matt runs inside a dedicated Proxmox LXC container.

### Required filesystem layout inside the container

When you `pct enter` an Matt, the following **must exist**:

The Docs must contain 0.0-Matt-Genesis_Prompt, 98-source_profile.md, 99-current_services.md, and Volition-1 through Volition-8 documentation.

```
/root
├── bin/
├── docs/
├── logs/
├── src/
├── .ssh/
├── .matt-identity
├── .matt-clipboard-<matt-name>.md
├── working.log
├── communications.log
├── todo.db
└── memory/
    ├── episodes/
    ├── tier_1_archive/
    ├── overflow/
    ├── downloads/
    └── vector.db/
```

If any of these are missing, the agent is malformed. You can manually copy files.

### Core services inside container

* `guppi.service` — main agent loop

The container **does not** run:

* GPU worker
* Ear
* Dashboard
* Logger
* Heartbeat

Those are infrastructure services.

---

## 3. GPU Worker (Mandatory)

The GPU Worker performs:

* Tier-3 vector embeddings
* Heavy summarization

Without this service, Matts cannot form long-term memory.

### Location

Run on **any Linux machine** that:

* Can reach Redis
* Has Python 3.10+
* Has either Ollama or OpenRouter access

### Ollama (recommended)

Install Ollama and pull required models:

```
ollama pull nomic-embed-text
ollama pull mistral:latest
```

### Python environment

```
mkdir -p /opt/volition
python3 -m venv /opt/volition/venv
/opt/volition/venv/bin/pip install aiohttp redis requests
```

### Service

Install `volition-gpu-worker.service` to:

```
/etc/systemd/system/volition-gpu-worker.service
```

Enable and start:

```
systemctl daemon-reload
systemctl enable --now volition-gpu-worker
```

---

## 4. Ear (Mandatory)

The Ear service provides:

* Social awareness
* Orientation digests
* Context recovery after sleep

Without Ear, Matts wake up context-blind.

Deployment is identical to GPU Worker:

* Same Redis access
* Same Python environment
* Separate systemd service: `volition-ear.service`

Enable:

```
systemctl enable --now volition-ear
```

---

## 5. Logger + Heartbeat (Strongly Recommended)

These services provide observability and safety.

### Logger

* Immutable audit trail
* Writes all actions and messages

### Heartbeat Monitor

* Tracks agent liveness
* Alerts humans via ntfy
* Alerts Matt-01 for subsequent Matts' deaths.

Install both services on the Redis or infra node:

```
/etc/systemd/system/volition-logger.service
/etc/systemd/system/volition-heartbeat.service
```

Enable:

```
systemctl enable --now volition-logger volition-heartbeat
```

---

## 6. Dashboard (Optional but Recommended)

The dashboard is the **human control plane**.

It runs **outside** Matt containers.

### Requirements

* Direct Redis access
* Python 3.10+

### Install

```
mkdir -p /opt/volition/dashboard
cp dashboard/* /opt/volition/dashboard/

python3 -m venv /opt/volition/venv
/opt/volition/venv/bin/pip install fastapi uvicorn redis aiohttp jinja2
```

### Service

Install:

```
/etc/systemd/system/volition-dashboard.service
```

Verify paths in service file:

```
ExecStart=/opt/volition/venv/bin/python /opt/volition/dashboard/volition_dashboard.py
```

Enable:

```
systemctl daemon-reload
systemctl enable --now volition-dashboard
```

Access:

```
http://<dashboard-host>:8000
```

---

## 7. Security Model (Critical)

Volition agents are powerful by design.

Rules:

* Matts may only access hosts listed in `/root/.ssh/config`
* SSH keys must be installed manually on target machines
* Passwordless sudo is recommended for effectiveness

If you are not comfortable granting SSH + sudo access to an LLM agent, **do not run Volition**.

---

## 8. Verification Checklist

After everything is running:

* Redis responds to PING
* GPU worker is active
* Ear is active
* Matt container has correct filesystem layout
* `systemctl status guppi` is healthy
* Dashboard shows heartbeats and logs

If any piece is missing, stop and fix it before continuing.

---

## Closing Notes

Genesis automates all of this, but this manual exists so nothing is mysterious.
