---
design_template: full
---

# WebChat 会话删除与附件格式支持 前端设计

> **文档编号**: MOD-WEBCHAT-LIFECYCLE-ATTACH-FE-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-12
> **文档状态**: 设计评审中

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | Console 前端 `modules/webchat`（会话侧栏、会话状态控制器、附件 Composer、客户端配置解析、模块 service） |
| Owner | `apps/console-platform/frontend/src/modules/webchat` |
| 前置模块 | agent-capacity-webchat（侧栏/会话状态/Composer/配置投影）、webchat 附件上传 |
| 建议代码位置 | `components/ConversationSidebar.tsx`、`conversationState.ts`、`hooks/useConversations.ts`、`hooks/usePendingSend.ts`、`components/ChatComposer.tsx`、`clientConfig.ts`、`services/webchatApi.ts`、`types.ts`、`locales/{zh-CN,en-US}.json` |

### 1.1 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-11-12 | fluxion-harness | 初始设计：侧栏删除入口与确认、状态收敛、支持格式提示与 `accept`、配置严格解析 |

## 2. 需求分析

### 2.1 需求概述

1. 会话历史条目增加「删除」操作：二次确认 → 调删除接口 → 条目消失、状态收敛。
2. Composer 展示后端投影的「支持格式」，并用同一清单设置文件选择器过滤；选到不支持的格式时立即提示，而不是等上传失败。

### 2.2 功能方案

| 功能ID | 功能名称 | 来源 |
|---|---|---|
| FEAT-04 | 删除入口、确认与状态收敛 | PRD |
| FEAT-07 | 支持格式展示与选择器过滤 | PRD |

### 2.3 范围与边界

- In：侧栏条目的删除操作与确认对话框、删除中的条目态、删除后的索引/选择/草稿/游标清理、URL 清理；Composer 的格式提示、`accept` 与选择预检。
- Out：Console 管理侧的会话列表与删除；批量删除；删除的撤销；拖拽上传（当前不存在）。

### 2.4 验收条件

人工场景见 `webchat-conversation-delete-and-attachment-formats.scenarios.md`；自动化断言见 §3.7。

## 3. 技术设计

### 3.1 方案选型

| 方案 | 结论 |
|---|---|
| 删除入口做成条目内常驻按钮 | 否：侧栏条目宽度有限，常驻操作挤压标题 |
| 条目右侧「更多」菜单（Semi `Dropdown`）+ 二次确认（Semi `Modal`） | **采用**：与 Console 列表的对象级操作一致，且可键盘操作 |
| `Modal.confirm` 命令式弹窗 | 否：文案/状态不在 React 树内，难以用真实渲染测试断言 |
| 删除失败只靠全局 Toast | **采用**：共享 axios 拦截器对非 GET 失败已统一 Toast（`api/client.ts` 的 `shouldShowErrorTip`），不在组件里复制第二套文案 |
| 删除失败再叠一条侧栏 Banner | 否：与全局提示重复；侧栏已有列表级重试面，语义不同 |
| 格式清单前端硬编码 | 否：会与后端白名单漂移（本次明确要收口） |
| 格式清单来自 API-19 投影并严格解析 | **采用**：与限额字段同一口径，缺字段即配置非法 |

### 3.2 组件与状态

```text
ChatPage
 ├─ useConversations(controller)
 │    └─ ConversationStateService.remove(conversationId)   ← 新增动作
 ├─ deleteTarget state（被删条目，驱动确认 Modal）
 └─ <ConversationSidebar
        items, selectedId, deletingIds,
        onDeleteRequest(item), onRetry, ... />

 <ChatComposer policy={{ ...limits, formats }} />           ← formats 来自投影
```

**ConversationStateService.remove(id, options)**

- 返回显式联合，沿用 `ConversationCreationResult` 的写法：

```ts
export type ConversationDeletionResult =
  | { deleted: true }
  | { deleted: false; errorCode: string };
```

