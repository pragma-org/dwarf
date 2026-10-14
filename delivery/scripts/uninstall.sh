#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-uninstall
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
purge_path=
confirmation=${DWARF_PURGE_CONFIRMATION:-}
remove_images=false

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --purge) (($# >= 2)) || die "--purge requires the exact runtime path"; purge_path=$2; shift 2 ;;
    --confirm) (($# >= 2)) || die "--confirm requires the exact confirmation text"; confirmation=$2; shift 2 ;;
    --remove-images|--remove-image) remove_images=true; shift ;;
    --help|-h)
      cat <<'EOF'
Usage: uninstall.sh [--purge EXACT_RUNTIME_PATH --confirm 'PURGE DWARF INSTALLATION_ID'] [--remove-images]

Stops the DWARF application. Persistent evidence and configuration are preserved
unless both an exact receipt-bound path and exact confirmation are supplied.
EOF
      exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

receipt=${install_root}/install-receipt
if [[ ! -f ${receipt} || -L ${receipt} ]]; then
  script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  source_compose=${script_dir}/../docker-compose.dwarf.yml
  source_root=$(cd "${script_dir}/../.." && pwd)
  [[ -f ${source_compose} ]] || die "verified installation receipt is missing"
  [[ -z ${purge_path} ]] || die "source-checkout installs cannot purge data without a verified release receipt"
  # shellcheck source=common.sh
  source "${script_dir}/common.sh"
  load_persisted_identity
  require_docker
  compose_args=(docker compose --project-directory "${source_root}")
  [[ -f ${source_root}/.env ]] && compose_args+=(--env-file "${source_root}/.env")
  "${compose_args[@]}" -f "${source_compose}" down --remove-orphans
  printf 'Stopped source-checkout stack; preserving all runtime data.\n'
  if [[ ${remove_images} == true ]]; then
    dashboard_image=
    controller_image=
    if [[ -f ${source_root}/.env ]]; then
      dashboard_image=$(sed -n 's/^DWARF_IMAGE=//p' "${source_root}/.env" | tail -n 1)
      controller_image=$(sed -n 's/^DWARF_CONTROLLER_IMAGE=//p' "${source_root}/.env" | tail -n 1)
    fi
    if [[ -n ${dashboard_image} ]]; then docker image rm "${dashboard_image}" || true; fi
    if [[ -n ${controller_image} ]]; then docker image rm "${controller_image}" || true; fi
  fi
  exit 0
fi
field() {
  local file=$1 key=$2 value count
  value=$(sed -n "s/^${key}=//p" "${file}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "${key} is missing or duplicated"
  printf '%s\n' "${value}"
}
receipt_get() { field "${receipt}" "$1"; }
runtime_root=$(receipt_get runtime_root)
installation_id=$(receipt_get installation_id)
recorded_install_root=$(receipt_get install_root)
[[ -n ${runtime_root} && -n ${installation_id} ]] || die "installation receipt is incomplete"
[[ ${recorded_install_root} == "${install_root}" ]] || die "installation root does not match its receipt"

canonical_runtime=
if [[ -n ${purge_path} ]]; then
  [[ -d ${runtime_root} ]] || die "recorded runtime root does not exist"
  [[ ! -L ${runtime_root} ]] || die "refusing to purge a symlink runtime root"
  canonical_runtime=$(cd "$(dirname "${runtime_root}")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "${runtime_root}")")
  canonical_requested=$(cd "$(dirname "${purge_path}")" 2>/dev/null && printf '%s/%s\n' "$(pwd -P)" "$(basename "${purge_path}")") \
    || die "purge path cannot be resolved"
  [[ ${canonical_requested} == "${canonical_runtime}" ]] || die "purge path does not exactly match the recorded runtime root"
  [[ ${canonical_runtime} != / && ${canonical_runtime} != "${HOME}" && ${canonical_runtime} != "${install_root}" ]] \
    || die "refusing unsafe purge target"
  slash_count=${canonical_runtime//[^\/]/}
  [[ ${#slash_count} -ge 3 ]] || die "refusing shallow purge target"
  marker=${canonical_runtime}/.dwarf-installation-id
  [[ -f ${marker} && $(tr -d '\r\n' <"${marker}") == "${installation_id}" ]] || die "runtime ownership marker does not match receipt"
  [[ ${confirmation} == "PURGE DWARF ${installation_id}" ]] \
    || die "purge requires exact confirmation: PURGE DWARF ${installation_id}"
  command -v docker >/dev/null 2>&1 || die "Docker is required to prove no managed topology containers remain before purge"
  docker info >/dev/null 2>&1 || die "Docker must be running and accessible before purge"
  managed_containers=$(docker ps -aq --filter label=io.dwarf.managed=true \
    --filter "label=io.dwarf.installation=${installation_id}" -q 2>/dev/null) \
    || die "Docker inventory failed; refusing purge"
  [[ -z ${managed_containers} ]] || die "managed topology containers still exist; remove them before purge"

  docker compose version >/dev/null 2>&1 || die "Docker Compose is required for purge"
  env_file=${install_root}/.env
  release_version=$(receipt_get release_version)
  receipt_manifest_sha=$(receipt_get manifest_sha256)
  compose_project=$(receipt_get compose_project_name)
  [[ -f ${env_file} && ! -L ${env_file} ]] || die "installed environment is missing or unsafe"
  [[ $(field "${env_file}" DWARF_COMPOSE_PROJECT_NAME) == "${compose_project}" ]] \
    || die "Compose project identity differs from the install receipt"

  current=${install_root}/current
  [[ -L ${current} && $(readlink "${current}") == "releases/${release_version}" ]] \
    || die "active release link differs from the install receipt"
  manifest=${current}/release-manifest.json
  signature=${current}/release-manifest.sig
  public_key=${current}/release-public.pem
  [[ -f ${manifest} && ! -L ${manifest} && -f ${signature} && ! -L ${signature} \
    && -f ${public_key} && ! -L ${public_key} ]] \
    || die "signed release identity is missing or unsafe"
  command -v openssl >/dev/null 2>&1 || die "OpenSSL is required for purge"
  sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
  }
  [[ $(sha256_file "${manifest}") == "${receipt_manifest_sha}" ]] \
    || die "release manifest hash differs from the install receipt"
  openssl dgst -sha256 -verify "${public_key}" -signature "${signature}" "${manifest}" >/dev/null 2>&1 \
    || die "release manifest signature verification failed"
  manifest_string() {
    local key=$1 value count
    value=$(sed -n "s/^[[:space:]]*\"${key}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\"[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p" "${manifest}")
    count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
    [[ ${count} == 1 ]] || die "signed manifest field ${key} is missing or duplicated"
    printf '%s\n' "${value}"
  }
  compose_file=$(manifest_string compose_file)
  compose_sha256=$(manifest_string compose_sha256)
  [[ ${compose_file} == docker-compose.dwarf.yml ]] \
    || die "signed Compose artifact name is unsupported"
  verified_compose=${current}/${compose_file}
  [[ -f ${verified_compose} && ! -L ${verified_compose} ]] \
    || die "signed Compose artifact is missing or unsafe"
  [[ $(sha256_file "${verified_compose}") == "${compose_sha256}" ]] \
    || die "Compose artifact differs from the signed manifest"
fi

if [[ -n ${purge_path} ]]; then
  docker compose --project-directory "${install_root}" --env-file "${install_root}/.env" \
    --project-name "${compose_project}" -f "${verified_compose}" \
    down --remove-orphans --volumes
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [[ -f ${install_root}/current/docker-compose.dwarf.yml ]]; then
  docker compose --project-directory "${install_root}" --env-file "${install_root}/.env" \
    -f "${install_root}/current/docker-compose.dwarf.yml" down --remove-orphans
else
  printf 'DWARF application stack was not running or Docker was unavailable.\n'
fi

if [[ -z ${purge_path} ]]; then
  printf 'Preserving runtime data: %s\n' "${runtime_root}"
else
  rm -rf -- "${canonical_runtime}"
  printf 'Purged verified runtime data: %s\n' "${canonical_runtime}"
fi

if [[ ${remove_images} == true ]] && command -v docker >/dev/null 2>&1; then
  for key in DWARF_IMAGE DWARF_CONTROLLER_IMAGE; do
    image=$(sed -n "s/^${key}=//p" "${install_root}/.env" | tail -n 1)
    if [[ -n ${image} ]]; then docker image rm "${image}" || true; fi
  done
else
  printf 'Preserving downloaded images.\n'
fi
