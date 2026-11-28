---
{
  "description": "Skill 产物、缓存与子进程",
  "next_rule": 11
}
---

# harness-skill

## Rules

- [RULE-skill-001] Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（宽限期保护进行中事务）。

- [RULE-001] 技能目录按 `MAX_CATALOG_ENTRIES`/`MAX_CATALOG_BYTES` 取前缀，超限整条丢弃并停止；确实丢弃时必须留 warning 与目录截断指标，并在目录尾部追加 `CATALOG_TRUNCATED_NOTE`；未丢弃时不得出现该提示。

- [RULE-002] 非 Skill 产物必须使用 `skills/` 之外的前缀（导出用 `exports/{tenant}/{export_id}/...`，工具大结果用 `tools/{tenant}/{run_id}/{artifact_id}/result.bin`）；孤儿清理只扫 `skills/**`，禁止复用该前缀。

- [RULE-003] `NfsArtifactStore.resolve` 必须校验解析路径落在 `root` 内且不等于 `root`，否则抛 `ValueError(invalid storage_key)`；禁止用 `root / storage_key` 直接拼接。

- [RULE-004] 可执行 Skill 可在 SKILL.md frontmatter 声明 invocation_contract（version、invocation=argv|stdin、参数 schema）；定时任务新 Draft 必须声明，对话直接调用有声明时校验；契约随不可变 artifact 版本冻结，导入链路必须保留，缺失或非法时不得进入调度。

- [RULE-005] 对话直接调用 Skill 时，若默认入口声明 invocation_contract，必须在启动子进程或提交异步操作前按同一通道/参数 schema 校验；失败返回 SKILL_INVOCATION_INVALID（retryable=true）且不启动执行；load_skill.manifest 返回 entrypoint 与 invocation_contract；无契约时保持现有行为。不存在辅助脚本绕过入口。

- [RULE-006] 可执行 Skill 的结果契约绑定不可变 artifact 及确定的默认入口，在导入时校验并随执行冻结。共享执行器独立记录真实退出事实、执行判定与业务结论。仅当受信 profile 允许且本次结果通过校验，才能将非零退出判为已完成的业务结果。宿主停止事实优先，结果声明不授予工具权限或重试安全性。

- [RULE-007] 模型侧执行入口只声明目标对应的受支持能力与参数。正式 Skill 通过冻结的默认入口执行，草稿通过受权的独立试跑能力执行。未在冻结集合中生效的 Skill 须以可解释的错误反馈指引发现可用能力。`execute_skill` / `run_skill_script` 不是可执行别名，`target=skill` 不接受 `script`。

- [RULE-008] Agent 根据用户指定的类型、数据性质和用途准备产物，按类型核验成品及其覆盖范围。仅有截断预览时不得推断缺失内容。程序产物通过既有工作区或产物入口登记，沿用其归属和不可变版本。

- [RULE-009] 模型唯一执行入口是 execute(target=...)；删除 ToolRegistry 兼容命名空间与 execute_skill/run_skill_script/workspace_shell/skill_draft_shell 隐藏别名；能力声明与注册在装配期校验，提示词承诺不得无工具。

- [RULE-010] read_attachment/view_image 由共享工具实现；文本/图片读取按 tenant + task execution 限定；跨 Task/租户 404；非文本明确不支持；不得用租户级引用端点替代 owner scope。

## Conventions

- **区分进程退出与业务结论**（RULE-006）：✅ 审查入口的非零退出按调用失败记录，草稿版本匹配的有效报告随调用结果交给 Agent；❌ 按发布包白名单把审查工具的非零退出改判为执行完成。

**Skill 包、目录与发现**：

