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
| `local-macos-bwrap.txt` | macOS/OrbStack 探索阶段实测输出（bwrap 0.13.2） |
| `new-base-bwrap-smoke.txt` | 最终底座 bwrap 0.13.1 的冒烟输出 |
| `intranet-k8s-kernel-smoke.txt` | 内网 runtime/worker Pod 实测输出 |
| `session-probe.txt` | bwrap + 真实 session_cli + Unix socket 前置测试输出 |
| `session-probe-job.yaml` | 内网 K8s 运行 session probe 的 Job |
| `session_probe.py` | session-manager 前置 probe 源码（模拟 broker） |
| `Dockerfile.session-probe` | probe 测试镜像 Dockerfile |
| `real_broker_probe.py` | P0.3 真实 Runtime broker canary（Console/Redis/真实凭据 + bwrap） |

> 外部复查反馈没有纳入仓库原文；问题与处理结论已归纳到主文档
> [`../../sandbox-solution.md`](../../sandbox-solution.md) 第 13 节。

## 结论摘要

- bwrap 在实际 PlatOS 基础镜像中可运行（macOS amd64 模拟环境）。
- 内网 runtime/worker 真实 Pod 均通过内核 namespace/mount 测试。
- 内网 K8s Job 的 bwrap + 真实 `session_cli.py` + Unix socket probe 已 PASS（模拟 broker）。
- 内网当前没有显式 `seccompProfile`，实际允许 `unshare`/`mount`，不需要改 seccomp。
- 生产内核 4.18 不支持 Landlock，`nono-py` 路线不可用。
- 基础镜像没有包管理器，bwrap 必须由 builder stage 构建后复制进镜像。
- 尚未执行：内网 Pod 内的 bwrap 二进制、真实 `session-manager`、真实 Skill 包。这是 P0 的剩余项。

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
  sh -c 'bwrap --version; python --version; node --version'
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
`--security-opt seccomp=unconfined`；内网 K8s 默认策略已实测允许，不需要该参数。

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

kubectl -n muad exec -i "$POD" -- sh -c '
  rm -rf /tmp/bwrap-test && mkdir -p /tmp/bwrap-test
  tar xzf /tmp/bwrap-test.tar.gz -C /tmp/bwrap-test
  cd /tmp/bwrap-test
  chmod 755 bwrap ld-musl-x86_64.so.1
  ./ld-musl-x86_64.so.1 --library-path . ./bwrap --version
'
```

这是测试路径；生产应把 bwrap 放进共享基础镜像，或使用只读 volume 挂载。

## P0.3 真实 broker canary

`real_broker_probe.py` 不经过对话 Skill，直接调用真实
`skill_platform_environment`，验证 Console/Redis/平台凭据 + bwrap +
`session-manager` + `requests` 的完整链路。

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
stdout {"ok": true, "status": 200, ...}
RESULT: PASS
```

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

