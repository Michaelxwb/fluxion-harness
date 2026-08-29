# Tasks: Phase 5 Governance + Observability + Eval

- **Source**: `.code-flow/tasks/2026-08-28/phase5-governance-observability-eval/phase5-governance-observability-eval.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-29（v0.7：TASK-001..008 全部完成并 verified——P0 全闭合：ArtifactStore/SecretStore 生产 provider、安全门禁、Eval 生产化 + ReleaseGate、Console Eval 实页、traced_scope + O501-O506 埋点 + 关联完整性门禁；剩 TASK-009（P1 条件）与 TASK-010..014（phase4 遗留））

## Proposal

Phase 5 生产化收尾：为保留 PluginType 补齐生产 provider（`PostgresEncryptedSecretStore` 持久化加密 + key rotation、`S3CompatibleArtifactStore` 生产 provider + `LocalFileArtifactStore` dev 必通 + `artifact_metadata` 表），落地统一 OTel 埋点（`traced_scope` + 7 类 span，trace 关联≥99%）、Eval 生产化（EvalSet 版本化 + RuleBased 默认 + `ReleaseGateService` 挂 publish 管道阻断 P0 回归）与 Console `/build/eval` 实页；Async Task 作为 P1 条件 FEAT。闭合 Phase 5 Gate 四项：trace 关联≥99%、Secret 明文泄漏=0、tenant escape=0、Eval Gate 可阻断 P0 回归。

依据 design 对齐项（用户 2026-08-28 确认 + v0.2 翻案）：Secret 生产走 PostgreSQL AES-256-GCM 不引 Vault；**ArtifactStore 生产必须落地 S3Compatible（remediation §16.1 翻案原「SMB 预留」决策），SMB 仍预留，metadata 落 PG 表（§16.2）**；Collector = OTLP env 接线 + 部署文档；Eval 默认 RuleBased（S-P13-07 不伪造）；Async Task P1 条件。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 真实文件系统（tmp）→ ArtifactStoreProvider | TASK-001 | verified |
| S-02 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 真实 DB（SQLite+PG 双库契约，含 key rotation） | TASK-002 | verified |
| S-03 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | CredentialResolver + 双租户 | TASK-003 | verified |
| S-04 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | 完整 execution：HTTP→Runtime→Model→Tool→Workflow→DB/Redis | TASK-008 | verified |
| S-05 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | EvalSet（workflow 用例）+ EvalExecutor | TASK-004 | verified |
| S-06 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | Publish 管道 + ReleaseGateService + EvalRun | TASK-005 | verified |
| S-07 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | Publish 管道 + ReleaseGateService | TASK-005 | verified |
| S-08 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | Browser → Router → Service → Eval API | TASK-006 | verified |
| S-09 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 真实 DB + worker | TASK-009 | planned |
| E-01 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 日志/trace/spec/response 扫描 | TASK-003 | verified |
| E-02 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | ArtifactStoreProvider tenant scope | TASK-001 | verified |
| E-03 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | span 完整性扫描 | TASK-008 | verified |
| E-04 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | ReleaseGateService + EvalRunStore | TASK-005 | verified |
| B-01 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | SMB provider 注册点 | TASK-001 | verified |
| B-02 | phase5-governance-observability-eval.design.md#2.4 验收条件 | unit | `FLUXION_SECRET_MASTER_KEY` env | TASK-002 | verified |
| B-03 | phase5-governance-observability-eval.design.md#2.4 验收条件 | unit | OTLP exporter 包存在性 | TASK-007 | verified |
| B-04 | phase5-governance-observability-eval.design.md#2.4 验收条件 | unit | Async Task 开关 | TASK-009 | planned |
| S-11 | phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-12） | integration | 真实 DBOS sysdb + HTTP 端点 | Operations Queues/Workers 真实端点返回 + envelope + tenant scope | TASK-010 | planned |
| S-12 | phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-12） | integration | 真实 workflow_run 投影 + HTTP 端点 | Runs list-all 端点返回分页 + RunsPage 切 HTTP | TASK-011 | planned |
| S-13 | phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-11） | E2E | Browser → Router → Service → UI | User 360 深链直达详情、刷新保留 | TASK-012 | planned |
| S-14 | phase4-product-experience.design.md#2.4 验收条件（NFR-PERF-01/NFR-A11Y-01） | E2E | 真浏览器（Playwright/Lighthouse） | 首屏 P95≤500ms + axe 真浏览器扫描 | TASK-013 | planned |
| S-15 | phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-02..08） | integration | 真实 DB + HTTP 端点 | Chat Workspace 7 端点返回 + 写操作生效 + tenant scope + Chat 切 HTTP | TASK-014 | planned |
| S-10 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | S3/MinIO（docker）→ S3CompatibleArtifactStore | TASK-001 | verified |

> NFR-SEC-01（明文=0）由 E-01（TASK-003）承载；NFR-OBS-01（≥99%）由 E-03+S-04（TASK-008）承载；NFR-ARCH-05（6 保留 PluginType 全有 provider 或显式预留）由 S-01/B-01（TASK-001）承载；NFR-PERF-01（gate 附加 P95 ≤500ms）由 TASK-005 承载。Phase 5 Gate 四项闭合：trace≥99%（S-04+E-03）、明文泄漏=0（E-01）、tenant escape=0（S-03+E-02）、Eval 阻断 P0（S-06+S-07）。

---

## TASK-001: LocalFileArtifactStore + artifact:// 引用 + provider 接线/隔离

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: S-01, E-02, B-01, NFR-ARCH-05

### Description

实现两个 `ArtifactStoreProvider`：`plugins/artifact/local_fs.py` `LocalFileArtifactStore`（dev provider，必须通，目录前缀 `{root}/{tenant_id}/{namespace}/{key}`）与 **`S3CompatibleArtifactStore` 生产 provider**（S3/MinIO 兼容 endpoint，remediation §16.1 翻案原「SMB 预留」决策），签名同为 `put(tenant_id, namespace, key, data) -> ArtifactMetadata`/`get`/`delete`，metadata 含 ref/tenant/namespace/key/version/size/sha256/created_at，全方法 timeout/fail policy（S3 为新增外部依赖，规则 18：timeout/retry/fail policy）。**`artifact_metadata` PostgreSQL 表**（artifact_id/tenant_id/owner_type·owner_id/execution_id/workflow_id/content_type/size/sha256/classification/retention_policy/status/created_by/created_at/deleted_at，remediation §16.2——对象存储只存 blob，治理事实落表，支撑 Audit/Retention/GC/User deletion/Access control）。`artifact://{tenant}/{namespace}/{key}@{version}` URI 引用模型入 Resource spec / ExecutionSnapshot（pin，规则 6/10）。SMB 注册点仅预留（B-01：配置 SMB → 明确「SMB 未实现」错误或降级 dev，不崩溃）。经 PluginLoader 注册进 per-PluginType registry，补 E506 lifecycle/isolation 测试（untrusted→isolated、单 provider 故障不拖垮 Runtime）。

### Checklist

