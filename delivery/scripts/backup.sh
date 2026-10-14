#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-backup
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
output=

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --output) (($# >= 2)) || die "--output requires a path"; output=$2; shift 2 ;;
    --help|-h) printf 'Usage: backup.sh [--output ABSOLUTE_TAR_GZ]\n'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

receipt=${install_root}/install-receipt
[[ -f ${receipt} && ! -L ${receipt} ]] || die "trusted installation receipt is missing"
field() {
  local key=$1 value count
  value=$(sed -n "s/^${key}=//p" "${receipt}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "receipt field ${key} is missing or duplicated"
  printf '%s\n' "${value}"
}
[[ $(field install_root) == "${install_root}" ]] || die "receipt does not belong to this install root"
runtime_root=$(field runtime_root)
installation_id=$(field installation_id)
release_version=$(field release_version)
release_revision=$(field release_revision)
[[ ${runtime_root} == /* && ${runtime_root} != / && ${runtime_root} != "${HOME}" ]] \
  || die "receipt contains an unsafe runtime root"
[[ -d ${runtime_root} && ! -L ${runtime_root} ]] || die "runtime root is missing or unsafe"
[[ -f ${runtime_root}/.dwarf-installation-id ]] \
  || die "runtime ownership marker is missing"
[[ $(tr -d '\r\n' <"${runtime_root}/.dwarf-installation-id") == "${installation_id}" ]] \
  || die "runtime ownership marker does not match the receipt"
[[ -L ${install_root}/current && -f ${install_root}/current/docker-compose.dwarf.yml \
  && -f ${install_root}/.env ]] || die "active release metadata is incomplete"
command -v docker >/dev/null 2>&1 || die "Docker CLI is required for a consistent backup"
docker info >/dev/null 2>&1 || die "Docker Engine/Desktop must be running for a consistent backup"
owned_running=$(docker ps -q --filter "label=io.dwarf.managed=true" \
  --filter "label=io.dwarf.installation=${installation_id}")
[[ -z ${owned_running} ]] \
  || die "remove or stop controller-owned topology containers before backup"
compose=(docker compose --project-directory "${install_root}" --env-file "${install_root}/.env" -f "${install_root}/current/docker-compose.dwarf.yml")
running_services=$("${compose[@]}" ps --status running --services)
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
[[ -n ${output} ]] || output=${install_root}/backups/dwarf-data-${timestamp}-$$.tar.gz
[[ ${output} == /* && ${output} != "${runtime_root}"/* ]] \
  || die "backup output must be absolute and outside the runtime root"
mkdir -p "$(dirname "${output}")"
[[ ! -e ${output} ]] || die "backup output already exists"

runtime_parent=$(dirname "${runtime_root}")
runtime_name=$(basename "${runtime_root}")
tmp=${output}.tmp.$$
meta=${output%.tar.gz}.json
writers_stopped=false
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  rm -f -- "${tmp}"
  if [[ ${writers_stopped} == true ]]; then
    if ! "${compose[@]}" up -d --wait --wait-timeout 180; then
      printf '%s: backup exit %s; managed services also failed to restart\n' "${PROGRAM}" "${rc}" >&2
      exit 1
    fi
  fi
  exit "${rc}"
}
trap cleanup EXIT HUP INT TERM
if [[ -n ${running_services} ]]; then
  writers_stopped=true
  "${compose[@]}" stop
fi
tar -czf "${tmp}" -C "${runtime_parent}" "${runtime_name}"
chmod 0600 "${tmp}"
mv "${tmp}" "${output}"
if command -v sha256sum >/dev/null 2>&1; then digest=$(sha256sum "${output}" | awk '{print $1}')
else digest=$(shasum -a 256 "${output}" | awk '{print $1}'); fi
json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
printf '{"installation_id":"%s","release_version":"%s","release_revision":"%s","runtime_root":"%s","archive":"%s","sha256":"%s"}\n' \
  "$(json_escape "${installation_id}")" "$(json_escape "${release_version}")" \
  "$(json_escape "${release_revision}")" "$(json_escape "${runtime_root}")" \
  "$(json_escape "${output}")" "${digest}" >"${meta}"
chmod 0600 "${meta}"
printf 'backup=%s\nsha256=%s\nmetadata=%s\n' "${output}" "${digest}" "${meta}"
