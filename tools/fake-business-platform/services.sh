#!/usr/bin/env bash
# 本地假业务平台 / 假 MCP 的启停脚本。
#
# 一个 server.py 进程就是一个服务；本脚本按下面的 SERVICES 表拉起多份实例，
# 每份实例的端口、模式与认证各自独立。零依赖：只用 python3 标准库。
#
#   ./services.sh start              # 启动全部服务（端口被本工具的旧实例占着会自动收走）
#   ./services.sh start mcp          # 只启动指定的一个/多个
#   ./services.sh start --force      # 连非本工具的占用者一起杀掉再启动（危险，默认不做）
#   ./services.sh stop               # 停止全部
#   ./services.sh restart [服务...]
#   ./services.sh status             # 看状态与地址
#   ./services.sh logs <服务>        # 跟踪某个服务的日志
#   ./services.sh async-batch        # 对 platform 服务跑 ASYNC BATCH Skill 端到端
#   ./services.sh real-skill 1001    # 真实内网 Skill 对 Fake MSSP（需真实包）
#
# PID、日志与就绪信息落在 .data/fake-business-platform/（已被 .gitignore 覆盖）。
#
# 服务表（名称: 端口 / 用途）：
#   platform      18080  mssw 模式业务平台（Skill 与 MSSM 适配器走这条）
#   mssp          18083  MSSP/SOAR 网关（daily-duty-report / fake MSSP 走这条）
#   mcp           18081  MCP Streamable HTTP，Authorization: Bearer
#   mcp-headers   18082  MCP Streamable HTTP，自定义请求头（含一个 secret 头）
#
# 可用环境变量覆盖：HOST / PLATFORM_MODE / PLATFORM_PORT / MSSP_PORT /
# MSSP_WECOM_WRITE / MCP_PORT / MCP_HEADERS_PORT / FAKE_AK / FAKE_SK /
# MCP_TOKEN / MCP_APP_ID / MCP_PLATFORM_UID / MCP_API_KEY / PYTHON /
# START_TIMEOUT / STOP_TIMEOUT
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
# 优先用仓库虚拟环境的解释器（与项目 Python 版本一致）；显式给 PYTHON 时不覆盖。
if [ -z "${PYTHON:-}" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"
SERVER="$ROOT/server.py"
RUN_DIR="${RUN_DIR:-$REPO_ROOT/.data/fake-business-platform}"

HOST="${HOST:-0.0.0.0}"
PLATFORM_MODE="${PLATFORM_MODE:-mssw}"
PLATFORM_PORT="${PLATFORM_PORT:-18080}"
MSSP_PORT="${MSSP_PORT:-18083}"
MSSP_WECOM_WRITE="${MSSP_WECOM_WRITE:-0}"
MCP_PORT="${MCP_PORT:-18081}"
MCP_HEADERS_PORT="${MCP_HEADERS_PORT:-18082}"
FAKE_AK="${FAKE_AK:-test-ak}"
FAKE_SK="${FAKE_SK:-test-sk}"
MCP_TOKEN="${MCP_TOKEN:-test-mcp-token}"
MCP_APP_ID="${MCP_APP_ID:-sdspmcp}"
MCP_PLATFORM_UID="${MCP_PLATFORM_UID:-53842}"
MCP_API_KEY="${MCP_API_KEY:-test-key}"
START_TIMEOUT="${START_TIMEOUT:-10}"
STOP_TIMEOUT="${STOP_TIMEOUT:-5}"

SERVICES="platform mssp mcp mcp-headers"

usage() {
    sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    printf '\n服务：%s\n' "$SERVICES"
}

port_of() {
    case "$1" in
        platform) printf '%s' "$PLATFORM_PORT" ;;
        mssp) printf '%s' "$MSSP_PORT" ;;
        mcp) printf '%s' "$MCP_PORT" ;;
        mcp-headers) printf '%s' "$MCP_HEADERS_PORT" ;;
        *) return 1 ;;
    esac
}

port_env_var() {
    case "$1" in
        platform) printf 'PLATFORM_PORT' ;;
        mssp) printf 'MSSP_PORT' ;;
        mcp) printf 'MCP_PORT' ;;
        mcp-headers) printf 'MCP_HEADERS_PORT' ;;
        *) return 1 ;;
    esac
}

# 端口被别的进程监听：start 前必须收走，否则 BSD 下 0.0.0.0 与 127.0.0.1 能同时绑上，
# 请求会落到谁身上说不清。
port_holders() {
    command -v lsof >/dev/null 2>&1 || return 0
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true
}

is_this_tool() {
    ps -o command= -p "$1" 2>/dev/null | grep -q 'fake-business-platform/server.py'
}

term_then_kill() {
    local pid=$1 waited=0
    kill -TERM "$pid" 2>/dev/null || true
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$waited" -ge $(( STOP_TIMEOUT * 5 )) ]; then
            kill -KILL "$pid" 2>/dev/null || true
            return 0
        fi
        sleep 0.2
        waited=$(( waited + 1 ))
    done
}

