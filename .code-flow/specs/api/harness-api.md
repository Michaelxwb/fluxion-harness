---
id: harness-api
description: Agent Harness 通用平台规则：api
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-api-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_api_i18n.py
    - tests/test_error_catalog.py
    - tests/acceptance/test_foundation_api_envelope.py
    cwd: .
    timeout: 300
- rule: RULE-api-002
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_skill/test_import_idempotency.py
    cwd: .
    timeout: 300
---

# harness-api

## Rules

- [RULE-api-002] 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次提交；请求指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256，**必须含 `endpoint` 与 `tenant_id` 两个判别键**，另含 actor/资源与关键参数（Runtime 另含 run_id/message_id）；同 key 同指纹重放首次持久化结果（SSE 按 submission 重放已落库事件、不重新执行），同 key 不同指纹返回 `IDEMPOTENCY_MISMATCH`；并发插入由 partial unique 兜底，落败者读取首次提交结果。真实并发控制是**先取 `pg_advisory_xact_lock`**（键由 `(tenant_id, idempotency_key, endpoint)` 派生）把同键请求串行化，partial unique 只作兜底（`channel_service.py:153-159`、`auth_service.py:270-276`、`audit_export_service.py:386-392`、`agent_service.py:159-162`、`mcp_service.py:240-243`、`skill_service.py:278-286`）。**存量待收敛**：console 侧 `agent_service`/`mcp_service`/`skill_service` 三处 `_fingerprint` 仍用 `|` 拼接且不含 endpoint/tenant，须按本口径对齐（合规参考 `channel_service.py:59-74`、`auth_service.py:253-267`、`audit_export_service.py:100-119`、`apps/agent-runtime/src/muad_agent_runtime/application/run_submission.py:30-49`）。
- [RULE-api-001] Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。

## Conventions

列表封套适用于**所有**列表端点，包括子资源列表（`/{parent}/{id}/items`）；返回裸数组会导致前端 Page 解析失败（`page.items` 为 undefined、表格恒空——05 指定用户 Tab 真实事故）。

✅ 子资源列表分页：

```python
items, total = await service.list_grants(tenant_id, skill_id, page, page_size)
return ok(catalog, paginate(items=[...], page=page, page_size=page_size, total=total))
```

❌ 裸数组：

```python
return ok(catalog, [item.model_dump() for item in items])   # 前端 Page<T> 解析为 undefined
```

## Conventions

服务底座统一复用 api-kit 原语，业务代码不得自建同类机制：

| 原语 | 用途 |
|---|---|
| `install_api_foundation(app, ...)` | 上下文中间件 + 统一异常/Envelope + `MessageCatalog` |
| `install_console_security(app, session_verifier, role_resolver)` | 会话校验（401 `UNAUTHORIZED`）与角色依赖（403 `FORBIDDEN`）|
| `install_health_probes(app, readiness_checks)` | `/healthz` 与 `/readyz`（依赖缺失返回 503）|
| `validate_startup(settings, engine, artifact_store)` | 配置/迁移到 head/存储挂载校验，失败快速退出 |
| `write_config_audit(session, ...)` | 业务同事务写 `control.config_audit_log`，Secret 键值不落盘 |

✅ 正确（装配共享底座，由 api-kit 统一行为）：

```python
install_api_foundation(app)
install_console_security(app, session_verifier, role_resolver)
install_health_probes(app, {"db": engine_probe})
await validate_startup(settings, engine, artifact_store, migrations_dir=...)
```

❌ 错误（自建探针/校验/审计，绕过 api-kit）：

```python
@app.get("/healthz")
async def healthz():          # 自建探针，缺少依赖态 503 与统一实现
    return {"ok": True}
```

## Conventions

- `message_args` 只有在 YAML 模板包含对应 `{placeholder}` 时才会渲染；通用错误码（如 `COMMON_CONFLICT`）不得承载需要展示给用户的细节。需要展示细节（引用数、字段名等）时必须登记专用错误码与中英文模板。

✅ 专用错误码 + 占位符：

```yaml
MODEL_IN_USE:
  http_status: 409
  messages:
    zh-CN: "模型被 {agent_count} 个 Agent 引用，无法删除"
    en-US: "Model is referenced by {agent_count} agent(s)"
```

❌ 用通用码携带细节，前端只能看到通用文案：

```python
raise AppError(ErrorCode.COMMON_CONFLICT, message_args={"agent_count": 3})
```

## Conventions

SSE 流式接口（Runtime `/v1/runs`、`/v1/runs/{id}/resume`）契约：

- 事件先写入 `canonical_event` 再输出，SSE 帧沿用已持久化的 `seq`/`timestamp`（禁止按连接自增）；`run.created` 同样先落库。
- 按 submission 重放：同 `Idempotency-Key` 命中时按已落库事件重放，不分配新序号、不重新执行；未结束的流可续订，重放结束于持久化终态事件。
- 心跳注释帧 `: heartbeat` 不带 event/seq、不计入序号；断流不写伪造终态，Run 交由 Reaper 按租约回收。

