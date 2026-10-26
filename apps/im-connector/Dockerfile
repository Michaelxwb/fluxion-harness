# im-connector：有状态通道运行时（design §4.1 的 im-gateway 运行时角色）。
# 依赖用 uv + 根 uv.lock 解析；不要改成 pip 手工列表（workspace 内部包不在 PyPI）。
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
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --no-editable --package muad-im-connector
RUN groupadd --gid 10001 muad \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin muad \
 && mkdir -p /var/log/muad /var/cache/muad/skills /app \
 && chown -R muad:muad /var/log/muad /var/cache/muad /app
USER muad

EXPOSE 8004
CMD ["uvicorn", "muad_im_connector.main:app", "--host", "0.0.0.0", "--port", "8004"]
