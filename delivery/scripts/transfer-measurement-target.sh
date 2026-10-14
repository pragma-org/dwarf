#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

action=${1:-}
if [[ "${action}" != export && "${action}" != import &&
      "${action}" != export-conformance && "${action}" != import-conformance &&
      "${action}" != export-cbor-conformance && "${action}" != import-cbor-conformance ]]; then
  echo "first argument must be export, import, export-conformance, import-conformance, export-cbor-conformance, or import-cbor-conformance" >&2
  exit 2
fi
shift

legacy_qualified_export=false
if [[ "${action}" == export-conformance || "${action}" == export-cbor-conformance ]]; then
  for argument in "$@"; do
    if [[ "${argument}" == --legacy-qualified-source ]]; then
      legacy_qualified_export=true
    fi
  done
fi

installation_file="${DWARF_RUNTIME_ROOT}/state/installation-id"
if [[ "${legacy_qualified_export}" != true &&
      (! -f "${installation_file}" || -L "${installation_file}") ]]; then
  echo "persisted installation-id is missing: ${installation_file}" >&2
  exit 1
fi
if [[ "${legacy_qualified_export}" != true ]]; then
  persisted_installation=$(tr -d '\r\n' <"${installation_file}")
  if [[ -z "${persisted_installation}" ]]; then
    echo "persisted installation-id is empty: ${installation_file}" >&2
    exit 1
  fi
  if [[ -n "${DWARF_INSTALLATION_ID}" && "${DWARF_INSTALLATION_ID}" != "${persisted_installation}" ]]; then
    echo "DWARF_INSTALLATION_ID conflicts with persisted installation-id" >&2
    exit 1
  fi
  DWARF_INSTALLATION_ID=${persisted_installation}
  export DWARF_INSTALLATION_ID
fi

for argument in "$@"; do
  if [[ "${argument}" == --registry-root || "${argument}" == --registry-root=* ||
        "${argument}" == --package-root || "${argument}" == --package-root=* ||
        "${argument}" == --source-installation-id || "${argument}" == --source-installation-id=* ]]; then
    echo "registry root, package root, and source installation identity are installation-owned" >&2
    exit 2
  fi
done

export PYTHONPATH="${PACKAGE_ROOT}/dwarf${PYTHONPATH:+:${PYTHONPATH}}"
if [[ "${action}" == import-conformance || "${action}" == import-cbor-conformance ]]; then
  exec python3 -m scripts.transfer_measurement_target "${action}" "$@" \
    --registry-root "${DWARF_RUNTIME_ROOT}/state/measurement-targets" \
    --package-root "${PACKAGE_ROOT}/dwarf"
fi
if [[ "${action}" == export-conformance || "${action}" == export-cbor-conformance ]]; then
  arguments=(
    --registry-root "${DWARF_RUNTIME_ROOT}/state/measurement-targets"
    --package-root "${PACKAGE_ROOT}/dwarf"
  )
  if [[ "${legacy_qualified_export}" != true ]]; then
    arguments+=(--source-installation-id "${DWARF_INSTALLATION_ID}")
  fi
  exec python3 -m scripts.transfer_measurement_target "${action}" "$@" "${arguments[@]}"
fi
if [[ "${action}" == import ]]; then
  exec python3 -m scripts.transfer_measurement_target import "$@" \
    --registry-root "${DWARF_RUNTIME_ROOT}/state/measurement-targets"
fi
exec python3 -m scripts.transfer_measurement_target export "$@" \
  --registry-root "${DWARF_RUNTIME_ROOT}/state/measurement-targets" \
  --source-installation-id "${DWARF_INSTALLATION_ID}"