- **SKILL.md 里进目录的字段必须单行且有界**：`name` / `description` / `platform_label` 会被逐字插进系统提示目录行，多行等于让上传者往受保护前缀里写段落。长度按 UTF-8 字节、边界严格大于；具体边界由 `MAX_*` 常量定义（`muad_skill_sdk.skill_package._catalog_field`，导入与加载共用）。✅ `_catalog_field(description, "description", MAX_DESCRIPTION_BYTES)`；❌ 只判非空。机检：`tests/agent_core/test_skill_package.py`。
- **目录是索引，不是清单**：目录项只有 key/name/description（+ `platform_label`），正文只在 `load_skill` 后进上下文。`description` 是**路由条件**（`Use when` 正例 + `Don't use for` 反例），不是功能简介；反例缺失是误触发主因。❌ 目录里出现正文/checksum/storage_key。机检：`test_prompt_builder.py`。
- **目录有界，且丢了要说出来**：`DefaultPromptBuilder.build` 按 `MAX_CATALOG_ENTRIES` / `MAX_CATALOG_BYTES` 取前缀，**超限整条丢弃并停止**（不挑小的凑、不截半行），丢弃顺序即 `sort_order`。被丢弃的技能模型看不见 key，因此必须：① 投影点留痕（warning + 目录截断指标）；② 目录尾部补 `CATALOG_TRUNCATED_NOTE`，**只在真的丢了条目时出现**。✅ `bounded_catalog(skills) -> (kept, dropped)`；❌ 无上限或静默丢弃。
- **被预算挡在目录外的技能要有第二条发现路径**：`search_skills(query)` 在本次 Run 的全部生效技能上按 key/name/description 做空白切词 **AND** 子串匹配，返回与目录同一行格式的条目，结果用**同一个 `bounded_catalog`** 收口；不查库、不加端点。**省略/空 `query` 时列出同一预算内的全部技能**（用户问「我有哪些技能」时没有关键词可给）；参数非法时**返回** `COMMON_VALIDATION_ERROR` 错误体（可纠正），不得抛异常把 Run 判成确定性失败。✅ 命中数 + 条目 + 截断时才带 `truncated`/`hint`，`externalizable_result=False`；❌ 返回自由格式文本、结果无上限、用 OR 匹配、拒绝空查询。机检：`tests/agent_runtime/test_skill_tools.py`。
- **一个生效技能都没有时，技能工具一个都不注册**（`SkillToolSet.registry` 早返回）：key 从哪来？注册只会让模型猜 key、白跑一轮换回 `SKILL_NOT_EFFECTIVE`。机检：`test_skill_tools.py::test_skill_tools_are_not_registered_without_effective_skills`。
- **导入拒绝必须给出可定位的原因，不能只回 generic 文案**：`SKILL_PACKAGE_INVALID` 的 `data` 携带稳定 `reason`（缺 SKILL.md / 非法路径 / 不支持的文件类型 / 嵌套压缩包 / 命中密钥扫描 / 超大小或文件数 / manifest 不合法），并尽量带出 `path`、`limit`、`detail`；Console 在导入 Modal 内以常驻 Banner 展示本地化原因。✅ `{"reason":"FILE_TYPE_UNSUPPORTED","path":"run.sh"}` + 前端映射；❌ 只回「Skill 包结构或文件类型不合法」。机检：`tests/console_skill/test_import_api.py`、`tests/frontend/test_skill_module_contract.py`。
- **Skill 删除是软删除，且不得静默破坏已绑定 Agent**：仍有活跃 `AgentSkillBinding` 时返回 `SKILL_IN_USE`（带 key 与绑定数），要求先解绑；删除保留 `skill_artifact` 行与文件供审计/版本追溯，partial unique 允许同 key 重新导入。机检：`tests/console_skill/test_skill_delete.py`。

**产物写入与缓存**：

- **非 Skill 产物必须用 `skills/` 之外的前缀**：孤儿清理只扫 `skills/**`；审计导出用 `exports/{tenant_id}/{export_id}/...`，Runtime Tool 大结果用 `tools/{tenant_id}/{run_id}/{artifact_id}/result.bin`。新产物类型必须显式声明前缀，❌ 复用 `skills/`（会被 `cleanup_orphan_files` 回收）。
- **不可变写入只有一份实现**：`muad_artifact_store.publish_if_absent`（临时文件 + `os.link`：目标已存在即失败，原子判定交给内核）。`NfsArtifactStore.write`、Runtime `write_immutable`、网关入站附件落盘都走它；DB 失败即删文件不留孤儿。❌ 任何地方再抄一份「临时文件 + `os.replace`」（两个写者都能"成功"）。机检：`tests/test_artifact_store_immutable.py`、`tests/agent_runtime/test_artifact_round_budget.py`、`tests/gateway/test_inbound_attachment_store.py`。
- **singleflight 的作用域是「每进程」**：`SkillArtifactCache` 的锁是进程内 `asyncio.Lock` + 内存索引，对跨 Pod 并发首次加载没有互斥力；跨进程正确性依赖 `os.replace` 幂等与 DB CAS。✅ 依靠 READY 标记与 checksum 判等；❌ 假设内存索引跨副本去重。
  - **唯一未收敛处**：`SkillArtifactCache._prepare` 是目录级的「检查再替换」——共享同一 `SKILL_CACHE_ROOT` 时后到者会删掉别人正在读的目录。键是内容寻址（不会发错字节），但跨进程不安全；收敛方向要先定「cache root 是否允许跨进程共享」：允许就改成「存在即用、不存在才发布」。
