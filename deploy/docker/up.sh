#!/bin/sh
# -----------------------------------------------------------------------------
# Fluxion Compose 一键构建部署（deploy/docker/up.sh）
#
# 环境变量全部来自 .env 文件（默认仓库根 .env，可用 --env-file 覆盖；
# 不存在则首次运行自动创建，密钥缺失自动生成）。用法：
#
#   ./deploy/docker/up.sh [up|down|ps|logs|config] [选项]
#
#   ./deploy/docker/up.sh                        # 构建 + 启动（默认 runtime x3）
#   ./deploy/docker/up.sh up --scale 2           # 构建 + 启动（runtime x2）
#   ./deploy/docker/up.sh up --no-build          # 只启动不构建
#   ./deploy/docker/up.sh up --with-postgres     # 无外部 PG 时顺手起一个
#                                                #（独立容器，不在 compose 内）
#   ./deploy/docker/up.sh down                   # 停止并删除容器（外部 PG 不受影响）
#   ./deploy/docker/up.sh ps                     # 查看状态
#
# 说明：
# - 构建产物与 deploy/docker/Dockerfile + docker-compose.yml 完全一致；
#   本脚本只负责传参、等健康，不改编排本身。
# - 外部 PG 接入需要容器间 DNS 可达：脚本自动生成临时 override，把三角色容器
#   接入外部网段（/tmp 下固定路径，不污染仓库）。
# - FLUXION_SECRET_MASTER_KEY 缺失时自动生成并写回 .env（首次运行）。
# -----------------------------------------------------------------------------
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
OVERRIDE_FILE="${TMPDIR:-/tmp}/fluxion-compose-extnet.yml"
EXT_NET="fluxion-ext-net"
EXT_PG="fluxion-ext-pg"

CMD="up"
SCALE=""
NO_BUILD=0
WITH_PG=0
ENV_FILE=""
WAIT_SECS=180

usage() {
  sed -n '2,/^# --*$/p' "$0" | sed 's/^# \{0,1\}//'
  echo "选项:"
  echo "  --scale N         runtime 副本数（默认 3）"
  echo "  --build/--no-build  构建镜像 / 跳过构建（默认构建）"
  echo "  --with-postgres   本地顺手起一个外部 PG（独立容器）"
  echo "  --env-file PATH   指定 .env（默认 <仓库根>/.env）"
  echo "  --wait SECS       健康等待上限（默认 180，0 表示不等）"
  echo "  -h, --help        显示本帮助"
}

while [ $# -gt 0 ]; do
  case "$1" in
    up|down|ps|logs|config) CMD="$1"; shift ;;
    --scale) SCALE="$2"; shift 2 ;;
    --scale=*) SCALE="${1#--scale=}"; shift ;;
    --build) NO_BUILD=0; shift ;;
    --no-build) NO_BUILD=1; shift ;;
    --with-postgres) WITH_PG=1; shift ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --env-file=*) ENV_FILE="${1#--env-file=}"; shift ;;
    --wait) WAIT_SECS="$2"; shift 2 ;;
    --wait=*) WAIT_SECS="${1#--wait=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1（用 --help 查看用法）" >&2; exit 2 ;;
  esac
done

if [ -z "$ENV_FILE" ]; then
  ENV_FILE="$REPO_ROOT/.env"
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "提示：${ENV_FILE} 不存在，已创建空模板（按需填值，可全留空用默认值/自动生成）。"
  cat > "$ENV_FILE" <<'EOF'
# Fluxion Compose 部署环境变量（仓库根 .env，已 gitignore，不要提交）。
# FLUXION_DATABASE_URL 留空 + up.sh 加 --with-postgres 可自动起一个外部 PG；
# FLUXION_SECRET_MASTER_KEY 留空则首次运行自动生成并写回本文件。
FLUXION_DATABASE_URL=
FLUXION_SECRET_MASTER_KEY=
FLUXION_REDIS_URL=
RUNTIME_SCALE=3
POSTGRES_PASSWORD=fluxion
POSTGRES_HOST_PORT=5433
EOF
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

SCALE="${SCALE:-${RUNTIME_SCALE:-3}}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-fluxion}"
POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-5433}"

# 1. 密钥：缺失则生成并写回 .env（openssl 为唯一依赖）。
if [ -z "${FLUXION_SECRET_MASTER_KEY:-}" ]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "错误：缺少 FLUXION_SECRET_MASTER_KEY 且本机无 openssl，请手动写入 ${ENV_FILE}。" >&2
    exit 1
  fi
  FLUXION_SECRET_MASTER_KEY="$(openssl rand -base64 32)"
  export FLUXION_SECRET_MASTER_KEY
  printf '\nFLUXION_SECRET_MASTER_KEY="%s"\n' "$FLUXION_SECRET_MASTER_KEY" >> "$ENV_FILE"
  echo "已生成 SECRET_MASTER_KEY 并写回 ${ENV_FILE}（请勿提交，.env 已 ignore）。"
