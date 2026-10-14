#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

bash "${SCRIPT_DIR}/verify-build-source.sh" \
  "${PACKAGE_ROOT}" \
  "${DWARF_SOURCE_REVISION}" \
  "${DWARF_CATALOG_REVISION}"

ensure_package_layout

BUILD_TMP_ROOT=${TMPDIR:-/tmp}
BUILD_TMP_ROOT=${BUILD_TMP_ROOT%/}
if [[ -z "${BUILD_TMP_ROOT}" ]]; then
  BUILD_TMP_ROOT=/tmp
fi
BUILD_CONTEXT=$(mktemp -d "${BUILD_TMP_ROOT}/dwarf-image-build.XXXXXX")
cleanup_build_context() {
  if [[ -n "${BUILD_CONTEXT:-}" && -d "${BUILD_CONTEXT}" ]]; then
    case "${BUILD_CONTEXT}" in
      "${BUILD_TMP_ROOT}"/dwarf-image-build.*) rm -rf -- "${BUILD_CONTEXT}" ;;
      *) echo "refusing unsafe build-context cleanup: ${BUILD_CONTEXT}" >&2 ;;
    esac
  fi
}
trap cleanup_build_context EXIT HUP INT TERM

bash "${SCRIPT_DIR}/stage-build-context.sh" \
  "${PACKAGE_ROOT}" \
  "${DWARF_SOURCE_REVISION}" \
  "${BUILD_CONTEXT}"

require_docker

echo "Building ${DWARF_IMAGE} from staged Git archive ${DWARF_SOURCE_REVISION}"
docker build \
  --build-arg "DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}" \
  --build-arg "DWARF_COMPONENT_ROLE=dashboard" \
  --build-arg "DWARF_CATALOG_REVISION=${DWARF_CATALOG_REVISION}" \
  --build-arg "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}" \
  -f "${BUILD_CONTEXT}/infrastructure/docker/dwarf-fw.Dockerfile" \
  -t "${DWARF_IMAGE}" \
  "${BUILD_CONTEXT}"

docker image inspect "${DWARF_IMAGE}" >/dev/null
echo "Built image: ${DWARF_IMAGE}"

echo "Building ${DWARF_CONTROLLER_IMAGE} from ${DWARF_IMAGE}"
docker build \
  --build-arg "DWARF_FRAMEWORK_IMAGE=${DWARF_IMAGE}" \
  --build-arg "DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}" \
  --build-arg "DWARF_CATALOG_REVISION=${DWARF_CATALOG_REVISION}" \
  -f "${BUILD_CONTEXT}/infrastructure/docker/dwarf-controller.Dockerfile" \
  -t "${DWARF_CONTROLLER_IMAGE}" \
  "${BUILD_CONTEXT}"

docker image inspect "${DWARF_CONTROLLER_IMAGE}" >/dev/null
if docker image inspect --format '{{json .Config.Volumes}}' "${DWARF_CONTROLLER_IMAGE}" \
  | grep -q '"/var/dwarf/state"'; then
  echo "controller image must not inherit the dashboard state volume" >&2
  exit 1
fi
docker run --rm --entrypoint sh "${DWARF_CONTROLLER_IMAGE}" -c '
  set -eu
  command -v cardano-testnet >/dev/null
  command -v cardano-cli >/dev/null
  command -v cardano-node >/dev/null
  cardano-testnet version >/dev/null
  cardano-cli --version >/dev/null
  cardano-node --version >/dev/null
'
echo "Built image: ${DWARF_CONTROLLER_IMAGE}"
