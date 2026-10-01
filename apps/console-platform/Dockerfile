# console-platform：前端产物 + Python 后端同一镜像（docs/01 §10 Multi-stage）
# 依赖用 uv + 根 uv.lock 解析（与本地/CI 同一口径）——不要改回 pip 手工列表：
# packages/* 之间是 uv workspace 依赖（[tool.uv.sources] … workspace = true），pip 不认识它。
# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend
WORKDIR /web
COPY apps/console-platform/frontend/package.json apps/console-platform/frontend/package-lock.json ./
RUN npm ci
COPY apps/console-platform/frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    LOG_DIR=/var/log/muad \
    API_MESSAGES_FILE=/app/config/api-messages.yaml
COPY pyproject.toml uv.lock ./
COPY config ./config
COPY migrations ./migrations
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --no-editable --package muad-console-platform
COPY --from=frontend /web/dist /app/static
# 非 root 运行（固定 UID/GID 10001，便于 k8s securityContext 与 PVC fsGroup 对齐）
RUN groupadd --gid 10001 muad \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin muad \
 && mkdir -p /var/log/muad /var/cache/muad/skills /app \
 && chown -R muad:muad /var/log/muad /var/cache/muad /app
USER muad

EXPOSE 8000
CMD ["uvicorn", "muad_console_platform.main:app", "--host", "0.0.0.0", "--port", "8000"]
