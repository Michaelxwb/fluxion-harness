# syntax=docker/dockerfile:1
# Test-only probe image: same base family + same dependency install script as
# build/docker-build/Dockerfile.agent-runtime and Dockerfile.agent-worker.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
ARG SKILL_DEPS_SHA256=unknown
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org/

USER root
COPY build/docker-build/skill-deps /tmp/skill-deps
RUN PIP_INDEX_URL="${PIP_INDEX_URL}" \
      NPM_REGISTRY="${NPM_REGISTRY}" \
      sh /tmp/skill-deps/install.sh /tmp/skill-deps \
 && rm -rf /tmp/skill-deps
LABEL org.muad.base-image="${BASE_IMAGE}"
LABEL org.muad.skill-deps.sha256="${SKILL_DEPS_SHA256}"

USER 10001:10001
