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

- [RULE-skill-001] Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml:7-8`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（temp+`os.replace` 原子写，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（默认 1h，保护进行中事务）。

✅ 不可变写入 + 孤儿清理（`infrastructure/skill_artifact_store.py`）：

```python
if target.exists():
    raise FileExistsError(f"artifact already exists and is immutable: {storage_key}")
...
removed = cleanup_orphan_files(known_keys, grace_seconds=3600)  # DB 无记录且早于宽限期
```

❌ 覆盖写 / 无宽限期清理（可能删掉进行中事务刚写入的文件）：

```python
target.write_bytes(data)                 # 覆盖已有不可变 Artifact
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
- **`tools/` 是第二个非 `skills/` 产物前缀**：Runtime 的 Tool 大结果落 `tools/{tenant_id}/{run_id}/{artifact_id}/result.bin`，走同款 temp + `os.replace` 原子写、DB 失败即删文件不留孤儿（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:21-22,32-36,110-117`）。✅ 新产物类型显式声明自己的前缀；❌ 复用 `skills/` 会被 `cleanup_orphan_files` 回收。
- **大结果外置不得截断「内容投递」类工具**（当前实例：`load_skill` / `read_skill_resource` / `recall`）：这类工具的返回**就是给模型读的正文**，按通用 8KB 阈值（`TOOL_RESULT_ARTIFACT_BYTES`）换成 Artifact 预览等于把工具废掉——事故实例：8320 字节的 `load_skill` 返回值被外置成 400 字符预览，模型只看到正文的 1/20，**且没有任何报错**。工具定义以 `externalizable_result=False` 声明内容投递（`packages/agent-core/src/muad_agent_core/tools/registry.py`），Runtime 据此改按内联上限 `MAX_INLINE_RESULT_BYTES`（256KB，与 `MAX_RESOURCE_BYTES` 取齐：工具本就被允许读这么大的正文，就不该在返回路上被截断）判定。**「不可外置」不是「无限直通」**：超过内联上限仍然外置，否则超大 SKILL.md 会打爆上下文；这道兜底同时把「渐进式披露」从建议变成硬约束——正文放 `SKILL.md`，大段规范放 `references/`。✅ `ToolDefinition(name="load_skill", ..., externalizable_result=False)`；❌ 内容投递工具沿用 8KB 阈值（模型收到的不是正文而是 `{"artifact": {...}}`）。机检：`tests/agent_runtime/test_skill_tools.py::test_load_skill_full_body_survives_tool_result_wrapper`（接线：真实定义必须带标记）、`tests/agent_runtime/test_execution_audit.py::test_content_delivery_tool_result_is_not_externalized` 与 `::test_content_delivery_tool_result_is_externalized_above_inline_cap`（判定：两端阈值各一条）、`tests/agent_runtime/test_memory_tools.py::test_b04_recall_result_is_not_truncated_by_artifact_externalization`（`recall`：接线断言 `recall_definition.externalizable_result is False`，行为断言返回体不被换成 Artifact 预览）。
- **8KB 外置阈值与「回执裁剪」必须同源**：`TOOL_RESULT_ARTIFACT_BYTES` 只有一处定义（`apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:20`），executor 的外置判定与 `create_archive` 的回执裁剪都读它。各写一份的话，「回执刚好不被外置」这条保证会随任一侧改动静默失效 —— 裁剪阈值一旦小于外置阈值，回执就被换成 `{"artifact": {...}}` 预览，模型再也看不到 `artifact_id`。
  - ✅ 两侧 `from .tool_results import TOOL_RESULT_ARTIFACT_BYTES`；❌ 任一侧写 `8192` 字面量（同型先例：`harness-im.md` 的「上限数值只有一个来源…绝不各写一份」）
  - 机检：`tests/agent_runtime/test_archive_tools.py::test_receipt_is_not_externalized_by_the_tool_result_wrapper`（含**前置条件**断言：未裁剪的回执必须确实越过阈值，否则这条用例会空转）
- **singleflight 的作用域是「每进程」**：`SkillArtifactCache` 的锁是进程内 `asyncio.Lock` + 内存索引（`packages/artifact-store/src/muad_artifact_store/skill_cache.py:25-27`），对跨 Pod 的并发首次加载**没有互斥力**；跨进程正确性依赖 `os.replace` 幂等与 DB CAS。✅ 依靠 READY 标记与校验 checksum 判等；❌ 假设内存索引能跨副本去重。
- **`NfsArtifactStore.resolve` 必须做越界防护**：解析后的路径必须落在 `root` 之内、且不得等于 `root` 本身，否则抛 `ValueError("invalid storage_key")`（`packages/artifact-store/src/muad_artifact_store/nfs.py:10-14`）。✅ 一律经 `resolve()` 取路径；❌ 用 `root / storage_key` 直接拼路径（`../` 可越出根目录）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
