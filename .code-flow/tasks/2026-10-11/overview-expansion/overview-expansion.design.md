# 概览指标扩充

用户于 2026-10-11 确认开始实现此前对齐的首版方案。

## 范围

- 单一 `GET /api/v1/overview?days=7` 聚合接口，days 为 1–30；移除旧 `/overview/metrics` 与不展示的 recent_tasks/next_schedules 数据，不保留兼容适配层。
- 今日/近7天/近30天、手动刷新、更新时间；资源摘要、6项核心指标、运行/任务趋势切换、运行/任务状态切换、输入/输出 Token 趋势、Agent Top5、待处理摘要。
- 仅租户内未删除事实；不新增事实表、不写数据库、不采集费用/在线状态，不实现第二批工具排行。

## 响应契约

封套沿用 APIEnvelope，data 为：

```text
days: int
timezone: IANA timezone
generated_at: YYYY-MM-DD HH:mm:ss (platform timezone)
resources: {enabled_agents, enabled_skills, enabled_mcps, enabled_models, users, active_schedules}: int
kpis: {
  run_count: int,
  run_success_rate: number|null (0–100),
  average_run_duration_ms: number|null,
  active_users: int,
  model_request_count: int,
  input_tokens: int|null, output_tokens: int|null, total_tokens: int|null,
  token_reported_request_count: int,
  token_coverage: number|null (0–100; both input and output known)
}
activity: {
  active_runs, queued_tasks, running_tasks, waiting_tasks,
  waiting_input_runs, waiting_tool_runs, overdue_tasks
}: int
attention: {failed_runs, failed_tasks, failed_deliveries}: int
trend: {
  runs: [{date: YYYY-MM-DD, total: int, failed: int}],
  tasks: [{date: YYYY-MM-DD, total: int, failed: int}],
  tokens: [{date: YYYY-MM-DD, input_tokens: int|null, output_tokens: int|null,
    model_request_count: int, token_reported_request_count: int}]
}
status: {runs: Record<status,int>, tasks: Record<status,int>}
agent_ranking: [{agent_id: UUID, agent_name: string|null, run_count: int}]
```

## 统计口径

- 以一次请求开始时捕获的 now 统一计算；按平台时区自然日，从 days-1 天前午夜至 now，排除未来记录。
- Run 数、成功率、平均耗时、活跃用户、排行、状态与失败 Run：期间创建的 Run 的当前结果；成功率为 COMPLETED/(COMPLETED+FAILED)，取消和未结束不进入分母，无完成/失败时为 null。
- Run 平均耗时仅期间样本的终态 COMPLETED/FAILED/CANCELLED 且 start/end 有效的数据；包含工具与用户等待。
- 运行/任务趋势：创建日 total；实际结束日 failed，失败线代表当天发生失败而非创建日队列的结果。无数据日期补零。
- 后台业务任务仅 parent_id IS NULL，子任务不计业务任务量；图表和提示明确该口径。
- 任务状态与失败/投递失败摘要：期间创建的业务任务的当前结果；当前排队、执行、等待、超期未结束等不受日期范围限制。
- 模型请求按期间实际审计时间统计，租户和关联 Run 均需有效且未删除；包含重试与压缩摘要请求，不能用事件条数计数。
- Token 每个方向仅求已记录值的和；没有请求为0，有请求但该方向全部缺失为null；total_tokens为已记录两方向之和，两方向均缺失为null。完整上报数仅两方向都非null的请求；不足100%说明已记录用量。
- 同一 Run 多条审计不会放大运行次数/活跃用户/排行。关联名称批量查询；删除的 Agent 仅名称缺失，不丢弃历史 Run。

## 验收

- 真实 PostgreSQL + authenticated HTTP：隔离租户/软删除/未来/历史数据、自然日时区边界、空数据null与真实0、父子业务任务、重试/摘要Token、成功率分母、排名确定性、days参数与统一封套。
- 服务/页面契约与 TypeScript 检查；日期切换的旧响应不能覆盖新结果，切换时不显示上个范围的数据，刷新失败可见且保持最后成功结果与时间。
- 真实浏览器：核心指标/4块图表/摘要展示，1/7/30切换、两组Run/Task切换、刷新/失败重试、导航、窄屏、明暗主题与中英语言。
- 同步接口文档、测试清单与既有概览用例，以最终实现为准。
