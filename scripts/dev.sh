#!/usr/bin/env bash
# 本地开发：Console / Runtime / Worker / Gateway + Console 前端的启停。
#
# 用法：
#   scripts/dev.sh          # 先停掉本仓库已有的同名服务，再全部起起来
#   scripts/dev.sh start    # 同上
#   scripts/dev.sh stop     # 只停，并逐个报告端口是否已释放
#
# 约定：
# - **必须在仓库根运行**：`.env`（`SharedSettings` 的 env_file）与 uvicorn 的 `--app-dir`
#   都是相对 cwd 解析的；脚本自己 cd 到仓库根，所以从哪儿调用都行。
# - **只停本仓库的进程**：后端按模块名匹配，前端 vite 额外校验 cwd 在本仓库内
#   （同机器上别的项目也有 vite，按端口或进程名盲杀会误伤）。
# - 后台运行，日志落 `.data/dev/<service>.log`（`.data/` 已在 .gitignore 里）。
# - start 起完做一次就绪探测，逐个报 ✓/✗；✗ 的去看对应日志。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/.data/dev"
mkdir -p "$LOG_DIR"

# 企微机器人直连：macOS 开着系统 SOCKS 代理而 venv 未装 python-socks 时，websockets 会抛
# `ImportError: connecting through a SOCKS proxy requires python-socks`，机器人永远连不上
# （日志表现为 wecom_bot_connect_failed / wecom_bot_error 的 BACKOFF 退避循环，收不到入站消息）。
# 副作用须知：一旦设了 NO_PROXY，urllib.getproxies() 只剩 no 项 ⇒ 这些服务**完全不走系统代理**，
# 不只是企微。若需要保留代理行为，应改走 `uv add python-socks`（另一条路）。
PROXY_BYPASS_HOSTS="openws.work.weixin.qq.com,qyapi.weixin.qq.com,127.0.0.1,localhost"
existing_proxy_bypass="${NO_PROXY:-${no_proxy:-}}"
export NO_PROXY="${existing_proxy_bypass:+$existing_proxy_bypass,}$PROXY_BYPASS_HOSTS"
export no_proxy="$NO_PROXY"

SERVICES=(
  "console|muad_console_platform.main:app|apps/console-platform/backend/src|8000"
  "runtime|muad_agent_runtime.main:app|apps/agent-runtime/src|8001"
  "worker|muad_agent_worker.main:app|apps/agent-worker/src|8002"
  "gateway|muad_im_gateway.main:app|apps/im-gateway/src|8003"
)

FRONTEND_LOG="$LOG_DIR/frontend.log"

# 停掉本仓库起的所有服务，回显实际停掉的进程数。
# 后端按模块名匹配（全仓唯一）；前端 vite 再按 cwd 归属过滤一次，避免误伤别的项目。
stop_services() {
  local stopped=0 pid cwd entry module

  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r _name module _app_dir _port <<<"$entry"
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      if kill "$pid" 2>/dev/null; then
        stopped=$((stopped + 1))
      fi
    done < <(pgrep -f "$module" 2>/dev/null || true)
  done

  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    case "$cwd" in
      "$ROOT"/*)
        if kill "$pid" 2>/dev/null; then
          stopped=$((stopped + 1))
        fi
        ;;
    esac
  done < <(pgrep -f "node_modules/.bin/vite" 2>/dev/null || true)

  echo "$stopped"
}

COMMAND="${1:-start}"
case "$COMMAND" in
  start | stop) ;;
  *)
    echo "用法：scripts/dev.sh [start|stop]" >&2
    exit 2
    ;;
esac

# ---------------------------------------------------------------- 1. 停掉已有服务
echo "== 停止已有服务 =="
stopped="$(stop_services)"
if [ "$stopped" -gt 0 ]; then
  echo "  已停 $stopped 个进程，等待端口释放…"
  sleep 2
else
  echo "  没有在跑的本仓库服务"
fi

# ---------------------------------------------------------------------- 2. 收尾分支
if [ "$COMMAND" = "stop" ]; then
  echo
  echo "== 端口检查 =="
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r _name _module _app_dir port <<<"$entry"
    holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (pid "$2")"}')"
    if [ -n "$holder" ]; then
      echo "  ⚠ $port 仍被 $holder 占用（不是本仓库进程）"
    else
      echo "  ✓ $port 已释放"
    fi
  done
  exit 0
fi

# --------------------------------------------------- 3. 端口占用检查（只告警不盲杀）
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name _module _app_dir port <<<"$entry"
  holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (pid "$2")"}')"
  if [ -n "$holder" ]; then
    echo "  ⚠ 端口 $port 仍被 $holder 占用（不是本仓库进程，未强杀）—— $name 可能起不来"
  fi
done

# ------------------------------------------------------------------ 4. 启动后端服务
echo
echo "== 启动服务 =="
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name module app_dir port <<<"$entry"
  nohup uv run uvicorn "$module" \
    --app-dir "$app_dir" --reload --port "$port" \
    >"$LOG_DIR/$name.log" 2>&1 &
  echo "  启动 $name (:$port) pid=$!"
done

# -------------------------------------------------------------------- 5. 启动前端
nohup npm --prefix apps/console-platform/frontend run dev >"$FRONTEND_LOG" 2>&1 &
echo "  启动 frontend pid=$!"

# -------------------------------------------------------------------- 6. 就绪探测
echo
echo "== 就绪探测 =="
sleep 3
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name _module _app_dir port <<<"$entry"
  ready=""
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ -n "$ready" ]; then
    echo "  ✓ $name    http://127.0.0.1:$port"
  else
    echo "  ✗ $name    http://127.0.0.1:$port 未就绪 —— 看 $LOG_DIR/$name.log"
  fi
done

frontend_url=""
for _ in $(seq 1 20); do
  frontend_url="$(grep -oE 'http://localhost:[0-9]+/' "$FRONTEND_LOG" 2>/dev/null | head -1)"
  [ -n "$frontend_url" ] && break
  sleep 1
done
if [ -n "$frontend_url" ]; then
  echo "  ✓ frontend $frontend_url"
else
  echo "  ✗ frontend 未就绪 —— 看 $FRONTEND_LOG"
fi

echo
echo "日志目录：$LOG_DIR"
echo "停止全部：scripts/dev.sh stop"