- **`NfsArtifactStore.resolve` 必须做越界防护**：解析路径必须落在 `root` 内且不等于 `root`，否则 `ValueError("invalid storage_key")`。✅ 一律经 `resolve()`；❌ `root / storage_key` 直接拼（`../` 可越界）。

**执行与结果**：

- **Skill 工具没有服务端 input schema 时按自由 JSON 注册**：allowlist 里的 Skill 工具入参是任意 JSON，不能注册成 `additionalProperties:false`（会把模型参数挡成 `{}`）；MCP 工具仍按目录 schema 校验。✅ Skill 工具 `{"type":"object","additionalProperties":true}`；❌ 把未知 schema 当成禁止参数。
- **草稿测试必须使用真实草稿和独立持久测试数据**：源码在临时副本执行，只有合法源码变更按 revision CAS 回写；测试数据按受信租户、owner、草稿及其所属 Agent 隔离，跨工具调用和 Run 保留，与正式数据分离。测试数据不进入提交包，审批不迁移测试数据；源码修改、失败、超时或 CAS 冲突不隐式重置数据，显式重置只影响当前草稿。测试和删除必须跨实例互斥；测试访问接入 workspace 租约、用量、配额和 TTL，超配额仍可通过显式重置释放本草稿测试数据后重新校验配额。✅ 同一草稿分次调用真实 `start` / `status`，状态保留且草稿 revision 不因数据写入而变化；❌ 用 workspace 探针或另一份试玩脚本宣称原草稿验证通过。机检：`tests/acceptance/user_skill_sandbox/test_s_02.py`。

- **统一执行入口的键按 target 分组**：`execute(target=skill|draft|workspace)` 是模型的唯一执行入口，不存在 `execute_skill` / `run_skill_script` / `workspace_shell` / `skill_draft_shell` 可执行别名。每个 target 只接受自己那组键（skill：`skill_key`/`input`/`argv`/`completion_mode`/`timeout_sec`；workspace：`command`/`cwd`/`timeout_sec`/`platforms`；draft：`draft_id`/`command`/`cwd`/`timeout_sec`/`reset_data`），多带跨 target 的键必须显式拒绝，且报错要写出**多带的键与允许的键**——只说"参数不匹配"会让模型挨个换调用形状重试。✅ `execute(target=skill, …, timeout_sec=300)` 正常执行；❌ 静默忽略跨 target 的键，或给出没有键名的通用报错。
- **Skill 调用的超时只能收紧，ASYNC 不接受**：`timeout_sec` 缺省取本次 Run 冻结的策略预算，给了就与预算取小值（只能缩短）；ASYNC Skill 的执行时间由 Task deadline 管，带 `timeout_sec` 显式拒绝而不是忽略。✅ 同步 Skill 带更短超时按它掐断并回收整棵进程组；❌ 允许调用方把超时延长到 Run 预算之外，或对 ASYNC 静默丢弃该参数。
- **旧 CLI Skill 的业务参数由执行工具传递**：`execute(target=skill)` 接受独立的字符串数组 `argv`，按原顺序直接追加在受校验的脚本路径后，JSON `input` 仍仅进入 stdin。共用 `validate_skill_argv` 的类型、编码和容量校验，拒绝非法参数后再执行。工具描述解释如何从旧 SKILL.md 的命令提取参数，无需改写 Skill 为 JSON 入口。✅ 重复参数、含空格时间和布尔开关逐项传递；❌ 拼接 shell 命令、猜测 JSON→CLI 映射、把解释器参数交给模型选择。

