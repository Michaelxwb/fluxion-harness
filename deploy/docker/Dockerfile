# syntax=docker/dockerfile:1

# =============================================================================
# Fluxion Harness 后端镜像（多阶段构建）
#
# 构建上下文必须是仓库根目录（Dockerfile 需要 backend/、frontend/、pyproject.toml、
# uv.lock、package.json、pnpm-lock.yaml、pnpm-workspace.yaml），示例：
#
#   docker build -f deploy/docker/Dockerfile -t fluxion-harness/fluxion:0.1.0 .
#
# 关于前端 dist 的路径约定（重要，见阶段三注释）：
#   后端 CLI 的 `_ensure_frontend_builds` 用 `Path(__file__).resolve().parents[4]`
#   定位仓库根，再拼接 `frontend/apps/{console,chat}/dist`。本镜像把后端源码放在
#   /app/backend/src、把前端产物放在 /app/frontend/apps/{console,chat}/dist，并设置
#   PYTHONPATH=/app/backend/src 保证 `import fluxion` 解析到源码路径，从而
#   parents[4] == /app 与仓库根一致。这样生产模式 `fluxion serve`（非 dev，不依赖
#   前端）和开发模式 `fluxion serve --dev`（挂载前端）都能自洽工作。
# =============================================================================

# -----------------------------------------------------------------------------
# 阶段一：构建前端（console + chat 两个 Vite 应用）
# -----------------------------------------------------------------------------
FROM node:22-slim AS frontend

# 与 package.json 的 packageManager 对齐，避免 corepack 版本漂移
RUN npm install -g pnpm@10.15.0

WORKDIR /build

# 先复制依赖清单与前端源码，最大化利用 Docker 层缓存
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend ./frontend

# 严格按锁文件安装前端依赖
RUN pnpm install --frozen-lockfile

# 构建 console 与 chat，各自产出 frontend/apps/{console,chat}/dist
# Vite 已配置 base=/console/ 与 base=/chat/，与 dev bundle 挂载路径一致
RUN pnpm build

# -----------------------------------------------------------------------------
# 阶段二：准备后端依赖（uv 同步 uv.lock）
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS backend

# 官方推荐方式：从 astral 镜像复制 uv 二进制；latest 能读取当前锁文件格式，
# 如需可复现构建可固定到具体版本（例如 ghcr.io/astral-sh/uv:0.6.x）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 使用独立 venv 路径，便于最终阶段直接复制；LINK_MODE=copy 让 venv 可被 COPY
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先复制依赖清单与后端源码（uv.lock 固定依赖版本）
COPY pyproject.toml uv.lock ./
COPY backend ./backend

# 同步依赖并安装本项目（默认 editable 安装到 /app/backend/src）
# --frozen 严格使用 uv.lock；--no-dev 跳过 dev 依赖组（pytest/mypy/ruff 属可选 extra）
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# 阶段三：最终运行镜像
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# PYTHONPATH 指向源码目录：保证 `import fluxion` 解析到 /app/backend/src/fluxion，
# 于是 cli/main.py 的 __file__ = /app/backend/src/fluxion/cli/main.py，其 parents[4]
# 恰好等于 /app（仓库根），`fluxion serve --dev` 的 _ensure_frontend_builds 才能在
# /app/frontend/apps/{console,chat}/dist 找到前端产物。
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/backend/src"

WORKDIR /app

# 复制虚拟环境与后端源码（editable 安装指向 /app/backend/src，路径须与镜像内一致）
COPY --from=backend /opt/venv /opt/venv
COPY --from=backend /app/backend ./backend

# 前端产物：放在与源码一致的相对路径结构下
#   /app/frontend/apps/console/dist
#   /app/frontend/apps/chat/dist
# 生产模式 fluxion serve（非 dev）不依赖前端；此处一并放置以便 --dev 模式可用。
RUN mkdir -p /app/frontend/apps/console /app/frontend/apps/chat
COPY --from=frontend /build/frontend/apps/console/dist /app/frontend/apps/console/dist
COPY --from=frontend /build/frontend/apps/chat/dist /app/frontend/apps/chat/dist

# 入口脚本：桥接 FLUXION_DATABASE_URL / FLUXION_MASTER_KEY 到 fluxion serve
COPY deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 生产模式服务端口（与 CLI 默认端口一致）
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
