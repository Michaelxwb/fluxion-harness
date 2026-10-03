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
#
# 三条容易做错、且做错了会**静默骗人**的地方（2026-10-03 修）：
# 1. `kill` 返回 0 只代表**信号送达**，不代表进程退出。停止计数必须按"真的消失了"来数，
#    否则会报"已停 N 个"而实际一个都没退（`uvicorn --reload` 的父子进程尤其如此）。
# 2. 端口被占时不能默认"不是本仓库进程"就放过——那可能正是本仓库上一轮没退干净的进程。
#    判定归属（命令行匹配模块名，或 cwd 在仓库内），是自家的就强杀。
# 3. **就绪探测必须保证"应答者是这次起的新进程"**。只要端口在启动前被占着，新进程会绑定失败
#    当场退出，而探测会打到旧进程上，把"完全没重启"报成 ✓ —— 这是本脚本最危险的失效模式，
#    所以启动前**强制**确认端口已空，否则该服务直接报 ✗。
#
# bash 3.2 兼容（macOS 自带）：`set -u` 下展开空数组会报 unbound variable，
# 因此凡 `${arr[@]}` 之前一律先判 `${#arr[@]}`。

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

#: SIGTERM 之后等多久才动强杀（0.5s × 20）
TERM_WAIT_TICKS=20

# 端口当前的监听者 PID（无则输出空）。
port_holder_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1
}

# 该 PID 是否属于本仓库：命令行含任一服务模块名、或是仓库内的 vite。
# **不按端口判**——端口上是谁需要显式查，别假设。
pid_is_ours() {
  local pid="$1" cmd="" cwd="" entry module
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
  [ -n "$cmd" ] || return 1
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r _name module _app_dir _port <<<"$entry"
    case "$cmd" in *"$module"*) return 0 ;; esac
  done
  case "$cmd" in *"node_modules/.bin/vite"*) return 0 ;; esac
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  case "$cwd" in "$ROOT"/*) return 0 ;; esac
  return 1
}

# 停掉本仓库起的所有服务，回显**真的退出了**的进程数。
# 后端按模块名匹配（全仓唯一）；前端 vite 再按 cwd 归属过滤一次，避免误伤别的项目。
stop_services() {
  local stopped=0 pid entry module cwd i alive
  local pids=""

  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r _name module _app_dir _port <<<"$entry"
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      pids="$pids $pid"
    done < <(pgrep -f "$module" 2>/dev/null || true)
  done

  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    case "$cwd" in "$ROOT"/*) pids="$pids $pid" ;; esac
  done < <(pgrep -f "node_modules/.bin/vite" 2>/dev/null || true)

  [ -n "${pids// /}" ] || { echo 0; return; }

  # ① 先礼：SIGTERM
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done

  # ② 等它们**真的退出**——`kill` 成功 ≠ 进程没了（这条以前被当成"已停"计数）
  i=0
  while [ "$i" -lt "$TERM_WAIT_TICKS" ]; do
    alive=""
    for pid in $pids; do kill -0 "$pid" 2>/dev/null && alive="1"; done
    [ -n "$alive" ] || break
    sleep 0.5
    i=$((i + 1))
  done

  # ③ 后兵：还活着且确实是自家的，SIGKILL（`uvicorn --reload` 的父子进程常在此列）
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then
      if pid_is_ours "$pid"; then
        echo "  (pid $pid 未响应 SIGTERM，强杀)" >&2
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
  done
  sleep 1

  # ④ 数**真的消失了的**，而不是"发出过几个信号"
  for pid in $pids; do
    kill -0 "$pid" 2>/dev/null || stopped=$((stopped + 1))
  done
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
  echo "  已停 $stopped 个进程（已确认退出），等待端口释放…"
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
    holder_pid="$(port_holder_pid "$port")"
    if [ -n "$holder_pid" ]; then
      if pid_is_ours "$holder_pid"; then
        echo "  ⚠ $port 仍被**本仓库**进程 pid=$holder_pid 占用（未强杀；要清就再跑一次 stop）"
      else
        echo "  ⚠ $port 仍被非本仓库进程 pid=$holder_pid 占用（不属于本仓库，按约定不动它）"
      fi
    else
      echo "  ✓ $port 已释放"
    fi
  done
  exit 0
fi

# --------------------------------------------------- 3. 端口占用检查（自家强杀，他家拒绝启动）
echo
echo "== 端口检查 =="
BLOCKED_PORTS=""
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name _module _app_dir port <<<"$entry"
  holder_pid="$(port_holder_pid "$port")"
  if [ -n "$holder_pid" ] && pid_is_ours "$holder_pid"; then
    echo "  ⚠ 端口 $port 仍被本仓库进程 pid=$holder_pid 占用，强杀"
    kill -9 "$holder_pid" 2>/dev/null || true
    sleep 1
    holder_pid="$(port_holder_pid "$port")"
  fi
  if [ -n "$holder_pid" ]; then
    # 端口不空就不许启动：新进程会绑定失败退出，而就绪探测会打到这个旧/他进程上，
    # 把"什么都没重启"报成 ✓（2026-10-03 实际踩到过）。宁可这里报 ✗。
    echo "  ✗ 端口 $port 被非本仓库进程 pid=$holder_pid 占用 —— $name 无法启动"
    BLOCKED_PORTS="$BLOCKED_PORTS $port"
  else
    echo "  ✓ $port 空闲"
  fi
done

# ------------------------------------------------------------------ 4. 启动后端服务
echo
echo "== 启动服务 =="
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name module app_dir port <<<"$entry"
  case " $BLOCKED_PORTS " in
    *" $port "*)
      echo "  跳过 $name (:$port) —— 端口被占用"
      continue
      ;;
  esac
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
  case " $BLOCKED_PORTS " in
    *" $port "*)
      echo "  ✗ $name    http://127.0.0.1:$port 未启动（端口被他人占用，不是它没起来）"
      continue
      ;;
  esac
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
