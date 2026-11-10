#!/bin/sh
# 本地 3 主 3 从 Redis Cluster（单容器 6 进程）。
#
# 为什么是一个容器 6 个进程，而不是 6 个容器：
#   Redis Cluster 节点之间要互连「通告地址」。macOS 上 Docker 没有 host 网络，
#   多容器方案里节点通告的容器 IP 从宿主机不可达，集群建不起来、客户端发现后也连不上。
#   放在一个容器内统一用 127.0.0.1:6391-6396，宿主机通过同样的端口映射访问，
#   容器内、宿主机看到的是同一组地址。
#
# 端口分配（与生产 3M3R 的槽位划分一致：0-5460 / 5461-10922 / 10923-16383）：
#   6391 6392 6393  master
#   6394 6395 6396  replica（分别跟随 6391 6392 6393）
set -eu

PORTS="6391 6392 6393 6394 6395 6396"

for p in $PORTS; do
  redis-server \
    --port "$p" \
    --cluster-enabled yes \
    --cluster-config-file "/data/nodes-$p.conf" \
    --cluster-node-timeout 5000 \
    --appendonly no \
    --dir /data \
    --protected-mode no &
done

# 等所有节点可应答
for p in $PORTS; do
  i=0
  until redis-cli -p "$p" ping >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 50 ]; then
      echo "redis node $p did not start" >&2
      exit 1
    fi
    sleep 0.2
  done
done

# 只在集群尚未建立时创建；容器重启（nodes.conf 仍在 /data）会直接跳过
state=$(redis-cli -p 6391 cluster info 2>/dev/null | tr -d '\r' | grep -o 'cluster_state:[a-z]*' || true)
if [ "$state" != "cluster_state:ok" ]; then
  redis-cli -p 6391 --cluster create \
    127.0.0.1:6391 127.0.0.1:6392 127.0.0.1:6393 \
    127.0.0.1:6394 127.0.0.1:6395 127.0.0.1:6396 \
    --cluster-replicas 1 --cluster-yes
fi

wait
