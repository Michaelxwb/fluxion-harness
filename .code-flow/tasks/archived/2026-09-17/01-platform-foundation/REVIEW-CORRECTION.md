# 01-platform-foundation 核验报告偏差修正记录

**修正日期**: 2026-09-20  
**修正人**: Claude Code  
**原核验报告**: 用户提供的功能真实性核验报告  

---

## 修正摘要

原核验报告发现 6 处偏差，经复测：
- ✅ **5 处已修正**（偏差 2/3/4/5/6）
- ❌ **1 处为误判**（偏差 1 不成立）

---

## 逐项修正结果

### ❌ 偏差 1: TASK-002 分页校验 - **原报告误判，无需修正**

**原报告声称**：
> 分页参数通过 clamp_page() 静默夹紧，400 仅发生在 Query ge/le 校验

**复测结果**：
```python
# packages/api-kit/src/muad_api/response.py:35
if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
    raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)  # 确实抛 400
```

**结论**：代码中不存在 clamp 逻辑，非法参数直接抛 400 异常。原核验报告的"静默夹紧"判断**错误**，无需修正任务文档。

---

### ✅ 偏差 2: TASK-002 locale 中间件归属 - **已修正**

**问题**：任务书声称"LIB-03 locale 归 api-kit"，但 Content-Language 响应头实际不在 api-kit。

**修正内容**：
- **Description**: 添加说明"Content-Language 响应头由各 app 的中间件设置"
- **Checklist**: 改为"locale 协商由 api-kit 的 MessageCatalog 提供；Content-Language 响应头由各 app 中间件设置；前端 locale 词条独立维护，通过 parity checker 保证 key 与后端错误目录一致"

**修正位置**: `.code-flow/tasks/archived/2026-09-17/01-platform-foundation/01-platform-foundation.md` TASK-002

---

### ✅ 偏差 3: TASK-003 audit 同事务 - **已修正**

**问题**：任务书措辞"业务与审计同事务"暗示全局保证，但实际是服务层约定（调用方传入 session）。

**修正内容**：
- **Description**: 改为"`write_config_audit(session)` 同事务审计原语（服务层通过传入同一 session 保证业务与审计同事务）"
- **Checklist**: 改为"实现 `write_config_audit(session, ...)`；服务层传入同一 session 保证同事务"

**修正位置**: `.code-flow/tasks/archived/2026-09-17/01-platform-foundation/01-platform-foundation.md` TASK-003

**架构说明**：这是有意的设计选择（api-kit 提供原语，业务层负责编排），非实现缺陷。

---

### ✅ 偏差 4: TASK-007 401 跳转 - **已修正**

**问题**：原报告错误地说 `main.tsx:9` 有 `import "./api/client"`（实际不存在），但结论成立——401 拦截器依赖模块副作用。

**修正内容**：
- **Description**: 添加说明"401 跳转登录原语（拦截器在 api/client.ts 模块加载时注册，业务模块首次 import 时触发）"
- **Checklist**: 改为"401 跳转登录并携带 returnUrl（拦截器在 api/client.ts 模块顶层注册，首次 import 时生效）"

**修正位置**: `.code-flow/tasks/archived/2026-09-17/01-platform-foundation/01-platform-foundation.md` TASK-007

**实际机制**：拦截器在 `apps/console-platform/frontend/src/api/client.ts:45` 模块顶层注册，业务模块首次 `import { api }` 时触发。

---

### ✅ 偏差 5: TASK-005 迁移链版本号 - **已修正**

**问题**：Evidence 写"0003→0002→0001 单头"，但当前迁移链已是 0007。

**修正内容**：
- **Checklist**: 改为"迁移链当前为 0007 单头，归档时点 2026-09-18 为 0003"
- **Acceptance Evidence**: 在真实边界证据列标注"迁移链当前 0007 单头（归档时点 2026-09-18 为 0003→0002→0001）"

**修正位置**: `.code-flow/tasks/archived/2026-09-17/01-platform-foundation/01-platform-foundation.md` TASK-005

---

### ✅ 偏差 6: TASK-002 前端 locale 来源 - **已修正**

**问题**：任务书暗示前端 locale "来自后端目录"，实际是双向独立维护 + 契约校验。

**修正内容**：
- 已在偏差 2 的修正中一并说明："前端 locale 词条独立维护，通过 parity checker 保证 key 与后端错误目录一致"

**修正位置**: `.code-flow/tasks/archived/2026-09-17/01-platform-foundation/01-platform-foundation.md` TASK-002 Checklist

---

## 质量改进措施

根据本次核验与修正，提出以下改进建议：

### 1. 核验报告质量要求
- 核验人必须 `grep` 确认关键函数/逻辑存在性（偏差 1 误判即因未实测代码）
- 区分"设计决策"与"实现缺陷"（偏差 3 属架构选择，非 bug）
- 引用代码位置需逐行验证（偏差 4 错误引用 main.tsx:9）

### 2. Evidence 时效性标注
- 易变数据（版本号/commit hash/分支名）需标注"as of 日期"
- 归档后若底层代码继续演进，Evidence 需注明"归档时点快照"

### 3. 任务描述精确性
- 避免"X 与 Y 同事务"等暗示全局保证的绝对化措辞
- 改用"提供 X 原语，调用方负责 Y"等明确责任边界的表述
- 对于"来自/直接/统一"等强因果词汇，需在 Description 中说明实际机制

---

## 修正验证

所有修正已应用到归档任务文件：
```bash
.code-flow/tasks/archived/2026-09-17/01-platform-foundation/01-platform-foundation.md
```

修正涉及的 TASK:
- TASK-002: Description + Checklist（偏差 2/6）
- TASK-003: Description + Checklist（偏差 3）
- TASK-005: Checklist + Acceptance Evidence（偏差 5）
- TASK-007: Description + Checklist（偏差 4）

---

## 后续行动

- [ ] 将本次核验经验更新到 `.code-flow/specs/shared/` 质量审查模板
- [ ] 在下次归档前，补充"Evidence 时效性检查"步骤到归档流程
- [ ] 复核其他已归档任务（02-14）是否存在类似的描述口径问题
