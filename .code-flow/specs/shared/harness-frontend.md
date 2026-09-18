---
id: harness-frontend
description: Agent Harness 通用平台规则：front、i18n、ui
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-front-001
  type: manual
  config:
    checklist: 确认前端 API 只经 services/、不裸用 axios/fetch、文案只用 i18n key。
    owner: project-owner
- rule: RULE-i18n-001
  type: manual
  config:
    checklist: 确认后端错误与前端页面 zh-CN/en-US 双语并可配置扩展。
    owner: project-owner
- rule: RULE-ui-001
  type: manual
  config:
    checklist: 确认 React + Semi、左上操作/右上搜索筛选/右下分页、主展示字段进详情。
    owner: project-owner
---

# harness-frontend

## Rules

- [RULE-front-001] 前端 API 调用只经 `src/api/`（services）层，组件禁止裸用 axios/fetch；所有文案只使用 i18n key（zh-CN/en-US）；列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。
- [RULE-i18n-001] 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。
- [RULE-ui-001] Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
