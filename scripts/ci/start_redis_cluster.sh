#!/usr/bin/env bash
# Start a real 3-master/3-replica Redis Cluster for CI acceptance tests.
# One container runs six nodes on 6391-6396 so the advertised addresses work
# both inside the container and from the GitHub runner host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="ci-redis-cluster"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  -p 6391-6396:6391-6396 \
  -v "$ROOT/scripts/ci/redis_cluster_entrypoint.sh:/entrypoint.sh:ro" \
  --entrypoint /entrypoint.sh \
  redis:6.2.18 >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$NAME" redis-cli -p 6391 cluster info 2>/dev/null | grep -q 'cluster_state:ok'; then
    echo "Redis Cluster ready (6391-6396)"
    exit 0
  fi
  sleep 1
done

docker logs "$NAME" || true
echo "Redis Cluster did not become ready in 60s" >&2
exit 1