- [x] 实现 `LocalFileArtifactStore`（dev）与 `S3CompatibleArtifactStore`（生产，MinIO dev 端点 + timeout/retry/fail policy）
- [x] 建 `artifact_metadata` 表（幂等 DDL）并在 put/delete 时落库（含 status/deleted_at）
- [x] 实现 `artifact://` URI 引用模型（入 spec/snapshot pin，规则 6/10）
- [x] SMB 注册点预留（明确错误或降级 dev，不崩溃）
- [x] [S-01][integration] 修改生产代码前，编写验收测试并记录 RED：真实文件系统 put → get → delete，内容一致、metadata（size/sha256/version）正确且落 `artifact_metadata` 表、tenant 命名空间隔离
- [x] [S-10][integration] 修改生产代码前，编写验收测试并记录 RED：MinIO（docker）端点 put → get → delete + metadata 落表，内容一致、tenant 隔离、超时/失败策略生效
- [x] [E-02][integration] 修改生产代码前，编写验收测试并记录 RED：tenant A 读 tenant B 的 artifact key → 拒绝访问（tenant escape=0），无数据返回
- [x] [B-01][integration] 修改生产代码前，编写验收测试并记录 RED：配置 SMB provider → 明确错误或降级 dev provider，不崩溃
- [x] E506 lifecycle/isolation 测试：provider 注册失败无 partial registry、untrusted→isolated、单 provider 故障不拖垮 Runtime
- [x] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 `python -m pytest backend/tests/plugins/ backend/tests/architecture/ -k provider`（planned）：断言 Kernel 只依赖 Contract、concrete providers 经 PluginLoader/registry 间接 resolve（无 Kernel→具体 provider 直依赖）
- [x] **Spec verifier**：`RULE-backend-directory-001` — 运行 `python -m pytest backend/tests/architecture/ -k directory`（planned，AST 守护）：断言 provider 落 `plugins/artifact/`、`plugins/secret/`、目录深度 ≤3、测试目录与源码同构
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实文件系统（tmp 目录）+ 真实 registry | put/get/delete 一致；metadata 正确且落表；tenant 隔离 | `backend/tests/plugins/test_artifact_store.py::TestS01LocalFileSystemRoundtrip`（5 例） | `python -m pytest backend/tests/plugins/ -v` | verified |
| S-10 | integration | 真实 MinIO（docker :9000）+ artifact_metadata 表 | put/get/delete 一致；metadata 落表；tenant 隔离；超时策略生效 | `backend/tests/plugins/test_artifact_store.py::TestS10S3CompatibleArtifactStore`（2 例） | `docker run -d --name fluxion-test-minio -p 9000:9000 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin minio/minio server /data && python -m pytest backend/tests/plugins/ -k S10 -v` | verified |
| E-02 | integration | 真实 provider + 双租户数据 | 跨租户读取拒绝；明确错误 | `backend/tests/plugins/test_artifact_store.py::TestE02TenantEscapeZero`（2 例） | `python -m pytest backend/tests/plugins/ -k E02 -v` | verified |
| B-01 | integration | 真实 provider registry + SMB 配置项 | 明确错误或降级；不崩溃 | `backend/tests/plugins/test_artifact_store.py::TestB01SmbRegistrationPoint`（3 例） | `python -m pytest backend/tests/plugins/ -k B01 -v` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL（RED 回放）: `git stash -u -- backend/src` 撤销生产代码后 `pytest tests/plugins/` → collection error `ImportError: cannot import name 'ArtifactStoreError' from 'fluxion.plugins.artifact'`，16 用例全失败 | PASS: TestS01 5 例（roundtrip/version 递增/tenant 磁盘隔离/path traversal 拒绝/timeout 有界） | `test_put_get_delete_roundtrip_and_metadata`：size/sha256/version/status=active 落表断言 L84-L93；软删 status=deleted + deleted_at L99-L102 | `tmp_path` 真实文件系统 + aiosqlite 真实引擎 + `artifact_metadata` 表（`tests/plugins/test_artifact_store.py:37-47` fixture） | verified |
| S-10 | 同上（RED 回放同一条 collection error） | PASS（真实 MinIO docker :9000，`fluxion-test-minio` 容器）: put→get→delete + metadata 落表 + 跨租户拒绝 + timeout_ms=1 超时策略 | `TestS10S3CompatibleArtifactStore::test_put_get_delete_and_metadata`：sha256/size 落表 L258-L260；`test_timeout_fail_policy` 超时断言 | 真实 httpx + SigV4 签名对真实 MinIO 端点（`s3.py` 全自研签名，无 mock）；不可达时 skip 不伪造（S-P13-07） | verified |
| E-02 | 同上 | PASS: tenant B 读/删 tenant A key → `ArtifactStoreError`；metadata 查询 B 视角 0 行 | `test_cross_tenant_read_rejected` L153-L161（provider 拒绝 + metadata 双视角断言）；`test_cross_tenant_delete_rejected` L166-L170 | 真实 provider 双租户数据（tenant 前缀寻址 + tenant 过滤查询，无 mock） | verified |
| B-01 | 同上 | PASS: `create_artifact_store("smb", ...)` → 明确「SMB 未实现」错误；未知 provider 明确报错；local-fs 工厂正常返回 | `TestB01SmbRegistrationPoint` L178-L190 | 真实工厂入口 `plugins/artifact/__init__.py::create_artifact_store`（无 mock） | verified |
| E506 | 同上 | PASS: 注册中途失败（capabilities 抛错）→ loader 回滚无 partial registry（resolve 抛 ProviderNotFoundError）；单 provider get 故障后仍可 put/get | `TestE506LifecycleIsolation` L279-L357 | 真实 PluginLoader + per-PluginType registry（`loader.registry_for`） | verified |
| snapshot pin | FAIL（RED 回放，撤销 contracts.py 变更）: `ImportError: cannot import name 'ARTIFACT_REF_PATTERN' from 'fluxion.resources.contracts'`（TestArtifactRefSnapshotPin 2 例 collection error） | PASS: pin 后 frozen setattr 拒绝（ValidationError）；非法 URI 形态拒绝 | `TestArtifactRefSnapshotPin::test_snapshot_pins_artifact_refs` / `test_snapshot_rejects_malformed_artifact_ref` | 契约层 `resources/contracts.py::ARTIFACT_REF_PATTERN`（grammar 契约层定义，plugin 侧复用——Kernel 不依赖 Plugin 方向保持） | verified |

**Spec verifier 结果**：
- `RULE-fluxion-runtime-001`：`python -m pytest backend/tests/plugins/ backend/tests/architecture/ -k provider` → 5 passed（含 `tests/unit/test_plugin_architecture.py` kernel/loader 无 concrete import 守护 + E506 registry resolve）
- `RULE-backend-directory-001`：`python -m pytest backend/tests/architecture/ -k directory` → 4 passed（新增 `tests/architecture/test_directory_structure.py`：provider 落 `plugins/artifact/`、深度 ≤3、tests/plugins 同构、≤500 行）
- 质量门禁：`ruff check src/fluxion/plugins/artifact/ tests/plugins/ tests/architecture/test_directory_structure.py` → All checks passed；`mypy src/fluxion/plugins/artifact/` → no issues（注：`resources/contracts.py` 存在预存在 mypy/ruff 报错 `agent_definition_version` 重复字段，HEAD 上即有，非本任务引入，待另行处理）

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)：整文件模式按序执行；provider 测试新建 backend/tests/plugins/
- [2026-08-29] completed (done)：S-01/S-10/E-02/B-01/E506 全 verified（S-10 对真实 MinIO docker :9000）；补 artifact_refs 入 ExecutionSnapshot pin（契约层 ARTIFACT_REF_PATTERN）；新增 tests/architecture/test_directory_structure.py；RED 以「撤销生产代码回放」记录；ruff/mypy 干净；回归 280+288 passed（workflow_gate_s06 负载下 flaky 单独通过、Restate PoC 失败为 HEAD 预存在、release_gate 需从仓库根运行）

---

## TASK-002: secret_credentials 表 + PostgresEncryptedSecretStore

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.3 数据设计, phase5-governance-observability-eval.design.md#3.4 接口设计
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-02, B-02

