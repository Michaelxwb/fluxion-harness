FROM python:3.12-slim
WORKDIR /app
COPY packages /app/packages
COPY config /app/config
COPY apps/agent-runtime /app/service
RUN pip install --no-cache-dir \
    /app/packages/logging-kit \
    /app/packages/api-kit \
    /app/packages/common \
    /app/packages/contracts \
    /app/packages/artifact-store \
    /app/packages/agent-core \
    /app/packages/platform-sdk \
    /app/service
ENV LOG_DIR=/var/log/muad
ENV API_MESSAGES_FILE=/app/config/api-messages.yaml
CMD ["uvicorn", "muad_agent_runtime.main:app", "--host", "0.0.0.0", "--port", "8001"]
