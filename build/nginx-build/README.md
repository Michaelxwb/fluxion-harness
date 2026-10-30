# muad nginx entry

A small nginx reverse proxy for the intranet:

```text
/                      -> muad-console-platform:8000
/api                   -> muad-console-platform:8000
/chat, /assets         -> muad-console-platform:8000
/api/v1/webchat/auth   -> muad-console-platform:8000
/api/v1/webchat/*      -> muad-im-gateway:8003
/api/v1/webchat/runs   -> muad-im-gateway:8003 (SSE, no buffering)
```

## Build

From the repository root:

```bash
BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2 \
IMAGE_REPOSITORY=docker.sangfor.com/mss-release/muad-nginx \
  ./build/nginx-build/build.sh
```

Qianliu task: `build/docker-build/task-image-nginx-entry.sh` (independent image
pipeline; no helm/sfspm needed).

The default mode installs nginx from the PlatOS/Kylin RPM repo:

```text
/opt/sangfor/scripts/install_rpm.sh nginx
```

If that fails (no nginx RPM / no package manager), use copy mode with an
existing nginx image that is compatible with the base glibc:

```bash
NGINX_BUILD_MODE=copy \
NGINX_SOURCE_IMAGE=<compatible-nginx-image> \
BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2 \
IMAGE_REPOSITORY=docker.sangfor.com/mss-release/muad-nginx \
  ./build/nginx-build/build.sh
```

`nginx:1.24-bullseye` is glibc 2.31; do not use it if the base is glibc 2.28
unless you have verified the binary runs.

## Deploy

1. Change console and gateway Services to ClusterIP and remove their NodePorts:

```text
console-platform: service.type=ClusterIP, remove service.nodePort
im-gateway:       service.type=ClusterIP, remove service.nodePort
```

2. Update the image in `build/nginx-entry.yaml` to the tag you built.
3. Apply:

```bash
kubectl apply -f build/nginx-entry.yaml
```

4. Access:

```text
http://<master-ip>:30090/
http://<master-ip>:30090/api/v1/webchat/auth
```

## Notes

- `portal-nginx` is a custom reverse proxy, not an Ingress controller; this
  image is a separate entry and does not touch `mss-pro/portal-nginx`.
- The nginx upstreams are Kubernetes Service names; deploy console/gateway
  Services before this Deployment.
- If TLS is terminated in front of this nginx, `X-Forwarded-Proto` is already
  forwarded; add the TLS listener/secret if you terminate here.
- The image runs as UID 10001; nginx pid/temp paths are under `/tmp/nginx`.