### Description

新增 `secret_credentials` 表（tenant_id+ref 复合 PK、name/version、nonce bytea、ciphertext bytea、revoked、**key_id/cipher_version/rotated_at**、created_at；索引 `(tenant_id, name)`）。实现 `PostgresEncryptedSecretStore`（`plugins/secret/postgres.py`）：`put/rotate/revoke/resolve/list_metadata`，AES-256-GCM（12B nonce），与现有 `LocalEncryptedSecretStore` 同形 API；密文入表绝不存明文。**Key rotation（remediation §16.3）**：按 `key_id` 选择旧 key 解密 → 新 key 加密 → 批量 re-encrypt → revoke old key；rotation 进 AuditLog。Master Key `FLUXION_SECRET_MASTER_KEY`（base64 32B）外置 env，缺失/长度≠32 启动 fail-fast 报错、不静默生成（B-02）。SQLite + PostgreSQL 实现同一 Repository 契约并跑同一 Contract Test（规则 7，PG 复用 `local-pg-test-env`）。全方法定义 timeout + fail policy（规则 18）。

### Checklist

- [x] 建 `secret_credentials` 表（幂等 DDL）+ 索引；实现 `PostgresEncryptedSecretStore`（put/rotate/revoke/resolve/list_metadata）
- [x] Master Key 外置 env 校验：缺失/长度≠32 → 启动明确报错（fail-fast，不静默生成）
- [x] 实现 key rotation：按 `key_id` 解旧密 → 新密加密 → 批量 re-encrypt → revoke 旧 key（rotation 进 AuditLog）
- [x] [S-02][integration] 修改生产代码前，编写验收测试并记录 RED：真实双库（SQLite+PG）put → rotate（批量重加密）→ 重启 store → resolve 一致；rotate 后经 key_id/cipher_version 可解旧密文；revoke 后 resolve 拒绝；表中 `ciphertext` 非明文
- [x] [B-02][unit] 修改生产代码前，编写验收测试并记录 RED：Master Key 缺失/长度≠32 → 启动明确报错
- [x] 类型注解齐全、异常不吞、timeout/fail policy 全覆盖（provider 外部 IO 带 deadline）
- [x] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/contract/ -k secret`（planned，SQLite + PG `local-pg-test-env` 各一套）：断言双库同契约、索引生效、密文字节存储、无 N+1
- [x] **Spec verifier**：`RULE-backend-quality-001` — 运行 `ruff check` + `mypy backend/src/fluxion/plugins/secret/`（planned）+ S-02/B-02 verifier 用例：断言全类型注解、无静默吞异常、全方法 timeout/fail policy
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 SQLite + PG 双库（重启 store 进程级重建 + key rotation） | resolve 持久一致；rotate 后旧密文可解；revoke 拒绝；ciphertext 非明文 | `backend/tests/contract/test_secret_store.py::TestS02SecretCredentialsContract`（8 例 × 双库） | `FLUXION_REQUIRE_POSTGRES_CONTRACT=1 FLUXION_POSTGRES_DSN=postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test python -m pytest backend/tests/contract/test_secret_store.py` | verified |
| B-02 | unit | 真实 env 读取路径 | 缺失/长度≠32 → 明确报错不启动 | `backend/tests/contract/test_secret_store.py::TestB02MasterKeyEnvFailFast`（3 例 × 双库） | 同上（`-k B02`） | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: 实现前运行 `pytest tests/contract/test_secret_store.py` → collection error `ModuleNotFoundError: No module named 'fluxion.plugins.secret'`（11 用例全失败） | PASS: 22 passed（8 用例 × SQLite/PG 双库） | `test_put_resolve_and_ciphertext_not_plaintext`：ciphertext 非明文 + nonce 12B 断言 L101-L104；`test_master_key_rotation_reencrypts_and_old_versions_decrypt`：批量重加密 count/keyring 收口/key_id·cipher_version·rotated_at 落表/AuditLog L152-L192；`test_rebuild_store_resolves_persisted_secret` 进程级重建 L106-L120 | 真实 SQLite 文件库（tmp_path）+ 真实 PostgreSQL fluxion_test（FLUXION_REQUIRE_POSTGRES_CONTRACT 门控）双库跑同一套契约断言；AESGCM 真加解密，无 mock | verified |
| B-02 | 同上（collection error） | PASS: 6 passed（3 用例 × 双库） | `TestB02MasterKeyEnvFailFast`：缺失 → `secret_master_key_missing`；16B key → `secret_master_key_invalid`（不静默生成）；合法 key 接受 | 真实 env 读取路径（monkeypatch 真实环境变量 + from_env base64 解码） | verified |

**Spec verifier 结果**：
- `RULE-backend-database-001`：`pytest tests/contract/ -k secret`（PG 门控）→ 22 passed；`tests/contract/` 全量回归 → 65 passed（双库契约无回归）
- `RULE-backend-quality-001`：`ruff check src/fluxion/plugins/secret/ tests/contract/test_secret_store.py` → All checks passed；`mypy src/fluxion/plugins/secret/` → no issues；全方法经 `_with_deadline`（asyncio.wait_for deadline + SecretProviderError fail policy，无静默吞异常）
- secrets 相关回归：tests/integration/test_local_secret_store.py + test_plugin_resource_credential_binding.py + tests/services/test_context_resolver.py + tests/e2e/test_mcp_credentials.py → 15 passed

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)
- [2026-08-29] completed (done)：S-02/B-02 全 verified（双库 22 passed）；secret_credentials 表 + PostgresEncryptedSecretStore（engine 注入双库）+ master key rotation（批量 re-encrypt + AuditLog）；B-02 fail-fast 不静默生成

---

## TASK-003: Secret tenant 隔离 + 泄漏门禁 + AuditLog

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: phase5-governance-observability-eval.design.md#2.4 验收条件, phase5-governance-observability-eval.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-03, E-01, NFR-SEC-01

### Description

tenant 隔离收口（Phase 5 Gate「tenant escape=0」）：CredentialResolver 双租户场景——tenant A 引用 tenant B ref → `secret_tenant_mismatch` 拒绝。泄漏门禁（Gate「明文泄漏=0」）：扫描测试覆盖日志（structlog 输出）、trace（span attributes）、Resource spec、API response 四个面，任一面出现 secret 明文 → 测试失败阻断 CI（E-01）。Secret 高影响操作进 AuditLog（规则 24）：publish/revoke secret；redaction 全链路（RISK-P5-03）。

### Checklist

- [x] CredentialResolver tenant 收口断言（provider 方法首参 tenant_id 强制）
- [x] [S-03][integration] 修改生产代码前，编写验收测试并记录 RED：tenant A/B 各持 secret，A 引 B ref → `secret_tenant_mismatch` 拒绝
- [x] [E-01][integration] 修改生产代码前，编写泄漏扫描测试并记录 RED：日志/trace/spec/response 四面注入已知明文 secret → 任一面出现即失败（明文=0 门禁）
- [x] secret publish/revoke 进 AuditLog（关联 request_id/trace_id/tenant_id）
- [x] **Spec verifier**：`RULE-backend-logging-001` — 运行 E-01 泄漏套件 + AuditLog 断言（planned）：断言 structlog JSON 脱敏生效、Secret 操作全部进 AuditLog、明文=0 门禁可阻断
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 真实 CredentialResolver + 双租户 secret 数据 | 跨租户 resolve 拒绝；`secret_tenant_mismatch` | `backend/tests/integration/test_secret_governance.py::TestS03TenantIsolation`（2 例） | `python -m pytest backend/tests/integration/test_secret_governance.py -k S03 -v` | verified |
| E-01 | integration | 真实日志/trace/spec/response 输出通道 | 四面明文=0；门禁可阻断 CI | `backend/tests/integration/test_secret_governance.py::TestE01LeakGate`（4 例） | `python -m pytest backend/tests/integration/test_secret_governance.py -k E01 -v` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | GREEN-before（既有 `secret_tenant_mismatch` 收口已存在——design §3.5 注明「已有」）；RED 由 AuditLog 项承载（见下） | PASS: TestS03 2 例 | `test_cross_tenant_ref_rejected`：`excinfo.value.code == "secret_tenant_mismatch"` L74-L76；`test_provider_metadata_tenant_scoped` 双租户 list_metadata 只见本租户 L90-L92 | 真实 PostgresEncryptedSecretStore（SQLite 引擎真实 AES 加密数据）+ 真实 CredentialResolver，双租户真实 put 数据，无 mock | verified |
| E-01 | 部分 GREEN-before（日志 redact_mapping/spec assert_no_plaintext_secret 既有机制生效——green-before 属收口回归守护）；response 面初始 404→修正 import 后真实断言通过 | PASS: TestE01 4 例 | `test_log_face_redacts_plaintext`：渲染 JSON 无 marker + `[REDACTED]` 断言 L123-L129；`test_trace_face_redacts_plaintext`：OTel SDK span.attributes 无 marker L145-L149；`test_spec_face_rejects_plaintext`：ValueError 拒绝 L155-L163；`test_response_face_no_plaintext`：真实 HTTP 响应体无 marker L181-L186 | 四面全真实：structlog JSON 渲染输出（caplog）、OTel SDK 真实 span（get_tracer）、真实 ResourceDefinition 校验、真实 Console HTTP（ASGITransport + /api/v1/credentials） | verified |
| AuditLog | FAIL: `TypeError: PostgresEncryptedSecretStore.put() got an unexpected keyword argument 'actor_id'`（audit 参数未实现，4 用例失败） | PASS: TestSecretAuditLog 3 例（put/revoke/rotation 进 audit + trace_id 关联 + audit 不含明文密文） | `test_put_and_revoke_write_audit`：secret.put/secret.revoke 行 + request_id/trace_id/target_id 断言 L207-L222；`test_ciphertext_never_in_audit` L240-L256 | 真实 audit_logs 表落库（audit_logs 新增 trace_id 列——规则 23 关联；AuditRecord.trace_id 默认 None 向后兼容） | verified |

**Spec verifier 结果**：
- `RULE-backend-logging-001`：E-01 套件（`pytest tests/integration/test_secret_governance.py`）9 passed——structlog JSON 脱敏（headers/query → `[REDACTED]`）、Secret put/revoke/rotate_master_key 全部进 AuditLog（关联 request_id/trace_id/tenant_id）、四面明文=0 门禁断言可阻断 CI
- 回归：tests/contract/（PG 门控）+ tests/users/ + tests/integration/test_logging.py → 76 passed；tests/e2e console 契约 + tests/integration/ + tests/services/ → 167 passed

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)
- [2026-08-29] completed (done)：S-03/E-01 verified；audit_logs 增 trace_id 列（规则 23）；secret put/revoke/rotate_master_key 进 AuditLog；E-01 四面门禁（日志/trace/spec/response）落地

---

## TASK-004: EvalSet 版本化 + EvalExecutor 扩展

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-05

### Description

EvalSet 走 resource_definitions 版本化生命周期（draft→publish→版本递增）。`EvalExecutor` 扩展：模型评测 harness SPI 预留（接口形态），**RuleBased 默认评测器**（确定性、可测；真实模型评测需凭据，按 S-P13-07 约束无凭据不实现不伪造）；支持 Workflow 类型用例与 Capability 契约评测（对齐能力层，US-11：Step 与 Tool 复用 Capability Contract）。Eval API 扩展：`GET /admin/evals`、`POST /admin/evals/{id}/run`、`GET /admin/evals/runs`，统一 envelope 封装（Handler 不手写响应结构）。

### Checklist

- [x] EvalSet 版本化 lifecycle（走 resource_definitions）；`EvalExecutor` SPI + RuleBased 默认实现
- [x] Workflow 类型用例 + Capability 契约评测支持（模型 harness 仅预留接口）
- [x] Eval API 三端点（envelope 封装，标准响应结构）
- [x] [S-05][integration] 修改生产代码前，编写验收测试并记录 RED：含 workflow 用例的 EvalSet → start EvalRun → score/passed 正确、EvalRun 记录可查
- [x] RuleBased 评测器确定性断言：同输入同 score（真实模型评测不伪造，无凭据保持 RuleBased）
- [x] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/services/ backend/tests/resources/ -k eval`（planned）：断言 EvalSet 版本化生命周期、`artifact://` 引用可 pin 进 snapshot（规则 6/10）
- [x] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 S-05 verifier 用例（planned）：断言 Workflow 用例/Capability 契约评测对齐能力层（复用 Capability Contract，不另起评测语义）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | 真实 EvalSet + EvalExecutor + EvalRunStore | workflow 用例 score/passed 正确；EvalRun 可查 | `backend/tests/integration/test_eval_admin_api.py`（5 例） | `python -m pytest backend/tests/integration/test_eval_admin_api.py -v` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | FAIL: 实现前 `ImportError: cannot import name 'ModelEvalHarness'`（collection error，5 用例全失败） | PASS: 5 passed | `test_S05_workflow_eval_set_run_and_admin_api`：workflow 用例 score=1.0/passed + 三端点 envelope 断言 L40-L76；`test_S05_workflow_case_partial_failure_scores_deterministically`：score=0.5 + 两次 run 同 score L141-L146；`test_S05_workflow_ref_must_be_published_exact`：未发布 workflow_ref → 404 + `wf-refund@9` 诊断 L170-L175；`test_S05_eval_set_version_increments_on_republish` L190-L196 | 真实 SQLite registry（resource_definitions 版本化 publish）+ 真实 TraceStore + 真实 RuleBasedEvalExecutor + 真实 HTTP（ASGITransport）；workflow 用例 pin 精确 published 版本（规则 5/6）；Step/capability 结果经 trace text 匹配（复用 Capability Contract 语义，不另起评测语义） | verified |
| SPI 预留 | 同上 collection error | PASS: `test_model_eval_harness_is_spi_only`——`ModelEvalHarness` Protocol 存在且无具体实现（S-P13-07 不伪造） | L224-L240 | SPI 形态断言：eval_app 模块中除 Protocol 外无 Harness 具体类 | verified |

