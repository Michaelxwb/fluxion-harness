FROM node:22-alpine AS frontend
WORKDIR /web
COPY apps/console-platform/frontend/package*.json ./
RUN npm install
COPY apps/console-platform/frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY packages /app/packages
COPY config /app/config
COPY apps/console-platform/backend /app/backend
COPY --from=frontend /web/dist /app/static
RUN pip install --no-cache-dir \
    /app/packages/logging-kit \
    /app/packages/api-kit \
    /app/packages/common \
    /app/packages/contracts \
    /app/packages/artifact-store \
    /app/backend
ENV LOG_DIR=/var/log/muad
ENV API_MESSAGES_FILE=/app/config/api-messages.yaml
CMD ["uvicorn", "muad_console_platform.main:app", "--host", "0.0.0.0", "--port", "8000"]
