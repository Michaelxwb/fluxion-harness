# Offline source tarballs

The two base images are built from the intranet `python:3.11.2` image and only
need two upstream source archives:

```text
Python-3.12.13.tgz
node-v24.22.2-linux-x64.tar.gz      # amd64
node-v24.22.2-linux-arm64.tar.gz    # only when PLATFORM=linux/arm64
```

Place them in this directory when the build machine cannot reach
`python.org` / `nodejs.org`. The Dockerfiles prefer files in this directory and
fall back to `PYTHON_SOURCE_URL` / `NODE_DIST_URL`.

Optional: put `pip-*.whl`, `setuptools-*.whl`, and `wheel-*.whl` here as well;
the Python base then installs the build tools with `--no-index`.

Tarballs are gitignored; do not commit them.
