# MSSWWEB 通道部署清单（含迁移、平台因子配置与回滚）

> **适用对象**：部署本分支（`feat/mssw-channel`）的研发/运维
> **更新日期**：2026-11-18
> **状态**：迁移与平台因子配置均已在真实环境实测通过（迁移见 §8 验证记录）

**部署全流程共四段，按序执行**：

1. 数据库迁移到 head（§1–§3）——不做会让三个服务 CrashLoop。
2. 更新工作负载镜像（CD 走 Helm chart，见 §4.1）。
3. 写平台验签因子 Secret 并重启（§4）——不做则外部平台未配置，页面上看不到 `mssw`。
4. 管理员在 Console 建入口、登记身份、授权（§6）。

---

## 1. 部署前必读：先迁移，后更新工作负载

本分支新增两个数据库迁移，应用的**启动自检**会核对 schema 版本，不匹配时**拒绝启动**（这是刻意的保护，不是缺陷）：

```
StartupValidationError: startup validation failed:
  database schema revision '0050' is not at head '0052'
```

| 服务 | schema 未迁移时 |
|---|---|
| `muad-console-platform` | CrashLoop（启动自检拒绝） |
| `muad-agent-runtime` | CrashLoop（启动自检拒绝） |
| `muad-agent-worker` | CrashLoop（启动自检拒绝） |
| `muad-im-gateway` | 能启动，但依赖的新表不存在，功能失败 |
| `muad-connector` | 能启动，同上 |

**正确顺序**：迁移到 `head` → 确认版本 → 再更新 Deployment。反过来会导致三个服务反复重启。

---

## 2. 迁移内容

| 迁移 | 内容 | 回滚 |
|---|---|---|
| `0051_external_identity_and_entry_grant` | 新建 `control.external_identity`、`control.entry_access_grant`；给 `control.platform_user` 加 `auth_revision bigint not null default 1`（既有行自动填 1） | 删两表 + 删列 |
| `0052_auth_cache_sync_state` | 新建 `control.auth_cache_sync_state` | 删表 |

性质：**只加表、只加列**，不改既有数据语义。实测确认升级后既有行原样保留，新表为空。

---

## 3. 执行迁移

`migrations/db_migrate.py` **不读环境变量**，连接串只来自 ini。所以要为生产单独准备一份 ini。

### 3.1 准备生产 ini

```bash
cat > /tmp/alembic.prod.ini <<'EOF'
[alembic]
script_location = migrations
prepend_sys_path = .
sqlalchemy.url = postgresql+asyncpg://<user>:<password>@<pg-host>:5432/muad_db
EOF
```

注意连接串要用 **`postgresql+asyncpg://`**（async driver），不要用同步驱动。

### 3.2 查看当前版本（只读，先做）

```bash
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini current
```

预期输出为 `0050`（或更高）。若输出为空，说明该库从未被 alembic 管理，**停下排查**，不要直接 upgrade。

### 3.3 确认迁移链没有分叉

```bash
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini heads
```

预期**只有一个 head**（`0052`）。出现多个 head 必须先合并，否则 `upgrade head` 行为不确定。

### 3.4 备份

加表加列虽然风险低，但回滚会丢数据（见 §5），所以先备份：

```bash
pg_dump -h <pg-host> -U <user> -d muad_db -Fc -f /backup/muad_db_pre_mssw_$(date +%Y%m%d_%H%M).dump
```

### 3.5 执行升级

```bash
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini upgrade head
```

### 3.6 校验

```bash
# 版本应为 0052
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini current
```

再用 SQL 核对（**注意表在 `control` schema，不是 `public`**）：

```sql
-- 三张新表存在
select tablename from pg_tables
where schemaname='control'
  and tablename in ('external_identity','entry_access_grant','auth_cache_sync_state');
-- 期望 3 行

-- 新列存在、无 NULL、最小值为 1
select min(auth_revision), count(*) filter (where auth_revision is null), count(*)
from control.platform_user;
-- 期望 min=1, nulls=0, total=既有用户数
```

新表**应为空**（升级不臆造业务数据）。

### 3.7 再更新 Deployment

迁移确认无误后，才滚动更新 workload。镜像更新后还要配平台因子，见 §4。

---

## 4. 配置外部平台验签因子（Secret）

迁移和镜像都上去之后，MSSW 入口仍然不可用——还差一步：给 `muad-external` Secret 写平台验签因子。没有它，Console 的 `GET /api/v1/external-platforms` 返回空数组，用户详情页的「外部身份」页签里看不到 `mssw` 平台。

### 4.1 两套清单：CD 走的是 Helm chart

仓库里有两套并行的部署定义，**改错一套不会生效**：

| 路径 | 用途 |
|---|---|
| `build/helm-build/<服务>/` | **CD 实际部署源**（Pod 上的 `app.kubernetes.io/managed-by: Helm` 标签为证） |
| `deploy/k8s/base/<服务>.yaml` | raw 清单，不参与本次 CD |

