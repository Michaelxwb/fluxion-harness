#!/usr/bin/env bash
# Shared image tag resolution for the muad build scripts.
#
# Default format (matches the intranet convention):
#   1.0.0.<yyMMddHHmm><branch>      e.g. 1.0.0.2610302008feature-tiyanwenti-1030-txj
#
# The timestamp is the build time of the pipeline run: one run produces one tag
# for all services, and the next run produces a different one. A per-task clock
# cannot provide it — parallel tasks start minutes apart and gave console and
# im-gateway 2611031311 and 2611031310 in the same run.
#
# Qianliu exposes no run-scoped timestamp variable, so the frontend node (the
# first node of the pipeline) writes the value once into a file inside the
# /tmp/muad-helm cache that every service node downloads, and every service
# reads it back. Under CI a missing file is a hard error: falling back to the
# task clock would silently produce mismatched tags within one run.
#
# Order:
#   1. explicit TAG
#   2. CI_COMMIT_TAG             (release: v1.0.0 -> 1.0.0)
#   3. TAG_TIMESTAMP             (yyMMddHHmm set once per pipeline run)
#      CI_PIPELINE_CREATED_AT    (pipeline creation time, ISO-8601)
#      anchor file               (written by the frontend node)
#   4. wall clock                (local runs only; CI fails instead)
# Branch slashes become dashes to stay a valid docker tag.
default_image_tag() {
  local repo_root="${1:-$(pwd)}"
  local branch=""
  local timestamp=""

  if [[ -n "${TAG:-}" ]]; then
    printf '%s\n' "${TAG//\//-}"
    return 0
  fi

  if [[ -n "${CI_COMMIT_TAG:-}" ]]; then
    printf '%s\n' "${CI_COMMIT_TAG#v}"
    return 0
  fi

  branch="${CI_COMMIT_BRANCH:-}"
  if [[ -z "${branch}" ]]; then
    branch="$(git -C "${repo_root}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [[ "${branch}" == "HEAD" ]]; then
      branch=""
    fi
  fi
  branch="${branch//\//-}"
  branch="${branch//:/-}"

  # Lives in the helm cache directory that every service build node shares. The
  # frontend node runs first and seeds it; later nodes only read it back. Kept
  # out of /tmp/muad-console-frontend so it can never disturb the frontend dist.
  local anchor_dir="${TAG_CACHE_DIR:-/tmp/muad-helm/tag}"
  local anchor_file="${anchor_dir}/${TAG_ANCHOR_NAME:-TIMESTAMP}"

  if [[ -n "${TAG_TIMESTAMP:-}" ]]; then
    timestamp="${TAG_TIMESTAMP}"
  elif [[ -n "${CI_PIPELINE_CREATED_AT:-}" ]]; then
    timestamp="$(date -d "${CI_PIPELINE_CREATED_AT}" +%y%m%d%H%M 2>/dev/null || true)"
  fi
  if [[ -z "${timestamp}" && -s "${anchor_file}" ]]; then
    timestamp="$(tr -d '[:space:]' < "${anchor_file}")"
  fi
  if [[ -z "${timestamp}" ]]; then
    # The frontend node is the first node of the pipeline and the only writer
    # of this file. A service node missing it means the cache was not restored
    # or the frontend node did not run; falling back to this task's clock would
    # silently give this service a different tag, so fail loudly instead.
    if [[ -n "${CI_PIPELINE_ID:-}" ]]; then
      printf '[image-tag] error: no run timestamp anchor at %s. The frontend node must run first and upload /tmp/muad-helm, otherwise services of one run get different tags.\n' \
        "${anchor_file}" >&2
      return 1
    fi
    timestamp="$(date +%y%m%d%H%M)"
    mkdir -p "${anchor_dir}" 2>/dev/null || true
    printf '%s\n' "${timestamp}" > "${anchor_file}" 2>/dev/null || true
    printf '[image-tag] info: local run, using this task clock (%s).\n' "${timestamp}" >&2
  else
    printf '[image-tag] info: run timestamp %s (from TAG_TIMESTAMP/CI_PIPELINE_CREATED_AT/anchor).\n' \
      "${timestamp}" >&2
  fi

  printf '%s\n' "1.0.0.${timestamp}${branch}"
}
