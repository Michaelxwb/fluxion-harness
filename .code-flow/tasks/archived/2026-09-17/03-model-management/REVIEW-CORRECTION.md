# 03-model-management 核验与修正记录

**核验日期**: 2026-09-20
**核验人**: Claude Code
**范围**: 两份 design → 3 个 TASK 的拆分，以及 `apps/console-platform/backend`（models API/service/repository）、`frontend/src/modules/model-management`、`tests/`、`e2e/` 的实现（15 个场景：backend 8 + frontend 7）

---

## 一、结论摘要

| 类别 | 数量 | 处理 |
|---|---|---|
| 设计→任务拆分缺陷 | 7 | 三份文档已回改 |
| 实现功能缺陷（后端） | 4 | 已修复并补有区分度的测试 |
| 实现功能缺陷（前端） | 7 | 已修复（含复用公共组件） |
| E2E 设施缺陷 | 1 | 改为自带端口 + 构建产物 + 不复用既有服务 |
| 验收证据不实 | 1 假断言 / 5 位置错 / 2 层级错 / RED 不可信 | 测试补强 + 证据表按实测位置重写 |

修正后：`cf_acceptance_runner --include-e2e` → **15/15 场景 pass**（decision=pass，单一 run_id）；后端 `uv run pytest -q --ignore=tests/acceptance/runtime` → **864 passed / 0 failed**；前端 101 passed；mypy 0 errors；ruff 仅剩 08 在途的 2 条。

---

## 二、设计 → 任务拆分缺陷

| ID | 问题 | 修正 |
|---|---|---|
| A1 | **§3.1 决策表与本模块实际决策完全相反**：写「Secret → SecretRef / 放弃 DB 明文 API Key」，而 §2.3.2/§2.4/RULE-03/§3.3 全部是「`api_key` 明文落库（产品决策）」，迁移 0004/0005 也是按明文做的。§1 核心目标、§2.2 亦残留 `SecretRef` | 决策表改为如实的「明文 `api_key`；放弃通用 SecretRef / 外部 Secret Provider；仍禁止进日志/审计/Snapshot/API 响应」；§1/§2.2 同步 |
| A2 | **RULE-08 / API-05 要求的 `{model_key, agent_count}` 不可实现**：`config/api-messages.yaml` 的 `COMMON_CONFLICT` 是通用文案、**无占位符**，而 api-kit 规定 msg 只能来自目录 → `message_args` 被静默丢弃 | 新增专用码 **`MODEL_IN_USE`**（409，带占位符）；RULE-08/API-05/场景表/任务书 checklist 与 Contract 全部同步 |
| A2b | **API-02 同一缺陷**（review 第二轮发现）：`key` 重复也传 `message_args={"key"}` 给无占位符的 `COMMON_CONFLICT` | 新增专用码 **`MODEL_KEY_EXISTS`**（409，带 `{key}`）；服务两处 raise 与文档同步 |
| A3 | Spec Compliance Matrix 5 行 owner 是不存在的 `harness-platform`（同表内 `harness-secret` 却是真实 owner） | 更为 `harness-api`/`harness-data`/`harness-model`/`harness-snapshot`/`harness-test`（frontend design 6 行同样处理），各加一行更正说明 |
| A4 | **E-06/E-08 层级标 `integration`，命令却是 playwright E2E** → manifest 生成 `kind=functional`，Done Gate 会把需要浏览器的用例当 functional 自动跑 | 层级改为 `E2E`（frontend design 的源头行一并改）；manifest 实证 `E-06 E2E e2e` / `E-08 E2E e2e` |
| A5 | 19 处 e2e 命令**都缺** `npm run build` 前导（`harness-test#RULE-test-001` Convention 要求先产出构建物，01/02 的命令有）；`playwright.model-management.config.ts` 用 Vite **dev server** + 三处 `reuseExistingServer: true` | 全部补 `npm run build &&`；config 改为**自带端口**（后端 8011 / 构建产物 4184 / 探测端点 4191）+ `reuseExistingServer:false` + `npm run preview`；spec 探测端口读 `E2E_PROBE_PORT`（默认 4191） |
| A6 | TASK-001 Acceptance Contract 的 5 行「执行命令」列是 `planned` 哨兵（解析器的"未注册命令"），状态列被追加成两格 | 换成与 Coverage 一致的真实 argv，状态恢复单值 |
| A7 | backend design §1「建议代码位置」写不存在的 `modules/models/`；frontend design 的 CMP-03 `ModelTestResultModal` 未独立成文件 | 改为实际路径 / 注明现状 |

