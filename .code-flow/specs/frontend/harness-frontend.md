---
id: harness-frontend
description: Agent Harness 通用平台规则：front
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-front-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck
    cwd: .
    timeout: 600
---

# harness-frontend

## Rules

- [RULE-front-001] 前端 HTTP 调用只经服务层：共享 axios 实例与拦截器在 `src/api/client.ts`（`src/api/` 只承载共享客户端与鉴权），各模块的 service 位于 `modules/<module>/services/*.ts` 并 import 该实例；组件与 hooks 禁止裸用 axios/fetch，也不直接 import `api/client`。所有文案只使用 i18n key（zh-CN/en-US）——`scripts/check_frontend_i18n.py` 校验两侧词条的键集齐平与空值，并校验源码中 `t('字面量键')` 引用的键**必须已定义**（增删词条后务必跑它）；模板串动态键变体的齐备性由各模块的 `tests/frontend/*_i18n*.py` 契约钉死。列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。

## Conventions

Semi `Upload` 选择的文件用**组件 state** 保存，不放入 Semi Form 字段：`onFileChange` 签名是 `(files: Array<File>) => void`（不是 FileItem，无 `fileInstance`）；未注册的 Form 字段用 `formApi.setValue` 不会进入 `onSubmit` values，导致提交时文件恒为空（05 导入 Modal 真实事故）。

✅：

```tsx
const [file, setFile] = useState<File | null>(null);
<Upload onFileChange={(files) => setFile(files[0] ?? null)} customRequest={() => undefined} />
```

❌：

```tsx
<Upload onFileChange={(files) => formApi.current?.setValue('file', files[0]?.fileInstance)} />
// files[0] 是 File 而非 FileItem；且 'file' 未注册为 Form 字段，submit values 不含它
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