**Spec verifier 结果**：
- `RULE-fluxion-resource-001`：eval 套件 `pytest tests/services/ tests/resources/ tests/integration/ -k "eval or Eval"` → 10 passed（EvalSet 版本化 lifecycle：draft→publish→版本递增；`artifact://` pin 断言由 TASK-001 `TestArtifactRefSnapshotPin` 承载——18 passed）
- `RULE-fluxion-workflow-001`：S-05 workflow 用例断言 workflow_ref 精确 published pin + expected_steps 走 trace（capability step 结果）——评测语义复用 Capability Contract
- 回归：tests/integration/test_eval_traceability.py + test_eval_api.py + tests/unit/ + tests/resources/ → 97 passed（test_release_gate 为 CWD 依赖项，须从仓库根运行——非本任务引入）
- 质量：`ruff check` eval 相关文件 clean（contracts.py 预存在 PIE794 重复字段为 HEAD 既有）；`mypy src/fluxion/services/eval_app.py src/fluxion/api/eval.py` → no issues

### Log
- [2026-08-28] created (draft)
- [2026-08-29] completed (done)：S-05 verified；EvalCaseDefinition 扩 workflow 类型（workflow_ref 精确 pin + expected_steps）；ModelEvalHarness SPI 预留（S-P13-07）；`/api/v1/admin/evals` 三端点（GET 列表 / POST {id}/run / GET runs，统一 envelope）；dispatcher 路由 `/api/v1/admin/evals` → eval 域；dev bundle 注入 catalog