---

## 三、实现功能缺陷

| ID | 问题 | 修正 | 验证 |
|---|---|---|---|
| B1 | **API-04 的 CAS 不是原子的**：`model_service.update_model` 先 SELECT 拿 revision、Python 里比对、再 ORM `flush()`；而设计 `:287` 明确要求 `UPDATE ... WHERE revision=expected_revision`。全仓 `ModelDefinition` 无 `version_id_col`/`__mapper_args__`，更新路径无 `with_for_update` → **两个并发编辑者持同一 `expected_revision` 会双双成功、后者覆盖前者，`REVISION_CONFLICT` 永不触发** | 新增 `ModelRepository.cas_update`（条件 UPDATE，`rowcount==1` 才算命中）+ `revision+1`/重置测试状态；服务改用后 `refresh` 取新值。另：`count_agent_references` 补 tenant 过滤（B8） | `test_e01_cas_primitive_rejects_stale_revision`（`:186`）、`test_e01_cas_update_sql_filters_on_revision`（`:218`） |
| B3 | **删除冲突的引用数拿不到**（见 A2），且 TASK-003 checklist 把它勾成 [x]、E2E 断言恰好把通用文案固化成了预期 | 新增 `MODEL_IN_USE`；E2E E-08 改为断言 Toast 含**模型 key** 与**「1 个 Agent」** | `spec.ts:193`（实测 7/7 E2E pass） |
| B2 | **前端详情从不调用详情 API**：`ModelPage.tsx:152` `setDetail(record)`（吃列表行对象），`services/models.ts:61` 的 `getModel` 成死代码 → API-03 事实上未被使用；**S-05 声明的「Browser→detail API」边界不成立**；`toggleEnabled` 后 detail 陈旧 | 新增 `openDetail(id)` → `getModel(id)`（失败有本地化 Toast）、`refreshDetail` 在启停/保存后刷新当前 SideSheet | typecheck/build + 15/15 验收 |
| B4 | 列表无错误态/无重试：`reload` 只有 `try/finally` 无 `catch`；`ErrorState` 从未 import（设计 §3.6 要求） | 加 `catch` + `failed` + `ErrorState onRetry` | 同上 |
| B5 | 筛选逐字符发请求（`onChange` 直接 `setParams`）+ 无请求序号（竞态） | 改草稿态 + 搜索/重置按钮 + 回车触发 + `requestSeq` 丢弃过期响应 | 同上 |
| B6 | 测试状态枚举未本地化，且模块内**两套口径**（列表/筛选/结果 Modal 裸渲染 `{value}`，只有详情用了词条） | 新建 `statusOptions.ts` 作为**唯一映射处**（`testStatusOptions`/`enabledOptions`/`apiKeyOptions`），四处统一走它 | 契约测试断言三条词条位于该文件 |
| B7 | 绕过设计「必须复用」的公共组件：`StatusTag`（5 处裸 `<Tag>` + 详情自写 `testStatusColor`/`testStatusKey` 重复映射）、`EntityLink`（裸 `Button`）、`ErrorState` | 三者全部改用公共组件（保留 E2E 依赖的 testid） | 契约测试新增「必须用 StatusTag」断言 |
| B9 | `api_key` 的「留空保持」说明用 `placeholder` 承载（`extraText` 被占用），违反 `harness-ui#RULE-ui-001` | 三条既有词条全部移入 `extraText`，移除 placeholder | 契约断言不变 |
| B10 | `toggleEnabled` 无 loading/catch；切筛选/翻页不清空 `selected`；结果 Modal 只显示 `model_id`；`revision` 列头硬编码英文；列头 key 混用 `model.form.*` | 逐项修复（`savingId`+Switch loading+catch；`useEffect([params])` 清空；Modal 显示 `key · name`；列头统一 `model.columns.*`） | typecheck/build |