- **解包与执行的逃逸防护**：zip 解包做 zip-slip 校验，越界成员按 `SKILL_ARTIFACT_UNAVAILABLE` 拒绝；脚本路径必须落在 READY 目录内，越界抛 `SkillExecutionError("script path escapes the skill package")`；子进程环境只透传 `PATH/HOME/LANG`（`packages/agent-core/src/muad_agent_core/skill/executor.py` 的 `ENV_ALLOWLIST`）。
- **Skill 子进程必须自成进程组，超时/取消回收整棵树**：`start_new_session=True` 启动，终止时给整个进程组发信号（`os.killpg`），不是只 `terminate()` 直接子进程。三个细节：组 id 直接用 `process.pid`（不要 `os.getpgid`，子进程被回收后查不到）；`_terminate` 不得按 `returncode` 早返回（孙进程仍持 stdout 管道时会让 worker 永久挂死）；终止后 `_drain_output` 有界等待。机检：`tests/agent_core/test_skill_executor.py::test_timeout_terminates_the_whole_process_group` 与 `::test_timeout_survives_a_child_that_exits_leaving_a_pipe_holder`。
- **Skill stdout 结果行不必是最后一行**：解析从末尾往前找第一个 JSON 对象，跳过非 JSON 行；只看最后一行会把「结果 + 日志」的常见形状判成无结果，`interpret_execution` 的兜底随即把整坨 stdout 当 `{"text": ...}`。机检：`test_result_survives_a_log_line_printed_after_it`。

**工具结果外置（Runtime）**：

- **内容投递类工具不得按通用阈值外置**（`load_skill` / `read_skill_resource` / `search_skills` / `recall`）：这类返回就是给模型读的正文，换成 Artifact 预览等于废掉工具（事故：`load_skill` 返回被外置成极短预览且无报错）。工具以 `externalizable_result=False` 声明，Runtime 改按 `MAX_INLINE_RESULT_BYTES`（与 `MAX_RESOURCE_BYTES` 取齐）判定；超过内联上限仍外置，否则超大 SKILL.md 会打爆上下文——正文放 `SKILL.md`，大段规范放 `references/`。机检：`tests/agent_runtime/test_skill_tools.py`、`test_execution_audit.py`、`test_memory_tools.py`。
- **外置阈值与「回执裁剪」必须同源，唯一可配置来源是 contracts 的 `ToolResultSettings.persist_threshold_bytes`**：该叶子随平台设置快照冻进本次 Run；整轮预算与预览头尾由共享 `derive_tool_result_budget` 从冻结的 `history_budget_tokens`/阈值/代码常量派生，Runtime 与 Worker 共用同一实现，不再作为设置叶子下发。`tool_results.py` 明确不再定义第二套模块常量（`tests/test_configuration_convergence.py` 断言旧模块常量已移除）。executor 的外置判定与 `ArchiveToolSet` 的回执裁剪都读同一个冻结值。❌ 任一侧写死字节数、复活旧模块常量，或重新引入 `round_budget_bytes`/`preview_*_bytes` 配置。机检：`test_archive_tools.py::test_receipt_is_not_externalized_by_the_tool_result_wrapper`、`tests/agent_core/test_tool_result_budget.py`。
- **外置判定单元是「回合」而不是「单条调用」**：单条超阈值要外置，多条合计超整轮预算也要外置。执行顺序：**整批判定 → 批量落盘（失败整批回滚）→ 换成引用 → 逐条写审计行 / 发 `tool.completed`**；产物 id 必须赶在 `tool.completed` 之前定下（该事件进 canonical `TOOL_CALL` 行，历史重建靠它找回产物）。✅ `ToolCallRecorder.finish_round` 一次 `_select`+`_persist`+`_flush_audit`；❌ 单条返回时立即外置并上报。
- **外置结果的写入侧与重建侧必须共用 `reference_payload()`**：模型可见形态恒为 `{"artifact": {artifact_id, size, checksum, preview}}` 四键、不二次截断；canonical `TOOL_CALL` 行的 `artifact_id` **列**必须写入（重建读列，不读 `payload_json`）。❌ 重建侧自己拼 JSON，或只把 id 留在 payload（跨 Run 重建退化成 `[tool:名称]`）。
- **工具结果阈值一律按 UTF-8 字节判定，边界是「严格大于」**：`len(str)` 会把汉字算成 1；尺寸恰等于阈值不得触发（用 `>`）。单条外置、整轮预算、memory 注入预算同此口径；历史裁剪则按冻结的 `history_budget_tokens` 与保守 token 估算判定，预算内尽量保留、不得按条数或回合数截断。✅ `message_bytes`/`history_bytes` 按 `encode("utf-8")`；memory 注入按 UTF-8 字节判整块预算（`memory/render.py` 的 `render_memory_block`）；`select_round_persists` 用 `size_bytes > persist_threshold_bytes` 判单条外置。❌ 字符数口径、`>= threshold`，或复活 `snip`/条数兜底。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。