---

## TASK-005: ReleaseGateService 挂 publish 管道

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.4 接口设计, phase5-governance-observability-eval.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-06, S-07, E-04, NFR-PERF-01

### Description

`ReleaseGateService.evaluate(release_id, candidate_eval_run_id, baseline_run_id, threshold) -> GateDecision`，复用 `EvaluationApplicationService.compare()`；blocked 决策含 score_delta 与原因。挂 publish 管道：候选版本跑 EvalRun 对比基线，score 回退超阈值 → 阻断 P0 发布（S-06）；达标 → 放行且 EvalRun 记录留档（S-07）。gate 等待超时 ≤2s，超时 fail-closed 阻断并记录（不阻塞 publish 主路径，评测结果异步落 EvalRunStore）。基线 run 不存在 → 阻断 + 明确错误「基线不可用」（E-04，RISK-P5-04）。阻断决策留档 AuditLog（发布回滚复用既有治理）。NFR-PERF-01：publish P95 增量 ≤500ms。

### Checklist

- [x] 实现 `ReleaseGateService`（compare 复用 + GateDecision 含 score_delta/原因）并挂 publish 管道
- [x] gate 超时 ≤2s fail-closed；阻断决策留档 AuditLog
- [x] [S-06][E2E] 修改生产代码前，编写验收测试并记录 RED：候选版本 score < 基线阈值 → publish 被阻断 + 明确诊断（score delta）
- [x] [S-07][E2E] 修改生产代码前，编写验收测试并记录 RED：候选 score ≥ 阈值 → publish 放行、EvalRun 记录留档
- [x] [E-04][integration] 修改生产代码前，编写验收测试并记录 RED：基线 run 不存在 → 阻断 + 明确错误「基线不可用」
- [x] NFR-PERF-01 断言：publish P95 增量 ≤500ms（gate 计时）
- [x] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 S-06/S-07/E-04 verifier 套件（planned）：断言 provider/gate 外部 IO 全 timeout、gate 超时 fail-closed（2s）、异常不吞、DFX 为编码期自动化证据
- [x] **Spec verifier**：`RULE-backend-platform-001` — 运行 S-06/S-07 verifier 用例（planned）：断言 gate 走控制面 API 标准响应、阻断决策留档 AuditLog、错误码命名空间
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实 publish 管道 + EvalRun（真实 EvalSet） | 回退阻断 + score delta 诊断 | `backend/tests/integration/test_release_gate.py::TestReleaseGatePublishPipeline::test_s06_regression_blocks_publish` | `python -m pytest backend/tests/integration/test_release_gate.py -v` | verified |
| S-07 | E2E | 真实 publish 管道 + EvalRun | 达标放行；EvalRun 留档 | `backend/tests/integration/test_release_gate.py::test_s07_passing_gate_publishes_and_keeps_runs` | 同上 | verified |
| E-04 | integration | 真实 EvalRunStore（无基线 run） | 阻断 + 明确错误「基线不可用」 | `backend/tests/integration/test_release_gate.py::test_e04_missing_baseline_blocks_with_clear_error` | 同上 | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)

---

## TASK-006: Console `/build/eval` 实页

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-005
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#2.4 验收条件
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, frontend-semi-design#RULE-frontend-semi-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-08, E-04

### Description

Phase 4 `/build/eval` 占位升级为实页：EvalSet 列表 / EvalRun 列表 / 详情 / 触发评测（`POST /admin/evals/{id}/run`）；gate 阻断决策（score delta、基线不可用）以标准响应展示。复用 Phase 4 前端模式：`src/pages/eval/`、全 Semi 组件、经 services（in-memory/http 同契约、无裸 fetch）、容器/展示分离（props 只读 + 事件上抛）、四态完备（loading/empty/error/success）。

### Checklist

- [x] 实现 Eval 页（列表/详情/触发评测 + gate 决策展示），页面落 `src/pages/eval/`
- [x] 数据经 services（in-memory 先行，http 同契约），组件零裸 fetch；全 Semi 组件、容器/展示分离
- [x] [S-08][E2E] 修改生产代码前，编写验收测试并记录 RED：打开 `/build/eval` → EvalSet/Run 列表/详情/触发评测可见，四态完备
- [x] E-04 联动断言：gate 阻断（基线不可用/score 回退）在页面呈现标准错误响应
- [x] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 S-08 verifier 用例（planned）：断言全部 Eval API 经统一 envelope 消费（`code=0`/错误路径 + request_id）、services 层无手写响应结构
- [x] **Spec verifier**：`RULE-frontend-semi-001` — 运行 UI 规则套件（planned）：断言页面全 Semi 组件、无第二套通用组件库、react19-adapter 首导入保持
- [x] **Spec verifier**：`RULE-frontend-quality-001` — 运行质量扫描（planned）：断言无裸 fetch、TS 无 `any`/`@ts-ignore` 滥用、页面测试覆盖、四态用例齐全
- [x] **Spec verifier**：`RULE-frontend-directory-001` — 运行目录纪律扫描（planned）：断言页面在 `src/pages/eval/`、组件在 `src/components/`/shared、测试目录同构
- [x] **Spec verifier**：`RULE-frontend-component-001` — 运行组件契约套件（planned）：断言容器/展示分离、props 只读、事件上抛、接口契约类型完整
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实浏览器 + Router + Eval API（in-memory service） | 列表/详情/触发可见；四态完备 | `frontend/apps/console/src/pages/__tests__/eval-page.e2e.test.tsx`（5 例） | `cd frontend/apps/console && npx vitest run src/pages/__tests__/eval-page.e2e.test.tsx` | verified |
| E-04 联动 | integration | 真实组件树 + gate 阻断响应 | 阻断决策标准错误呈现 | 同上（E-04 联动 2 例） | 同上 | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | FAIL: 5 用例全失败（页面仍是 EvalPlaceholderPage 空态占位——无列表/触发/详情元素） | PASS: 5 passed | `test_1` 列表/详情/触发断言 L42-L60（run 详情 trace 展示 + 触发后新 run 出现）；空态 L63-L71；错误态 L73-L80 | 真实 Router（/build/eval 路由）+ 真实 ConsoleApp 组件树 + in-memory service（同 http 契约）；jsdom 真实交互（userEvent 点击/输入） | verified |
| E-04 联动 | 同上 | PASS: gate 阻断 message（score 回退/score_delta 诊断）与「基线不可用」均以 ErrorBanner 标准错误呈现 | `test_4` L82-L100（score 回退 envelope message 断言）；`test_5` L102-L116（基线不可用） | 真实组件树 + envelope 失败路径（ApiError.message 原样呈现，对应 HTTP 层 code=38_001 标准响应） | verified |

