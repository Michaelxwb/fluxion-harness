# Sandbox manual test evidence (2026-11-06)

本目录保存“Skill 自建 + 执行沙箱”方案探索阶段的手动测试证据。  
主文档：[`../../sandbox-solution.md`](../../sandbox-solution.md)

## 文件

| 文件 | 内容 |
|---|---|
| `kernel_smoke.py` | 不依赖 bwrap 的内核能力测试：user/mount/PID/net namespace、tmpfs mount、断网 |
| `bwrap_smoke.sh` | 在 PlatOS 测试镜像里跑 bwrap：Python/Node、文件隔离、网络开关、Unix socket |
| `Dockerfile.sandbox-test` | 从实际 `mss-base/python-node:3.12.13-node24` 构建的 bwrap 测试镜像 |
| `base-image-inspection.txt` | 基础镜像 OS/glibc/Python/Node/包管理器检查 |
| `landlock-seccomp.txt` | Landlock 不可用、seccomp 现状与结论 |
| `bwrap-overhead.txt` | bwrap 每次执行的启动与 RSS 开销基准 |
| `service-auth-overhead.txt` | 门控配置构造/同步调度的本地 ASGI 对照与复现命令 |
| `local-macos-bwrap.txt` | macOS/OrbStack 探索阶段实测输出（bwrap 0.13.2） |
| `new-base-bwrap-smoke.txt` | 最终底座 bwrap 0.13.1 的冒烟输出 |
| `intranet-k8s-kernel-smoke.txt` | 内网 runtime/worker Pod 实测输出 |
| `session-probe.txt` | bwrap + 真实 session_cli + Unix socket 前置测试输出 |
| `session-probe-job.yaml` | 内网 K8s 运行 session probe 的 Job |
| `session_probe.py` | session-manager 前置 probe 源码（模拟 broker） |
| `Dockerfile.session-probe` | probe 测试镜像 Dockerfile |
| `real_broker_probe.py` | P0.3 真实 Runtime broker canary（Console/Redis/真实凭据 + bwrap） |
| `real-broker-probe.txt` | P0.3 真实链路实测输出（真实平台 HTTP 200） |

> 外部复查反馈没有纳入仓库原文；问题与处理结论已归纳到主文档
> [`../../sandbox-solution.md`](../../sandbox-solution.md) 第 13 节。

## 结论摘要

- bwrap 在实际 PlatOS 基础镜像中可运行（macOS amd64 模拟环境）。
- 内网 runtime/worker 真实 Pod 均通过内核 namespace/mount 测试。
- 内网 K8s Job 的 bwrap + 真实 `session_cli.py` + Unix socket probe 已 PASS（模拟 broker）。
- P0.3 真实 Runtime broker + Console/Redis/session-manager + `requests` 已 PASS（根路径 HTTP 200）；
  证明状态交付/HTTP 连通性，受保护业务接口的 Cookie 鉴权仍待实测。
- 受测 Pod 没有显式 `seccompProfile`，实际允许 `unshare`/`mount`，本次无需修改；其他节点/运行时须复核。
- 生产内核 4.18 不支持 Landlock，`nono-py` 路线不可用。
- 基础镜像没有包管理器，bwrap 必须由 builder stage 构建后复制进镜像。
- 内网 Runtime Pod 中的 bwrap 二进制和真实 session-manager 已在独立 canary 中执行。
- 尚未验证：受保护业务接口的有效/无/无效 Cookie 对照、真实 Skill 包经统一执行器运行、
  Worker async、受信 API 拒绝路径、输出/挂载/资源控制和最终镜像/Pod 指纹。
- 当前对话 Skill 尚未接入 bwrap；本次为方案细化和手动联调准备，受保护接口真实结果待实测后记录。

## 复现方式

### 1. 内核 smoke（无 bwrap，可直接跑在现有 Pod）

```bash
POD=$(kubectl -n muad get pod -l app=muad-agent-runtime \
  -o jsonpath='{.items[0].metadata.name}')

kubectl -n muad exec -i "$POD" -- python - < kernel_smoke.py
```

期望：

```text
OK user
OK namespaces
OK mount
OK pid 1
OK net 101
RESULT: PASS
```

### 2. 本地构建含 bwrap 的基础镜像

在仓库根目录执行：

```bash
docker build --platform linux/amd64 \
  --build-arg BASE_IMAGE=docker.sangfor.com/mss-base/python:3.12.13 \
  --build-arg TARGETARCH=amd64 \
  -t docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  -f build/docker-build/base/Dockerfile.python-node \
  build/docker-build/base

docker tag \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24
```

验证基础镜像：

```bash
docker run --rm --platform linux/amd64 \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  sh -ec 'bwrap --version; python --version; node --version'
```

预期：

```text
bubblewrap 0.13.1
Python 3.12.13
v24.22.2
```

### 3. 本地构建 session-manager 前置测试镜像

```bash
docker build --platform linux/amd64 \
  --build-arg BASE_IMAGE=docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  -t docker.sangfor.com/mss-base/sandbox-session-probe:0.1 \
  -f build/docker-build/sandbox/Dockerfile.session-probe .
```

本地运行（Docker 默认 seccomp 会拦 `unshare`，所以本地加 `--security-opt seccomp=unconfined`）：

```bash
docker run --rm --platform linux/amd64 \
  --security-opt seccomp=unconfined \
  docker.sangfor.com/mss-base/sandbox-session-probe:0.1
```

预期：

```text
bwrap_rc 0
stdout session_ok probe-cookie
stderr
RESULT: PASS
```

