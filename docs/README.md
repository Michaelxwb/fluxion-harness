# Fluxion 设计文档 V1.11 — 模块分档拆分版

本版本以完整总体设计、完整 Playbook、Console V0.8 交互稿及 `cf-task:align` 三套模板为基线，重点解决 V1.10 “内容详细但文档边界仍过大/平铺”的问题。

## 目录

- `00-总体设计/`：完整总体设计与 Playbook，保持上游设计事实；
- `01-架构与规范/`：工程、DB、API、Workspace/Sandbox 规范和 Owner 索引；
- `02-模块设计/`：后端按一级模块目录拆分，每个模块明确 Full/Lite 分档；
- `03-前端设计/`：按 Console 产品模块拆分，每个模块独立 Frontend Design；
- `04-追溯与验收/`：总体设计→模块→页面→API→DB 追溯与 Gate；
- `05-变更记录/`：设计演进；
- `99-cf-task-align模板基线/`：本轮使用的 align/full/lite/frontend 模板。

## 关键规则

1. 模块目录是架构边界，不是文件整理手段。
2. DB 表与 API/Library/CLI Contract 必须有唯一 Owner。
3. Full/Lite 按复杂度选择，不追求数量均衡。
4. 前端按页面/旅程模块拆分，公共列表/Shell 只实现一次。
5. Skill 继续采用 IDE 离线开发 + SDK + Dev Gateway 验证 + Console 导入，Console 不做 Skill 在线编排。
