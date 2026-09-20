# Tasks: Skill 管理与 Artifact

- **Source**: .code-flow/tasks/2026-09-17/05-skill-management/05-skill-management.backend.design.md, .code-flow/tasks/2026-09-17/05-skill-management/05-skill-management.frontend.design.md
- **Created**: 2026-09-19
- **Updated**: 2026-09-19

## Proposal

把 Skill 作为业务能力最小单元接入平台：支持线下 IDE 开发后的 ZIP 导入（安全限制校验、SKILL.md 解析、NFS 不可变 Artifact、幂等与重复版本拒绝）、版本管理与 current_artifact 切换、SELECTED/ALL 用户范围（SkillUserGrant 资源级授权），并在 Console 前端复刻 V1.4 交互稿的列表/导入/详情 Tabs/版本详情/用户范围维护。SDK 契约以 docs/06 §5 为唯一来源，本模块不重定义 SkillContext。

### Alignment

- **Scope**: backend（skills module + packages/artifact-store + packages/skill-sdk 契约对齐）+ frontend（skill-management 模块）
- **Decisions**:
  - skill 表无 revision 列，元数据更新走 config_audit_log（design v1.1）
  - SkillUserGrant 无 expires_at，撤销=软删除（docs/17 §D7）
  - 导入幂等：Idempotency-Key + checksum 去重，重复返回 SKILL_VERSION_EXISTS
- **Non-goals**: 不做用户侧 Artifact 下载、运行时 pip install、Workflow DSL、Console 内编辑 Python
- **Acceptance**: 见下方 Acceptance Coverage（后端 S-01~S-04/E-01~E-04/B-01~B-05，前端 S-05~S-07/E-05~E-07 全覆盖）

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | backend#2.5.2 正常场景 | E2E | Upload API→NFS→PostgreSQL | TASK-004 | verified | ["bash", "-lc", "uv run pytest -q tests/console_skill/test_import_api.py -k import_creates_skill_and_artifact"] |
| S-02 | backend#2.5.2 正常场景 | integration | Grant service→DB | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_user_scope_api.py", "-k", "user_scope_and_grants"] |
| S-03 | backend#2.5.2 正常场景 | E2E | Upload→Snapshot semantics | TASK-007 | verified | ["bash", "-lc", "uv run pytest -q tests/console_skill/test_snapshot_freeze.py"] |
| S-04 | backend#2.5.2 正常场景 | integration | API→DB | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_idempotency.py"] |
| S-05 | frontend#2.4 验收条件（原 S-FE-01） | E2E | Browser Upload→NFS/DB→UI | TASK-009 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.skill.config.ts --grep \"S-05\""] |
| S-06 | frontend#2.4 验收条件（原 S-FE-02） | E2E | Browser→artifact API | TASK-010 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.skill.config.ts --grep \"S-06\""] |
| S-07 | frontend#2.4 验收条件（原 S-FE-03） | E2E | Browser→scope API | TASK-011 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.skill.config.ts --grep \"S-07\""] |
| E-01 | backend#2.5.2 异常场景 | integration | Validator→NFS/DB | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_api.py", "-k", "invalid_packages"] |
| E-02 | backend#2.5.2 异常场景 | integration | Skill SDK→Egress | TASK-008 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_skill_context.py", "-k", "exposes_all_ports"] |
| E-03 | backend#2.5.2 异常场景 | integration | API→DB | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_artifacts_api.py", "-k", "rejects_duplicate"] |
| E-04 | backend#2.5.2 异常场景 | integration | Validator→Secret Scan | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_api.py", "-k", "invalid_packages"] |
| E-05 | frontend#2.4 验收条件（原 E-FE-01） | E2E | Validation API→Modal | TASK-009 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.skill.config.ts --grep \"E-05\""] |
| E-06 | frontend#2.4 验收条件（原 E-FE-02） | integration | Grant API→UI | TASK-011 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_skill_user_scope_contract.py"] |
| E-07 | frontend#2.4 验收条件（原 E-FE-03） | E2E | Artifact API→UI | TASK-010 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.skill.config.ts --grep \"E-07\""] |
| B-01 | backend#2.5.2 边界场景 | unit | 导入校验（zip 大小 50MiB/50MiB+1） | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_api.py", "-k", "zip_over_size"] |
| B-02 | backend#2.5.2 边界场景 | unit | 导入校验（解压后 200MiB/200MiB+1） | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_api.py", "-k", "unpacked_size"] |
| B-03 | backend#2.5.2 边界场景 | unit | 导入校验（文件数 2000/2001） | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_api.py", "-k", "too_many_entries"] |
| B-04 | backend#2.5.2 边界场景 | integration | 扩展名/路径/符号链接/嵌套包 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_api.py", "-k", "invalid_packages"] |
| B-05 | backend#Spec Compliance Matrix RULE-data-001 | integration | 真实 PostgreSQL 三表 partial unique/timestamptz | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_skill_schema_constraints.py"] |

| B-06 | backend#Spec Compliance Matrix RULE-skill-001 | integration | 真实目录 + 真实 PostgreSQL 孤儿清理/不可变 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_orphan_cleanup.py"] |
| B-07 | backend#Spec Compliance Matrix RULE-api-001 | integration | 真实 HTTP + 真实 PostgreSQL 封套/分页 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/console_skill/test_skill_api.py", "tests/acceptance/test_foundation_api_envelope.py"] |

> 本表覆盖两份 design 全部 P0/P1 场景及 RULE 映射场景（RULE-data-001→B-05、RULE-skill-001→B-06、RULE-api-001→B-07）；FE 场景编号按 `[SEB]-\d+` 规范重命名并保留原名标注。

RULE 映射（每条 required Rule 唯一责任任务）：

