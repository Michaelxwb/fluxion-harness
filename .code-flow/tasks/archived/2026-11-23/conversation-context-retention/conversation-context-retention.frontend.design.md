---
design_template: frontend
---
# 会话上下文保留：Console 前端设计

> v0.4 · 2026-11-24 · 待评审 · FEAT-01

## 入口与页面

不新增页面、不改模型管理。改动只落在「系统设置 → 上下文压缩」分组：该分组的字段由后端设置目录（dataclass 派生）自动渲染，字段增删无需前端逻辑，前端只需要词条、单位与范围。

## 字段与词条

压缩分组只保留 4 个字段：

| 字段 | 词条 | 说明 |
|---|---|---|
| `history_budget_tokens` | `settings.field.history_budget_tokens` = 历史预算（tokens） | 正整数；`min=1`、`max` 由后端目录下发；单位 `settings.unit.tokens` |
| `summary.enabled` | `settings.field.summary.enabled` = 启用摘要压缩 | 默认关 |
| `summary.model_ref` | `settings.field.summary.model_ref` = 摘要模型 | 现有模型选择控件 |
| `tool_result.persist_threshold_bytes` | `settings.field.tool_result.persist_threshold_bytes` = 结果外置阈值 | 字节单位 |

删除以下词条与字段：

- `settings.field.history_budget_messages`
- `settings.field.max_history_tool_rounds`
- `settings.field.snip.enabled` / `snip.max_groups` / `snip.keep_head_groups` / `snip.keep_tail_groups`
- `settings.field.micro.enabled` / `micro.keep_recent_tool_groups`
- `settings.field.summary.threshold_bytes`
- `settings.field.tool_result.round_budget_bytes` / `preview_head_bytes` / `preview_tail_bytes`

新增词条 `settings.unit.tokens`（en-US = `tokens`，zh-CN = `tokens`）。`settings.field.range` 已存在，`max` 由后端目录下发时自动显示。整轮工具结果外置预算与预览头尾不是设置项，不在页面出现。

## 校验与展示

- 非法值（0、负数、小数、超过 `max`）的保存错误继续按后端字段路径落到控件，沿用现有 `locateField` 映射；不新增前端校验分支。
- `history_budget_tokens` 的默认值与范围来自后端目录，不在前端写死；`max` 已由后端按部署最小窗口收紧。
- 字段顺序按后端目录返回顺序；压缩分组只剩 4 行，不再需要折叠/分组。
- 其他分组的精简属于 `settings-page-simplification`，本需求不改。
- 开发阶段的平台设置全量重置由后端/运维执行；前端只需在重置后呈现 schema 默认（revision 0），不新增“重置”按钮。

## 审计展示

Run 事件详情复用现有 `CONTEXT_COMPACTED` 展示，增加：

- 保留起点 `retained_from_seq`
- 丢弃条数 `dropped_records`

不展示 `head_end_seq` 等内部边界；不改会话视图，不新增用户可见的裁剪横幅；裁剪提示只对模型可见。

## 验收与测试

- 人工场景见 `conversation-context-retention.scenarios.md` 的 E-01、E-02、E-03、E-04。
- 前端契约测试：压缩分组只渲染 4 个字段，不再出现 `snip`/`micro`/条数/回合数/预览头尾；`settings.unit.tokens` 与新增词条齐备（i18n 键完整性检查）；Run 事件详情显示保留起点/丢弃条数。
- i18n 同步 `zh-CN` / `en-US`。

## 发布

前端产物随 Console 镜像发布。发布后在真实浏览器打开系统设置，确认压缩分组只显示 4 项、保存合法值后刷新仍可见、保存非法值时错误落在该字段；Run 事件详情能看到裁剪统计。