fi

# 2. 外部 PG（可选）：独立容器 + 建表，DSN 仅在用户未提供时回落使用。
if [ "$WITH_PG" -eq 1 ]; then
  docker network create "$EXT_NET" >/dev/null 2>&1 || true
  if ! docker inspect "$EXT_PG" >/dev/null 2>&1; then
    docker run -d --name "$EXT_PG" --network "$EXT_NET" \
      -e POSTGRES_USER=fluxion -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
      -e POSTGRES_DB=fluxion -p "127.0.0.1:${POSTGRES_HOST_PORT}:5432" postgres:16 >/dev/null
    echo "已启动外部 PG 容器 ${EXT_PG}（数据在容器内，down 不删除）。"
  fi
  for _ in $(seq 1 30); do
    if docker exec "$EXT_PG" pg_isready -U fluxion >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if [ -z "${FLUXION_DATABASE_URL:-}" ]; then
    FLUXION_DATABASE_URL="postgresql+asyncpg://fluxion:${POSTGRES_PASSWORD}@${EXT_PG}:5432/fluxion"
    export FLUXION_DATABASE_URL
    echo "使用内置外部 PG：$FLUXION_DATABASE_URL"
  fi
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/init_db.py" \
      --dsn "postgresql+asyncpg://fluxion:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/fluxion" \
      >/dev/null 2>&1 && echo "PG 表结构就绪。" || echo "警告：init_db 未成功，服务启动可能连不上表，请手动执行 scripts/init_db.py。" >&2
  else
    echo "警告：缺少 .venv，跳过 init_db；请确保 PG 已建表（scripts/init_db.py）。" >&2
  fi
fi

# 3. DSN 必填（compose 层 fail-fast，这里先给一句人话）。
if [ -z "${FLUXION_DATABASE_URL:-}" ]; then
  echo "错误：缺少 FLUXION_DATABASE_URL。请写入 ${ENV_FILE}，或本次加 --with-postgres 自动起一个。" >&2
  exit 1
fi
export FLUXION_DATABASE_URL FLUXION_SECRET_MASTER_KEY
export FLUXION_REDIS_URL="${FLUXION_REDIS_URL:-}"

# 4. 外部网段 override（临时文件）：只做网络接入，不改编排本体。
docker network create "$EXT_NET" >/dev/null 2>&1 || true
cat > "$OVERRIDE_FILE" <<EOF
# up.sh 自动生成（临时）：把三角色容器接入外部网段，仅网络接入。
services:
  api:
    networks:
      - default
      - ext
  runtime:
    networks:
      - default
      - ext
  worker:
    networks:
      - default
      - ext

networks:
  ext:
    external: true
    name: $EXT_NET
EOF

COMPOSE="docker compose -f $COMPOSE_FILE -f $OVERRIDE_FILE"

case "$CMD" in
  config)
    # shellcheck disable=SC2086
    $COMPOSE config --services
    ;;
  ps)
    # shellcheck disable=SC2086
    $COMPOSE ps
    ;;
  logs)
    # shellcheck disable=SC2086
    $COMPOSE logs -f --tail=100
    ;;
  down)
    # shellcheck disable=SC2086
    $COMPOSE down
    echo "已停止（外部 PG / ${EXT_NET} 未动；清它们用 docker rm -f ${EXT_PG} && docker network rm ${EXT_NET}）。"
    ;;
  up)
    if [ "$NO_BUILD" -eq 0 ]; then
      echo "构建镜像…"
      # shellcheck disable=SC2086
      $COMPOSE build
    fi
    echo "启动（runtime x${SCALE}）…"
    # shellcheck disable=SC2086
    $COMPOSE up -d --scale "runtime=$SCALE"
    if [ "$WAIT_SECS" -gt 0 ]; then
      echo "等健康（上限 ${WAIT_SECS}s）…"
      deadline=$(( $(date +%s) + WAIT_SECS ))
      # shellcheck disable=SC2086
      while [ "$(date +%s)" -lt "$deadline" ]; do
        # shellcheck disable=SC2086
        ids=$($COMPOSE ps -q api runtime 2>/dev/null) || ids=""
        [ -z "$ids" ] && sleep 3 && continue
        # shellcheck disable=SC2086
        unhealthy=$(docker inspect $ids --format '{{.Name}} {{.State.Health.Status}}' 2>/dev/null | grep -cv healthy || true)
        [ "$unhealthy" -eq 0 ] && break
        sleep 3
      done
    fi
    # shellcheck disable=SC2086
    $COMPOSE ps
    echo "API: http://127.0.0.1:8000/healthz"
    ;;
esac
