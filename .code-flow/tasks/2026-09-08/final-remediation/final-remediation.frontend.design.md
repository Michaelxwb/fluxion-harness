# 终版整改 Console 前端设计简报

> **文档编号**: MOD-FLXRM-V2-FE-v0.1
> **文档版本**: v0.1（草稿）
> **创建日期**: 2026-09-08
> **文档状态**: 草稿
> **来源**: `/Users/jahan/Downloads/fluxion-105-commits-final-issues.md`（P1-01 方案 A 的 Console 侧＋P2-02 status 展示）
> **配套后端设计**: `final-remediation.backend.design.md`（FEAT-02/FEAT-08）

---

## 目录

- [2.1 需求概述](#21-需求概述)
- [2.2 功能方案](#22-功能方案)
- [2.3 范围与边界](#23-范围与边界)
- [2.4 验收条件](#24-验收条件)
- [3.1 技术选型](#31-技术选型)
- [3.2 页面与路由结构](#32-页面与路由结构)
- [3.3 组件设计](#33-组件设计)
- [3.4 组件接口契约](#34-组件接口契约)
- [3.5 状态与数据流](#35-状态与数据流)
- [3.6 UI 状态](#36-ui-状态)
- [3.7 样式方案](#37-样式方案)

---

## 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | Console 配合 V2 删字段＋trace status 展示 |
| **需求类型** | 功能优化（删除无效 UI＋新增一列） |
| **业务背景** | 后端 V2 删除 4 个幽灵字段；trace 落盘四态终态 |
| **核心目标** | Console 不再展示/提交已删字段；执行列表可见终态 |

## 2.2 功能方案

| FEAT-ID | 功能 | 来源 |
|---|---|---|
| FEAT-F1 | `inMemorySchemas.ts` runtime_profile 镜像删 4 字段（保留 `max_rounds`/`default`/`bootstrapped_from`）；删兼容文案（无兼容对象了） | 后端 FEAT-02 |
| FEAT-F2 | `AgentEditorForm` 默认创建 spec 去 `request_timeout_ms`/`max_retries`（只留 `default`）；删兼容说明文案，换一句"运行配置仅轮数上限生效"（如需） | 后端 FEAT-02 |
| FEAT-F3 | runs 执行列表＋详情加 `status` 列（completed/failed/cancelled/timed_out 徽标，复用 FU-01/02 的列模式） | 后端 FEAT-08 |
| FEAT-F4 | 同步改：`SchemaForm.test.tsx` 断言、e2e seed（`agent-editor-lifecycle`/`chat-nfr`/`runtime-profile-contract` 建 profile 的 spec 去字段）、`ModelResourceFields` 等 provider 级字段**保留不动**（同名不同义） | 后端 FEAT-02 |

## 2.3 范围与边界

In：console 应用内上述 4 项。Out：chat 应用（无 profile 编辑面）；后端 schema 驱动表单的只读兼容态（v1 已删，无对象）；`error_code` 改名（维持现状）。

## 2.4 验收条件

| TC-ID | 层级/边界 | 输入 → 可观测输出 | 覆盖 |
|---|---|---|---|
| S-F1 | vitest/组件 | 渲染 runtime_profile mirror 表单 → 无 4 字段输入；`specFromSchema` 缺省不含它们 | FEAT-F1 |
| S-F2 | vitest/组件 | 默认创建请求体 → 仅 `default`（＋后端要求的最小集） | FEAT-F2 |
| S-F3 | vitest＋playwright/真浏览器＋dev 后端 | 四种终态执行 → runs 列表徽标一一对应 | FEAT-F3 |
| E-F1 | integration（后端已覆盖，前端回归） | 提交含已删字段的 draft → 后端 422，表单展示字段级错误（既有 PublishIssues 通道） | FEAT-F1/F2 |

## 3.1 技术选型

React 19＋Semi Design 不变（`react19-adapter` 首导入约束不变）；API 经 `services/`（`httpConsoleApi` 加 `status` 透传字段，无新端点）；约束脚本（semi/bare-fetch/目录结构/ts-hygiene）全过。

## 3.2 页面与路由结构

无新页面新路由。改动点：高级设置 tab（默认创建区）、runs 列表页＋详情抽屉。

## 3.3 组件设计

- 删除优于隐藏：mirror 字段删除（非 `disabled`），避免"灰掉但存在"的二次歧义。
- status 徽标：`Tag` 语义色（completed 绿 / failed 红 / cancelled 灰 / timed_out 橙），与现有 `ActionTag` 模式一致；未知值回落灰色"未知"（防御未来值）。

## 3.4 组件接口契约

- `specFromSchema` 输入输出不变（子集变化）。
- runs 行数据类型加 `status: string`（API 透传，后端 nullable→前端回落"未知"）。

## 3.5 状态与数据流

`httpConsoleApi` 列表解析加 `status` 字段；无新 store；loading/success/error 三态沿用现有。

## 3.6 UI 状态

空终态（历史数据无 status，因 DB 重建本场景不存在；防御性回落即可）；加载失败沿用现有错误态。

## 3.7 样式方案

沿用 Design Token；徽标色按上表；不引入新组件库（约束脚本守门）。

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态 |
|---|---|---|---|---|---|
| frontend-quality-standards / RULE-frontend-quality-001 | required | 禁 `any`、三态齐全 | FEAT-F1～F4 | S-F1/S-F2/S-F3/E-F1 | 待确认 |
| frontend-semi-design / RULE-frontend-semi-001 | required | 仅 Semi 组件、adapter 首导入 | 全局 | typecheck＋约束脚本 | 待确认 |
| frontend-component-specs / RULE-frontend-component-001 | required | 受控表单、API 经 services | FEAT-F2/F3 | S-F2/S-F3 | 待确认 |