内网 K8s 运行（2026-11-06 已 PASS）：

```bash
kubectl apply -f docs/operations/evidence/sandbox-2026-11-06/session-probe-job.yaml
kubectl -n muad wait --for=condition=complete job/fx-session-probe --timeout=120s
kubectl -n muad logs job/fx-session-probe
```

### 4. 本地 bwrap 测试镜像

```bash
docker build --platform linux/amd64 \
  -f Dockerfile.sandbox-test \
  -t fx-sandbox-test:local .

docker run --rm --platform linux/amd64 \
  --security-opt seccomp=unconfined \
  -v "$PWD/bwrap_smoke.sh:/smoke.sh:ro" \
  fx-sandbox-test:local sh /smoke.sh
```

注意：本地 Docker 默认 seccomp 会拦截 `unshare`，所以本地测试需要
`--security-opt seccomp=unconfined`；受测内网 Pod 实际允许，本次不需该参数，结论不推广到其他环境。

### 5. 在现有 Pod 里测试 bwrap 二进制（可选，不构建镜像）

从本地测试镜像取出：

```bash
docker create --name fx-bwrap fx-sandbox-test:local
docker cp fx-bwrap:/usr/local/bin/bwrap ./bwrap
docker cp fx-bwrap:/lib/ld-musl-x86_64.so.1 ./ld-musl-x86_64.so.1
docker cp fx-bwrap:/usr/lib/libcap.so.2 ./libcap.so.2
docker rm fx-bwrap
tar czf bwrap-test.tar.gz bwrap ld-musl-x86_64.so.1 libcap.so.2
```

复制进 Pod 并用 musl loader 运行：

```bash
POD=$(kubectl -n muad get pod -l app=muad-agent-runtime \
  -o jsonpath='{.items[0].metadata.name}')

kubectl -n muad cp ./bwrap-test.tar.gz "$POD":/tmp/

kubectl -n muad exec -i "$POD" -- sh -ec '
  rm -rf /tmp/bwrap-test && mkdir -p /tmp/bwrap-test
  tar xzf /tmp/bwrap-test.tar.gz -C /tmp/bwrap-test
  cd /tmp/bwrap-test
  chmod 755 bwrap ld-musl-x86_64.so.1
  ./ld-musl-x86_64.so.1 --library-path . ./bwrap --version
'
```

这是探索测试路径；生产采用共享基础镜像内置 bwrap，不依赖临时复制或额外 volume 分发。

## P0.3 真实 broker canary

`real_broker_probe.py` 不经过对话 Skill，直接调用真实
`skill_platform_environment`，验证 Console/Redis/平台凭据 + bwrap +
`session-manager` + `requests` 的状态交付与 HTTP 链路，不证明 Run 身份绑定或业务鉴权。

在 canary runtime Pod 内：

```bash
kubectl cp real_broker_probe.py <pod>:/tmp/real_broker_probe.py

kubectl exec -it <pod> -- env \
  TEST_ACTOR_USER_ID='<platform-user-uuid>' \
  TEST_PLATFORM_KEY='<platform-key>' \
  TEST_URL='https://test-platform.example/health' \
  TEST_SKILL_NAME='real-broker-probe' \
  python /tmp/real_broker_probe.py
```

URL 可直接带 path/query；需要 query 对象或 POST body 时：

```bash
TEST_METHOD=POST \
TEST_URL='/api/v1/query' \
TEST_PARAMS_JSON='{"page":1}' \
TEST_BODY_JSON='{"customer_id":"C-1001"}' \
python /tmp/real_broker_probe.py
```

预期：

```text
bwrap_rc 0
stdout {"ok": true, "check_scope": "http_status", "status": 200, ...}
RESULT: PASS
```

探针已禁止自动跳转，默认仅接受 2xx，保留 POST 空 JSON `{}`，并区分 `/api` 根路径与
`api` 相对路径。`TEST_REQUIRE_JSON=1` 检查 JSON；`TEST_EXPECT_JSON` 匹配非空的顶层业务字段。
HTTP/JSON 检查通过不等于 Cookie 鉴权通过，成功码必须来自真实响应契约。

受保护接口 `GET /order/v1/get_expert` 的三组实测命令与验收条件见
[主文档 P0.3.2](../../sandbox-solution.md)。命令先复制最新脚本，再用 `TEST_COOKIE_MODE`
分别发送 `valid`/`none`/`invalid`；`TEST_EXPECT_STATUS=any` 只输出 `RESULT: OBSERVED`，
不能记为鉴权 PASS。Cookie/CSRF 来自 broker，不复制浏览器登录凭据。
`TEST_HEADERS_JSON` 仅用于非凭据请求头；禁止 Cookie/Authorization 覆盖。
`TEST_SHOW_BODY=1` 输出最多 200 字符的脱敏 JSON 前缀，仍可能含业务数据，不原样归档。

基础镜像更新后仍须通过现有流水线重建、部署 runtime/worker 服务镜像；已有镜像不会自动继承。
下述 tar SHA256 只校验归档文件，最终运行身份需记录 OCI digest、Pod imageID 与源码 commit SHA。

## 本地产物

```text
/Users/jahan/Downloads/muad-bwrap-images/muad-python-node-bwrap-20261106.tar
sha256: f1d76f96fed62619e1da2a76bf047b0c366800ce0ff472f398527c558982eeec
```

内含：

```text
docker.sangfor.com/mss-base/python-node:3.12.13-node24
docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106
docker.sangfor.com/mss-base/sandbox-session-probe:0.1
```

重建命令：

```bash
bash build/docker-build/sandbox/build.sh
```

