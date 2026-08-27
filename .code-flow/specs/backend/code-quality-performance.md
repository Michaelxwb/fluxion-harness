---
id: backend-code-quality-performance
description: 写后端代码时适用：错误处理、测试、超时重试、缓存等质量与性能约束
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-backend-quality-001
    type: manual
    config:
      checklist: Confirm all Guidance and Avoid items for this Spec.
      owner: project-owner
---

# Backend Code Quality & Performance

## Examples

> ✅/❌ 对照示例对 AI 的引导远强于规则条文——新写规范优先用这个格式（cf-learn 候选会自动生成草稿）。

✅ 显式错误处理

```python
try:
    result = service.call()
except ServiceError as exc:
    logger.warning("call failed: %s", exc)
    raise
```

❌ 静默吞异常

```python
try:
    result = service.call()
except Exception:
    pass
```


## Rules
- [RULE-backend-quality-001] 实现必须满足 Guidance 中所有适用要求，并避免 Avoid 中列出的所有禁止项。

### 代码约定（cf-learn 2026-08-27 证据沉淀）
- 自定义错误类必须定义字符串 `code` 属性（31 处一致：`PluginLoadError.code="plugin_load_error"`、`ModelProviderError.code="model_provider_error"` 等）——错误码集中、可机检
- 数据对象统一 `@dataclass(frozen=True, slots=True)`（110 处）——不可变 + `__slots__`，防可变默认值/属性漂移
- 模块以 `from __future__ import annotations` 开头（全仓库仅 13 文件缺该行）——延迟注解求值，避免 forward-ref 循环，mypy 友好
- 外部调用方法带显式 `timeout_ms` 参数（Rule 18 的代码形态；contracts/summarizer/personal_memory 统一 `timeout_ms: int = 30_000`），禁止无超时签名
- SPI Protocol 用 `@runtime_checkable`（12 处）——loader 以 isinstance 分派 typed provider，依赖运行时结构校验
- 公共函数完整类型注解（pyproject `[tool.mypy] strict=true` 硬门禁），禁止裸 `dict`/未注解参数

## Guidance
- 所有公开函数 / 方法必须有类型注解（type hints / 类型签名）
- 异常必须显式处理或显式上抛，禁止 `except Exception: pass` / `catch (e) {}` 静默吞掉
- 外部依赖调用（HTTP / RPC / DB）必须设置超时，关键调用补重试 + 指数退避
- 单元测试覆盖核心业务路径：happy path + 边界 + 错误分支，每个需求 ≥ 1 个用例

## Patterns
- 缓存可计算结果以减少重复 IO，明确缓存 key、TTL 与失效策略
- 重 IO 用异步或批处理，CPU 密集任务下沉到 worker / 队列
- 资源（连接、文件、锁）使用 `with` / `using` / `defer` 确保释放
- 性能敏感路径加监控指标（QPS / P95 延迟 / 错误率）

## Avoid
- 禁止在请求链路中吞掉异常导致客户端拿到错误结果却无日志
- 禁止无超时的外部调用（容易导致线程 / 协程泄漏）
- 禁止用循环模拟批量操作（DB 批量 / 网络批量必须用原生批量 API）
- 禁止把缓存失败当致命错误，缓存层必须可降级为直接查询
