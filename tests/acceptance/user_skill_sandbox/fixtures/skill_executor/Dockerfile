# syntax=docker/dockerfile:1
# TASK-003 真实 bwrap 验收探针：只预装 agent-core/skill-sdk 的第三方依赖。
# 仓库源码通过 /repo 只读挂载；探针在真实 bwrap 中执行 Skill 脚本。
ARG BASE_IMAGE=fx-sandbox-test:local
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple
USER root
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
      --index-url "${PIP_INDEX_URL}" \
      "fastapi>=0.115,<1" \
      "pydantic>=2.9" \
      "pydantic-settings>=2.5" \
      "redis>=5,<7" \
      "PyYAML>=6,<7" \
      "httpx>=0.28,<1" \
      "sqlalchemy[asyncio]>=2.0,<3"
USER 10001:10001
WORKDIR /app
CMD ["sleep", "infinity"]