# 默认只收本工具遗留的 server.py（旧实例/手工起的实例）；别人的进程一律不动，
# 除非显式 --force。返回 0 表示端口已可绑。
reclaim_port() {
    local name=$1 holder holders
    holders=$(port_holders "$(port_of "$name")")
    for holder in $holders; do
        if is_this_tool "$holder"; then
            printf '  %-12s 收走遗留实例  pid=%s（端口 %s）\n' \
                "$name" "$holder" "$(port_of "$name")"
            term_then_kill "$holder"
        elif [ "${FORCE:-0}" = "1" ]; then
            printf '  %-12s --force 杀掉占用者 pid=%s  %s\n' \
                "$name" "$holder" "$(ps -o command= -p "$holder" 2>/dev/null | cut -c1-120)"
            term_then_kill "$holder"
        else
            printf '  %-12s 端口 %s 被其它进程占用  pid=%s  %s\n' \
                "$name" "$(port_of "$name")" "$holder" \
                "$(ps -o command= -p "$holder" 2>/dev/null | cut -c1-120)" >&2
            printf '  %-12s 先 `kill %s` / 用 --force，或改 %s 换端口\n' \
                "$name" "$holder" "$(port_env_var "$name")" >&2
            return 1
        fi
    done
    return 0
}

pid_file_of() { printf '%s/%s.pid' "$RUN_DIR" "$1"; }
log_file_of() { printf '%s/%s.log' "$RUN_DIR" "$1"; }
ready_file_of() { printf '%s/%s.ready.json' "$RUN_DIR" "$1"; }

public_host() {
    case "$HOST" in
        "" | 0.0.0.0 | :: | "[::]") printf '127.0.0.1' ;;
        *) printf '%s' "$HOST" ;;
    esac
}

url_of() { printf 'http://%s:%s' "$(public_host)" "$(port_of "$1")"; }

is_known() {
    case " $SERVICES " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

read_pid() {
    local file=$1 pid=""
    [ -f "$file" ] || return 0
    pid=$(awk 'NR==1{print $1}' "$file" 2>/dev/null || true)
    case "$pid" in
        "" | *[!0-9]*) return 0 ;;
    esac
    printf '%s' "$pid"
}

# pid 文件第二列记的是启动时的端口：换了端口再 start 就是在改配置，需要重启而不是"已在运行"。
read_pid_port() {
    awk 'NR==1{print $2}' "$1" 2>/dev/null || true
}

# 只认同一个 pid 仍是我们的 server.py：pid 会被系统复用，不能只看存活。
is_our_server() {
    local pid=$1
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    ps -o command= -p "$pid" 2>/dev/null | grep -q 'fake-business-platform/server.py'
}

build_args() {
    ARGS=()
    case "$1" in
        platform)
            ARGS=(--platform "$PLATFORM_MODE" --host "$HOST" --port "$PLATFORM_PORT"
                --ak "$FAKE_AK" --sk "$FAKE_SK")
            ;;
        mssp)
            ARGS=(--platform mssp --host "$HOST" --port "$MSSP_PORT")
            if [ "$MSSP_WECOM_WRITE" = "1" ]; then
                ARGS+=(--enable-wecom-write)
            fi
            ;;
        mcp)
            ARGS=(--host "$HOST" --port "$MCP_PORT"
                --mcp-auth bearer --mcp-token "$MCP_TOKEN")
            ;;
        mcp-headers)
            ARGS=(--host "$HOST" --port "$MCP_HEADERS_PORT"
                --mcp-auth headers
                --mcp-header "X-App-Id=$MCP_APP_ID"
                --mcp-header "X-Platform-Uid=$MCP_PLATFORM_UID"
                --mcp-header "X-Api-Key=$MCP_API_KEY")
            ;;
        *) return 1 ;;
    esac
}

# 0=就绪 1=进程已退出 2=超时
wait_ready() {
    local pid=$1 log=$2
    local deadline=$(( $(date +%s) + START_TIMEOUT ))
    while :; do
        if grep -q '"status": *"ready"' "$log" 2>/dev/null; then
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
        if [ "$(date +%s)" -ge "$deadline" ]; then
            return 2
        fi
        sleep 0.2
    done
}

start_one() {
    local name=$1 pid_file log_file ready_file pid
    pid_file=$(pid_file_of "$name")
    log_file=$(log_file_of "$name")
    ready_file=$(ready_file_of "$name")

    local stale_pid=""
    pid=$(read_pid "$pid_file")
    if is_our_server "$pid"; then
        local running_port
        running_port=$(read_pid_port "$pid_file")
        if [ -z "$running_port" ] || [ "$running_port" = "$(port_of "$name")" ]; then
            printf '  %-12s 已在运行  pid=%-6s %s\n' "$name" "$pid" "$(url_of "$name")"
            return 0
        fi
        stale_pid=$pid
    fi

    # 先确认目标端口能绑：换端口时不能先把在跑的实例停掉，再发现新端口被别人占着。
    reclaim_port "$name" || return 1

    if [ -n "$stale_pid" ]; then
        printf '  %-12s 端口从 %s 改为 %s，重启该实例\n' \
            "$name" "$(read_pid_port "$pid_file")" "$(port_of "$name")"
        term_then_kill "$stale_pid"
    fi
    rm -f "$pid_file" "$ready_file"

    build_args "$name"
    : > "$log_file"
    (
        cd "$ROOT"
        exec "$PYTHON" "$SERVER" "${ARGS[@]}"
    ) >>"$log_file" 2>&1 &
    pid=$!

    if wait_ready "$pid" "$log_file"; then
        printf '%s %s\n' "$pid" "$(port_of "$name")" > "$pid_file"
        grep -m1 '"status": *"ready"' "$log_file" > "$ready_file" || true
        printf '  %-12s 已启动    pid=%-6s %s\n' "$name" "$pid" "$(url_of "$name")"
        return 0
    fi

    printf '  %-12s 启动失败\n' "$name" >&2
    tail -n 20 "$log_file" >&2 || true
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
    return 1
}

