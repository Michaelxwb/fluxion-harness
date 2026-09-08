#!/bin/sh
# -----------------------------------------------------------------------------
# Fluxion 容器入口：把部署约定的环境变量桥接到 `fluxion serve` /
# `fluxion-workflow-worker serve`。
#
# 为什么需要这个脚本而不是直接 `fluxion serve`：
# 1. CLI 的 serve 命令通过 `--registry-dsn` 接收数据库 DSN（默认本地 PG），
#    不会自动读取 FLUXION_DATABASE_URL，因此这里显式桥接。
# 2. CLI 默认监听 127.0.0.1，容器内必须监听 0.0.0.0 才能被宿主机访问。
# 3. Phase 6 TASK-006：FLUXION_ROLE=api（默认，--production 生产 bundle：Console/
#    Chat/Workspace/Eval/Operations + PG + 真实 provider + enforced release gate）
#    或 FLUXION_ROLE=worker（fluxion-workflow-worker serve，DBOS 执行进程）。
# -----------------------------------------------------------------------------
set -eu

: "${FLUXION_HOST:=0.0.0.0}"
# 端口用 FLUXION_HTTP_PORT：k8s 会给同名 Service 自动注入 docker-link 风格的
# FLUXION_PORT（tcp://10.x.x.x:8000），直接读会被注入值污染。
: "${FLUXION_HTTP_PORT:=8000}"
: "${FLUXION_DATABASE_URL:=postgresql+asyncpg://fluxion:fluxion@postgres:5432/fluxion}"
: "${FLUXION_ROLE:=api}"

# SECRET_MASTER_KEY：AES-256-GCM 需要 32 字节 key，base64 编码（唯一变量名，不做兼容）。
# 生成方式：openssl rand -base64 32
if [ -z "${FLUXION_SECRET_MASTER_KEY:-}" ]; then
  echo "错误：缺少 FLUXION_SECRET_MASTER_KEY（32 字节 AES-256-GCM key 的 base64）。" >&2
  echo "      生成命令：openssl rand -base64 32" >&2
  exit 1
fi

# 校验 key 为合法 base64 且解码后为 32 字节
python - "${FLUXION_SECRET_MASTER_KEY}" <<'PY'
import base64
import sys

try:
    key = base64.b64decode(sys.argv[1], validate=True)
except Exception as exc:
    raise SystemExit(f"FLUXION_SECRET_MASTER_KEY 不是合法 base64：{exc}") from exc
if len(key) != 32:
    raise SystemExit(f"FLUXION_SECRET_MASTER_KEY 解码后为 {len(key)} 字节，必须是 32 字节")
PY

# worker 角色：DBOS 执行进程（fluxion-workflow-worker serve，唯一执行进程；
# API/Console 进程只做 client 侧 start/signal，rule 13）。DBOS sysdb 用
# FLUXION_DBOS_SYSDB_DSN（psycopg 格式），缺省回落 FLUXION_DATABASE_URL。
if [ "${FLUXION_ROLE}" = "worker" ]; then
  WORKER_DB="${FLUXION_DBOS_SYSDB_DSN:-${FLUXION_DATABASE_URL}}"
  # psycopg 不识别 SQLAlchemy 方言后缀（postgresql+asyncpg://）；回落值为
  # 应用 DSN 时转回标准 postgresql://（S-01 实机发现：未转义直接 crash）。
  case "${WORKER_DB}" in
    postgresql+asyncpg://*)
      WORKER_DB="postgresql://${WORKER_DB#postgresql+asyncpg://}"
      ;;
  esac
  # argparse 顶层参数（--database-url/--bootstrap）必须在子命令 serve 之前
  WORKER_ARGS="--database-url ${WORKER_DB} --bootstrap ${FLUXION_WORKER_BOOTSTRAP:-fluxion.runtime.workflow_worker_bootstrap:install_production_worker_bootstrap}"
  SERVE_ARGS="serve"
  if [ -n "${FLUXION_WORKER_CONCURRENCY:-}" ]; then
    SERVE_ARGS="$SERVE_ARGS --worker-concurrency ${FLUXION_WORKER_CONCURRENCY}"
  fi
  # 生产常驻（review P0-1：缺省导致每小时定时退出重启）；仅测试需要有限生命期时注入正值
  WORKER_IDLE="${FLUXION_WORKER_IDLE_SECONDS:-0}"
  SERVE_ARGS="$SERVE_ARGS --idle-seconds ${WORKER_IDLE}"
  # shellcheck disable=SC2086
  exec fluxion-workflow-worker $WORKER_ARGS $SERVE_ARGS
fi

# 三服务拆分（TASK-010）：runtime 角色 = AgentLoop 执行独立进程（不含 Console）
if [ "${FLUXION_ROLE}" = "runtime" ]; then
  exec fluxion serve --runtime \
    --host "${FLUXION_HOST}" \
    --port "${FLUXION_HTTP_PORT}" \
    --registry-dsn "${FLUXION_DATABASE_URL}"
fi

# 生产模式启动（--production 生产 bundle）：读取 FLUXION_DATABASE_URL 指向的
# PostgreSQL；FLUXION_DBOS_SYSDB_DSN / FLUXION_S3_* 由 Helm/env 注入。
# 如需容器内开发模式（挂载前端），可改为 `--dev`。
exec fluxion serve --production \
  --host "${FLUXION_HOST}" \
  --port "${FLUXION_HTTP_PORT}" \
  --registry-dsn "${FLUXION_DATABASE_URL}"
