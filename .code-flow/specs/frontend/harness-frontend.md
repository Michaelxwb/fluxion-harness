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

- [RULE-front-001] 前端 HTTP 调用只经服务层：共享 axios 实例与拦截器在 `src/api/client.ts`（`src/api/` 只承载共享客户端与鉴权），各模块的 service 位于 `modules/<module>/services/*.ts` 并 import 该实例；组件与 hooks 禁止裸用 axios/fetch，也禁止拿 `api` 实例自建请求。**HTTP 调用面**之外的 `api/client` 导出不在此禁令内——`apiErrorBody`/`newRequestId` 是无副作用的纯工具，组件可直接 import（现网 7 处，均为纯函数引用）。所有文案只使用 i18n key（zh-CN/en-US）——`scripts/check_frontend_i18n.py` 校验两侧词条的键集齐平与空值，并校验源码中 `t('字面量键')` 引用的键**必须已定义**（增删词条后务必跑它）；模板串动态键变体的齐备性由各模块的 `tests/frontend/*_i18n*.py` 契约钉死。列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。

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

`api/client.ts` 的拦截器契约（机器钉死在 `tests/frontend/test_api_client_contract.py`，改动拦截器前先读它）：

- **请求头固定三件**：`X-Locale`（当前语言，`currentLocale()`）、`X-Request-Id`（每次请求新生成）、`X-CSRF-Token`（读 `muad_csrf` cookie，缺失则不带头）。三件都由请求拦截器统一设置，业务代码不得自行添加或覆盖。机检：`tests/frontend/test_api_client_contract.py:14-18`（`X-Locale` 注入）；三头同时发出另由 e2e `e2e/tests/console-auth.spec.ts` 的 S-09 断言。
- **`newRequestId()` 必须保留三级兜底**：`crypto.randomUUID` → `crypto.getRandomValues`（手搓 UUIDv4）→ `Date.now()+Math.random`。`crypto.randomUUID` 只在 secure context（https / localhost）存在，内网 `http://<IP>` 打开 Console 时它是 `undefined`。机检：`tests/frontend/test_api_client_contract.py:35-46`。

✅：走带兜底的生成器

```ts
config.headers['X-Request-Id'] = newRequestId();
```

❌：无条件直接 `crypto.randomUUID()`——非 secure context 下请求拦截器抛错，**全部 API 调用失败**

```ts
config.headers['X-Request-Id'] = crypto.randomUUID();
```

- **业务码非 `'0'` 由响应拦截器统一处理**：`Toast.error(body.msg)` 后 reject 原始 body；`/auth/me` 的会话探测失败是**唯一不弹 Toast** 的例外（避免启动期噪音）。不得改成 `Toast.error(body.code)`（后端返回的是本地化 msg）。机检：`tests/frontend/test_api_client_contract.py:20-24`。
- **401 统一跳登录**：`/login?returnUrl=<encodeURIComponent(pathname + search)>`；**已在 `/login` 时不跳**（防重定向循环）。机检：`tests/frontend/test_api_client_contract.py:27-32`。
- **组件层取错误码只走纯函数 `apiErrorBody(error)`**：先读 `error.response.data.code`，再回退 `error.code`，两者皆无返回 `null`。不要在组件里手工拼 `error.response?.data?.code`（`src/api/client.ts` 的 `apiErrorBody`，现网 7 个调用点）。
- **模块骨架是两件套**：`modules/<module>/services/<x>.ts`（该模块唯一的 HTTP 出口）+ `tests/frontend/test_<module>_contract.py`（**源码契约测试**：正则读源码断言结构与拦截器约定，而非渲染单测——本仓 `tests/frontend/` 现有 40 个此类文件）。

**凭据入口仅 ADMIN（2026-09-28 落地）**：`ProjectPlatform` 的平台详情与用户详情的 `credentials` TabPane 都包在 `isAdmin && (`（`account?.role === 'ADMIN'`）门控内，非 ADMIN 不渲染该页签，自动切换到凭据 Tab 也受同一门控约束。这是「前端隐藏 + 后端 403 兜底」的前端一半；后端门控见 auth spec。

✅：页签在门控内

```tsx
{isAdmin && (
  <Tabs.TabPane itemKey="credentials" tab={t('platform.detail.credentials')}>
    <PlatformCredentialTab platform={platform} onChanged={props.onChanged} />
  </Tabs.TabPane>
)}
```

❌：页签无条件渲染，靠后端 403 兜底——非 ADMIN 会看到入口并点到报错

机检：`tests/frontend/test_platform_detail_contract.py::test_credentials_pane_is_admin_only`、`tests/frontend/test_user_credential_tab_contract.py::test_credentials_tab_is_admin_only`。

> 登记缺口：`tests/frontend/test_api_client_contract.py` 已钉死上述拦截器契约，但**未登记进任何 spec 的 `verifiers`**（仅在已归档 `01-platform-foundation` 里作 E-07 的 argv 出现过）；`tests/frontend/test_form_layout_contract.py` 在整个 `.code-flow/` 下**零引用**。两者都应在后续 refresh 时并入对应 rule 的 verifier 清单。
>
> 机检覆盖边界：`scripts/check_frontend_api_usage.py` 目前只扫 `axios`/`fetch` 的裸用，**不覆盖**「import `api` 实例」这一类——RULE-front-001 的调用面禁令靠评审把关。若要机器兜底，可在该脚本补一条：除 `src/api/` 与 `services/` 外不得出现 `api.` 调用。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
