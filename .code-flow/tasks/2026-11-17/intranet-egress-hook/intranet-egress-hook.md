---
{"version":1,"requirement":"intranet-egress-hook"}
---

# Tasks: intranet-egress-hook

## TASK-001: 内网 Runtime/Worker 镜像安装 Skill 出站启动钩子

- **Status**: done
- **Depends**:
- **Source**: 2026-11-17 现场排障：两个 Runtime 的 Python 系统 site 缺少 muad_egress.pth；用户确认修复并提交上游 main PR。
- **Scenarios**:
- **Spec-Refs**: test/harness-test
- **Paths**: build/docker-build/Dockerfile.agent-runtime, build/docker-build/Dockerfile.agent-worker, tests/architecture/test_egress_image_bootstrap.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {"id":"image-hook-regression","argv":["uv","run","--no-sync","python","-m","pytest","-q","tests/architecture/test_egress_image_bootstrap.py","tests/architecture/test_skill_system_deps.py","tests/sdk/test_egress_site_bootstrap.py"],"timeout":180},
  {"id":"lint","argv":["uv","run","--no-sync","ruff","check","tests/architecture/test_egress_image_bootstrap.py"],"timeout":60}
]
```

### Description

已确认的构建缺口：标准 apps 镜像安装了系统 site 钩子，而内网 build/docker-build 镜像只安装 Python 包，导致受控 Skill 启动以 EGRESS_HOOK_NOT_READY / 78 失败。

在两个内网镜像安装项目包之后、切换非 root 用户之前安装钩子。回归契约覆盖 Runtime/Worker 的标准与内网四个入口，检查构建时安装、安装顺序、依赖失败阻断和非 root 运行边界。

本次不修改自定义 Host 的出站目标检查；HostTargetDenied 是独立故障。现场镜像重建、Pod 更新与真实业务验收由部署后确认，静态契约和本地单测不替代真实内网部署验证。

### Checklist

- [x] 新测试能检出两个内网镜像缺少钩子的旧行为。
- [x] 两个内网镜像在项目包安装后以 root 安装钩子。
- [x] 四入口构建契约、邻近启动钩子与依赖基线测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-17] 用户已确认具体补丁范围并要求直接实现、提交 PR；不改变平台地址或 Host 配置。
- [2026-11-17] RED：补丁前四入口契约中两个标准入口通过，两个内网入口因缺少安装步骤失败。
- [2026-11-17] 最初复用本机 venv 时，已有 .pth 使“缺少钩子”的 SDK 负向测试在 site 初始化阶段失败；改用独立干净 venv，保留原测试断言。
- [2026-11-17T03:12:21.147Z] started tests=c791cfc18e5b

### Verification

- 尚未执行。
- 2026-11-17T03:12:47.825Z FAIL commands=0/2 executed=1 deduped=0 elapsed=4040ms tests=c791cfc18e5b head=4f26f081db88 dirty=1
  - image-hook-regression FAIL exit=1
  - lint NOT-RUN
- 2026-11-17T03:13:43.094Z FAIL commands=0/2 executed=1 deduped=0 elapsed=2963ms tests=c791cfc18e5b head=4f26f081db88 dirty=1
  - image-hook-regression FAIL exit=1
  - lint NOT-RUN
- 2026-11-17T03:15:50.250Z PASS commands=2/2 executed=2 deduped=0 elapsed=5428ms tests=c791cfc18e5b head=4f26f081db88 dirty=1
  - image-hook-regression PASS exit=0
  - lint PASS exit=0
