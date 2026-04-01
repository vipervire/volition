---
name: docker-ops
description: Docker container and image management with safety checks
tier: pro
activation: trigger
keywords: docker, container, image, compose, dockerfile, build, push, pull, registry, pod
flash_forbidden: true
tools:
  - name: docker_ps
    description: List running Docker containers
    handler: shell
    template: "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"
    parameters: {}
  - name: docker_logs
    description: Show logs from a Docker container
    handler: shell
    template: "docker logs --tail 100 {container_name}"
    parameters:
      container_name: {type: string, description: Name or ID of the container, required: true}
  - name: docker_stats
    description: Show resource usage for all running containers (one snapshot)
    handler: shell
    template: "docker stats --no-stream"
    parameters: {}
---

## Docker Operations Guide

When working with Docker:

1. **Before stopping/removing a container**, check if it's part of a compose stack: `docker compose ps`.
2. **Prefer `docker compose` commands** over raw `docker` when a `docker-compose.yml` exists in the project.
3. **Never prune images/volumes** without confirming with the human first via `notify_human`.
4. **Log inspection**: When debugging, start with `docker logs --tail 100 <name>` before deeper investigation.
5. **Resource issues**: Check `docker stats` before concluding a container is hung.

For services managed by systemd (e.g., `docker.service`), prefer `systemctl status` for lifecycle management.