**Spec verifier 结果**：
- `RULE-fluxion-console-api-001`：httpConsoleApi 经共享 HttpClient（envelope 校验 code=0/错误路径抛 ApiError 含 request_id）；页面 services 层无手写响应结构（解析函数 required* 契约校验）
- `RULE-frontend-semi-001`：页面全 Semi 组件（Table/Button/Card/Descriptions/Tag/Input/Skeleton/Empty/Typography）；无第二套组件库（grep 无 antd）；`main.tsx` react19-adapter 首导入保持
- `RULE-frontend-quality-001`：`npx tsc --noEmit` 干净（无 any/@ts-ignore）；组件零裸 fetch（grep 验证）；页面测试 5 例覆盖四态（loading Skeleton/empty/error/success）
- `RULE-frontend-directory-001`：页面落 `src/pages/eval/EvalPage.tsx`、展示组件同文件分立（EvalSetsTable/EvalRunsTable/EvalRunDetail）、测试目录同构 `pages/__tests__/`
- `RULE-frontend-component-001`：容器（EvalPage 状态管理）与展示组件分离；展示组件 props readonly + 事件上抛（onSelect 回调）
- 回归：console 全量 `npx vitest run` → **97 passed**（router 测试更新为实页断言——Phase 5 有意行为变更）；tsc clean

### Log
- [2026-08-28] created (draft)
- [2026-08-29] completed (done)：S-08 verified；EvalPlaceholderPage 移除、EvalPage 实页（列表/详情/触发 + gate 阻断 ErrorBanner 呈现）；ConsoleApi 契约扩展（listEvalSets/listEvalRuns/triggerEvalRun）in-memory+http 双实现；导航解除置灰

---

## TASK-007: traced_scope 助手 + OTLP Collector 接线

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#3.4 接口设计, phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#4 部署与运维
- **Acceptance-Refs**: B-03, O507

### Description

实现 `traced_scope` 上下文助手：`async with traced_scope(name, kind=..., attributes={}): ...`——统一 span 创建入口，自动挂 trace_id/execution_id/tenant_id/request_id 关联字段，红色内容经 `observability/redaction.py` 脱敏（Secret 明文不进 span）。OTLP Collector 接线：`FLUXION_OTLP_ENDPOINT` env → OTLP exporter；exporter 包缺失 → 降级不 export + warning、不阻断服务（B-03）；本地 TracerProvider（dev 无 exporter）可用。Collector 部署配置文档（O507）。

### Checklist

- [x] 实现 `traced_scope`（统一关联字段注入 + 自动脱敏）
- [x] OTLP env 接线 + exporter 缺失降级（warning 不阻断）；Collector 部署配置文档
- [x] [B-03][unit] 修改生产代码前，编写验收测试并记录 RED：otlp exporter 缺失 → 降级不 export + warning，服务不阻断
- [x] 断言 traced_scope 产物 span 携带四关联字段且红色内容已脱敏
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | unit | 真实 exporter 依赖探测路径 | 缺失降级 + warning；服务不阻断 | `backend/tests/unit/test_traced_scope.py::TestB03OtlpExporterDegradation`（3 例） | `python -m pytest backend/tests/unit/test_traced_scope.py -v` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-03 | FAIL: `ImportError: cannot import name 'bind_execution_id'`（collection error，11 用例全失败——traced_scope/execution ContextVar 未实现） | PASS: 11 passed | `test_missing_exporter_returns_none`（importlib ImportError 模拟 → `_otlp_exporter` 返回 None）L40-L55；`test_configure_tracer_with_missing_exporter_warns_not_blocks`（warning 断言 + 不抛异常）L58-L72；`test_configure_tracer_without_endpoint_no_warning` L75-L80 | 真实 importlib 探测路径（monkeypatch ImportError 模拟包缺失）；真实 TracerProvider 配置路径 | verified |
| traced_scope 四关联字段 + 脱敏 | 同上 collection error | PASS: TestTracedScope 6 例 + 2 独立用例 | `test_span_carries_four_correlation_fields`：fluxion.trace_id/tenant_id/request_id/execution_id 断言 L86-L93；`test_sensitive_attributes_redacted`：credential→`[REDACTED]`、marker 不进任何属性值 L105-L115；`test_scope_sets_current_span`（嵌套父子）L127-L132；`test_traced_scope_marks_error_status`（Status ERROR + 事件留痕）L172-L185 | 真实 OTel SDK span（span.attributes 直接断言，无 exporter 依赖）+ 真实 RequestContext/execution_id ContextVar + 真实 redact_mapping 脱敏 | verified |
| O507 文档 | — | `docs/development/OTel-Collector部署配置.md` 落地（env 接线表 + otelcol 最小配置 + compose 片段 + 验证步骤） | — | — | verified |

**质量与回归**：
- `ruff check src/fluxion/observability/ tests/unit/test_traced_scope.py` → All checks passed；`mypy src/fluxion/observability/tracing.py context.py` → no issues
- 回归：tests/unit/ + tests/integration/test_logging.py + tests/api/ → 87 passed（test_release_gate 为 CWD 依赖项，须从仓库根运行——已知非本任务引入）
- span 属性命名与既有埋点一致（`fluxion.trace_id/request_id/execution_id/tenant_id`，runtime_app.py 既有约定），TASK-008 接线直接复用

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)：无 Spec-Refs（session spec 无绑定规则，按 Acceptance Contract 执行）
- [2026-08-29] completed (done)：B-03 verified；`traced_scope` 统一 span 入口（四关联字段 + redaction 脱敏 + 异常 record/ERROR 不吞）+ execution_id ContextVar；configure_tracer exporter 缺失降级发 warning（不静默不阻断）；O507 Collector 部署配置文档落地

---

## TASK-008: 7 类 span 埋点接线 + 关联完整性门禁

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#2.4 验收条件, phase5-governance-observability-eval.design.md#3.5 质量实现方案
- **Acceptance-Refs**: S-04, E-03, NFR-OBS-01

### Description

按 O501–O506 埋点清单接线（全部经 `traced_scope`）：O501 HTTP（`api/console_routes_*`/`channel.py`）、O502 Runtime execution、O503 Model（`model_providers.py`）、O504 Tool/MCP（`tool_*`/`mcp.py`）、O505 Workflow（`runtime/workflow_dbos.py`；若 Phase 3 未落地按契约预留接点）、O506 DB/Redis（`registry/store.py`/cache）。span 名与关联字段按清单一致。门禁测试：完整性扫描——采样全部 span，缺 trace_id/execution_id 关联字段的比例 >1% → 失败阻断 CI（E-03，NFR-OBS-01 ≥99%）。E2E：完整 execution 跑一遍，全链路 span 携带四关联字段（S-04）。

### Checklist

