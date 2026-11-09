#!/usr/bin/env bash
# Best-effort cleanup for the CI Redis Cluster container.
set -u
docker rm -f ci-redis-cluster >/dev/null 2>&1 || true
