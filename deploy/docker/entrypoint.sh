#!/bin/sh
# -----------------------------------------------------------------------------
# Fluxion 容器入口：把部署约定的环境变量桥接到 `fluxion serve`。
#
# 为什么需要这个脚本而不是直接 `fluxion serve`：
# 1. CLI 的 serve 命令通过 `--registry-dsn` 接收数据库 DSN（默认 SQLite），
#    不会自动读取 FLUXION_DATABASE_URL，因此这里显式桥接。
# 2. 代码内 Secret Store 实际读取的环境变量是 FLUXION_SECRET_MASTER_KEY（base64），
#    而 .env.example / 部署约定使用 FLUXION_MASTER_KEY，这里做兼容桥接。
# 3. CLI 默认监听 127.0.0.1，容器内必须监听 0.0.0.0 才能被宿主机访问。
# -----------------------------------------------------------------------------
set -eu

: "${FLUXION_HOST:=0.0.0.0}"
: "${FLUXION_PORT:=8000}"
: "${FLUXION_DATABASE_URL:=sqlite+aiosqlite:///./fluxion-dev.db}"

# MASTER_KEY：AES-256-GCM 需要 32 字节 key，部署约定为 base64 编码。
# 生成方式：openssl rand -base64 32
if [ -z "${FLUXION_MASTER_KEY:-}" ]; then
  echo "错误：缺少 FLUXION_MASTER_KEY（32 字节 AES-256-GCM key 的 base64）。" >&2
  echo "      生成命令：openssl rand -base64 32" >&2
  exit 1
fi

# 校验 key 为合法 base64 且解码后为 32 字节
python - "${FLUXION_MASTER_KEY}" <<'PY'
import base64
import sys

try:
    key = base64.b64decode(sys.argv[1], validate=True)
except Exception as exc:
    raise SystemExit(f"FLUXION_MASTER_KEY 不是合法 base64：{exc}") from exc
if len(key) != 32:
    raise SystemExit(f"FLUXION_MASTER_KEY 解码后为 {len(key)} 字节，必须是 32 字节")
PY

# 兼容代码内实际读取的变量名（LocalEncryptedSecretStore.from_env 默认读此变量）
export FLUXION_SECRET_MASTER_KEY="${FLUXION_MASTER_KEY}"

# 生产模式启动（非 dev）：读取 FLUXION_DATABASE_URL 指向的 PostgreSQL。
# 如需容器内开发模式（挂载前端），可改为追加 `--dev`，前端 dist 已按仓库相对路径放置。
exec fluxion serve \
  --host "${FLUXION_HOST}" \
  --port "${FLUXION_PORT}" \
  --registry-dsn "${FLUXION_DATABASE_URL}"