---

## 四、验收证据真实性

| 判定 | 数量 | 明细 |
|---|---|---|
| **断言与实际不符** | 1 | **E-08**：Contract/checklist 要求断言 `agent_count`，Evidence 自认未展示、E2E 只断言通用文案，却标 `verified` → 已改为真断言 |
| **断言位置写错** | 5 | S-01（`:27` 非断言）、S-03（`:51` 落在 **test_s01 尾部**）、E-01（`:89` 非断言）、E-02/E-05（`:122` 落在 **test_e03 体内**）、E-03（`:104` 落在 **test_e01 体内**）—— **3 条指向别的测试** |
| **层级标注错** | 2 | E-06、E-08（见 A4） |
| **RED 不可信** | backend 全部 | 文档称「测试先行（404 → GREEN）」「首次运行 2 failed」，但实现与测试**同落 commit `7c94ab2`**，无法证明先后 → 已改为如实表述 |
| **结构缺陷** | 5 行 | Contract 的 `planned` 哨兵 + 状态列两格（见 A6） |
| **计数过期** | 1 | E-09 写「4 passed」，该文件现存 5 个用例 |
| **成立** | 其余 | 全部 15 个场景的**测试本体**真实；spec 内无 `page.route`/`skip`/`fulfill` |

- 三段 Evidence 已由 bullet 统一改为表格，断言位置全部换用**实测抽出**的位置。
- **批量测试的「真实外呼、禁止 mock」经专门否证后成立**：`ProbeEndpoint` 是真实本地 HTTP 服务（GET 404 → 回退 POST chat 200、鉴权 401），测试文件无 `mock`/`respx`/`monkeypatch`。
- 65 条 `(confirmed_by: runner)` 历史行一条未丢。

---

## 五、修正后验证

| 项目 | 结果 |
|---|---|
| **03 验收（15 场景，含 9 条 E2E）** | **15/15 passed**，decision=`pass`；manifest `validate → (True,'')`；单一真实 run_id |
| 后端全量 | `pytest -q --ignore=tests/acceptance/runtime` → **864 passed / 0 failed** |
| 前端 | `tests/frontend` 101 passed；typecheck / build / i18n（482 keys）/ api-usage 全过 |
| mypy / ruff | 0 errors / 2 errors（**仅 `tests/acceptance/runtime/` 的 08 在途文件**） |
| model-management E2E | 7/7 passed（自带端口 8011/4184/4191 + 构建产物） |

**一处过程自纠**：我为 CAS 先写的「两个并发 HTTP 请求」用例，**在旧的先读后写实现下也会通过**（ASGI 传输下两者未在关键点交错）→ 属无区分度测试，已废弃；换成原语层两条并**用「临时移除 revision 谓词」验证它们在坏实现下确实失败**。

---

## 六、遗留（不在本模块范围）

1. **同类缺陷的systemic面**：`COMMON_CONFLICT` 无占位符却被多个模块当作"带参数的冲突"用 —— 02 的 `user_code` 重复（`message_args={user_code}`）、以及 05/06/07 的 `key` 重复。本模块已用 `MODEL_IN_USE`/`MODEL_KEY_EXISTS` 各自解决；**其余模块是否同样新增专用码，需要先定一个口径**（每模块一个专用码 vs 引入通用带参数的 `COMMON_DUPLICATE`）。02 的 E-08 E2E 目前断言通用文案，若统一口径需同步改它。
2. **其余域的 playwright config**（agent / skill / mcp / platform）仍用 dev server + `reuseExistingServer: true`，与 `RULE-test-001` Convention 相悖。本次只改了 model-management 一份（02 上一轮改过一份）。
3. `make check` 当前因 08-runtime-execution 的未提交在途重构而失败（lint 2 条 + test 步），与本模块无关；TASK-001 Evidence 下已加注说明。