`EXTERNAL_ASSERTION_PLATFORMS_JSON` 这个 env 必须在 Helm chart 的 template 里声明，容器才读得到 Secret。两边都有同名项时保持内容一致，避免以后排查时误判。

该 env 在 chart 里是 `optional: true`：Secret 里没有这个键时，容器照常启动、只是「外部平台未配置」，其它通道（WEBCHAT）行为不变。所以**先上 chart、后写 Secret、顺序谁先都安全**。

### 4.2 组装平台 JSON

字段含义与取值来源：

| 字段 | 来源 |
|---|---|
| `issuer` / `audience` / `salt` | 需求方提供的 MSSW 接入参数（开发环境实测值见 `docs/mssw-channel/01-jwt-spec.md` §5.1） |
| `public_key` | 需求方提供的 **PKCS#1** 公钥（`-----BEGIN RSA PUBLIC KEY-----`） |
| `identity_keys` | 平台身份键集合，MSSW 用 `["_id", "tenant_id"]` |
| `identity_field_labels` | claim 名 → Console 表单标签的中文名 |

**`identity_keys` 必须写 `_id`，不能写 `expert_id`**。验签后按 `identity_keys` 直接取 JWT 顶层同名字段（`external_assertion/principal.py`），而真实 token 里专家 ID 的字段名就是 `_id`；写成 `expert_id` 会让每个真实 token 都因取值缺失被 401 拒绝。管理员在表单上看到的是「专家 ID」，由 `identity_field_labels` 映射，不影响使用。

`public_key` 写进单行 JSON 时，PEM 的换行必须是 `\n`（反斜杠加字母 n），不能是真换行。

写入前先校验 JSON 合法，避免写进 Secret 后 Pod 启动崩溃：

```bash
python3 -c "import json; json.load(open('platforms.json', encoding='utf-8')); print('JSON OK')"
```

平台因子损坏（JSON 语法错、公钥不是合法 RSA、`salt` 超过 32 字节）会在**启动期**抛 `PlatformConfigError`，Pod 直接 CrashLoop——这是刻意的，见 `external_assertion/config.py`。

### 4.3 写入 Secret

先备份，便于回滚：

```bash
kubectl get secret muad-external -n muad -o yaml > muad-external.backup.yaml
```

再把新键合并进 Secret。这条命令读出既有键的**原值**（`data` 里是 base64，`base64 -d` 解回明文），连同 `platforms.json` 整份重建，不会丢键：

```bash
kubectl create secret generic muad-external -n muad \
  --from-literal=DATABASE_URL="$(kubectl get secret muad-external -n muad -o jsonpath='{.data.DATABASE_URL}' | base64 -d)" \
  --from-literal=REDIS_URL="$(kubectl get secret muad-external -n muad -o jsonpath='{.data.REDIS_URL}' | base64 -d)" \
  --from-literal=INTERNAL_SERVICE_TOKEN="$(kubectl get secret muad-external -n muad -o jsonpath='{.data.INTERNAL_SERVICE_TOKEN}' | base64 -d)" \
  --from-file=EXTERNAL_ASSERTION_PLATFORMS_JSON=./platforms.json \
  --dry-run=client -o yaml | kubectl apply -f -
```

`--from-literal` / `--from-file` 收的都是**明文**，k8s 自动 base64 存进 `data`；不要手动编码后再传。

### 4.4 重启两个服务

Secret 变更不会让 Pod 自动重读：平台因子在进程启动时加载并缓存（`external_assertion/config.py` 的 `lru_cache`），必须重启：

```bash
kubectl rollout restart deploy/muad-console-platform deploy/muad-im-gateway -n muad
kubectl rollout status deploy/muad-console-platform -n muad
kubectl rollout status deploy/muad-im-gateway -n muad
```

两个服务都读同一个 Secret 键：Console 用它验签，Gateway 用它判定「该通道是否已配置」并挂载路由。只重启一个会出现「Console 能验签、Gateway 找不到平台」的割裂状态。

### 4.5 验证

```bash
# 1) 变量真的进了容器（应为三位数以上的 len，而非 0）
kubectl exec -n muad deploy/muad-console-platform -- sh -c 'echo "len=${#EXTERNAL_ASSERTION_PLATFORMS_JSON}"'
kubectl exec -n muad deploy/muad-im-gateway -- sh -c 'echo "len=${#EXTERNAL_ASSERTION_PLATFORMS_JSON}"'

# 2) Pod Running 且 RESTARTS 不涨
kubectl get pods -n muad -l 'app in (muad-console-platform,muad-im-gateway)'

# 3) 接口能列出平台（带管理员 token；端口以 Service 实际值为准）
kubectl exec -n muad deploy/muad-console-platform -- \
  curl -s -H "Authorization: Bearer <管理员token>" \
  http://localhost:<port>/api/v1/external-platforms
```

第 3 步期望返回 `data` 里有 `"platform_key": "mssw"` 且 `"configured": true`。返回空数组说明 Secret 没读到；503 是因子未配置；401 是 token 不对。

