---
id: harness-skill
description: Skill 产物、缓存与子进程
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

## Conventions

**Skill 包、目录与发现**：

- **SKILL.md 里进目录的字段必须单行且有界**：`name` / `description` / `platform_label` 会被逐字插进系统提示目录行，多行等于让上传者往受保护前缀里写段落。长度按 UTF-8 字节、边界严格大于：`description` ≤ 500，`name` / `platform_label` ≤ 128（`muad_skill_sdk.skill_package._catalog_field`，导入与加载共用）。✅ `_catalog_field(description, "description", MAX_DESCRIPTION_BYTES)`；❌ 只判非空。机检：`tests/agent_core/test_skill_package.py`。
- **目录是索引，不是清单**：目录项只有 key/name/description（+ `platform_label`），正文只在 `load_skill` 后进上下文。`description` 是**路由条件**（`Use when` 正例 + `Don't use for` 反例），不是功能简介；反例缺失是误触发主因。❌ 目录里出现正文/checksum/storage_key。机检：`test_prompt_builder.py`。
- **目录有界，且丢了要说出来**：`DefaultPromptBuilder.build` 按 `MAX_CATALOG_ENTRIES`(100) / `MAX_CATALOG_BYTES`(8000) 取前缀，**超限整条丢弃并停止**（不挑小的凑、不截半行），丢弃顺序即 `sort_order`。被丢弃的技能模型看不见 key，因此必须：① 投影点留痕（warning + `skill_catalog_truncated_total`）；② 目录尾部补 `CATALOG_TRUNCATED_NOTE`，**只在真的丢了条目时出现**。✅ `bounded_catalog(skills) -> (kept, dropped)`；❌ 无上限或静默丢弃。
- **被预算挡在目录外的技能要有第二条发现路径**：`search_skills(query)` 在本次 Run 的全部生效技能上按 key/name/description 做空白切词 **AND** 子串匹配，返回与目录同一行格式的条目，结果用**同一个 `bounded_catalog`** 收口；不查库、不加端点。✅ 命中数 + 条目 + 截断时才带 `truncated`/`hint`，`externalizable_result=False`；❌ 返回自由格式文本、结果无上限、用 OR 匹配。
- **一个生效技能都没有时，技能工具一个都不注册**（`SkillToolSet.registry` 早返回）：key 从哪来？注册只会让模型猜 key、白跑一轮换回 `SKILL_NOT_EFFECTIVE`。机检：`test_skill_tools.py::test_skill_tools_are_not_registered_without_effective_skills`。

**产物写入与缓存**：

- **非 Skill 产物必须用 `skills/` 之外的前缀**：孤儿清理只扫 `skills/**`；审计导出用 `exports/{tenant_id}/{export_id}/...`，Runtime Tool 大结果用 `tools/{tenant_id}/{run_id}/{artifact_id}/result.bin`。新产物类型必须显式声明前缀，❌ 复用 `skills/`（会被 `cleanup_orphan_files` 回收）。
- **不可变写入只有一份实现**：`muad_artifact_store.publish_if_absent`（临时文件 + `os.link`：目标已存在即失败，原子判定交给内核）。`NfsArtifactStore.write`、Runtime `write_immutable`、网关入站附件落盘都走它；DB 失败即删文件不留孤儿。❌ 任何地方再抄一份「临时文件 + `os.replace`」（两个写者都能"成功"）。机检：`tests/test_artifact_store_immutable.py`、`tests/agent_runtime/test_artifact_round_budget.py`、`tests/gateway/test_inbound_attachment_store.py`。
- **singleflight 的作用域是「每进程」**：`SkillArtifactCache` 的锁是进程内 `asyncio.Lock` + 内存索引，对跨 Pod 并发首次加载没有互斥力；跨进程正确性依赖 `os.replace` 幂等与 DB CAS。✅ 依靠 READY 标记与 checksum 判等；❌ 假设内存索引跨副本去重。
  - **唯一未收敛处**：`SkillArtifactCache._prepare` 是目录级的「检查再替换」——共享同一 `SKILL_CACHE_ROOT` 时后到者会删掉别人正在读的目录。键是内容寻址（不会发错字节），但跨进程不安全；收敛方向要先定「cache root 是否允许跨进程共享」：允许就改成「存在即用、不存在才发布」。
- **`NfsArtifactStore.resolve` 必须做越界防护**：解析路径必须落在 `root` 内且不等于 `root`，否则 `ValueError("invalid storage_key")`。✅ 一律经 `resolve()`；❌ `root / storage_key` 直接拼（`../` 可越界）。

**执行与结果**：

