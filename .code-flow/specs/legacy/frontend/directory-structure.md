---
id: frontend-directory-structure
description: 新建/移动前端文件时适用：目录结构、路由/页面/组件存放约束
stages: [design, plan, code, review]
enforcement: required
checks:
  - id: no-bare-fetch-in-views
    type: regex
    pattern: "(fetch\\s*\\(|axios\\.(get|post|put|delete)\\s*\\()"
    files: "*pages*"
    message: "视图层禁止裸 fetch/axios，走 src/services/ 封装"
  - id: no-bare-fetch-in-components
    type: regex
    pattern: "(fetch\\s*\\(|axios\\.(get|post|put|delete)\\s*\\()"
    files: "*components*"
    message: "组件内禁止裸 fetch/axios，走 src/services/ 封装"
verifiers:
  - rule: RULE-frontend-directory-001
    type: manual
    config:
      checklist: Confirm all Guidance and Avoid items for this Spec.
      owner: project-owner
---

# Frontend Directory Structure

## Examples

✅ 三层分离：service 发请求 → hook/composable 管状态 → 组件纯展示

```ts
// services/order.ts — 只管 HTTP，不含状态
export const fetchOrder = (id: string) => http.get(`/orders/${id}`);
// hooks/useOrder.ts — 管 loading/error/data，复用逻辑收口于此
export const useOrder = (id: string) => { /* 调 fetchOrder，返回 { data, loading, error } */ };
// components/OrderCard.tsx — 纯展示，消费 hook
const { data, loading, error } = useOrder(id);
```

❌ 组件内裸 `fetch`，请求/状态/样式糊在一处

```tsx
const order = await fetch(`/orders/${id}`).then((r) => r.json());
```

## Rules
- [RULE-frontend-directory-001] The implementation must satisfy every applicable item in Guidance and avoid every item in Avoid.

## Guidance
- 通用组件放 `src/components/`，页面级组件放 `src/pages/`，业务复用逻辑放 `src/hooks/`（或 `composables/`）
- **接口调用独立成层**：API 调用必须在 `src/services/` 封装；组件 / hook / composable 经 service 消费，**禁止组件或视图层内裸 `fetch` / `axios`**
- 数据流单向：页面 → hook（状态/分页/过滤）→ service（纯 HTTP，无 `try`）→ `api/client.ts` 单例；错误由拦截器统一转 `ApiError` + `Toast`（携带 `request_id`），业务层不 `catch`
- 类型不集中 `src/types/`：数据契约由所属 `services/*.ts` 导出，组件 Props 在文件内联 `interface`；列表行泛型约束 `T extends Record<string,unknown>`
- 路由集中在 `src/App.tsx`（现状无独立 `router.*`，有需要再拆）
- 新增一级目录必须更新路由 / 入口索引与导航地图

## Patterns
- 组件目录按业务域分子目录（`components/order/`、`components/user/`）
- 页面与路由一一对应，路由配置集中在 `router.*`
- 资源文件（图片 / 字体）放 `src/assets/`，构建工具处理 hash 与压缩
- 测试文件与源码同目录（`Foo.test.tsx`）或镜像放 `tests/`

## Avoid
- 禁止在 `src/` 下随意新增未登记的一级目录
- 禁止页面与组件互相循环引用
- 禁止把仅本组件用的 hook / 类型上提到全局目录