## Applied Deltas

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-003","op":"add","rule":"RULE-001","payload_sha256":"0b6fd9c34651601c72166936f4ebcd20f239db25a3e16217b1971530d0f671a3","at":"2026-11-18T14:06:44.261Z"}

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-004","op":"add","rule":"RULE-002","payload_sha256":"9e706d1fbd1842dad69a605d1d1904f54c571fd5a2fc28b9c3037a6266fd16dc","at":"2026-11-18T14:06:44.261Z"}

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-005","op":"add","rule":"RULE-003","payload_sha256":"36cc951d588e314846b1cc5f21426ac786f18935f1a6ec46471da1d51a66a322","at":"2026-11-18T14:06:44.261Z"}

- {"source":"2026-11-20/scheduled-skill-invocation/scheduled-skill-invocation.md#TASK-001#DELTA-001","op":"add","rule":"RULE-004","payload_sha256":"6b4dcbe5db91886efd30ba011070d13461ca198c362761212e37c041e69b0aba","at":"2026-11-20T13:18:06.406Z"}

- {"source":"2026-11-20/scheduled-skill-invocation/scheduled-skill-invocation.md#TASK-008#DELTA-008","op":"add","rule":"RULE-005","payload_sha256":"a9d34e87b9b7b63a8a8b049a252acc36a1bd71dd7b4a0f3e5efa3f0d8a21c264","at":"2026-11-20T13:18:06.408Z"}

- {"source":"2026-11-24/skill-results-conversation-finalization/skill-results-conversation-finalization.md#TASK-001#DELTA-001","op":"add","rule":"RULE-006","payload_sha256":"b9d4c970fc8acd873dcd66e082c851e155c05d6a1852ce100cd4f89c4f2dae54","at":"2026-11-24T09:27:16.301Z"}

- {"source":"2026-11-27/agent-tool-failure-chain/agent-tool-failure-chain.md#TASK-003#DELTA-001","op":"update","rule":"RULE-005","payload_sha256":"c2e2170887353d2517a99cba8dad2cf2bd34fd67ac5885a9f7d21606fa6df310","at":"2026-11-28T00:53:13.870Z"}

- {"source":"2026-11-27/agent-tool-failure-chain/agent-tool-failure-chain.md#TASK-003#DELTA-002","op":"add","rule":"RULE-007","payload_sha256":"5d90f949a7d7283a92d81adb4d90b18468964c723f098e3c8b1411145344d6f3","at":"2026-11-28T00:53:13.871Z"}

- {"source":"2026-11-27/agent-tool-failure-chain/agent-tool-failure-chain.md#TASK-007#DELTA-001","op":"add","rule":"RULE-008","payload_sha256":"68e9e1b4e5d444a0c281f8531e05467ae5e71f1a1821dd8aa2d1714c55cbb68e","at":"2026-11-28T00:53:13.871Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-007#DELTA-001","op":"add","rule":"RULE-009","payload_sha256":"9da849aa2e477595b51b404faec388cfc8a727f18a5ec24d3b235f19cf271f5b","at":"2026-11-28T01:04:28.948Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-010#DELTA-001","op":"add","rule":"RULE-010","payload_sha256":"3ec257d164edf7ab679c63ea4b5bcfa786b45ba76f03bf17f31f328ad4426c8e","at":"2026-11-28T01:04:28.948Z"}
