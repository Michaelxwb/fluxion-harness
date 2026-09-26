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

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
