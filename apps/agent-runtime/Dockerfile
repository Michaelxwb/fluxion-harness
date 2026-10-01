# agent-runtime：依赖用 uv + 根 uv.lock 解析（与本地/CI 同一口径）。
# 不要改回 pip 手工列表：packages/* 之间是 uv workspace 依赖（[tool.uv.sources] … workspace = true），pip 不认识它。
# syntax=docker/dockerfile:1

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
RUN uv sync --frozen --no-dev --no-editable --package muad-agent-runtime
EXPOSE 8001
CMD ["uvicorn", "muad_agent_runtime.main:app", "--host", "0.0.0.0", "--port", "8001"]