stop_one() {
    local name=$1 pid_file pid
    pid_file=$(pid_file_of "$name")
    pid=$(read_pid "$pid_file")
    if ! is_our_server "$pid"; then
        rm -f "$pid_file" "$(ready_file_of "$name")"
        printf '  %-12s 未在运行\n' "$name"
        return 0
    fi
    term_then_kill "$pid"
    rm -f "$pid_file" "$(ready_file_of "$name")"
    printf '  %-12s 已停止    pid=%s\n' "$name" "$pid"
}

status_one() {
    local name=$1 pid_file pid
    pid_file=$(pid_file_of "$name")
    pid=$(read_pid "$pid_file")
    if is_our_server "$pid"; then
        printf '  %-12s 运行中     pid=%-6s %s\n' "$name" "$pid" "$(url_of "$name")"
    else
        printf '  %-12s 已停止     %s\n' "$name" "$(url_of "$name")"
    fi
}

resolve_targets() {
    TARGETS=""
    local name
    if [ "$#" -eq 0 ]; then
        TARGETS="$SERVICES"
        return 0
    fi
    for name in "$@"; do
        if ! is_known "$name"; then
            printf '未知服务：%s（可用：%s）\n' "$name" "$SERVICES" >&2
            return 1
        fi
        TARGETS="$TARGETS $name"
    done
}

cmd_start() {
    mkdir -p "$RUN_DIR"
    printf '启动（日志目录 %s）\n' "$RUN_DIR"
    local name failed=0
    for name in $TARGETS; do
        start_one "$name" || failed=1
    done
    return "$failed"
}

cmd_stop() {
    printf '停止\n'
    local name
    for name in $TARGETS; do
        stop_one "$name"
    done
}

cmd_status() {
    printf '状态\n'
    local name
    for name in $TARGETS; do
        status_one "$name"
    done
}

cmd_async_batch() {
    resolve_targets platform && cmd_start || return 1
    printf '运行 fake-platform-async-batch 端到端（输出 %s/async-batch-demo）\n' "$RUN_DIR"
    RUN_DIR="$RUN_DIR" "$PYTHON" "$ROOT/async_batch_check.py"
}

cmd_real_skill() {
    resolve_targets mssp && cmd_start || return 1
    local extra=()
    if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
        extra+=(--company-id "$1")
        shift
    fi
    printf '真实内网 Skill 对 Fake MSSP 冒烟（输出 .data/real-mssp-skill/）\n'
    "$PYTHON" "$ROOT/real_skill_smoke.py" --base-url "$(url_of mssp)" "${extra[@]}" "$@"
}

cmd_logs() {
    local name=${1:-platform} log
    if ! is_known "$name"; then
        printf '未知服务：%s（可用：%s）\n' "$name" "$SERVICES" >&2
        return 1
    fi
    log=$(log_file_of "$name")
    if [ ! -f "$log" ]; then
        printf '还没有日志：%s（先 start）\n' "$log" >&2
        return 1
    fi
    printf '跟踪 %s（%s），Ctrl-C 退出\n' "$log" "$name"
    tail -n 50 -f "$log"
}

main() {
    local command=${1:-help}
    case "$command" in
        -h | --help | help)
            usage
            return 0
            ;;
    esac
    [ "$#" -gt 0 ] && shift
    FORCE=0
    NAMES=""
    local arg
    for arg in "$@"; do
        case "$arg" in
            -f | --force) FORCE=1 ;;
            *) NAMES="$NAMES $arg" ;;
        esac
    done
    case "$command" in
        start)
            resolve_targets $NAMES && cmd_start
            ;;
        stop)
            resolve_targets $NAMES && cmd_stop
            ;;
        restart)
            resolve_targets $NAMES && { cmd_stop; cmd_start; }
            ;;
        status)
            resolve_targets $NAMES && cmd_status
            ;;
        logs)
            cmd_logs $NAMES
            ;;
        async-batch)
            cmd_async_batch
            ;;
        real-skill)
            cmd_real_skill "$@"
            ;;
        *)
            printf '未知命令：%s\n\n' "$command" >&2
            usage >&2
            return 1
            ;;
    esac
}

main "$@"
