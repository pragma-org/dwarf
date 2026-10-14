#!/usr/bin/env bash
set -euo pipefail

PACKAGE_ROOT=${1:?usage: verify-build-source.sh PACKAGE_ROOT SOURCE_REVISION CATALOG_REVISION}
SOURCE_REVISION=${2:?usage: verify-build-source.sh PACKAGE_ROOT SOURCE_REVISION CATALOG_REVISION}
CATALOG_REVISION=${3:?usage: verify-build-source.sh PACKAGE_ROOT SOURCE_REVISION CATALOG_REVISION}

if ! git -C "${PACKAGE_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "refusing image build: package root is not a Git checkout: ${PACKAGE_ROOT}" >&2
  exit 1
fi

CHECKOUT_ROOT=$(git -C "${PACKAGE_ROOT}" rev-parse --show-toplevel)
PACKAGE_ROOT_ABS=$(cd "${PACKAGE_ROOT}" && pwd -P)
CHECKOUT_ROOT_ABS=$(cd "${CHECKOUT_ROOT}" && pwd -P)
if [[ "${PACKAGE_ROOT_ABS}" != "${CHECKOUT_ROOT_ABS}" ]]; then
  echo "refusing image build: package root is not the Git checkout root: ${PACKAGE_ROOT_ABS}" >&2
  exit 1
fi

HEAD_REVISION=$(git -C "${PACKAGE_ROOT}" rev-parse HEAD)
if [[ "${SOURCE_REVISION}" != "${HEAD_REVISION}" ]]; then
  echo "refusing image build: DWARF_SOURCE_REVISION does not match HEAD (${SOURCE_REVISION} != ${HEAD_REVISION})" >&2
  exit 1
fi
if [[ "${CATALOG_REVISION}" != "${HEAD_REVISION}" ]]; then
  echo "refusing image build: DWARF_CATALOG_REVISION does not match HEAD (${CATALOG_REVISION} != ${HEAD_REVISION})" >&2
  exit 1
fi

WORKTREE_STATUS=$(git -C "${PACKAGE_ROOT}" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)
if [[ -n "${WORKTREE_STATUS}" ]]; then
  echo "refusing image build: worktree is not clean; commit or remove tracked and untracked changes first" >&2
  printf '%s\n' "${WORKTREE_STATUS}" >&2
  exit 1
fi

echo "Verified clean build source: ${HEAD_REVISION}"