- **解包与执行的逃逸防护**：zip 解包做 zip-slip 校验，越界成员按 `SKILL_ARTIFACT_UNAVAILABLE` 拒绝；脚本路径必须落在 READY 目录内，越界抛 `SkillExecutionError("script path escapes the skill package")`；子进程环境只透传 `PATH/HOME/LANG`（`packages/agent-core/src/muad_agent_core/skill/executor.py` 的 `ENV_ALLOWLIST`）。
- **Skill 子进程必须自成进程组，超时/取消回收整棵树**：`start_new_session=True` 启动，终止时给整个进程组发信号（`os.killpg`），不是只 `terminate()` 直接子进程。三个细节：组 id 直接用 `process.pid`（不要 `os.getpgid`，子进程被回收后查不到）；`_terminate` 不得按 `returncode` 早返回（孙进程仍持 stdout 管道时会让 worker 永久挂死）；终止后 `_drain_output` 有界等待。机检：`tests/agent_core/test_skill_executor.py::test_timeout_terminates_the_whole_process_group` 与 `::test_timeout_survives_a_child_that_exits_leaving_a_pipe_holder`。
- **Skill stdout 结果行不必是最后一行**：解析从末尾往前找第一个 JSON 对象，跳过非 JSON 行；只看最后一行会把「结果 + 日志」的常见形状判成无结果，`interpret_execution` 的兜底随即把整坨 stdout 当 `{"text": ...}`。机检：`test_result_survives_a_log_line_printed_after_it`。

**工具结果外置（Runtime）**：

- **内容投递类工具不得按通用阈值外置**（`load_skill` / `read_skill_resource` / `search_skills` / `recall`）：这类返回就是给模型读的正文，换成 Artifact 预览等于废掉工具（事故：8320 字节 `load_skill` 被外置成 400 字符预览且无报错）。工具以 `externalizable_result=False` 声明，Runtime 改按 `MAX_INLINE_RESULT_BYTES`（256KB，与 `MAX_RESOURCE_BYTES` 取齐）判定；超过内联上限仍外置，否则超大 SKILL.md 会打爆上下文——正文放 `SKILL.md`，大段规范放 `references/`。机检：`tests/agent_runtime/test_skill_tools.py`、`test_execution_audit.py`、`test_memory_tools.py`。
- **外置阈值与「回执裁剪」必须同源，唯一来源是 contracts 的 `ToolResultSettings`**：`persist_threshold_bytes`（默认 8KB）/`round_budget_bytes`/`preview_head_bytes`/`preview_tail_bytes` 只在 `muad_contracts.platform_settings.ToolResultSettings` 定义，随平台设置快照冻进本次 Run；`tool_results.py` 明确不再定义第二套模块常量（`tests/test_configuration_convergence.py` 断言三个旧常量已移除）。executor 的外置判定与 `ArchiveToolSet` 的回执裁剪都读同一个冻结值。❌ 任一侧写 `8192` 或复活 `TOOL_RESULT_ARTIFACT_BYTES`。机检：`test_archive_tools.py::test_receipt_is_not_externalized_by_the_tool_result_wrapper`。
- **外置判定单元是「回合」而不是「单条调用」**：单条超阈值要外置，多条合计超整轮预算也要外置。执行顺序：**整批判定 → 批量落盘（失败整批回滚）→ 换成引用 → 逐条写审计行 / 发 `tool.completed`**；产物 id 必须赶在 `tool.completed` 之前定下（该事件进 canonical `TOOL_CALL` 行，历史重建靠它找回产物）。✅ `ToolCallRecorder.finish_round` 一次 `_select`+`_persist`+`_flush_audit`；❌ 单条返回时立即外置并上报。
- **外置结果的写入侧与重建侧必须共用 `reference_payload()`**：模型可见形态恒为 `{"artifact": {artifact_id, size, checksum, preview}}` 四键、不二次截断；canonical `TOOL_CALL` 行的 `artifact_id` **列**必须写入（重建读列，不读 `payload_json`）。❌ 重建侧自己拼 JSON，或只把 id 留在 payload（跨 Run 重建退化成 `[tool:名称]`）。
- **阈值一律按 UTF-8 字节判定，边界是「严格大于」**：`len(str)` 会把汉字算成 1；尺寸恰等于阈值不得触发（用 `>`）。单条外置、整轮预算、memory 注入预算、snip 组数同此口径。✅ `message_bytes`/`history_bytes` 按 `encode("utf-8")`；memory 注入按 UTF-8 字节判整块预算（`memory/render.py` 的 `render_memory_block`）；`snip` 用 `<= max_groups` 判不触发。❌ 字符数口径或 `>= threshold`。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
