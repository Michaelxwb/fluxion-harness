---
id: frontend-component-specs
description: 写/改组件时适用：props、hooks、渲染、UI 样式约束
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-frontend-component-001
    type: manual
    config:
      checklist: Confirm all Guidance and Avoid items for this Spec.
      owner: project-owner
---

# 前端组件规范

## Examples

✅ 稳定唯一 key + 改值经回调上抛

```tsx
{items.map((it) => <Row key={it.id} item={it} onChange={onChange} />)}
```

❌ 用数组 index 作 key + 组件内直接改 props

```tsx
{items.map((it, i) => { it.value = next; return <Row key={i} item={it} />; })}
```

✅ 容器/展示分离 + 样式走 token，展示组件不碰数据获取

```tsx
// 展示组件：纯 props 驱动，样式用 class/token，无请求
const OrderRow = ({ order, onSelect }: Props) =>
  <div className={styles.row} onClick={() => onSelect(order.id)}>{order.title}</div>;
```

❌ 展示组件内自取数据 + 内联魔法样式

```tsx
const OrderRow = ({ id }) => {
  const [o, setO] = useState(); useEffect(() => { fetch(`/orders/${id}`)... }, []);
  return <div style={{ margin: 13, color: '#3a7' }}>{o?.title}</div>;
};
```

## Rules
- [RULE-frontend-component-001] 实现必须满足 Guidance 中所有适用要求，并避免 Avoid 中列出的所有禁止项。

## Guidance
- Props 必须类型化（TS interface / PropTypes / defineProps），可选项给默认值
- 组件文件名与导出组件名一致，使用 PascalCase
- 单组件文件 ≤ 300 行，超出拆成子组件或提取 hook
- 组件内禁止直接修改 props，需改值通过事件 / 回调上抛
- **样式与逻辑/数据分离**：样式走 CSS Modules / 原子类 / 设计 token，不与数据获取或业务逻辑混在同一处；展示组件不发起请求

## Patterns
- 拆分容器组件（数据获取 / 状态）与展示组件（纯 UI），便于测试和复用
- 复用逻辑提取为 hook（`useXxx`）/ composable，禁止跨组件复制粘贴
- 列表渲染必须给 `key`，且 `key` 稳定唯一，避免使用数组索引
- 受控表单优先；非受控仅用于不需要回读值的场景
- 样式遵循设计系统：间距用 4 的倍数，强调色 ≤ 2 种
- 列表「新建成功」的 `onCreated` 回调必须携带新建实体 id（`onCreated(resourceId)`），由调用方据此导航到编辑/详情闭环；禁止只触发列表刷新——新建实体（如 draft）可能因后端列表过滤而不可见，依赖重查 list 会引入跨层时序耦合

## Avoid
- 禁止在组件内直接修改 props 或 store 内部状态
- 禁止把大量逻辑塞进 JSX 表达式，复杂条件提取变量或子组件
- 禁止用 `index` 作为列表 `key`（顺序变更会触发错误复用）
- 禁止省略 `alt` / `aria-*` 等可访问性属性

## Semi Design 组件约束

- 通用 UI 默认使用 Semi Design；不得引入 Ant Design 等第二套组件体系。
- 自定义组件优先组合 Semi 组件，并通过 Design Token/CSS Variable 定制。
- React 19 入口必须最先加载 `@douyinfe/semi-ui/react19-adapter`。
- 具体规则见 `frontend/semi-design.md`。
