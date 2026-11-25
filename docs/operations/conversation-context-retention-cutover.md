# 会话上下文保留：开发阶段 cutover reset

本文件是 `conversation-context-retention` 的发布 runbook，不是产品代码路径。开发阶段执行一次
**整份平台设置重置**与旧摘要清理，不写数据迁移或反向迁移。

## 前置

1. 停止 Runtime/Worker/Console/Gateway 的写入流量，排空在途 Run/Task。
2. 确认没有需要保留的生产数据；本重置会把所有平台设置分组恢复为 schema 默认。
3. 备份只用于审计；回滚不依赖旧数据可被新代码解析。

## 重置 SQL

```sql
-- 平台设置版本表：全租户、全 revision；所有分组回到 schema 默认
DELETE FROM control.platform_setting_idempotency;
DELETE FROM control.platform_setting;

-- 受理策略是平台设置 revision 的 PG 投影；旧 revision 会让新 Run 判 stale
DELETE FROM runtime.run_admission_policy;

-- Agent 级压缩覆盖整块删除，其他 runtime_config 保留
UPDATE control.agent_definition
SET runtime_config_json = runtime_config_json #- '{budget,compaction}',
    update_time = now()
WHERE runtime_config_json #> '{budget,compaction}' IS NOT NULL;

-- 旧摘要 coverage 不可信；raw canonical events 保留，后续按新规则重新摘要
DELETE FROM runtime.canonical_event
WHERE event_type = 'CONTEXT_SUMMARY';
```

`CONTEXT_COMPACTED` 旧审计行保留，不参与重建。旧 `policy_json` 只存在于已排空的在途对象；
终态快照只作审计，新代码不解析旧 compaction 键。

## 发布门禁

以下查询必须全部为 0：

```sql
SELECT count(*) FROM control.platform_setting;
SELECT count(*) FROM control.agent_definition
WHERE runtime_config_json #> '{budget,compaction}' IS NOT NULL;
SELECT count(*) FROM runtime.run_admission_policy;
SELECT count(*) FROM runtime.canonical_event
WHERE event_type = 'CONTEXT_SUMMARY';
```

再确认：

- `GET /api/v1/platform-settings` 返回 `revision=0`，压缩分组只有 4 个字段；
- 保存一次合法压缩配置后 `revision=1`，`policy_json.compaction` 只含
  `history_budget_tokens`、`tool_result.persist_threshold_bytes`、`summary.enabled`、
  `summary.model_ref`；
- 旧会话继续一轮时不再引用旧 `CONTEXT_SUMMARY`。

## 回滚

回退镜像后旧代码同样不认识新 compaction 键（fail-closed）；回滚 = 回退镜像 + 再次执行本重置，
不声称“只回退镜像”。索引迁移按正常 Alembic downgrade/upgrade 处理。
