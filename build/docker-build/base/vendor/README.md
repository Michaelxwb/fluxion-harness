# Offline source archives

The base images are built from the intranet `python:3.11.2` image (PlatOS 1.3,
glibc 2.28). The base image has no gcc/make or package manager, so Python
3.12.13 comes from the relocatable python-build-standalone archive.

Place these files here when the build machine cannot reach GitHub/nodejs.org:

```text
cpython-3.12.13+20260325-x86_64-unknown-linux-gnu-install_only.tar.gz
node-v24.22.2-linux-x64.tar.gz      # amd64
node-v24.22.2-linux-arm64.tar.gz    # only when PLATFORM=linux/arm64
```

Optional wheels for a fully offline base build:

```text
setuptools-*.whl
wheel-*.whl
```

The Dockerfiles prefer files in this directory and fall back to
`PYTHON_STANDALONE_URL` / `NODE_DIST_URL`. Archives are gitignored; do not commit
them.