- 单飞：同一 `conversationId` 在 `deletingIds` 中时再次调用直接返回进行中的 promise（防重复点击）。
- 成功：从 `index.items` 移除该条目（`total` 递减）、清掉该会话的 `draft`/`appliedSeq`/`scrollAnchor`/`history` 缓存（`runtime.scopes.delete(victim)` 既有机制）；若它是当前选中项 → `select(null)`，进入「无会话」空态，**不自动新建**。
- 失败：条目保留；记录 `deleteErrorCode`（供快照断言与后续统一提示），不改变选择。
- 身份边界：`reset()` 清空 `deletingIds` 与 `deleteErrorCode`；边界之后到达的响应按既有 generation fence 丢弃。

**Snapshot 扩展**（`ConversationIndexSnapshot`）

```ts
deletingIds: readonly string[];
deleteErrorCode: string | null;
```

**ConversationSidebar 结构**

- 条目从「`<li>` 内一个 `<button>`」改为「`<li>` 内一行：选择 `button` + 操作区」——按钮不能嵌套按钮。
- 选择按钮保留 `data-testid="chat-sidebar-item"` 与既有 `aria-current`、`data-conversation-id`、`data-active-run-status`、`data-unread`，既有测试与 E2E 不受影响。
- 操作区：`Dropdown`（trigger 为带 `aria-label` 的 `Button` + `IconMore`），菜单只有一项「删除会话」。
- 条目处于 `deletingIds` 时：选择按钮与操作按钮 `disabled`，条目内显示 `Spin` 小尺寸，`data-deleting="true"`。
- 确认对话框由页面持有 `deleteTarget` 状态驱动：标题含会话标题，正文说明「消息、附件与通知将不再显示且不可撤销」，按钮文案走词条（不用 Semi 内置 ok/cancel 文案）。

**ChatPage 接线**

- `onDeleteRequest(item)` → `setDeleteTarget(item)`；确认 → `controller.remove(id)`；成功后 `setDeleteTarget(null)` 并在被删项正是 URL `conversation` 参数时以 `replace` 去掉该参数（避免刷新后回到已删会话的报错面）。

### 3.3 数据与接口

**types.ts**

```ts
export interface WebchatConversationDeletion {
  conversation_id: string;
  deleted: true;
}

export interface WebchatUploadFormat {
  media_type: string;
  extensions: readonly string[];
}

export interface WebchatClientLimits {
  // 既有字段…
  upload_formats: readonly WebchatUploadFormat[];
}
```

**services/webchatApi.ts**

```ts
export async function deleteConversation(
  conversationId: string,
  options: WebchatRequestOptions = {}
): Promise<WebchatConversationDeletion>
```

- `DELETE ${WEBCHAT_BASE}/conversations/{id}`，走共享 axios 实例（`RULE-front-001`），复用既有 `unwrap`。
- `WebchatClient` 端口增加 `deleteConversation`；`ConversationStateClient` 端口增加同方法（更新其文档注释：不再是「只读/建」）。

**clientConfig.ts**

- `parseLimits` 增加 `upload_formats` 严格解析：必须为非空数组；每项 `media_type` 为非空字符串、`extensions` 为非空字符串数组且每项以 `.` 开头；重复 media_type 或重复扩展名视为非法 → `CLIENT_CONFIG_INVALID`。
- 新增纯函数 `acceptedExtensions(limits)`：展平、去重、保持投影顺序，供 `accept` 与提示共用（单一派生点）。

**Composer 预检**

- `ComposerAttachmentPolicy` 增加 `formats: readonly string[]`（扩展名，含点）。
- `<input type="file" accept={policy.formats.join(',')} />`。
- `addFiles`：文件名带扩展名且不在 `formats` 内 → `inputErrorCode = 'ATTACHMENT_TYPE_NOT_ALLOWED'` 并跳过该文件（与超限的 `continue` 语义一致，其余文件继续处理）；文件名无扩展名时**不拦截**，交给服务端门控（避免误伤）。
- 提示新增一段：`chat.composer.hint.formats`，值由扩展名去点后大写、以 ` / ` 连接（语言中立，不做逐类型翻译）。

**Toast 与文案**

- 删除失败的消息来自后端封套 `msg`，由共享拦截器统一 Toast；前端只负责保留条目与可重试。
- 全部新增文案双语齐备（`RULE-i18n-001`）。

### 3.4 交互与可访问性

