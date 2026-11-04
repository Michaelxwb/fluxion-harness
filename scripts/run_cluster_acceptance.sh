#!/usr/bin/env bash
# 一条命令跑 Redis Cluster 验收：探针判定环境 -> 集群不可达时按需拉起 localdb 的
# compose profile -> 跑 tests/acceptance/redis_cluster 全部用例。
#
# 用法:
#   scripts/run_cluster_acceptance.sh [pytest 参数...]
#
# 环境变量:
#   MUAD_CLUSTER_REDIS_URL   集群 DSN（默认 redis://127.0.0.1:6391/0）
#   MUAD_CLUSTER_SEEDS       发现种子（默认 127.0.0.1:6391,6392,6393）
#   MUAD_CLUSTER_COMPOSE_FILE  localdb compose 文件；默认 $HOME/workspace/localdb/docker-compose.yaml
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_URL="${MUAD_CLUSTER_REDIS_URL:-redis://127.0.0.1:6391/0}"
SEEDS="${MUAD_CLUSTER_SEEDS:-127.0.0.1:6391,127.0.0.1:6392,127.0.0.1:6393}"
COMPOSE_FILE="${MUAD_CLUSTER_COMPOSE_FILE:-$HOME/workspace/localdb/docker-compose.yaml}"

cd "$ROOT"

probe() {
  uv run python scripts/probe_redis_cluster_commands.py --url "$CLUSTER_URL" --seeds "$SEEDS"
}

if ! probe; then
  if [[ -f "$COMPOSE_FILE" ]]; then
    echo "==> 集群不可用，启动 localdb compose profile: $COMPOSE_FILE"
    docker compose -f "$COMPOSE_FILE" --profile redis-cluster up -d redis-cluster
    for _ in $(seq 1 30); do
      if probe; then
        break
      fi
      sleep 2
    done
  else
    echo "集群不可用，且未找到 compose 文件（$COMPOSE_FILE）。" >&2
    echo "启动本地 3 节点集群（localdb 仓）:" >&2
    echo "  docker compose -f <localdb>/docker-compose.yaml --profile redis-cluster up -d redis-cluster" >&2
    exit 1
  fi
fi

exec uv run pytest -q tests/acceptance/redis_cluster "$@"
