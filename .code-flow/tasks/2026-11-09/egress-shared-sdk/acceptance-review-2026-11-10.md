# TASK-002 验收复核与后续工作

上轮 Done Gate 返回 pass，但以下场景的已登记测试与真实边界不一致，该结果不能证明设计验收通过，也不能作为后续任务已具备正确前提的证据。不要删除历史运行记录。

- S-01：`tests/sdk/test_shared_redis_meter.py::test_s_01` 只调用同进程内两个 SDK 对象，使用一个 Redis 客户端；没有两个宿主进程、真实 Redis Cluster、HTTP 探针或 50 次到达断言。
- S-02：原测试只覆盖两个公共规则；场景要求两个 invocation 叠加 execution 和 target 规则，不足以证明冻结预算参与原子扣减。当前责任改为 TASK-009，原 verified 只保留为历史运行记录。
- B-01：`test_b_01` 只验证新窗口初始阻断及 epoch；没有测试滑窗边界或时钟回拨。
- B-04：`test_b_04` 在同一进程测试 revision 与累计桶；没有测试两个进程、窗口增长历史不足及冻结执行预算。
- RED 记录描述的是占位测试，而不是针对功能缺陷的有效 RED。
- `SharedRedisMeter` 目前只被测试调用，尚未接入业务请求路径。
- `admit()` 对同一 attempt_id 确定性生成 proof 并返回原 permit；调用方若重新执行发送，无法防止同一许可被重复用于多次物理请求，也未验证请求元数据相同。需先设计并验证仅限 Redis 协议重试、不得复用 permit 发送的边界。

后续须按 Code Flow 显式新增修复任务，恢复以上场景的真实边界，并补齐真实 Cluster、请求生命周期、恢复/故障保护、监控等剩余任务；完成真实需求级验收后才可声称功能完成。

## 关联聊天合并记录

已读取聊天 `01a1c31e-b7c8-7001-b059-8d2ec3a0ee39`（上传限频限流冲突修复）。两个聊天共用同一工作区，该聊天新增的 `shared_redis.py`、`shared_redis_scripts.py`、`test_shared_redis_meter.py` 已在当前工作树，无需复制或覆盖。此前未推送、未创建 PR、未触发网页 CI；其记录的 Ruff 静态检查不构成请求链路验证。当前聊天后续的单机 Redis 测试也不满足 S-01/B-01/B-04 的完整真实边界。

用户在该聊天要求：先实现未完成的功能，再上传分支；不在本地运行 cf 测试，测试通过网页 CI 执行。当前聊天继续承担全部工作。执行身份及恢复、Redis 代际守卫、旧 Skill/Adapter/MCP 请求接入、进程监督和监控仍需交付。

本聊天新增 TASK-009 承担 S-01/B-01/B-04 的最终验收，TASK-003 依赖该修复；历史结果不删除。规划已经生成但测试骨架和真实边界仍待实现。

## TASK-009 的 CI 证据补充

- GitHub Actions run `42767229084`、attempt 1、commit `e2caf818` 的 `acceptance (redis_cluster)` 分片通过。该分片实际执行了两个新探针：跨两个子进程的 S-01 请求探针，以及 B-01 的窗口过期和回拨探针。
- 这不是 TASK-009 的完整验收：S-01 探针直接调用共享计数器，尚未接入真实 Skill/平台/MCP 请求路径；B-01 尚未覆盖约定的可控隔离时钟和精确边界；B-04 冻结预算仍未实现。不得根据分片成功把整个任务标记完成。
- 同次运行的 `acceptance (task_schedule)` 有一例失败，已重跑失败分片；与本任务的因果关系尚未确认。具体排查结果以重跑和诊断日志为准。

## 本地定向验证（2026-11-10）

