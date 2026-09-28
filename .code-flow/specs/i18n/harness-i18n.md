---
id: harness-i18n
description: Agent Harness 通用平台规则：i18n
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-i18n-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py
    cwd: .
    timeout: 300
---

# harness-i18n

## Rules

- [RULE-i18n-001] 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。

## Conventions

- **前端词条只有两个扁平 JSON**：`src/locales/zh-CN.json` 与 `en-US.json`。点号键、值为字符串，**不得嵌套对象、不得按模块拆分 TS 词条文件**（任务书里写 `locales/zh-CN/<module>.ts` 与仓库现状不符，以本约定为准）；后端词条只加进 `config/api-messages.yaml`。
- **语言切换安全**：组件文案一律在**本次渲染内**经 `useTranslation()` 取得；不得把译文存进模块常量或 `useState`/`useMemo`/`useRef` 缓存，也不得直接 import i18n 实例取 `t` —— 否则 `changeLanguage` 不会触发重渲染。需要 `t` 的入参工厂组件，由调用方以 `t: TFunction` 注入本次渲染的 `t`。
- **动态键必须枚举齐全**：模板串拼接的键（如 `audit.resultStatus.${status}`、`task.trigger.${trigger}`）要把**全部枚举变体**写进两侧词条；枚举口径取后端域（frozenset/常量），前端 `as const` 数组与后端不一致即失败。
- **错误码词条的键名必须与 `config/api-messages.yaml` 的 code 同名**，未登记的码落 fallback 词条；组件里经「码 → key」映射取值，不得直出后端 `msg`、也不得自造键名。

  ✅：`audit.export.error.IDEMPOTENCY_MISMATCH` 对应 catalog 里的 `IDEMPOTENCY_MISMATCH`，映射表 + `?? 'audit.export.errorFallback'` 兜底（见 `AuditExportButton.tsx` 的 `EXPORT_ERROR_KEYS`）

  ❌：自造键名（`audit.export.error.idempotency`）、或把后端 `msg` 直接渲染出来

  机检：`tests/frontend/test_audit_i18n_contract.py:314-322`（键名须与 catalog 码同名、码必须已登记、两侧都有词条、必须有 fallback 词条）。

- **枚举/状态类词条必须真本地化**：zh-CN 与 en-US 文案不同，且**都不等于裸枚举码**。

  ⚠️ **术语类词条明确豁免**（如 `audit.resourceType.MCP`、`audit.columns.traceId` 这类专有名词），两侧本就同名，不属"必须翻译"的范围。只钉 `audit.auditType.*` 与 `audit.resultStatus.*` 两组枚举。

  机检：`tests/frontend/test_audit_i18n_contract.py`（`:370-376` 本地化、`:363-368` 术语豁免）、`tests/frontend/test_task_schedule_i18n.py:141-151`（业务枚举）。

- **模块源文件（含 service/types/hooks）不得承载硬编码中文文案**：去注释后字符串字面量不得含 CJK/全角字符。后端同向由 `scripts/check_error_message_hardcode.py` 守住错误文案。机检：`tests/frontend/test_audit_i18n_contract.py:60`（CJK 正则）、`:258-264`（扫描全部模块源文件）。
- **按模块前缀的词条键集两侧必须完全一致，且每条都必须被模块源码引用**（无孤儿词条）——增删词条后跑对应模块的 `*_i18n*.py` 契约（`tests/frontend/test_audit_i18n_contract.py:244-252`）。
- **i18n 单例约定**：持久化键 `muad.locale`（`localStorage`）、`fallbackLng: 'zh-CN'`、浏览器语言以 `en*` 开头取 `en-US` 否则 `zh-CN`；切换语言时 `changeLocale()` 同时写 `localStorage` 与 `document.documentElement.lang`，并调 `i18n.changeLanguage`（`src/i18n/index.ts`；e2e 断言 `e2e/tests/console-auth.spec.ts:234`）。

> 登记缺口：RULE-i18n-001 的「新增业务仅新增配置/词条，**不改框架代码**」目前**无任何脚本或测试约束**（纯人工约定，`tests/acceptance/test_foundation_i18n.py` 与 `scripts/check_frontend_i18n.py` 都不覆盖"是否动了框架代码"）。评审时需人工确认，或后续补一条对 `src/i18n/` 与 `src/api/client.ts` 的改动门禁。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
