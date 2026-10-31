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

Qianliu tasks:

```text
build/docker-build/task-release-nginx-entry.sh   image + Helm chart
build/docker-build/task-image-nginx-entry.sh     image only (no helm/sfspm)
```

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

### `libprofiler.so.0` after the RPM install

The PlatOS/Kylin nginx RPM links against gperftools:

```text
nginx: error while loading shared libraries: libprofiler.so.0
```

The Dockerfile now detects this, prints `ldd` / `rpm -qR` output, reuses a
vendored `libprofiler` if the base ships one, then tries `gperftools-libs`,
`gperftools`, `google-perftools`, `libprofiler` and `libunwind` from the same
repo.

If the repo has none of them, use copy mode with the portal-nginx image, which
already runs on this base.

## Helm chart

`build/helm-build/muad-nginx/` packages the same Deployment + Service:

```bash
bash ./build/helm-build/package-service.sh nginx-entry
```

Deploy:

```bash
helm upgrade --install muad-nginx <chart source>/muad-nginx \
  --namespace muad \
  --set global.imageRegistry=docker.sangfor.com/mss-release \
  --set global.imageTag=<TAG> \
  --set service.type=NodePort \
  --set service.nodePort=30090
```

`build/nginx-entry.yaml` remains available for plain `kubectl apply`.

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