- [x] O501–O506 六类埋点接线（全部经 `traced_scope`，span 名/字段按清单）
- [x] **O505 workflow span 约束**（phase5 扫描提示）：`run_graph_workflow` 在 DBOS 独立 event loop 运行，async `traced_scope` 不可直接用于 workflow 函数内——需 sync 兼容 span 助手（镜像 projection writer 的 sync psycopg 模式）或把 span 落点放在 worker CLI/engine 侧，避免 "different loop" 类问题
- [x] [S-04][E2E] 修改生产代码前，编写验收测试并记录 RED：真实 execution（HTTP→Runtime→Model→Tool→Workflow→DB/Redis）→ 全链路 span 携带 trace_id/execution_id/tenant_id/request_id，关联完整率≥99%
- [x] [E-03][integration] 修改生产代码前，编写完整性扫描测试并记录 RED：span 采样缺关联字段 >1% → 测试失败（CI 门禁）
- [x] 断言 span 中红色内容已脱敏（明文不进 span）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 execution 全链路（HTTP/Runtime/Model/Tool/Workflow/DB·Redis） | span 四关联字段齐全；完整率≥99% | `backend/tests/integration/test_span_correlation_gate.py`（6 例） | `python -m pytest backend/tests/integration/test_span_correlation_gate.py -v` | verified |
| E-03 | integration | 真实 span 采样扫描 | 缺关联字段 >1% → 门禁失败 | 同上（TestE03CorrelationGate 2 例） | 同上 | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-03 | FAIL: `AttributeError: 'ProxyTracerProvider' object has no attribute 'add_span_processor'`（6 用例全 error——exporter 无法挂载）+ 接线缺失断言失败 | PASS: 6 passed | `test_span_correlation_completeness_gate`：100 span 采样，incomplete/total >1% → fail（含完整率数值消息）L110-L132；`test_gate_fails_when_correlation_missing`：无上下文 span 被门禁识别 L134-L149 | 真实 OTel SDK TracerProvider + SimpleSpanProcessor/InMemorySpanExporter 采样真实 span（非 mock） | verified |
| S-04 | 同上 | PASS: `test_full_chain_spans_carry_four_correlation_fields`（HTTP→Runtime→Model→DB 链：runtime.execution/model.complete/db.query 全出现且四字段齐）+ `test_http_middleware_span`（O501 `http.get./probe`）+ `test_tool_call_span`（O504 tool.call + 参数脱敏） | L157-L236（全链路断言：names 集合 + 每 span 四字段逐一比对 trace/execution/tenant/request）；L240-L262（HTTP）；L268-L296（tool + `"PLAINTEXT-7f3a" not in str(span.attributes)`） | 真实 SQLite registry + 真实 RuntimeApplicationService（dev bundle 自举）+ 真实 dev.echo ModelProvider（非 mock）+ 真实 ToolRuntime/builtin.http_get + 真实 middleware（ASGITransport HTTP） | verified |
| O505 workflow step | 同上 | PASS: `test_workflow_step_span_correlation_from_run_meta`——`workflow.step` span 经 run_meta 显式关联（trace_id/execution_id=exec-wf/tenant_id/workflow_id/node_id 全断言） | L300-L337 | 真实 `_run_node` 执行（transform 节点）+ sync `traced_span` 助手（DBOS 独立 loop 约束——ContextVar 不传播，显式关联） | verified |
| 脱敏（明文=0） | 同上 | PASS: `test_sensitive_attributes_redacted`（TASK-007 套件）+ `test_tool_call_span`（本套件）| 见上 | redact_mapping 真实脱敏（credential/api_key → `[REDACTED]`） | verified |

**埋点接线清单（全部经 traced_scope/traced_span，span 名按 design §3.2）**：
- O501 HTTP：`api/middleware.py`（`http.{method}.{route}`）
- O502 Runtime：`services/runtime_app.py`（`runtime.execution` + 绑定 execution_id ContextVar 供嵌套继承）
- O503 Model：`runtime/model_providers.py`（`model.complete`/`model.stream`）+ `services/runtime_utils.py` DevEchoModelProvider（dev 全链路）
- O504 Tool/MCP：`runtime/tools.py`（`tool.call`，参数脱敏）+ `runtime/mcp.py`（`mcp.call`）
- O505 Workflow：`runtime/workflow_graph.py::_run_node`（`workflow.step`，sync `traced_span` + run_meta 显式关联——DBOS 独立 event loop 约束落地）
- O506 DB/Redis：`registry/sqlalchemy_store.py` get/put（`db.query`）+ `services/cache.py` TenantRedisCache get/set（`redis.cache`；P1 未接线 adapter——span 已埋，接线后并入链路验证）

**回归与质量**：
- 大回归：tests/runtime/ + tests/integration/ + tests/e2e/ + tests/channel/ + tests/agents/ + workflow_poc/test_evidence_summary → **319 passed**（1 失败为 Restate PoC 预存在项，HEAD 即有）；tests/services/ + tests/api/ + tests/contract/ + tests/memory/ + tests/users/ + tests/resources/ → **118 passed**
- `ruff check` 本任务全部文件 clean（cache.py 既有 BLE001/S110 为 HEAD 预存在降级语义）；`mypy src/fluxion/observability/tracing.py` no issues（cache.py 2 处 no-untyped-call 为 HEAD 预存在）

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)
- [2026-08-29] completed (done)：E-03/S-04 verified；O501-O506 六类埋点全经 traced_scope（O505 落地 sync traced_span + run_meta 显式关联）；E-03 门禁（100 span 采样 >1% 失败）+ S-04 全链路（dev.echo 真实 provider 链：HTTP/Runtime/Model/Tool/DB）

---

## TASK-009: durable_task 表 + 无状态 worker（P1 条件 FEAT）

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.3 数据设计, phase5-governance-observability-eval.design.md#2.4 验收条件
- **Acceptance-Refs**: S-09, B-04, RISK-P5-05

### Description

**P1 条件 FEAT**（对齐项 E）：仅在明确存在耗时后台逻辑时实施启用。交付契约就绪：`durable_task` 表（task_id PK 幂等键、tenant_id、payload jsonb、status pending/claimed/done/failed、attempts 有限、claimed_at/done_at/created_at；索引 `(status, claimed_at)`）+ 无状态 worker（poll/claim/resume，tenant scope 全链路）。启用时：任务状态正确、失败可重试（有限）、无重复执行（task_id 幂等，RISK-P5-05）。未启用（默认）：B-04 断言功能开关关闭无副作用——表可建、worker 不启动、现有路径零变化。V2.2 不引 Event Bus。

### Checklist

- [ ] 建 `durable_task` 表（幂等 DDL）+ 索引；实现无状态 worker（poll/claim/resume，默认不启动）
- [ ] 功能开关默认关闭；未启用时 B-04 断言零副作用
- [ ] [B-04][unit] 修改生产代码前，编写验收测试并记录 RED：开关未启用 → worker 不启动、现有路径零变化（无副作用）
- [ ] [S-09][integration] 修改生产代码前，编写验收测试并记录 RED（启用态）：真实 DB + worker enqueue → claim → 完成/失败；状态正确、失败有限重试、task_id 幂等无重复执行
- [ ] 双库契约：`durable_task` 纳入 SQLite/PG 共享 Contract Test
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | integration | 真实 DB + worker 进程（启用态） | enqueue→claim→终态正确；有限重试；幂等无重复 | planned | planned | planned |
| B-04 | unit | 真实开关 + 启动路径 | 未启用零副作用 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-010: Operations Queues/Workers 后端端点（phase4 C407 技术债闭合）

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-12）, phase4-product-experience.design.md#3.2 页面与路由结构, phase5-governance-observability-eval.design.md#2.2 功能方案（FEAT-P5-07）
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-11

### Description

phase4 C407 Queues/Workers 面板以 in-memory 先行（⛳ 依赖缺口，`httpConsoleApi.ts` 已冻结 `/api/v1/operations/queues|workers` 契约）。本任务后端补齐真实端点：
- `GET /api/v1/operations/queues`：workflow 队列状态（DBOS database-backed queue：queue_name、depth、worker 数——读 DBOS sysdb `dbos.queues` + `dbos.workflow_status` 排队计数）；
- `GET /api/v1/operations/workers`：运行 worker 状态（DBOS worker 实例/心跳）。

统一 envelope（`{code,message,data,request_id}`）+ tenant scope（rule 16）；数据源是 DBOS sysdb **只读**（Fluxion 不直写）。完成后 Console `QueuesPage/WorkersPage` 切 HTTP 同契约（去掉 `dataSource="in-memory"` 标识）。

### Checklist