✅ 先落库再输出（沿用持久 seq）：

```python
seq = await EventWriter(session).append(..., stream_type="message.delta", payload={"delta": chunk})
yield ExecutorEvent(type="message.delta", data={"delta": chunk}, seq=seq, timestamp=ts)
```

❌ 生产路径每连接自增序号（resume 会从 1 重排，重放无法对齐；`SseEmitter` 的连接内递增只服务无 `event.seq` 的合成/测试事件，见 `apps/agent-runtime/src/muad_agent_runtime/application/sse.py:31-38`）：

```python
self._seq += 1          # 生产路径的连接内计数器，不来自 canonical_event
yield ExecutorEvent(type="message.delta", data={"delta": chunk})
```

## Conventions

成功码是**字符串 `"0"`**，且 `config/api-messages.yaml` 的码集合与 `ErrorCode` 枚举**双向一致**（`packages/api-kit/src/muad_api/response.py:73-74`、`tests/test_error_catalog.py:44-48`）：`ErrorCode` 里有的码必须在 catalog 登记，catalog 里的码（除 `"0"`）也必须在 `ErrorCode` 里存在。

- **catalog 硬校验**：每个码必须有 `zh-CN` 与 `en-US` 文案、`http_status ∈ [100,599]`，且 `COMMON_INTERNAL_ERROR` 必须存在（`packages/api-kit/src/muad_api/catalog.py:30-58`、`tests/test_error_catalog.py:65-86`）。
- **未登记 code 的兜底语义**：HTTP 500 + `code` 原样回显 + `msg` 回退「系统内部错误」（`tests/acceptance/test_foundation_api_envelope.py:72-77`）。

✅ 成功封套：

```python
return ok(catalog, data)          # code == "0"
```

❌ 任一侧多/少一个 code：`catalog.codes() - {'0'}` 与 `ErrorCode` 对不上即测试红。

关联头口径（`packages/api-kit/src/muad_api/middleware.py:30-36,60-64`；前端发送侧 `apps/console-platform/frontend/src/api/client.ts:78-86`）：

- **入站**：`X-Request-Id`（缺失则生成）、`X-Trace-Id`（缺失时可由 `traceparent` 推导，再缺失回落 request_id）、`X-Locale`（或 `Accept-Language`）。
- **出站回写**：`X-Request-Id`、`X-Trace-Id`、`Content-Language`、`Vary`。

错误文案硬编码三禁（机检 `scripts/check_error_message_hardcode.py:13-32`，扫描 `apps/` 与 `packages/` 下全部 `*.py`）：

- 禁 `HTTPException(detail="字面量")`；
- 禁 `{"msg": "字面量"}`；
- `AppError("CODE")` 传入裸字符串时，该 CODE 必须在 catalog 登记，否则即红。

✅ `raise AppError(ErrorCode.MODEL_IN_USE)`　❌ `raise AppError("MODEL_IN_USE")`（该码未登记即红）。

分页常量在 contracts **二次声明**：`packages/contracts/src/muad_contracts/channel.py:10-20` 复刻 `DEFAULT_PAGE_SIZE=20` / `MAX_PAGE_SIZE=100`（注释说明 contracts 是独立包，不反向依赖 api-kit）。

✅ 两处同值；❌ 只改 api-kit 一处会让走 contracts 校验的内部渠道列表越界。

api-kit 原语清单（上表之外的其余共享原语，同样禁止业务代码自建）：`require_internal_service` / `INTERNAL_SERVICE_HEADER`、`require_session` / `require_roles`（`packages/api-kit/src/muad_api/security.py:15,18-31,58-79`）、`install_metrics` / `declare_metric` / `inc_counter`（`packages/api-kit/src/muad_api/metrics.py`）、`database_readiness` / `ReadinessDetail`（`packages/api-kit/src/muad_api/probes.py:14-33`）。

`/internal/*` 的**每个业务路由**都要有服务身份门控（2026-10-06 评审 #1）：这些端点的租户与 actor 都从请求头取，只有先确认调用方是受信服务（Runtime / Console / 网关）之后，那些头才谈得上「可信上下文」。门控本身只有一份实现（api-kit 的 `require_internal_service`），路由可以按路由器挂（`APIRouter(dependencies=[Depends(require_internal_service)])`）或按端点挂（`InternalServiceDep`），但**不能有例外**——只给 `/internal/admin/*` 加门控、把业务路由留在外面，等于任何能连上内网端口、知道目标租户/资源 id 的人都能读、建、取消别人的东西。

✅ 路由器级门控，一处生效全部端点：

```python
router = APIRouter(
    prefix="/internal/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_internal_service)],
)
```

❌ 只在 Admin 面做门控，业务路由裸奔：

