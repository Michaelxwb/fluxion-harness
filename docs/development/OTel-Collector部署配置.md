# OTel Collector 部署配置（O507）

Phase 5（FEAT-P5-04）：Fluxion 的 span 导出经 OTLP HTTP 协议发送到 Collector；应用侧只做 env 接线，Collector 负责采集/转发（Prometheus/Jaeger/ Tempo 等）。

## 应用侧接线（env）

| 变量 | 说明 | 缺省行为 |
|------|------|---------|
| `FLUXION_OTLP_ENDPOINT` | OTLP HTTP endpoint（如 `http://collector:4318/v1/traces`） | 未设置 → 本地 TracerProvider，不 export（dev 默认） |
| `FLUXION_ENV` | deployment.environment resource 标签 | `development` |

- exporter 依赖包（`opentelemetry-exporter-otlp-proto-http`）缺失时**降级不 export + warning**（`fluxion.observability.tracing` logger），服务不阻断（B-03）。
- 埋点统一入口：`fluxion.observability.tracing.traced_scope`（span 自动携带 `fluxion.trace_id/execution_id/tenant_id/request_id` 关联字段，attributes 自动脱敏）。

## Collector 最小配置（otelcol）

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
  memory_limiter:
    check_interval: 1s
    limit_mib: 512

exporters:
  # 按后端选择；以下为本地调试用 logging exporter
  logging:
    verbosity: basic
  # otlp/jaeger:
  #   endpoint: jaeger:4317
  #   tls:
  #     insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [logging]
```

Docker Compose 片段：

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol/config.yaml:ro
    ports:
      - "4318:4318"   # OTLP HTTP
```

Fluxion 服务环境：

```yaml
services:
  fluxion-console:
    environment:
      FLUXION_OTLP_ENDPOINT: http://otel-collector:4318/v1/traces
      FLUXION_ENV: production
```

## 验证

1. 启动 Collector（上例 compose）。
2. 设置 `FLUXION_OTLP_ENDPOINT` 后启动 Fluxion 服务，发起任意 Console API 请求。
3. Collector 日志出现 `fluxion` service 的 span（含 `fluxion.trace_id` 等关联字段）即接线成功。
4. 不安装 exporter 包 / 不设 endpoint：服务正常启动，`fluxion.observability.tracing` 出现降级 warning（或无 warning 的 dev 本地模式）。

## 关联

- 埋点清单 O501–O506（HTTP/Runtime/Model/Tool·MCP/Workflow/DB·Redis）全部经 `traced_scope`（TASK-008 接线）。
- trace 关联完整率 ≥99% 为 Phase 5 Gate 项（E-03/S-04 承载）。
