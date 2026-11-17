# MSSWWEB 通道部署清单（含迁移与回滚）

> **适用对象**：部署本分支（`feat/mssw-channel`）的研发/运维
> **更新日期**：2026-11-17
> **状态**：已在测试 Pod 与临时库上实测通过（详见 §6 验证记录）

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

迁移确认无误后，才滚动更新 workload。

---

## 4. 部署后检查

```bash
# 三个服务应能起来且不重启
kubectl get pod -n muad -l 'app in (muad-console-platform,muad-agent-runtime,muad-agent-worker)'
# 日志里不应再出现 schema revision 报错
kubectl logs -n muad <pod> | grep -i "schema revision"
```

---

## 5. 回滚预案

### 5.1 回滚能做什么、不能做什么

实测结论：

- 回滚到 `0050` 会删掉两张新表、同步状态表和 `auth_revision` 列，**业务行本身保留**。
- **回滚会丢失新功能数据**：升级后写入的 `external_identity` / `entry_access_grant` 全部消失；`auth_revision` 的值归零重来（实测把 `auth_revision=7` 回滚再升级后变回 `1`）。
- 回滚后可以再升级回来，re-upgrade 实测 `EXIT=0`。

### 5.2 什么时候该回滚

只有当**新服务无法启动且短时间修不好**时才回滚。如果只是功能异常，优先修代码或回退镜像，不要动 schema——回滚会丢用户已配置的授权。

### 5.3 回滚步骤

```bash
# 1) 先把 workload 回到旧镜像（否则旧库 + 新代码会再次启动失败）
kubectl set image ... # 回到部署前镜像

# 2) 回退 schema
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini downgrade 0050

# 3) 校验
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini current   # 期望 0050
```

### 5.4 灾难恢复

如果回滚过程中出错，用 §3.4 的 dump 恢复：

```bash
pg_restore -h <pg-host> -U <user> -d muad_db --clean --if-exists /backup/muad_db_pre_mssw_<ts>.dump
```

---

## 6. 验证记录（2026-11-17 实测）

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

## 7. 常见坑

- **不要手工 `alembic -c` 直跑**：用 `migrations/db_migrate.py`，它会 `chdir` 到仓库根，保证 `script_location` 与 `prepend_sys_path` 相对路径正确。
- **表在 `control` / `runtime` / `task` schema**：校验 SQL 写 `schemaname='public'` 会查不到东西，误判成「迁移失败」。
- **连接串只来自 ini**：设 `DATABASE_URL` 环境变量对迁移无效。
- **`downgrade` 只回 schema，不回镜像**：先回镜像再回 schema，顺序反了会出现「新代码 + 旧库」。