```python
admin = APIRouter(prefix="/internal/admin/tasks", dependencies=[Depends(require_internal_service)])
tasks = APIRouter(prefix="/internal/tasks")     # 同样的租户/actor 头，却没有门控
```

`write_config_audit` 的**租户归属不得取自请求头**（2026-09-28 收口）：`tenant_id` 缺省时只回落部署默认租户，**不再**回落 `current_tenant_id()`——后者是中间件从 `X-Tenant-Id` 写入 contextvar 的值（客户端可任意改写）。读数据还能靠租户谓词兜，而审计归属被污染是合规问题：会产出「调用方自选租户」的审计行。

**规范化 JSON 只认严格 JSON，且语义 ↔ 文本必须一一对应**（2026-10-07）。指纹/确定性键的口径唯一实现是 `muad_contracts.canonical.canonical_json`（`sort_keys` + 紧凑分隔符 + `ensure_ascii=False` + **`allow_nan=False`**），**不接受** `default=str` 之类的兜底。这条规矩有两面，两面都会出血：

- **漏**（键少了决定性输入）⇒ 复用别人的结果：`RULE-api-002` 要求判别键含 `endpoint`/`tenant_id`；
- **多**（键含了良性变化的输入）⇒ 把良性重试判成 `IDEMPOTENCY_MISMATCH`：投递指纹**不含正文文案**，因为 Worker 每次尝试都按当前 locale 重渲染它（`apps/im-gateway/src/muad_im_gateway/api/delivery.py:161-182`）。

Python 的三个陷阱要一起堵：`sort_keys`（键序）、`allow_nan=False`（`json` 默认既接受也生成 `NaN`/`Infinity`——不同的 NaN 会序列化成同一个 `"NaN"`，两份语义不同的载荷拿到同一个键；这串文本写进 `jsonb` 还会被 PostgreSQL 直接拒 ⇒ **用户拿 500 而不是 422**）、**不用** `default=str`（`date(2026,1,1)` 与 `"2026-01-01"` 会撞成同一个键）。自由形态字段（`input`/`execution_snapshot`/`input_template`）在**契约 DTO** 上用 `ensure_strict_json` 拒一次，别指望持久层兜底。

✅ 边界即拒（契约里的 `field_validator`，非有限数 → 422 `COMMON_VALIDATION_ERROR`）：

```python
@field_validator("input", "execution_snapshot")
@classmethod
def _strict_json(cls, value: dict[str, Any]) -> dict[str, Any]:
    return ensure_strict_json(value)
```

❌ 让 `NaN` 穿过 DTO 与指纹，直到 `INSERT ... input_json::jsonb` 才炸（`invalid input syntax for type json`）。**同一份载荷在不同层有不同表示**是这条最贵的形态：`model_dump(mode="json")` 会把 `NaN` 静默转成 `None`，于是指纹算的是 `{"threshold": null}`，而落库用的是原始 `{"threshold": NaN}`。

✅ 调用方显式传 `tenant_id=`（用户态路由请传**账号租户**）；❌ 省略 `tenant_id` 并依赖 `current_tenant_id()` 兜底。

密码策略违规的错误码**统一为 `COMMON_VALIDATION_ERROR`（422）**，不因"被哪一层拦住"而分叉（2026-10-05 收口，实例：platform-settings 的收口期修正）。密码长度其实有**两个下界**：DTO 的绝对下界（`MIN_PASSWORD_LENGTH_FLOOR=8`，低于它 pydantic 直接 422）与平台策略下界（`auth.min_password_length`，`[8, 策略)` 由服务层判定）。两者必须返回**同一个码**，且 `create_account` 与 `change_password` 一致。

✅ 服务层策略校验抛校验错，与 DTO 的 422 同码：

```python
if len(password) < policy.min_password_length:
    raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)   # 与 DTO 下界同码
```

❌ 服务层抛 `COMMON_BAD_REQUEST`（400）：

```python
if len(password) < policy.min_password_length:
    raise AppError(ErrorCode.COMMON_BAD_REQUEST)        # 同一违规两个码
```

**为什么这条值得写下来**：平台策略可配之后，下界可以下调（12 → 8），**拦截点会从 DTO 悄悄挪到服务层**——同一个输入从 422 变成 400，而没有任何人决策过这件事；下游只会看到"某条归档验收红了"，最省事的处置恰好是**改那条验收去迁就实现**，于是一次无人决策的行为变更被洗成事实。两个桶同码可以让这种漂移根本发生不了。

- Agent 更新的 `name/instructions/model_id/runtime_config/enabled` 可省略但不可显式 null；`description` 可清空为 null。创建与更新的 `runtime_config` 在 DTO 用 `ensure_strict_json` 递归拒绝非有限数，创建指纹复用 `canonical_json`。
  - ✅ `{"expected_revision": 1, "description": null}`；❌ 必填字段 null 或嵌套 NaN/Infinity 穿过请求边界，直到 JSONB 写入才返回 500。
  - 机检：`tests/console_platform/test_agent_input_validation.py`。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
