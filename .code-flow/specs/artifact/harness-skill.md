---
id: harness-skill
description: Agent Harness 通用平台规则：skill
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-skill-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_skill_artifact_cache.py
    cwd: .
    timeout: 300
---

# harness-skill

## Rules

- [RULE-skill-001] Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml:7-8`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（默认 1h，保护进行中事务）。

✅ 不可变写入 + 孤儿清理（`infrastructure/skill_artifact_store.py`）：

```python
publish_if_absent(target, data)   # 共享原语：os.link 是内核级原子判定，目标已存在即 FileExistsError
...
removed = cleanup_orphan_files(known_keys, grace_seconds=3600)  # DB 无记录且早于宽限期
```

❌ 覆盖写 / 先检查再替换 / 无宽限期清理（可能删掉进行中事务刚写入的文件）：

```python
target.write_bytes(data)                 # 覆盖已有不可变 Artifact
if target.exists():                      # 检查与发布之间有窗口：两个写者都能通过检查
    raise FileExistsError(...)
os.replace(temp, target)                 # 后者**静默覆盖**前者，两边都返回成功
cleanup_orphan_files(known_keys, grace_seconds=0)  # 竞态删除进行中导入
```

## Conventions

- **非 Skill 产物必须用 `skills/` 之外的前缀**：孤儿清理只扫描 `skills/**`（`skills/*/*/skill.zip` 与 `skills/*/*/.tmp-*`），因此任何非 Skill 产物写进 `skills/` 都会被 `cleanup_orphan_files` 误回收。既有实例：审计导出用 `exports/{tenant_id}/{export_id}/audits.{csv|json}`（`application/audit_export_service.py` 的 `EXPORT_ARTIFACT_PREFIX`）。新增产物类型必须显式声明自己的前缀，不得复用 `skills/`。
- **解包与执行的逃逸防护**：zip 解包必须做 zip-slip 校验，越界成员按 `SKILL_ARTIFACT_UNAVAILABLE` 拒绝；脚本选择必须校验路径落在 READY 目录内，越界抛 `SkillExecutionError("script path escapes the skill package")`；Skill 子进程环境变量只透传 `PATH/HOME/LANG` 白名单（`packages/artifact-store/.../skill_cache.py`、`packages/agent-core/.../skill/executor.py`）。
- **Skill 子进程必须自成进程组，超时/取消回收整棵树**：子进程以 `start_new_session=True` 启动；终止时给**整个进程组**发信号（`os.killpg`），而不是只 `terminate()` 直接子进程 —— Skill 脚本自己 spawn 的孙进程不挂在 `process.pid` 上，漏掉它们会变成孤儿继续跑（占端口、写文件、**抓着 stdout 管道**）。三处细节各有真实事故撑着（2026-10-03）：
  - 组 id **直接用 `process.pid`**：`setsid()` 之后子进程的组 id 就是自己的 pid。**不要**用 `os.getpgid(pid)` —— 子进程被回收后它已经查不到，而「孙进程还抓着管道」恰恰发生在子进程已退出**之后**。只要组里还有活着的成员，组 id 就不会被系统回收，所以这里不可能误伤别的进程组。
  - `_terminate` **不得按 `returncode` 早返回**：子进程先退出、孙进程仍持有 stdout 管道时，早返回既不回收孙进程，又让随后的 `communicate()` 永远等不到 EOF —— **worker 永久挂死**。
  - 终止后的输出收尾必须**有界等待**（`_drain_output` 超时即返回空）：`_terminate` 负责杀掉整组，它负责「即使杀不掉也不挂死」。
  - 机检：`tests/agent_core/test_skill_executor.py::test_timeout_terminates_the_whole_process_group` 与 `::test_timeout_survives_a_child_that_exits_leaving_a_pipe_holder`
- **Skill 脚本的 stdout 结果行不必是最后一行**：解析按**从末尾往前找第一个 JSON 对象**，跳过非 JSON 行。脚本打印结果之后再打一行日志是极常见的形状；只看最后一行会返回「无结果」，而 `interpret_execution` 的 stdout 兜底随即把**整坨 stdout（含日志）**当成 `{"text": ...}` 交给模型 —— 结构化结果被日志吞掉，且失败语义从「空结果」悄悄变成「一坨日志文本」。跳过非 JSON 行**不会**放宽成功路径：旧行为能解析出的最后一行，新行为第一个就命中它。
  - 机检：`tests/agent_core/test_skill_executor.py::test_result_survives_a_log_line_printed_after_it`
- **`tools/` 是第二个非 `skills/` 产物前缀**：Runtime 的 Tool 大结果落 `tools/{tenant_id}/{run_id}/{artifact_id}/result.bin`，发布走共享原语 `publish_if_absent`（临时文件 + `os.link`）、DB 失败即删文件不留孤儿（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:21-22,32-36,110-117`）。✅ 新产物类型显式声明自己的前缀；❌ 复用 `skills/` 会被 `cleanup_orphan_files` 回收。
- **大结果外置不得截断「内容投递」类工具**（当前实例：`load_skill` / `read_skill_resource` / `recall`）：这类工具的返回**就是给模型读的正文**，按通用 8KB 阈值（`TOOL_RESULT_ARTIFACT_BYTES`）换成 Artifact 预览等于把工具废掉——事故实例：8320 字节的 `load_skill` 返回值被外置成 400 字符预览，模型只看到正文的 1/20，**且没有任何报错**。工具定义以 `externalizable_result=False` 声明内容投递（`packages/agent-core/src/muad_agent_core/tools/registry.py`），Runtime 据此改按内联上限 `MAX_INLINE_RESULT_BYTES`（256KB，与 `MAX_RESOURCE_BYTES` 取齐：工具本就被允许读这么大的正文，就不该在返回路上被截断）判定。**「不可外置」不是「无限直通」**：超过内联上限仍然外置，否则超大 SKILL.md 会打爆上下文；这道兜底同时把「渐进式披露」从建议变成硬约束——正文放 `SKILL.md`，大段规范放 `references/`。✅ `ToolDefinition(name="load_skill", ..., externalizable_result=False)`；❌ 内容投递工具沿用 8KB 阈值（模型收到的不是正文而是 `{"artifact": {...}}`）。机检：`tests/agent_runtime/test_skill_tools.py::test_load_skill_full_body_survives_tool_result_wrapper`（接线：真实定义必须带标记）、`tests/agent_runtime/test_execution_audit.py::test_content_delivery_tool_result_is_not_externalized` 与 `::test_content_delivery_tool_result_is_externalized_above_inline_cap`（判定：两端阈值各一条）、`tests/agent_runtime/test_memory_tools.py::test_b04_recall_result_is_not_truncated_by_artifact_externalization`（`recall`：接线断言 `recall_definition.externalizable_result is False`，行为断言返回体不被换成 Artifact 预览）。
- **8KB 外置阈值与「回执裁剪」必须同源**：`TOOL_RESULT_ARTIFACT_BYTES` 只有一处定义（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:20`），executor 的外置判定与 `create_archive` 的回执裁剪都读它。各写一份的话，「回执刚好不被外置」这条保证会随任一侧改动静默失效 —— 裁剪阈值一旦小于外置阈值，回执就被换成 `{"artifact": {...}}` 预览，模型再也看不到 `artifact_id`。
  - ✅ 两侧 `from .tool_results import TOOL_RESULT_ARTIFACT_BYTES`；❌ 任一侧写 `8192` 字面量（同型先例：`harness-im.md` 的「上限数值只有一个来源…绝不各写一份」）
  - 机检：`tests/agent_runtime/test_archive_tools.py::test_receipt_is_not_externalized_by_the_tool_result_wrapper`（含**前置条件**断言：未裁剪的回执必须确实越过阈值，否则这条用例会空转）
- **不可变写入只有一份实现**：`muad_artifact_store.publish_if_absent`（临时文件 + `os.link`：目标已存在即失败，**原子判定交给内核**，不做"先检查再替换"）。`NfsArtifactStore.write`、Runtime 的 `write_immutable`（工具结果 / 压缩 transcript）、网关的入站附件落盘都走它，各自只在这之上叠加自己的语义（网关多了"同内容复用 / 不同内容冲突"）。2026-10-06 之前这条形状有三份实现、其中两份是 `exists()` + `os.replace` —— 网关那处因此产出过「两次 persist 都返回成功、内容只剩一份、checksum 对不上」。
  - ✅ 三处都委托同一个原语；❌ 任何地方再抄一份"临时文件 + `os.replace`"（并发下两个写者都能"成功"）
  - 机检：`tests/test_artifact_store_immutable.py`（原语本身的并发赢家唯一）、`tests/agent_runtime/test_artifact_round_budget.py::test_rule_artifact_write_is_atomic_under_concurrency`、`tests/gateway/test_inbound_attachment_store.py::test_concurrent_writers_publish_exactly_once`
- **singleflight 的作用域是「每进程」**：`SkillArtifactCache` 的锁是进程内 `asyncio.Lock` + 内存索引（`packages/artifact-store/src/muad_artifact_store/skill_cache.py:25-27`），对跨 Pod 的并发首次加载**没有互斥力**；跨进程正确性依赖 `os.replace` 幂等与 DB CAS。✅ 依靠 READY 标记与校验 checksum 判等；❌ 假设内存索引能跨副本去重。
  - **同一族里唯一还没收敛的一处**（2026-10-06 登记）：`SkillArtifactCache._prepare` 是「**目录级**的检查-再-替换」——`if final_dir.exists(): shutil.rmtree(final_dir)` 然后 `os.replace(unpack_dir, final_dir)`。键是**内容寻址**的（`cache_root/{checksum}`），所以两个进程写出来的内容一致、不会发错字节；但共享同一个 `SKILL_CACHE_ROOT` 时，后到的那个会把**别人正在读**的目录删掉（正在执行的 skill 中途丢文件）。进程内安全（per-checksum 锁），跨进程不安全。收敛方向要先定一个口径：**cache root 是不是允许跨进程共享**——允许就改成"存在即用、不存在才发布"（与 `publish_if_absent` 同形），不允许就把它写成部署约束。
- **`NfsArtifactStore.resolve` 必须做越界防护**：解析后的路径必须落在 `root` 之内、且不得等于 `root` 本身，否则抛 `ValueError("invalid storage_key")`（`packages/artifact-store/src/muad_artifact_store/nfs.py:10-14`）。✅ 一律经 `resolve()` 取路径；❌ 用 `root / storage_key` 直接拼路径（`../` 可越出根目录）。
- **工具结果外置的判定单元是「回合」而不是「单条调用」**：单条超阈值要外置，多条**合计**超整轮预算也要外置——后者只有把本轮全部结果放在一起看才算得出（`packages/agent-core/src/muad_agent_core/tools/round_results.py:1-9`）。执行顺序是硬约束：**整批判定 → 批量落盘（失败整批回滚）→ 把被外置的结果换成引用 → 再逐条写审计行 / 发 `tool.completed`**；产物 id 必须赶在 `tool.completed` 之前定下来，因为该事件（`STREAM_BUSINESS_TYPES["tool.completed"] = "TOOL_CALL"`）进了 canonical 的 `TOOL_CALL` 行，历史重建靠它把产物找回来。
  - ✅ `ToolCallRecorder.finish_round` 一次 `_select` + `_persist` + `_flush_audit`（`apps/agent-runtime/src/muad_agent_runtime/application/executor.py:560-574`），`_finish_tool_round` 等端口改写完消息后才逐条 `_notify_tool_completed`（`packages/agent-core/src/muad_agent_core/agent/runner.py:398-421`）
  - ❌ 在单条工具返回时立即外置并上报 `tool.completed`（整轮预算插不进来，产物 id 也追不上事件）
- **外置结果的写入侧与重建侧必须共用同一个 `reference_payload()` 序列化**：模型可见形态恒为 `{"artifact": {artifact_id, size, checksum, preview}}` 这**四个键、不做二次截断**，写入时生成、重建时原样重现，才能保证「重建的那份 == 当时真正发出去的那份」。同时 canonical `TOOL_CALL` 行的 `artifact_id` **列**必须写入——重建读的是**列**，不是 `payload_json`。
  - ✅ 写入侧 `ToolCallRecorder.finish_round` 与重建侧 `context_builder._to_messages` 都调 `reference_payload(...)`（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:85-102`、`apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py:264-271,279-309`）；`_event_artifact_id` 把载荷里的 id 提上来写进列（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:192-207,1136`）
  - ❌ 重建侧自己拼一份 JSON，或只把 id 留在 `payload_json` 而 `artifact_id` 列恒为 NULL（跨 Run 重建退化成 `[tool:名称]`）
- **阈值一律按 UTF-8 字节判定，边界是「严格大于」**：一个汉字 3 字节，`len(str)` 会算成 1；尺寸**恰等于**阈值**不得**触发（用 `>` 而非 `>=`）。单条外置阈值、整轮预算、memory 注入预算、snip 的组数阈值同此口径。
  - ✅ `message_bytes`/`history_bytes` 按 `encode("utf-8")` 计量（`packages/agent-core/src/muad_agent_core/context/compactor.py:35-50`）；`select_round_persists` 用 `>` 判必落盘（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:57-82`）；memory 注入按 `len(line.encode("utf-8"))` 累加、`total_bytes + size > budget_bytes` 即停（`apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py:337-346`）；`snip` 用 `len(groups) <= max_groups` 判「不触发」、触发即严格大于（`compactor.py:205`）
  - ❌ `len(text) > threshold`（字符数口径，把中文当 1 字节）或 `>= threshold`（恰等即触发）

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