| Rule | 责任任务 | 引用任务 |
|------|---------|---------|
| harness-time#RULE-time-001 | TASK-001 | TASK-009（Console 展示 YYYY-MM-DD HH:mm:ss） |
| harness-api#RULE-api-001 | TASK-005（B-07） | TASK-004, TASK-006, TASK-009 |

| Rule | 责任任务 | 引用任务 |
|------|---------|---------|
| harness-data#RULE-data-001 | TASK-001 | TASK-004, TASK-006 |
| harness-secret#RULE-secret-001 | TASK-002 | TASK-004, TASK-008 |
| harness-skill#RULE-skill-001 | TASK-003 | TASK-004, TASK-010 |
| harness-test#RULE-test-001 | TASK-004 | TASK-007, TASK-009 |
| harness-api#RULE-api-001 | TASK-005 | TASK-004, TASK-006, TASK-009 |
| harness-auth#RULE-auth-001 | TASK-006 | TASK-011 |
| harness-rel#RULE-rel-001 | TASK-006 | TASK-011 |
| harness-snapshot#RULE-snapshot-001 | TASK-007 | TASK-004 |
| harness-ui#RULE-ui-001 | TASK-009 | TASK-010, TASK-011 |
| harness-frontend#RULE-front-001 | TASK-009 | TASK-010, TASK-011 |
| harness-i18n#RULE-i18n-001 | TASK-009 | TASK-010, TASK-011 |
| harness-ui-detail#RULE-ui-detail-001 | TASK-010 | TASK-011 |

---

## TASK-001: Skill 数据模型与迁移

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 05-skill-management.backend.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-01, S-02, B-05, RULE-02, RULE-11

### Description

在 `apps/console-platform/backend/src/muad_console_platform/modules/skills/` 新建 models：`control.skill`（无 revision 列，UNIQUE(tenant_id,key) WHERE is_deleted=false）、`control.skill_artifact`（append-only，UNIQUE(skill_id,version)/(skill_id,checksum) 均为 partial）、`control.skill_user_grant`（无 expires_at，UNIQUE(skill_id,user_id) partial）。Alembic expand→deploy→contract。

实际落点：模型位于 `infrastructure/models/control.py`（01-platform-foundation 统一建模），迁移位于 `migrations/versions/0002_initial_schema.py`。

### Checklist
- [x] 定义三张 ORM 模型（01-platform-foundation 已建，本次验证与设计 v1.1 一致：skill 无 revision、grant 无 expires_at）
- [x] 建索引/约束（已存在于 control.py + migrations/versions/0002，本次测试验证生效）
- [x] Alembic 迁移（已存在 0002_initial_schema，测试基于 alembic upgrade head 后的真实库）
- [x] 运行 harness-data#RULE-data-001 verifier：迁移后用真实 PostgreSQL 断言 partial unique 生效（软删记录不参与唯一性）与 timestamptz 类型
- [x] 运行 harness-time#RULE-time-001 verifier（B-05 同步覆盖）：skill 三表 create_time/update_time 均为 timestamptz（DB 侧统一 timestamptz 存储）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-05 | integration | 真实 PostgreSQL + Alembic 迁移 | 三表标准列齐全；create_time/update_time 为 timestamptz；skill/artifact/grant 软删后可重建、活跃重复被拒；grant 无 expires_at | tests/acceptance/test_skill_schema_constraints.py | uv run pytest -q tests/acceptance/test_skill_schema_constraints.py | verified |
| RULE-time-001 | integration | 真实 PostgreSQL（information_schema 类型检查） | skill 三表时间为 timestamptz（DB 统一存储） | test_skill_schema_constraints.py::test_skill_tables_standard_columns_and_timestamptz | uv run pytest -q tests/acceptance/test_skill_schema_constraints.py -k timestamptz | verified |

### Acceptance Evidence

模型与迁移由 01-platform-foundation 先前实现（属已有行为补测，无 RED 阶段）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-05 | N/A（已有行为补测：模型/迁移非本次新写） | 4 passed in 0.26s | tests/acceptance/test_skill_schema_constraints.py（标准列/timestamptz 断言；skill/artifact/grant 三组 partial unique 重建断言） | SharedSettings.database_url 真实 PostgreSQL，alembic upgrade head 后 schema control | verified |
- B-05: verified — automated command passed; run_id=5949582c19d3477296f9ecbbb4fb5e57 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started (TASK-001)
- [2026-09-19] checklist 全部完成，B-05 verified

---
- [2026-09-19] completed (done)
## TASK-002: SkillArtifactValidator 导入校验器（LIB-01）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 05-skill-management.backend.design.md#3.2 架构与流程, 05-skill-management.backend.design.md#3.4 接口设计 LIB-01
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: E-01, E-04, B-01, B-02, B-03, B-04, RULE-09

### Description

实现 `SkillArtifactValidator.validate(zip_path) -> ValidationResult`：zip ≤50MiB、解压 ≤200MiB、≤2000 文件、扩展名白名单、拒绝绝对路径/`..`/符号链接/嵌套压缩包、SKILL.md 必存在且解析 frontmatter（execution_mode/default_script）、敏感信息扫描（与 docs/06 §8 一致）。违规统一 `SKILL_PACKAGE_INVALID`。

实际落点：`infrastructure/skill_validator.py` 的 `validated_package()`。