- 隔离 Redis 7 Cluster：`MUAD_CLUSTER_REDIS_URL=redis://127.0.0.1:6391/0 uv run pytest -q .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_02`。实现前因缺少执行作用域登记失败，之后 1 passed；使用两个 SDK 客户端而非真实 Runtime/Worker。
- 同一隔离 Cluster：`MUAD_CLUSTER_REDIS_URL=redis://127.0.0.1:6391/0 uv run pytest -q .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_04`，1 passed；仅证明当前断言覆盖的公共规则切换与测试身份累计，不证明旧客户端自动刷新或真实执行恢复。
- 隔离单机 Redis：`REDIS_URL=redis://127.0.0.1:16379/0 uv run pytest -q tests/sdk/test_shared_redis_meter.py`，19 passed；含作用域丢失、配额变更和不规范计数的负向用例。
- 后续新增独立作用域用例，先获得 3 项因不接受禁用作用域而失败的 RED；实现后同一单机回归扩展为 23 passed，Cluster S-02/B-04 定向回归 2 passed。当前无任何规则时的真实请求调用链旁路仍未验证。
- S-01 本地隔离 Redis 7 Cluster、两个独立进程和 HTTP 探针重新执行：1 passed，探针实际收到 50 次。该探针仍是计数器加测试发送，不代表实际 Skill/Adapter/MCP 已接入。
- B-01 补充受控边界测试：在真实 Redis 执行仅替换 `TIME` 读取的测试版准入 Lua，验证跨整分、到期前 1ms、恰好到期和回拨；此证据不冒充未改动生产脚本的真实时间测试。生产脚本配套真实时钟测试需单独记录结果。
- B-04 补充同一个旧客户端先遇到策略版本拒绝、再从 Redis 读取当前策略并重新申请的断言；刷新由测试客户端实现，不证明生产调用链已经自动刷新。
- B-01 完整命令在本地隔离 Redis 7 Cluster 通过（1 passed，121.02 秒）：先执行上述测试版 Lua 的可控时钟边界，再以未改动生产 Lua 的 Redis `TIME` 验证窗口期拒绝、到期后放行和回拨拒绝。B-04 在补充客户端刷新后重新执行通过（1 passed）；S-02/B-04 已加到 Cluster 验收分片的收集入口，完整 CI 尚未重跑。
- 恢复内核新增 `resume_execution` / `resume_invocation`：先取得因接口缺失导致的 2 项 RED，再验证新客户端沿用累计、缺登记拒绝且不创建、阈值不符与损坏计数拒绝。单机定向回归更新为 26 passed；B-04 的独立进程改用恢复接口后，Cluster 回归 1 passed（3.91 秒）。这些接口校验 Redis 状态，不认证宿主身份，不替代 PG 权威授权、执行 fencing 或故障切换历史丢写检测。
- 冻结 target 累计规则：先取得 2 项因不支持 targets 登记而失败的 RED，再实现执行/脚本作用域内不可变目标规则、Lua 目标匹配与原子扣减；目标拒绝不扣公共桶，计数缺失拒绝恢复和准入。单机回归 29 passed；S-02 加入冻结目标规则后，与 B-04 的 Cluster 定向回归合计 2 passed（3.77 秒）。当前仅支持精确 target_instance_id/route_id 的累计规则，其他冻结窗口/并发维度显式拒绝，未声称完成配置映射或真实发送链路。
- 收尾尝试：mypy 两个共享 Redis 源文件通过；架构套件 229 passed、2 项准备错误，随后显式注入独立 PG/Redis 重跑整个 multi-worker-state 模块 3 passed。首次环境准备错误不是功能 RED。`task finish` 实际返回 block、执行检查数 0，并自动暂停 TASK：从早期基线合并的非本需求 API/前端/E2E 改动被判为新增 required Spec。保留工具生成的范围扩展和暂停状态；未绕过、未标记 done，须先恢复正确任务范围再重新收尾。
- TASK-003 已启动；E-01 连接失败、B-03 基础并发租约、B-05 缺状态/epoch 边界已取得局部定向通过。新增 `renew` 原子操作，真实 Redis 验证合法续租、错误 proof 和释放后续租拒绝；B-03 仍未完成真实流式 HTTP、deadline 监督和续租失败关闭传输。
- B-03 增加真实 ThreadingHTTPServer 流探针：响应 EOF 前第二请求因 `INFLIGHT` 拒绝，读取完成后释放自身 lease，下一请求通过；本地隔离 Redis 1 passed。宿主强制 deadline、续租失败关闭本地传输仍待接线。
- 重新建立当前基线后，移除了旧基线误带入的 API/前端/E2E 绑定，并按当前改动路径重新绑定 platform/skill/snapshot/worker。TASK-009 的四项场景在独立 Redis Cluster 上执行；skill verifier 4 passed、schema parity 55 passed、mypy 2 源文件通过、架构 multi-worker-state 独立 PG/Redis 3 passed。snapshot verifier 的 2 项失败均为开发数据库未执行 migration 0040，错误为 `runtime.run_record.input_message_id` 缺失；这属于环境 schema 漂移，未将其标记为实现失败。

- TASK-003 续租修复：新增四个真实 Redis 用例取得功能 RED（4 failed），修复后共享内核 39 passed（2.01 秒）。覆盖消费前拒绝、epoch 变化拒绝且无写入、混合累计/并发规则、续期不缩短占用、不延长发送许可、跨原到期点仍占用、过期不能复活、key 集合伪造及多桶校验无部分写入。局部 E-01/B-03/B-05 回归 3 passed（1.06 秒）；不代表故障代理、宿主强制 deadline 或 PG/Cluster failover 已交付。此前把局部新增探针称为 RED/GREEN 的表述已在 TASK-003 Evidence 中纠正。

- TASK-003 宿主流式监督原语：`shared_http_transport.py` 新增单次 prepared send 的 admit/consume、固定 deadline、按剩余时长续租、取消/失败关闭及关闭后释放。原语测试 4 项先因缺少模块失败；最终新增 11 项真实 HTTP socket + 隔离 Redis 测试，与内核合计 50 passed（3.46 秒）。真实 TCP 故障代理验证已提交 EVAL 回包丢失不发送 HTTP、同 attempt 不重扣、读取中续租超时关闭；同时验证 429/Retry-After/body、固定 CSRF/Cookie、禁自动 redirect、无规则绕过、外部取消及释放失败告警。Ruff、mypy、diff 空白检查通过。仅 SDK 原语，不声称生产 Runtime/Worker/Skill/MCP 已接入，不替代执行权 fencing 或进程级 deadline；B-05 PG/Cluster failover 仍待交付，未触发全量 CI。

- TASK-003 真实故障切换：首次受控主从切换后旧实现仍可准入；显式 TAKEOVER 切回原进程后也仍可准入，均取得功能 RED。选举冷却导致的最初演练超时不计 RED。实现 Lua 内 run_id + cluster_my_epoch 代际检查后，专属 Redis 7 3 主 3 从 Cluster 的受控切换/强制切回/流式断连演练 3 passed（4.25 秒）；单机 Redis + HTTP 原语 56 passed（3.48 秒），包含缺 generation 与 INFO ACL 拒绝。仍缺 PG 持久身份联动，不标记完整 B-05 verified；不承诺同进程同代际内任意历史回滚可检测，不自动重新学习代际或清零恢复。