- 操作按钮 `aria-haspopup="menu"`、`aria-label` 形如「删除会话：{标题}」；键盘可达（Tab 进入，Enter/Space 打开，Esc 关闭）。
- 确认对话框用 Semi `Modal`，焦点陷阱与 Esc 由组件提供；确认按钮在请求进行中 `loading` 且 `disabled`。
- 删除成功后焦点回到侧栏「新建会话」按钮（被删条目已不存在，焦点不能留在被移除节点）。
- 窄屏抽屉：删除成功后不强制关闭抽屉；若被删项是当前所选，工作区显示空态。
- 触摸设备（`@media (hover: none)`）常显操作按钮——不能只靠悬停。

### 3.5 样式与视觉

- `chat-sidebar-item` 改为 flex 行：标题按钮 `flex: 1`，操作区固定宽度；悬停/`focus-within` 时操作区显形。
- 复用 Semi 组件与既有侧栏排版；新增样式只追加到 `webchat.css`，不覆盖其它模块样式。
- 归档/未读/忙碌徽标位置与现有视觉保持一致，操作区不挤压标题截断逻辑。

### 3.6 词条清单

| 键 | zh-CN | en-US |
|---|---|---|
| `chat.sidebar.delete` | 删除会话 | Delete chat |
| `chat.sidebar.deleteAria` | 删除会话：{{title}} | Delete chat: {{title}} |
| `chat.sidebar.deleting` | 正在删除… | Deleting… |
| `chat.sidebar.deleteConfirmTitle` | 删除这个会话？ | Delete this chat? |
| `chat.sidebar.deleteConfirmContent` | 该会话的消息、附件与通知将不再显示，且无法恢复。 | Messages, attachments and notifications in this chat will no longer be shown, and this cannot be undone. |
| `chat.sidebar.deleteConfirmOk` | 删除 | Delete |
| `chat.composer.hint.formats` | 支持格式：{{formats}} | Supported formats: {{formats}} |

（取消按钮复用既有 `common.cancel`。）

### 3.7 测试建议

| 层 | 用例 |
|---|---|
| contract（esbuild + 真实渲染） | 侧栏含操作按钮与菜单项、确认弹窗文案；`deletingIds` 时条目禁用并有 `data-deleting` |
| contract（conversationState） | `remove` 成功：索引移除、total 递减、选中项回退为 `null`、草稿/游标/历史清除；失败：条目保留、`deleteErrorCode` 记录；单飞去重 |
| contract（clientConfig） | `upload_formats` 缺字段/空数组/非法扩展名 → `CLIENT_CONFIG_INVALID`；`acceptedExtensions` 去重与顺序 |
| contract（usePendingSend） | `accept` 与提示文本来自投影；扩展名不支持的选文件被就地拒绝且不进入附件列表；无扩展名文件放行 |
| 词条齐平 | 新增键 zh-CN/en-US 双侧存在且非空 |
| 浏览器 E2E | 删除普通会话；删除执行中的会话；刷新后不回退到已删会话；选文件时看到格式提示；上传 zip 成功 |

## 4. 风险与依赖

| 风险 | 缓解 |
|---|---|
| 条目 DOM 结构调整影响既有测试 | 保留 `chat-sidebar-item` 及全部 data 属性在选择按钮上 |
| `upload_formats` 为必填字段 | 前后端同版本发布；严格解析沿用既有口径，不做兼容回落 |
| 删除失败的提示重复 | 只用全局拦截器 Toast，组件不再渲染第二条文案 |
| 删除进行中切换会话 | `remove` 不绑定视图信号；成功只按会话 id 收敛，不影响其它会话 |

## 5. 需求追溯

| FEAT | 设计 | 测试 |
|---|---|---|
| FEAT-04 | §3.2 / §3.3 / §3.4 | contract + E2E |
| FEAT-07 | §3.3 / §3.6 | contract + E2E |

## 附录：术语表

| 术语 | 定义 |
|---|---|
| 状态收敛 | 条目移除、选择回退、按会话缓存清理三件事一起完成 |
| `acceptedExtensions` | 由投影派生的可接受扩展名列表，提示与 `accept` 的唯一来源 |