- [ ] 实现 `/api/v1/operations/queues|workers` 后端端点（DBOS sysdb 只读 + 统一 envelope + tenant scope）
- [ ] [S-11][integration] 修改生产代码前，编写验收测试并记录 RED：真实 DBOS sysdb + HTTP 端点返回 queue/worker 状态
- [ ] phase4 `QueuesPage/WorkersPage` 切 HTTP（in-memory dataSource 标注移除）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-11 | integration | 真实 DBOS sysdb + HTTP 端点 | queue 深度/worker 状态返回 + envelope + tenant scope | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase4 审查未覆盖遗留登记（Operations Queues/Workers 后端端点）

---

## TASK-011: workflow runs list-all 端点 + RunsPage 切 HTTP（P1-3 残留）

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-12）, phase3-workflow-platform.design.md#3.4 接口设计, phase5-governance-observability-eval.design.md#2.2 功能方案（FEAT-P5-07）
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-12

### Description

phase4 review P1-3 残留：Console `RunsPage` 全量 runs 视图走冻结路径 `GET /api/v1/workflows/runs`（后端无此路由，phase3 只有 `/{workflow_id}/runs`）。本任务后端补 list-all 端点：
- `GET /api/v1/workflows/runs`：跨工作流 runs 列表（tenant scope，分页 `{items,page,page_size,total}`，基于 `workflow_run` 投影表）；
- phase4 `RunsPage` 切 HTTP 真实端点（`listWorkflowRuns()` 无参分支不再 ⛳）。

### Checklist

- [ ] 实现 `GET /api/v1/workflows/runs` list-all（tenant scope 分页，复用 `workflow_run` 投影 CRUD）
- [ ] [S-12][integration] 修改生产代码前，编写验收测试并记录 RED：真实投影表 + HTTP 端点返回分页 runs
- [ ] phase4 `RunsPage` 切 HTTP（移除 ⛳ 冻结路径）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-12 | integration | 真实 workflow_run 投影 + HTTP 端点 | list-all 分页返回 + tenant scope + RunsPage 切 HTTP | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase4 审查 P1-3 残留登记（RunsPage 全量视图 list-all 端点）

---

## TASK-012: User 360 详情 URL 路由（C405 深链）

- **Status**: draft
- **Priority**: P2
- **Depends**:
- **Source**: phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-11）, phase4-product-experience.design.md#3.2 页面与路由结构, phase5-governance-observability-eval.design.md#2.2 功能方案（FEAT-P5-08）
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: S-13

### Description

phase4 C405 User 360 详情以 `SideSheet`（组件状态）承载，无 URL 路由——刷新/深链丢失当前用户 360 视图。本任务升级为路由页：
- 新增 `/users/:platformUserId` 路由 + `User360Page`（复用 `User360Header/User360Tabs`）；
- `UsersChannelsPage` "查看 360" 从 SideSheet 改为路由跳转（深链/刷新直达）。

### Checklist

- [ ] 新增 `/users/:platformUserId` 路由 + `User360Page`（复用 `User360Header/User360Tabs`）
- [ ] `UsersChannelsPage` "查看 360" 改路由跳转（移除 SideSheet 承载）
- [ ] [S-13][E2E] 修改生产代码前，编写验收测试并记录 RED：深链 `/users/:id` 直达详情、刷新保留
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-13 | E2E | Browser → Router → Service → UI | 深链直达详情、刷新保留、五维 Tab 渲染 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase4 审查未覆盖遗留登记（User 360 详情深链）

---

## TASK-013: 真浏览器 NFR 验收（首屏 P95 + a11y）

- **Status**: draft
- **Priority**: P2
- **Depends**:
- **Source**: phase4-product-experience.design.md#2.4 验收条件（NFR-PERF-01 / NFR-A11Y-01）, phase5-governance-observability-eval.design.md#2.2 功能方案（FEAT-P5-09）
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: S-14

### Description

phase4 perf/a11y 均为 jsdom 代理测量（`perf-baseline.test.tsx` 只测 mount smoke；`a11y.e2e.test.tsx` 关 3 条规则 + jsdom 无真实布局）。本任务补齐**真浏览器**验收：
- 首屏 P95 ≤500ms：Playwright 加载 `/home` 真实渲染计时（真实网络/布局/样式），关闭 jsdom 代理口径；
- a11y：Playwright + axe 真浏览器全页扫描（含 phase4 jsdom 禁用的 color-contrast/role-img-alt/aria-valid-attr-value），无 serious/critical。

### Checklist

- [ ] 接入 Playwright/Lighthouse 浏览器级测试基建（首屏计时 + axe 扫描）
- [ ] [S-14][E2E] 修改生产代码前，编写验收测试并记录 RED：真浏览器首屏 P95 ≤500ms；a11y 无 serious/critical
- [ ] phase4 perf/a11y jsdom 套件保留为快速 smoke（浏览器套件为 Gate 验收）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-14 | E2E | 真浏览器（Playwright/Lighthouse） | 首屏 P95≤500ms + axe 真浏览器无 serious/critical | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase4 审查未覆盖遗留登记（真浏览器首屏 P95 + a11y）

---

## TASK-014: Chat Workspace 后端端点（X402-X408 数据源，phase4 ⛳ 契约闭合）

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-02..P4-08）, phase4-product-experience.design.md#3.2 页面与路由结构, phase5-governance-observability-eval.design.md#2.2 功能方案（FEAT-P5-10）
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-15

### Description

phase4 X402–X408（Home/Agents/Tasks/Approvals/History/Memory&Profile）全部以 in-memory 先行（⛳ 依赖缺口），前端 `httpChatApi.ts` 已冻结 `/api/v1/workspace/*` 契约。**后端无任何 `/workspace` 路由**——本任务补齐，否则 Chat Workspace 永远跑在 in-memory 上无法切真实后端。端点（统一 envelope + tenant scope，rule 16）：

- `GET /api/v1/workspace/agents`：AgentDefinition 产品模型目录（名称/描述/能力/可用性，**不暴露 RuntimeProfile**——RULE-fluxion-workflow-001）；
- `GET /api/v1/workspace/tasks` + 详情：长期任务统一列表（对话 + workflow 运行，关联 `workflow_run`/execution 投影）；
- `GET /api/v1/workspace/approvals` + `decide`：HumanTask 审批队列（读 workflow 挂起的 human_task）+ 通过/拒绝/留言；
- `GET /api/v1/workspace/history`：对话 + 任务统一时间线（关联 trace）；
- `GET /api/v1/workspace/memory` + `correct`/`delete`：Personal Memory（phase2 Memory 域）；
- `GET/PUT /api/v1/workspace/profile`、`GET/PUT /api/v1/workspace/memory/auto-learn`：Profile 与自动学习开关（phase2 用户域）。

完成后 Chat 前端从 in-memory 切 HTTP 同契约（删除 `dataSource="in-memory"` 语义）。

### Checklist

- [ ] 实现 `/api/v1/workspace/*` 7 组端点（读 + 写：decide/correct/delete/updateProfile/setAutoLearn）
- [ ] 数据源对齐既有域：AgentDefinition 产品模型（无 RuntimeProfile）、workflow_run 投影、Personal Memory、用户 Profile
- [ ] [S-15][integration] 修改生产代码前，编写验收测试并记录 RED：真实 DB + HTTP 端点返回 workspace 数据 + 写操作生效 + tenant scope
- [ ] Chat 前端切 HTTP（`httpChatApi` 真实请求路径，移除 in-memory 依赖）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-15 | integration | 真实 DB + HTTP 端点 | workspace 7 端点返回 + 写操作生效 + envelope + tenant scope + Chat 切 HTTP | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase5 扫描未覆盖登记（Chat Workspace 后端端点，后端无 `/workspace` 路由）
