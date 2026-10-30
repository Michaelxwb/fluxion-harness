#!/usr/bin/env bash
# Shared image tag resolution for the muad build scripts.
#
# TAG is optional everywhere. The order is:
#   1. explicit TAG
#   2. CI_COMMIT_TAG           (git tag releases)
#   3. CI_COMMIT_SHA           (same commit -> same tag in all five pipelines)
#   4. git short SHA of the checkout
#   5. timestamp + branch      (no git metadata available)
#
# Using the commit SHA keeps the five independent service pipelines aligned
# without anyone setting TAG by hand.
default_image_tag() {
  local repo_root="${1:-$(pwd)}"
  local tag=""

  if [[ -n "${TAG:-}" ]]; then
    tag="${TAG}"
  elif [[ -n "${CI_COMMIT_TAG:-}" ]]; then
    tag="${CI_COMMIT_TAG#v}"
  elif [[ -n "${CI_COMMIT_SHA:-}" ]]; then
    tag="${CI_COMMIT_SHA:0:8}"
  else
    tag="$(git -C "${repo_root}" rev-parse --short=8 HEAD 2>/dev/null || true)"
    if [[ -z "${tag}" ]]; then
      tag="$(date +%y%m%d%H%M)"
      if [[ -n "${CI_COMMIT_BRANCH:-}" ]]; then
        tag="${tag}$(printf '%s' "${CI_COMMIT_BRANCH}" | tr '/:' '__')"
      fi
    fi
  fi

  printf '%s\n' "${tag//\//_}"
}