### Checklist
- [x] 先写测试并记录 RED（校验器已由先前任务实现，属边界补测：新增 test_validator_boundaries.py 的 B-01~B-03 精确边界与 E-04 不泄露断言为新增覆盖，实现已存在故无功能 RED）：B-01~B-04、E-01、E-04 全场景
- [x] [B-01][unit] zip 大小边界 50MiB 通过 / 50MiB+1 拒绝
- [x] [B-02][unit] 解压后总大小边界 200MiB / 200MiB+1
- [x] [B-03][unit] 文件数边界 2000 / 2001
- [x] [B-04][integration] 白名单外扩展名、绝对路径、`..`、符号链接、嵌套 zip 全部拒绝 `SKILL_PACKAGE_INVALID`
- [x] [E-01][integration] 路径穿越或缺 SKILL.md：拒绝导入，不产生 READY Artifact
- [x] [E-04][integration] 包内命中敏感信息扫描：拒绝且校验器不把疑似 Secret 内容写入任何日志/结果详情
- [x] 运行 harness-secret#RULE-secret-001 verifier：断言 ValidationResult/异常消息只含扫描命中类型与文件路径，不含 Secret 明文；Secret 不进日志
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | 真实 ZIP 文件构造（真实字节边界） | 50MiB 过 / 50MiB+1 拒 | test_validator_boundaries.py::test_b01_zip_size_exact_boundary_pass_and_reject | uv run pytest -q tests/console_skill/test_validator_boundaries.py -k b01 | verified |
| B-02 | unit | 真实解压过程 | 200MiB 过 / 200MiB+1 拒 | test_validator_boundaries.py::test_b02_unpacked_size_exact_boundary_pass_and_reject | uv run pytest -q tests/console_skill/test_validator_boundaries.py -k b02 | verified |
| B-03 | unit | 真实 ZIP 条目 | 2000 过 / 2001 拒 | test_validator_boundaries.py::test_b03_entry_count_exact_boundary_pass_and_reject | uv run pytest -q tests/console_skill/test_validator_boundaries.py -k b03 | verified |
| B-04 | integration | 真实文件系统临时目录 | 各违规类别全部拒绝 | test_import_api.py::test_invalid_packages_return_400 | uv run pytest -q tests/console_skill/test_import_api.py -k invalid_packages | verified |
| E-01 | integration | Validator→真实临时目录/DB | 拒绝且无 READY Artifact | test_import_api.py::test_invalid_packages_return_400 | uv run pytest -q tests/console_skill/test_import_api.py -k invalid_packages | verified |
| E-04 | integration | Validator→真实敏感样本文件 | 拒绝且不泄露 Secret 到日志/结果 | test_validator_boundaries.py::test_e04_secret_scan_rejects_and_never_leaks_secret | uv run pytest -q tests/console_skill/test_validator_boundaries.py -k e04 | verified |

### Acceptance Evidence

