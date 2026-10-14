#!/usr/bin/env bash
set -euo pipefail

PACKAGE_ROOT=${1:?usage: stage-build-context.sh PACKAGE_ROOT SOURCE_REVISION DESTINATION}
SOURCE_REVISION=${2:?usage: stage-build-context.sh PACKAGE_ROOT SOURCE_REVISION DESTINATION}
DESTINATION=${3:?usage: stage-build-context.sh PACKAGE_ROOT SOURCE_REVISION DESTINATION}

if ! git -C "${PACKAGE_ROOT}" cat-file -e "${SOURCE_REVISION}^{commit}" 2>/dev/null; then
  echo "cannot stage build context: revision is not a Git commit: ${SOURCE_REVISION}" >&2
  exit 1
fi
if [[ ! -d "${DESTINATION}" ]]; then
  echo "cannot stage build context: destination does not exist: ${DESTINATION}" >&2
  exit 1
fi
if [[ -n "$(find "${DESTINATION}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "cannot stage build context: destination is not empty: ${DESTINATION}" >&2
  exit 1
fi

git -C "${PACKAGE_ROOT}" archive --format=tar "${SOURCE_REVISION}" \
  | tar -xf - -C "${DESTINATION}"

echo "Staged Git archive build context: ${SOURCE_REVISION}"