### 4.6 常见坑

- **改了 `deploy/k8s/base` 不生效**：CD 读 `build/helm-build`，确认改的是 chart template 而不是 raw 清单。
- **Secret 改完不重启**：`printenv` 仍是旧值/空，因为因子是启动时缓存的。
- **`identity_keys` 写成 `expert_id`**：验签后取不到身份，真实 token 全部 401。必须是 `_id`。
- **`public_key` 换行写成真换行**：JSON 非法，Pod 启动期抛 `PlatformConfigError` 并 CrashLoop。
- **Secret 回滚**：`kubectl apply -f muad-external.backup.yaml` 后再 `rollout restart` 两个服务。

---

## 5. 部署后检查

```bash
# 三个服务应能起来且不重启
kubectl get pod -n muad -l 'app in (muad-console-platform,muad-agent-runtime,muad-agent-worker)'
# 日志里不应再出现 schema revision 报错
kubectl logs -n muad <pod> | grep -i "schema revision"
```

---

## 6. 管理员配置入口与用户身份

平台因子配好、服务重启后，Console 上才有 `mssw` 平台。入口与用户的配置分两步，顺序是先建入口、再授权：

1. **建入口**：Agent 详情页 → IM 入口 → 新增 → 通道类型选「MSSW 网页入口」。表单只有三项必填：通道名称（任意可读名）、入口标识（稳定英文标识，全表唯一）、扩展配置（留 `{}`）。这类通道**不填 Bot Secret**——表单不显示该框，后端也显式拒收。
2. **登记身份**：用户详情页 → 外部身份 → 新增，按平台 `identity_keys` 填写（表单显示为「专家 ID」「租户 ID」）。
3. **授权入口**：在该身份行点「授权入口」，从列表里选第 1 步建的入口。
4. **授权 Agent**：用户还需有对应 Agent 的使用权，在「用户 → Agent」授权里配（不在外部身份页签）。

只登记身份、不授权入口 → 该入口返回 `NO_ENTRY_GRANT`；身份登记了但用户没有 Agent 授权 → `NO_AGENT_GRANT`。

---

## 7. 回滚预案

### 7.1 回滚能做什么、不能做什么

实测结论：

- 回滚到 `0050` 会删掉两张新表、同步状态表和 `auth_revision` 列，**业务行本身保留**。
- **回滚会丢失新功能数据**：升级后写入的 `external_identity` / `entry_access_grant` 全部消失；`auth_revision` 的值归零重来（实测把 `auth_revision=7` 回滚再升级后变回 `1`）。
- 回滚后可以再升级回来，re-upgrade 实测 `EXIT=0`。

### 7.2 什么时候该回滚

只有当**新服务无法启动且短时间修不好**时才回滚。如果只是功能异常，优先修代码或回退镜像，不要动 schema——回滚会丢用户已配置的授权。

### 7.3 回滚步骤

```bash
# 1) 先把 workload 回到旧镜像（否则旧库 + 新代码会再次启动失败）
kubectl set image ... # 回到部署前镜像

# 2) 回退 schema
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini downgrade 0050

# 3) 校验
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini current   # 期望 0050
```

### 7.4 灾难恢复

如果回滚过程中出错，用 §3.4 的 dump 恢复：
```bash
pg_restore -h <pg-host> -U <user> -d muad_db --clean --if-exists /backup/muad_db_pre_mssw_<ts>.dump
```

---

## 8. 验证记录（2026-11-17 实测）

在测试 Pod `mssw-test-runner-fff5ccdf8-cbhzm` 上，用临时库 `muad_mssw_verify` 连接实际 PG 集群执行，验证后已销毁该库。

| 验证项 | 结果 |
|---|---|
| 空库全量 `upgrade head` | `EXIT=0`，到达 `0052 (head)` |
| `0050` 状态下新表/新列不存在 | 确认不存在（对齐生产现状） |
| `0050` → `0052` 增量升级 | `EXIT=0`，既有行保留，`auth_revision` 全量填 1 |
| 升级后启动五个服务 | 全部 `healthz=200 readyz=200`，日志零 ERROR |
| `0052` → `0050` 回滚 | `EXIT=0`，新表与新列移除，业务行保留 |
| 回滚后再次升级 | `EXIT=0`，回到 `0052 (head)` |

迁移链 `0049 → 0050 → 0051 → 0052` 线性、单 head。

---

## 9. 常见坑

- **不要手工 `alembic -c` 直跑**：用 `migrations/db_migrate.py`，它会 `chdir` 到仓库根，保证 `script_location` 与 `prepend_sys_path` 相对路径正确。
- **表在 `control` / `runtime` / `task` schema**：校验 SQL 写 `schemaname='public'` 会查不到东西，误判成「迁移失败」。
- **连接串只来自 ini**：设 `DATABASE_URL` 环境变量对迁移无效。
- **`downgrade` 只回 schema，不回镜像**：先回镜像再回 schema，顺序反了会出现「新代码 + 旧库」。