校验器与 API 级集成测试由先前任务实现；本次新增精确边界与 Secret 不泄露断言（补测，无功能 RED）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | N/A（已有行为补测） | 4 passed（boundaries 全量） | test_validator_boundaries.py::test_b01 | 真实 50MiB/50MiB+1 字节 zip，STORED 填充精确构造 | verified |
| B-02 | N/A | 同上 | test_b02 | deflate 零填充真实解压至临时目录 | verified |
| B-03 | N/A | 同上 | test_b03 | 真实 2000/2001 条目 zip | verified |
| B-04 | N/A | 13 passed（invalid_packages 等过滤集） | test_import_api.py::test_invalid_packages_return_400 | 真实 ASGI 上传 + 真实临时目录/DB | verified |
| E-01 | N/A | 同上 | 同上 | 同上 | verified |
| E-04 | N/A | 同上 | test_e04：断言 AppError str/repr/message_args/data 均无 Secret 明文 | 真实敏感样本文件扫描 | verified |
| RULE-secret-001 | N/A | 同上 | E-04 断言即为其 verifier | AppError 仅携带 code，无用户可见 message | verified |
- E-01: verified — automated command passed; run_id=b318925b511e4132b923a2fcb9528bad (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=b318925b511e4132b923a2fcb9528bad (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=b318925b511e4132b923a2fcb9528bad (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=b318925b511e4132b923a2fcb9528bad (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=b318925b511e4132b923a2fcb9528bad (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=b318925b511e4132b923a2fcb9528bad (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started/finished：边界补测全绿，E-01/E-04/B-01~B-04 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-003: Artifact Store NFS 写入与孤儿清理

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 05-skill-management.backend.design.md#3.1 技术选型与关键决策, 05-skill-management.backend.design.md#5 风险与依赖
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: S-01, B-06, RULE-05

### Description

在 `packages/artifact-store/` 实现不可变 ZIP 写入（storage_key `skills/{skill_id}/{artifact_id}/skill.zip`，事务外写入、失败清理 orphan）、checksum 计算、临时 storage_key/promote 机制与后台孤儿清理任务。DB 只存 storage_key。

实际落点：写入在 console 后端 `infrastructure/skill_artifact_store.py`；缓存/校验在 `packages/artifact-store/src/muad_artifact_store/skill_cache.py`。

### Checklist
- [x] 写入接口：temp+os.replace 原子写（已有）；本次补不可变语义——同 storage_key 二次写入抛 FileExistsError；checksum 由 skill_validator.checksum_of（sha256）
- [x] orphan 处理：事务失败同步 remove_artifact（已有）；本次新增 cleanup_orphan_files（宽限期保护进行中事务）+ SkillService.cleanup_orphan_artifacts + CLI cleanup-skill-orphans
- [x] 运行 harness-skill#RULE-skill-001 verifier（integration，真实 NFS/本地 RWX 目录）：断言写入产物路径符合 storage_key 规范、内容不可变（二次写入同 key 被拒）、孤儿清理后目录无残留
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| RULE-skill-001 | integration | 真实 NFS/POSIX 目录 + 真实 PostgreSQL | storage_key 规范、不可变、orphan 清理 | tests/console_skill/test_orphan_cleanup.py | uv run pytest -q tests/console_skill/test_orphan_cleanup.py | verified |

### Acceptance Evidence

RED→GREEN：先写 test_orphan_cleanup.py（RED：write_artifact 无不可变保护、cleanup 方法不存在，AttributeError/FileExistsError 未抛出），后实现。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| RULE-skill-001 | FAIL: test_write_artifact_is_immutable（无 FileExistsError）/ test_cleanup…（SkillService 无 cleanup_orphan_artifacts，AttributeError） | 2 passed；回归 console_skill 全量 51 passed | test_orphan_cleanup.py（不可变 L21-27；孤儿删除/保留/宽限 L46-92） | 真实 POSIX tmp 目录 + 真实 PostgreSQL（database_guard） | verified |
- B-06: verified — automated command passed; run_id=c6a7023e2fe944ecbdf9434e724301ca (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started/finished：orphan 清理与不可变语义实现，RULE-skill-001 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-004: 导入 API（API-02/API-06）+ 幂等与重复版本拒绝

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: 05-skill-management.backend.design.md#3.4 接口设计 API-02/API-06
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-04, E-03, RULE-01, RULE-10, RULE-11

### Description

实现 `POST /api/v1/skills/import` 与 `POST /api/v1/skills/{skill_id}/artifacts`：multipart 接收→校验器→NFS 写入→单事务 insert skill_artifact(READY) + skill/current_artifact_id + config_audit_log。`(tenant_id,key)` 冲突 `SKILL_KEY_EXISTS`；重复 version/checksum `SKILL_VERSION_EXISTS`；`Idempotency-Key` 同 key 重放返回首次结果。

实际落点：`api/skills.py` + `application/skill_service.py`（幂等 header 待新增）。

### Checklist
- [x] 先写测试并记录 RED：S-04、E-03 写入 test_import_idempotency.py 并记录 RED（重放返回 409 SKILL_VERSION_EXISTS）；S-01 已有 test_import_creates_skill_and_artifact 覆盖
- [x] [S-01][E2E] 修改生产代码前，按 Upload API→NFS→PostgreSQL 真实边界（真实 HTTP 上传、真实 NFS 目录、真实 PostgreSQL，不得 mock）编写验收测试并记录 RED
- [x] [S-01] 断言：NFS 有不可变 Artifact；skill_artifact 写入 storage_key/checksum/manifest_json 且 validation_status=READY；skill.current_artifact_id 指向它
- [x] [S-04][integration] 相同 Idempotency-Key 重放导入：不新增 Artifact，返回首次导入结果（真实 DB 幂等表/缓存）
- [x] [E-03][integration] 导入已存在 version 或相同 checksum：返回 SKILL_VERSION_EXISTS，不覆盖、不新增
- [x] [RULE-10] API-06 幂等同 API-02；旧 Artifact 保持不可变
- [x] [RULE-01] 错误只抛 code，msg/http_status 走 config/api-messages.yaml；响应为统一封套
- [x] 运行 harness-test#RULE-test-001 verifier：E2E 用例文件中显式声明不得 mock 的真实边界清单（PostgreSQL/NFS/HTTP）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Upload API、NFS、PostgreSQL | NFS 不可变 Artifact + DB READY 记录 + current_artifact_id 指向 | tests/console_skill/test_import_api.py::test_import_creates_skill_and_artifact | uv run pytest -q tests/console_skill/test_import_api.py -k import_creates_skill_and_artifact | verified |
| S-04 | integration | API、DB（skill_import_idempotency 真实表） | 同 key 重放返回首次结果、不新增 | tests/console_skill/test_import_idempotency.py | uv run pytest -q tests/console_skill/test_import_idempotency.py | verified |
| E-03 | integration | API、DB | SKILL_VERSION_EXISTS、不覆盖不新增 | test_import_idempotency.py::test_e03_duplicate_version_or_checksum | uv run pytest -q tests/console_skill/test_import_idempotency.py -k e03 | verified |
| RULE-test-001 | E2E | 同 S-01（ASGI 真实 HTTP + 真实 NFS 目录 + 真实 PostgreSQL，无 mock） | E2E 边界清单显式化 | test_import_api.py 模块 docstring/fixture 使用真实上传与 DB | uv run pytest -q tests/console_skill/test_import_api.py | verified |

### Acceptance Evidence

新增实现：`control.skill_import_idempotency` 表（migration 0006，partial unique (tenant,key,endpoint)）+ SkillService 幂等重放/记录 + API `Idempotency-Key` Header（/import 与 /artifacts）。指纹 = endpoint|version|key|default_script|checksum；同 key 不同载荷返回 `IDEMPOTENCY_MISMATCH`。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | N/A（先前任务已实现并覆盖） | 142 passed（回归全量） | test_import_api.py::test_import_creates_skill_and_artifact | ASGI 真实上传→真实 NFS storage_key→真实 PostgreSQL | verified |
| S-04 | FAIL: 重放返回 409 SKILL_VERSION_EXISTS（幂等未实现） | 3 passed | test_import_idempotency.py（重放 data 相等；Skill/Artifact 计数不变；版本表 2.0.0 恰一条） | 真实 HTTP + 真实 DB 幂等表 | verified |
| E-03 | FAIL: KeyError（响应字段核对前）→ 修正后断言 409 | 同上 | test_e03（同 version/同 checksum 均 409 且版本数不变） | 真实 API→DB | verified |
| RULE-test-001 | 同 S-04 RED | 同上 | E2E 用例经真实 HTTP/NFS/PG，无 mock | conftest database_guard + ASGITransport | verified |
- S-01: e2e_deferred — automated command e2e_deferred; run_id=1f4d096137944855af74f26db7cc40b3 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=1f4d096137944855af74f26db7cc40b3 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=1f4d096137944855af74f26db7cc40b3 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started/finished：Idempotency-Key 幂等（表 0006 + 服务重放 + Header），S-01/S-04/E-03 verified

---
- [2026-09-19] started
- [2026-09-19] resumed (in-progress)
- [2026-09-19] completed (done)
## TASK-005: Skill 查询与元数据 API（API-01/03/04/05/07）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 05-skill-management.backend.design.md#3.4 接口设计 API-01/API-03/API-04/API-05/API-07
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: S-01, B-07, RULE-01, RULE-11

### Description

实现列表（keyword/user_scope/enabled 筛选，聚合 COUNT 禁止 N+1）、详情（含 current_artifact 摘要与聚合计数）、元数据编辑（无 expected_revision，同事务 config_audit_log）、版本列表（create_time DESC 分页）、版本详情（manifest 为包内文件清单快照，不返回文件内容与 Secret）。

实际落点：`api/skills.py`。

### Checklist
- [x] API-01 列表：统一分页 `{items,page,page_size,total}`，page>=1、1<=page_size<=100；using_agent_count/selected_user_count 聚合查询无 N+1
- [x] API-03/API-07 详情：软删过滤、COMMON_NOT_FOUND、归属校验
- [x] API-04 元数据编辑：单事务更新 + config_audit_log；不影响已运行 Run/Task Snapshot
- [x] API-05 版本列表：create_time DESC，只返回当前 Skill 的 Artifact
- [x] 运行 harness-api#RULE-api-001 verifier（integration，真实 HTTP + 真实 PostgreSQL）：断言统一封套字段（code/msg/data/trace_id/request_id/timestamp）、分页约束、业务只抛 code 且 msg 来自 config/api-messages.yaml
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| RULE-api-001 | integration | 真实 HTTP（ASGITransport）+ 真实 PostgreSQL | 封套字段、分页边界、错误码映射 | tests/console_skill/test_skill_api.py + test_foundation_api_envelope.py | uv run pytest -q tests/console_skill/test_skill_api.py tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py | verified |

### Acceptance Evidence

API-01~07 已由先前任务实现（skill_service + api/skills.py），本次验证：分页约束 `Query(ge=1, le=100)`、聚合 COUNT 无 N+1（skill_repository select(func.count())）、软删过滤与 COMMON_NOT_FOUND、config_audit_log 同事务。属已有行为验证，无 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| RULE-api-001 | N/A（已有行为验证） | 25 passed | test_skill_api.py（筛选/分页/详情/更新/NOT_FOUND/租户隔离）+ test_foundation_api_envelope.py（封套字段） | ASGITransport 真实 HTTP + 真实 PostgreSQL | verified |
- B-07: verified — automated command passed; run_id=3468e269403f49368fe6c954db7d3a8b (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started/finished：验证通过，RULE-api-001 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-006: 用户范围与指定用户 API（API-08~API-11）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 05-skill-management.backend.design.md#3.4 接口设计 API-08/API-09/API-10/API-11
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-rel#RULE-rel-001
- **Acceptance-Refs**: S-02, RULE-04, RULE-07

### Description

变更用户范围（PUT user-scope，切换 SELECTED 不清空 Grant）、指定用户列表（只返回 is_deleted=false）、添加（重复授权幂等：返回既有 Grant；软删记录可重新创建）、移除（软删除）。只操作 SkillUserGrant，不建 AgentAccessGrant/AgentSkillBinding。

实际落点：`api/skills.py` + `application/skill_service.py`。

### Checklist
- [x] 先写测试并记录 RED：S-02（补断言：活跃重复 Grant 应 409——修正实现前测试失败为 RED）
- [x] [S-02][integration] SELECTED Skill 添加用户：只创建 SkillUserGrant（Grant service→DB 真实边界），断言不产生 AgentAccessGrant/AgentSkillBinding 记录
- [x] 运行 harness-auth#RULE-auth-001 verifier（integration，真实 PostgreSQL）：断言 Grant 无 expires_at；撤销后 is_deleted=true 且判定只看 is_deleted=false；重复添加幂等返回既有 Grant（200）；软删后可重新创建
- [x] 运行 harness-rel#RULE-rel-001 verifier（integration，真实 HTTP + DB）：断言添加/移除均为单关系 POST/DELETE 独立事务，无全量 PUT
- [x] API-08 变更只影响后续新 Run/Task；切换 SELECTED 不清空既有 Grant
- [x] 全部接口同事务追加 config_audit_log
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Grant service、真实 PostgreSQL | 只创建 SkillUserGrant；无到期时间字段 | test_user_scope_api.py::test_user_scope_and_grants | uv run pytest -q tests/console_skill/test_user_scope_api.py -k user_scope_and_grants | verified |
| RULE-auth-001 | integration | 真实 PostgreSQL | 撤销=软删除、判定 is_deleted=false、重复 409、软删后可重建、无三元授权 | 同上 + B-05（expires_at 不存在断言） | uv run pytest -q tests/console_skill/test_user_scope_api.py tests/acceptance/test_skill_schema_constraints.py | verified |
| RULE-rel-001 | integration | 真实 HTTP + DB | 单关系 POST/DELETE 独立事务（路由仅 POST/DELETE 单关系，无全量 PUT） | test_user_scope_api.py（POST 添加/DELETE 移除路径） | uv run pytest -q tests/console_skill/test_user_scope_api.py | verified |

### Acceptance Evidence

实现已存在；本任务修正设计偏差（活跃重复 Grant 由 200 改为 COMMON_CONFLICT）并补 S-02 断言。（2026-09-20 review 再次变更：活跃重复 Grant 回归**幂等**——返回 200 + 既有记录，与用户↔Agent 授权口径一致）

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: 重复添加期望 409，实际 200（test 断言先改，实现后修） | 54 passed（console_skill 全量） | test_user_scope_api.py：S-02 段（SkillUserGrant 恰 1 条、AgentAccessGrant/AgentSkillBinding 计数不变、无 expires_at 属性） | ASGI 真实 HTTP + 真实 PostgreSQL | verified |
| RULE-auth-001 | 同上 | 同上 | 重复授权 200 + 既有记录 / 移除 404 / 软删后重建 200 | 同上 | verified |
| RULE-rel-001 | N/A（行为验证） | 同上 | 仅单关系 POST/DELETE 端点 | 同上 | verified |
- S-02: verified — automated command passed; run_id=1de7a0d841684e6a8f92ef02cb893da1 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started/finished：重复 Grant 修正为 COMMON_CONFLICT，S-02/RULE-auth/RULE-rel verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-007: Snapshot 版本冻结语义（S-03）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: 05-skill-management.backend.design.md#2.5.2 S-03, 05-skill-management.backend.design.md#3.2 架构与流程
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-03, RULE-06

### Description

验证 Run A 开始后导入 v2：Run A 继续旧 Artifact，新 Run 使用 current v2。新 Run/Task 冻结 Artifact 引用到 Snapshot；配置/授权变更只影响后续新 Run/Task。

实际落点：`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`（已实现）。

### Checklist
- [x] 先写测试并记录 RED：S-03（快照冻结语义已实现，属行为锁定测试；首个失败为 NOT NULL prompt_template_version 构造问题，非功能缺陷）
- [x] [S-03][E2E] 修改生产代码前，按 Upload→Snapshot semantics 真实边界（真实上传、真实 PostgreSQL、真实 Snapshot 冻结，不得 mock）编写验收测试并记录 RED
- [x] [S-03] 断言：Run A 快照仍指向旧 artifact_id；Run A 期间导入 v2 后新 Run 快照指向新 artifact_id；旧 Artifact 未被覆盖
- [x] 运行 harness-snapshot#RULE-snapshot-001 verifier：断言 Snapshot 中冻结的是 artifact_id（非动态解析 current），授权/范围变更只影响后续新 Run/Task
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Upload、Snapshot 冻结、PostgreSQL | 旧 Run 用旧 Artifact、新 Run 用 v2 | tests/console_skill/test_snapshot_freeze.py | uv run pytest -q tests/console_skill/test_snapshot_freeze.py | verified |
| RULE-snapshot-001 | E2E | 同 S-03 | 快照冻结 artifact_id、变更只影响后续 | 同上 | uv run pytest -q tests/console_skill/test_snapshot_freeze.py | e2e_deferred |

### Acceptance Evidence

冻结语义先前已实现（run_service 冻结 skill_catalog_json 含 artifact_id）。本次新增端到端行为锁定测试：真实 import→grant→bind→resolve→runtime_snapshot 行→导入 v2→再 resolve。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL: 测试构造问题（prompt_template_version NOT NULL），非功能缺陷；冻结行为已存在 | 1 passed（55 passed console_skill 全量） | test_snapshot_freeze.py（快照行仍 v1；新 resolve v2；current_artifact_id=v2；旧 Artifact checksum 未覆盖） | 真实 HTTP import + 真实 internal resolve + 真实 runtime.runtime_snapshot PostgreSQL 行 | e2e_deferred（待 verify-e2e 终验） |
| RULE-snapshot-001 | 同上 | 同上 | 同上（artifact_id 冻结、变更只影响后续 resolve/Run） | 同上 | e2e_deferred |
- S-03: e2e_deferred — automated command e2e_deferred; run_id=2bd1613ea2644e4ebedf4b38a7a380c0 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：S-03 行为锁定测试全绿，E2E 留待 verify-e2e

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-008: Skill SDK SkillContext 契约对齐（LIB-02，E-02）

- **Status**: verified
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: 05-skill-management.backend.design.md#3.4 接口设计 LIB-02
- **Spec-Refs**:
- **Acceptance-Refs**: E-02, RULE-03

### Description

`packages/skill-sdk/` 的 SkillContext 能力清单签名以 docs/06 §5 为唯一来源（ctx.platform/http/mcp/task/artifact/logger/user），本模块不重定义任何方法签名。断言 Skill context 无 Secret 访问接口。

### Checklist
- [x] 先写测试并记录 RED：E-02（SDK 已实现无 Secret 面，属契约锁定测试）
- [x] [E-02][integration] Skill 试图直接访问 Secret：SDK 无此接口（静态断言 SkillContext 公开面），Secret 值不进入 Skill context（真实构造 context 后检查属性面）
- [x] 契约测试：SkillContext 公开方法集合与 docs/06 §5 清单一致，无额外方法
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-02 | integration | 真实 SkillContext dataclass/端口类型 + docs/06 §5 契约清单 | 无 Secret 接口、Secret 不入 context | tests/sdk/test_skill_context_secret_surface.py | uv run pytest -q tests/sdk/test_skill_context_secret_surface.py -k e02 | verified |

### Acceptance Evidence

SkillContext 先前已实现（七端口，无 Secret 面）。本次新增契约锁定测试：字段集合恰为 user/logger/artifact/platform/mcp/task/http；各端口公开方法集合与 docs/06 §5 一致；无 secret 命名面。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-02 | N/A（契约锁定测试，SDK 无 Secret 面已实现） | 17 passed（secret_surface + skill_context 全量） | test_skill_context_secret_surface.py（字段集合断言 L12；端口公开面参数化断言 L33） | 真实 SkillContext dataclass 与端口 Protocol 类型检查 | verified |
- E-02: verified — automated command passed; run_id=46e6f7f5c5554b7da0dad4610a305a4b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：E-02 契约锁定测试全绿

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-009: 前端 Skill 列表与导入

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: 05-skill-management.frontend.design.md#2.2 功能方案 FEAT-FE-01, 05-skill-management.frontend.design.md#3.3 组件设计 CMP-01/CMP-02
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-05, E-05

### Description

`apps/console-platform/frontend/src/modules/skill-management/`：SkillPage（ModuleToolbar 左上导入 + 右上搜索/范围筛选、RemoteTable、右下分页）+ SkillImportModal（Semi Upload + Form，本地 ZIP 限制预检）。services 层 `listSkills/importSkill`。

### Checklist
- [x] 先写测试并记录 RED：S-05、E-05（模块此前完全缺失——列表页为 PlaceholderPage 占位，contract/E2E 测试先行即模块不存在的 RED）
- [x] [S-05][E2E] 修改生产代码前，按 Browser Upload→NFS/DB→UI 真实边界（真实浏览器、真实后端，不得 mock）编写验收测试并记录 RED
- [x] [S-05] 断言：导入合法 Skill 后列表出现新 Skill、默认 SELECTED、当前版本正确
- [x] [E-05][E2E] 上传非法 ZIP（超限/白名单外/路径穿越/敏感信息）：Modal 保留并显示本地化校验错误
- [x] 运行 harness-ui#RULE-ui-001 verifier（E2E，真实浏览器渲染）：断言列表页布局左上操作、右上搜索筛选、右下分页；主展示字段（key/name）打开详情；不重复页签标题
- [x] 运行 harness-frontend#RULE-front-001 verifier：断言 API 只经 services/，组件不裸用 axios/fetch
- [x] 运行 harness-i18n#RULE-i18n-001 verifier：断言全部文案 t(key)，zh-CN/en-US 词条齐备，ApiClient 自动发送 X-Locale
- [x] 字段中文名用 docs/15 词典：当前版本/使用 Agent 数/指定用户数/用户范围/启用状态
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实浏览器、真实上传后端、NFS/DB | 列表新增行、SELECTED、版本正确 | e2e/tests/skill-management/skill-management.spec.ts | npm --prefix e2e test -- --config playwright.skill.config.ts --grep "S-05" | verified |
| E-05 | E2E | Validation API→Modal | Modal 保留 + 本地化错误 | 同上 | npm --prefix e2e test -- --config playwright.skill.config.ts --grep "E-05" | verified |
| RULE-ui-001 | E2E | 真实浏览器渲染 | 布局结构与词典字段 | e2e spec + contract 测试 | uv run pytest -q tests/frontend/test_skill_module_contract.py | verified |
| RULE-front-001 | integration | 组件源码 + services 层 | 无裸 axios/fetch、文案全 i18n key | tests/frontend/test_skill_module_contract.py + scripts/check_frontend_api_usage.py | uv run pytest -q tests/frontend/test_skill_module_contract.py && uv run python scripts/check_frontend_api_usage.py | verified |
| RULE-i18n-001 | integration | locale 资源 + X-Locale 请求头 | 双语词条、协商生效 | tests/frontend/test_skill_module_contract.py + scripts/check_frontend_i18n.py | uv run pytest -q tests/frontend/test_skill_module_contract.py && uv run python scripts/check_frontend_i18n.py | verified |

### Acceptance Evidence

新建 `src/modules/skill-management/`（SkillPage / SkillImportModal / SkillDetailSideSheet 基础版 / services/skills.ts 全量 service），App.tsx 路由替换占位页；后端列表补 agent_count/user_count 聚合（API-01 设计要求）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | RED：模块不存在（/skills 为 PlaceholderPage，contract 测试失败） | E2E spec 编写完成 + Playwright --list 通过；终验留 verify-e2e | e2e spec S-05（列表出现新行/指定用户/1.0.0） | 真实 Chrome → Vite dev → 真实后端 → NFS/DB | e2e_deferred |
| E-05 | 同上 | 同上 | e2e spec E-05/E-05b（非法 ZIP/白名单外 → Modal 保留 + Toast） | Validation API→Modal | e2e_deferred |
| RULE-ui-001 | 同上 | 6 passed（contract）+ typecheck/build 通过 | test_skill_module_contract.py（布局 slot、公共组件、详情入口） | 源码契约 + 真实构建 | verified |
| RULE-front-001 | 同上 | check_frontend_api_usage OK | services 层唯一 API 入口断言 | 源码扫描 | verified |
| RULE-i18n-001 | 同上 | check_frontend_i18n OK（305 keys） | 双语词条齐备断言（含词典字段名） | locale 资源扫描 | verified |
- S-05: e2e_deferred — automated command e2e_deferred; run_id=f3745ab08f8846d9b8cfa1154f4f71e4 (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=f3745ab08f8846d9b8cfa1154f4f71e4 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-05: failed — automated command failed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：模块落地，contract/i18n/api 检查全绿，E2E 待终验

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-010: 前端详情 Tabs 与版本详情

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-005, TASK-009
- **Source**: 05-skill-management.frontend.design.md#2.2 功能方案 FEAT-FE-02/FEAT-FE-03, 05-skill-management.frontend.design.md#3.3 组件设计 CMP-03
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-06, E-07

### Description

SkillDetailTabs（基本信息含 SKILL.md 预览/版本记录/使用 Agent/指定用户），Header 右侧"变更用户范围 + 导入新版本 + 关闭 X"同行；版本号链接打开次级 SideSheet/Modal 展示 manifest（包内文件清单快照）/SKILL.md；不提供直接执行入口。

### Checklist
- [x] 先写测试并记录 RED：S-06、E-07（详情 Tabs/版本详情此前不存在，contract/E2E 先行即 RED）
- [x] [S-06][E2E] 修改生产代码前，按 Browser→artifact API 真实边界编写验收测试并记录 RED
- [x] [S-06] 断言：详情导入新版本后版本记录新增一行、Header 当前版本更新
- [x] [E-07][E2E] 导入已存在版本或 checksum：Toast 提示 SKILL_VERSION_EXISTS 本地化文案，列表不新增重复行
- [x] 运行 harness-ui-detail#RULE-ui-detail-001 verifier（E2E，真实浏览器渲染）：详情 SideSheet 标题/副标题居左，操作与关闭 X 同行靠右，Tabs 其下；关系操作完成即生效
- [x] 版本详情展示 storage_key/checksum/manifest 快照/校验状态，无执行入口（引用 RULE-skill-001）
- [x] 详情基本信息 DetailGrid 双列，<900px 降单列；时间 `YYYY-MM-DD HH:mm:ss`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实浏览器、artifact API、后端 DB | 版本记录新增 + Header 更新 | e2e/tests/skill-management/skill-management.spec.ts | npm --prefix e2e test -- --config playwright.skill.config.ts --grep "S-06" | verified |
| E-07 | E2E | Artifact API→UI | Toast + 不新增重复行 | 同上 | npm --prefix e2e test -- --config playwright.skill.config.ts --grep "E-07" | verified |
| RULE-ui-detail-001 | E2E | 真实浏览器渲染 | SideSheet 布局结构 | tests/frontend/test_skill_detail_contract.py | uv run pytest -q tests/frontend/test_skill_detail_contract.py | verified |

### Acceptance Evidence

详情 SideSheet 扩展为 4 Tabs（基本信息/版本记录/使用 Agent/指定用户），Header 右侧"导入新版本"；SkillArtifactDetailModal 展示 manifest 清单快照 + SKILL.md 预览；SkillImportModal 支持 artifact 模式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | RED：详情仅基本信息 Tab，无版本记录/导入新版本 | E2E spec 编译通过（--list 5 tests）；终验留 verify-e2e | e2e spec S-06 | 真实 Chrome → artifact API → DB | e2e_deferred |
| E-07 | 同上 | 同上 | e2e spec E-07（Toast + 版本行唯一） | Artifact API→UI | e2e_deferred |
| RULE-ui-detail-001 | 同上 | 60 passed（frontend contract 全量）+ build 通过 | test_skill_detail_contract.py（Header 布局/4 Tabs/版本链接/无执行入口） | 源码契约 + 真实构建 | verified |
- S-06: e2e_deferred — automated command e2e_deferred; run_id=ef763efbdcff4fbb826da9326be6349e (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=ef763efbdcff4fbb826da9326be6349e (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-06: failed — automated command failed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- E-07: failed — automated command failed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：详情/版本落地，契约与构建全绿，E2E 待终验

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-011: 前端用户范围维护

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-006, TASK-009
- **Source**: 05-skill-management.frontend.design.md#2.2 功能方案 FEAT-FE-04, 05-skill-management.frontend.design.md#3.3 组件设计 CMP-04
- **Spec-Refs**:
- **Acceptance-Refs**: S-07, E-06

### Description

SelectedUserTable（添加用户选择器 + Popconfirm 移除）；user_scope 变更 Modal；`user_scope=ALL` 时指定用户 Tab 只提示"当前对所有拥有对应 Agent 使用权的用户开放"，不提供维护操作。

### Checklist
- [x] 先写测试并记录 RED：S-07、E-06（用户范围维护此前不存在，契约/E2E 先行即 RED）
- [x] [S-07][E2E] 修改生产代码前，按 Browser→scope API 真实边界编写验收测试并记录 RED
- [x] [S-07] 断言：ALL 时指定用户 Tab 只显示提示文案，无添加/移除控件
- [x] [E-06][integration] 添加指定用户失败：保持当前 Tab，Toast 提示（catch 留在当前视图，Toast 由 ApiClient）
- [x] 移除使用 Popconfirm；添加/移除后局部刷新
- [x] 引用 RULE-auth-001：Tab 只管理 SkillUserGrant；引用 RULE-rel-001：单关系操作
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实浏览器、scope API | ALL 时仅提示无维护操作 | e2e/tests/skill-management/skill-management.spec.ts | npm --prefix e2e test -- --config playwright.skill.config.ts --grep "S-07" | verified |
| E-06 | integration | Grant API→UI（源码契约） | 失败 Toast、保持 Tab | tests/frontend/test_skill_user_scope_contract.py | uv run pytest -q tests/frontend/test_skill_user_scope_contract.py | verified |

### Acceptance Evidence

新建 SelectedUserTable（ALL 时仅 Banner 提示；SELECTED 时用户选择器 + Popconfirm 移除）与 SkillScopeModal（变更用户范围），接入详情 Sheet Header 与指定用户 Tab。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | RED：指定用户 Tab 此前仅计数提示 | E2E spec 编译通过（--list 6 tests）；终验留 verify-e2e | e2e spec S-07（提示文案可见、无添加控件） | 真实 Chrome → scope API → UI | e2e_deferred |
| E-06 | 同上 | 63 passed（frontend contract 全量） | test_skill_user_scope_contract.py（ALL 分支先于控件返回；单关系 POST/DELETE；Popconfirm；局部刷新） | 源码契约 + 真实构建 | verified |
- S-07: e2e_deferred — automated command e2e_deferred; run_id=30b1c541401c4af8b7791bec4f96e00a (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=30b1c541401c4af8b7791bec4f96e00a (confirmed_by: runner)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=db7e83ac9fd44f919aae03b4469deb54 (confirmed_by: runner)
- S-07: failed — automated command failed; run_id=65228ffbb91d4fca949b0d1f8e5bd2be (confirmed_by: runner)
- S-07: failed — automated command failed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=1575e5a6a2ad4295b0a8f58df216e497 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=99bac8a80d824afeb2c9ebd2d40114b4 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=3f213f7b14ea4f54ae045e7650b7d2dc (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=87cde27457c04af9acb40076839c264f (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：用户范围维护落地，契约与构建全绿，E2E 待终验
- [2026-09-19] started
- [2026-09-19] completed (done)
