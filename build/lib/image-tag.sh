#!/usr/bin/env bash
# Shared image tag resolution for the muad build scripts.
#
# Default format (matches the intranet convention):
#   1.0.0.<yyMMddHHmm><branch>      e.g. 1.0.0.2610302008feature-tiyanwenti-1030-txj
#
# The timestamp is the build time, so every pipeline run produces a new tag.
# Branch slashes become dashes to stay a valid docker tag.
#
# Order:
#   1. explicit TAG
#   2. CI_COMMIT_TAG           (release: v1.0.0 -> 1.0.0)
#   3. commit time + branch    (CI_COMMIT_BRANCH or the current git branch)
#   4. wall clock + branch     (no git metadata available)
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

  timestamp="$(date +%y%m%d%H%M)"

  printf '%s\n' "1.0.0.${timestamp}${branch}"
}
