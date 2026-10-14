#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-migrate
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
target_schema=${DWARF_TARGET_DATA_SCHEMA_VERSION:-1}

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
receipt=${install_root}/install-receipt
[[ -f ${receipt} && ! -L ${receipt} ]] || die "trusted installation receipt is missing"
receipt_get() {
  local key=$1 value count
  value=$(sed -n "s/^${key}=//p" "${receipt}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "receipt field ${key} is missing or duplicated"
  printf '%s\n' "${value}"
}

recorded_install=$(receipt_get install_root)
runtime_root=$(receipt_get runtime_root)
installation_id=$(receipt_get installation_id)
[[ ${recorded_install} == "${install_root}" ]] || die "receipt does not belong to this install root"
[[ ${runtime_root} == /* && ${runtime_root} != / && ${runtime_root} != "${HOME}" ]] \
  || die "receipt contains an unsafe runtime root"
[[ -d ${runtime_root} && ! -L ${runtime_root} ]] || die "runtime root is missing or unsafe"
marker=${runtime_root}/.dwarf-installation-id
[[ -f ${marker} && $(tr -d '\r\n' <"${marker}") == "${installation_id}" ]] \
  || die "runtime ownership marker does not match the receipt"
[[ ${target_schema} =~ ^[0-9]+$ && ${target_schema} == 1 ]] \
  || die "no migration path is available for data schema ${target_schema}"

lock=${runtime_root}/.dwarf-migration.lock
lock_host=$(hostname)
lock_owner=${lock_host}\ $$
acquire_lock() {
  if mkdir "${lock}" 2>/dev/null; then
    printf '%s\n' "${lock_owner}" >"${lock}/owner"
    return
  fi
  existing=
  [[ -f ${lock}/owner ]] && existing=$(tr -d '\r\n' <"${lock}/owner")
  existing_host=${existing%% *}
  existing_pid=${existing#* }
  if [[ -n ${existing} && ${existing_host} == "${lock_host}" && ${existing_pid} =~ ^[0-9]+$ ]] \
    && kill -0 "${existing_pid}" 2>/dev/null; then
    die "another data migration is active at ${lock} (pid ${existing_pid})"
  fi
  if [[ -n ${existing} && ${existing_host} != "${lock_host}" ]]; then
    die "migration lock belongs to another host (${existing_host}); inspect ${lock} before retrying"
  fi
  stale=${lock}.stale.$$
  mv "${lock}" "${stale}" 2>/dev/null \
    || die "migration lock changed while reconciling; retry"
  rm -f -- "${stale}/owner"
  rmdir "${stale}" 2>/dev/null \
    || die "stale migration lock contains unexpected files: ${stale}"
  mkdir "${lock}" 2>/dev/null || die "migration lock was acquired concurrently; retry"
  printf '%s\n' "${lock_owner}" >"${lock}/owner"
}
release_lock() {
  [[ -f ${lock}/owner && $(tr -d '\r\n' <"${lock}/owner") == "${lock_owner}" ]] || return 0
  rm -f -- "${lock}/owner"
  rmdir "${lock}" 2>/dev/null || true
}
acquire_lock
cleanup() { release_lock; }
trap cleanup EXIT HUP INT TERM

schema_marker=${runtime_root}/.dwarf-data-schema
if [[ -f ${schema_marker} ]]; then
  current=$(tr -d '\r\n' <"${schema_marker}")
  [[ ${current} =~ ^[0-9]+$ ]] || die "data schema marker is invalid"
  (( current <= target_schema )) || die "data schema ${current} is newer than supported schema ${target_schema}"
  (( current == target_schema )) || die "no migration path exists from schema ${current} to ${target_schema}"
else
  # Schema 1 is an idempotent ownership marker only. This release deliberately
  # refuses every other transition above; no runtime payload is mutated here.
  marker_tmp=${schema_marker}.tmp.$$
  printf '%s\n' "${target_schema}" >"${marker_tmp}"
  chmod 0600 "${marker_tmp}"
  mv "${marker_tmp}" "${schema_marker}"
fi

printf 'DWARF data schema %s is ready at %s\n' "${target_schema}" "${runtime_root}"
