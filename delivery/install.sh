#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-install
INSTALLER_VERSION=1.2.0
RELEASE_BASE_URL=${DWARF_RELEASE_BASE_URL:-https://github.com/pragma-org/dwarf/releases/download}
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
runtime_root=${DWARF_RUNTIME_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf}
bundle_dir=
version=
test_public_key=
prepare_only=false
downloaded_bundle=false
requested_dashboard_port=

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
need() {
  if [[ ${DWARF_INSTALLER_TEST_MODE:-0} == 1 && ${DWARF_TEST_MISSING_COMMAND:-} == "$1" ]]; then
    die "missing required command: $1"
  fi
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

detect_platform() {
  local os arch
  os=${DWARF_TEST_UNAME_S:-$(uname -s)}
  arch=${DWARF_TEST_UNAME_M:-$(uname -m)}
  case "${os}:${arch}" in
    Linux:x86_64|Linux:amd64) printf '%s\n' linux-amd64 ;;
    Darwin:arm64|Darwin:aarch64) printf '%s\n' darwin-arm64 ;;
    *) die "unsupported platform ${os}/${arch}; this release supports Linux amd64 and macOS arm64" ;;
  esac
}

usage() {
  cat <<'EOF'
Usage: install.sh --version VERSION [options]
       install.sh --bundle-dir DIR [options]
       install.sh --detect-platform

Options:
  --version VERSION       Immutable release tag to download.
  --bundle-dir DIR        Use an already-downloaded release bundle.
  --install-root DIR      Program/releases root.
  --runtime-root DIR      Persistent data root.
  --port PORT             Loopback dashboard port (default: 8787).
  --prepare-only          Verify and stage; do not contact Docker.
  --test-public-key FILE  Test-only signing key override; requires
                          DWARF_INSTALLER_TEST_MODE=1.
EOF
}

while (($#)); do
  case "$1" in
    --detect-platform) detect_platform; exit 0 ;;
    --version) (($# >= 2)) || die "--version requires a value"; version=$2; shift 2 ;;
    --bundle-dir) (($# >= 2)) || die "--bundle-dir requires a value"; bundle_dir=$2; shift 2 ;;
    --install-root) (($# >= 2)) || die "--install-root requires a value"; install_root=$2; shift 2 ;;
    --runtime-root) (($# >= 2)) || die "--runtime-root requires a value"; runtime_root=$2; shift 2 ;;
    --port) (($# >= 2)) || die "--port requires a value"; requested_dashboard_port=$2; shift 2 ;;
    --prepare-only) prepare_only=true; shift ;;
    --test-public-key) (($# >= 2)) || die "--test-public-key requires a value"; test_public_key=$2; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -n ${requested_dashboard_port} ]]; then
  [[ ${requested_dashboard_port} =~ ^[0-9]+$ ]] \
    && ((10#${requested_dashboard_port} >= 1 && 10#${requested_dashboard_port} <= 65535)) \
    || die "dashboard port must be an integer from 1 to 65535"
fi

for root in "${install_root}" "${runtime_root}"; do
  [[ ${root} == /* ]] || die "install and runtime roots must be absolute paths"
  [[ ${root} != / && ${root} != "${HOME}" ]] || die "install and runtime roots must not be / or HOME"
  [[ ${root} != *$'\n'* && ${root} != *'='* ]] || die "install and runtime roots contain unsupported characters"
done

platform=$(detect_platform)
need openssl
need sed
need awk
need find
need tar

[[ ! -L ${install_root} ]] || die "install root must not be a symbolic link"
if [[ -e ${install_root}/install-receipt || -L ${install_root}/install-receipt ]]; then
  [[ -f ${install_root}/install-receipt && ! -L ${install_root}/install-receipt ]] \
    || die "install receipt must be a regular file"
elif [[ -e ${install_root}/.env || -L ${install_root}/.env \
  || -e ${install_root}/current || -L ${install_root}/current ]]; then
  die "unreceipted install state cannot supply credentials or an active release"
fi
if [[ -e ${runtime_root} ]]; then
  [[ -d ${runtime_root} && ! -L ${runtime_root} ]] || die "runtime root must be a real directory"
  if [[ ! -f ${install_root}/install-receipt \
    && -n $(find "${runtime_root}" -mindepth 1 ! -type d -print -quit) ]]; then
    die "nonempty runtime root is not owned by a trusted DWARF install receipt; explicit import is required"
  fi
fi

ssh_key_path=
ssh_known_hosts=
ssh_key_created=false
ssh_known_hosts_created=false
installation_committed=false
catalog_mutated=false
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/dwarf-install.XXXXXX")
cleanup() {
  if [[ ${installation_committed} == false ]]; then
    if [[ ${catalog_mutated} == true ]] && declare -F restore_runtime_catalog >/dev/null 2>&1; then
      restore_runtime_catalog
    fi
    [[ ${ssh_key_created} == false || -z ${ssh_key_path} ]] || rm -f -- "${ssh_key_path}"
    [[ ${ssh_known_hosts_created} == false || -z ${ssh_known_hosts} ]] || rm -f -- "${ssh_known_hosts}"
  fi
  rm -rf -- "${tmp_dir}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -n "${test_public_key}" ]]; then
  [[ ${DWARF_INSTALLER_TEST_MODE:-0} == 1 ]] || die "--test-public-key is disabled outside installer tests"
  [[ -f "${test_public_key}" ]] || die "test public key does not exist"
  public_key=${test_public_key}
else
  public_key=${tmp_dir}/dwarf-release-public.pem
  cat >"${public_key}" <<'DWARF_RELEASE_PUBLIC_KEY'
-----BEGIN PUBLIC KEY-----
DWARF_RELEASE_PUBLIC_KEY_NOT_CONFIGURED
-----END PUBLIC KEY-----
DWARF_RELEASE_PUBLIC_KEY
fi

if [[ -z "${bundle_dir}" ]]; then
  [[ "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] \
    || die "--version must be an immutable semantic release tag"
  need curl
  bundle_dir=${tmp_dir}/bundle
  downloaded_bundle=true
  mkdir -p "${bundle_dir}"
  for artifact in release-manifest.json release-manifest.sig; do
    curl --fail --silent --show-error --location \
      "${RELEASE_BASE_URL}/${version}/${artifact}" -o "${bundle_dir}/${artifact}"
  done
fi

manifest=${bundle_dir}/release-manifest.json
signature=${bundle_dir}/release-manifest.sig
[[ -f "${manifest}" && -f "${signature}" ]] || die "release bundle is missing manifest or signature"
openssl dgst -sha256 -verify "${public_key}" -signature "${signature}" "${manifest}" >/dev/null 2>&1 \
  || die "release manifest signature verification failed"

manifest_get() {
  local key=$1 value count
  value=$(sed -n "s/^[[:space:]]*\"${key}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\"[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p" "${manifest}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "signed manifest field ${key} is missing or duplicated"
  printf '%s\n' "${value}"
}
manifest_get_number() {
  local key=$1 value count
  value=$(sed -n "s/^[[:space:]]*\"${key}\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p" "${manifest}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "signed manifest field ${key} is missing or duplicated"
  printf '%s\n' "${value}"
}

schema_version=$(manifest_get schema_version)
release_version=$(manifest_get release_version)
release_revision=$(manifest_get release_revision)
dashboard_image=$(manifest_get dashboard_image)
controller_image=$(manifest_get controller_image)
golden_amaru_image=$(manifest_get golden_amaru_image)
golden_cardano_image=$(manifest_get golden_cardano_image)
compose_file=$(manifest_get compose_file)
compose_sha256=$(manifest_get compose_sha256)
backup_file=$(manifest_get backup_file)
backup_sha256=$(manifest_get backup_sha256)
doctor_file=$(manifest_get doctor_file)
doctor_sha256=$(manifest_get doctor_sha256)
migrate_file=$(manifest_get migrate_file)
migrate_sha256=$(manifest_get migrate_sha256)
status_file=$(manifest_get status_file)
status_sha256=$(manifest_get status_sha256)
update_file=$(manifest_get update_file)
update_sha256=$(manifest_get update_sha256)
uninstall_file=$(manifest_get uninstall_file)
uninstall_sha256=$(manifest_get uninstall_sha256)
installer_file=$(manifest_get installer_file)
installer_sha256=$(manifest_get installer_sha256)
schema_file=$(manifest_get schema_file)
schema_sha256=$(manifest_get schema_sha256)
bundle_file=$(manifest_get bundle_file)
bundle_sha256=$(manifest_get bundle_sha256)
catalog_sha256=$(manifest_get catalog_sha256)
minimum_installer_version=$(manifest_get minimum_installer_version)
data_schema_version=$(manifest_get_number data_schema_version)

[[ ${schema_version} == 1 ]] || die "unsupported release manifest schema ${schema_version}"
[[ ${release_version} =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] || die "invalid signed release version"
[[ ${release_revision} =~ ^[0-9a-f]{40}$ ]] || die "invalid signed release revision"
[[ ${data_schema_version} == 1 ]] || die "unsupported data schema ${data_schema_version}"
version_at_least() {
  local have=$1 need_version=$2 have_major have_minor have_patch need_major need_minor need_patch
  IFS=. read -r have_major have_minor have_patch <<<"${have}"
  IFS=. read -r need_major need_minor need_patch <<<"${need_version}"
  [[ ${have_major} =~ ^[0-9]+$ && ${have_minor} =~ ^[0-9]+$ && ${have_patch} =~ ^[0-9]+$ ]] || return 1
  [[ ${need_major} =~ ^[0-9]+$ && ${need_minor} =~ ^[0-9]+$ && ${need_patch} =~ ^[0-9]+$ ]] || return 1
  ((10#${have_major} > 10#${need_major})) && return 0
  ((10#${have_major} < 10#${need_major})) && return 1
  ((10#${have_minor} > 10#${need_minor})) && return 0
  ((10#${have_minor} < 10#${need_minor})) && return 1
  ((10#${have_patch} >= 10#${need_patch}))
}
version_at_least "${INSTALLER_VERSION}" "${minimum_installer_version}" \
  || die "release requires installer ${minimum_installer_version}; this bootstrap is ${INSTALLER_VERSION}"
platform=$(detect_platform)
grep -Eq "\"${platform}\"" "${manifest}" || die "release ${release_version} does not support ${platform}"
digest_reference='^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?/[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'
for image in "${dashboard_image}" "${controller_image}" "${golden_amaru_image}" "${golden_cardano_image}"; do
  [[ ${image} =~ ${digest_reference} ]] || die "manifest contains a non-digest image reference"
done
for file in "${compose_file}" "${backup_file}" "${doctor_file}" "${migrate_file}" "${status_file}" "${update_file}" "${uninstall_file}" "${installer_file}" "${schema_file}"; do
  [[ ${file} =~ ^[A-Za-z0-9._-]+$ ]] || die "manifest contains an unsafe artifact name"
done
for digest in "${compose_sha256}" "${backup_sha256}" "${doctor_sha256}" "${migrate_sha256}" "${status_sha256}" "${update_sha256}" "${uninstall_sha256}" "${installer_sha256}" "${schema_sha256}"; do
  [[ ${digest} =~ ^[0-9a-f]{64}$ ]] || die "manifest contains an invalid artifact checksum"
done
[[ ${bundle_file} =~ ^[A-Za-z0-9._-]+$ && ${bundle_sha256} =~ ^[0-9a-f]{64}$ ]] \
  || die "signed manifest has invalid bundle identity"
[[ ${catalog_sha256} =~ ^[0-9a-f]{64}$ ]] || die "signed manifest has invalid runtime catalog identity"
if [[ -n "${version}" && ${release_version} != "${version}" ]]; then
  die "downloaded release version does not match requested version"
fi

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

if [[ ${downloaded_bundle} == true ]]; then
  need tar
  curl --fail --silent --show-error --location \
    "${RELEASE_BASE_URL}/${release_version}/${bundle_file}" -o "${tmp_dir}/${bundle_file}"
  [[ $(sha256_file "${tmp_dir}/${bundle_file}") == "${bundle_sha256}" ]] \
    || die "release bundle checksum verification failed"
  while IFS= read -r entry; do
    case "${entry}" in /*|../*|*/../*|*/..) die "release bundle contains an unsafe path" ;; esac
  done < <(tar -tzf "${tmp_dir}/${bundle_file}")
  extracted=${tmp_dir}/extracted
  mkdir -p "${extracted}"
  tar -xzf "${tmp_dir}/${bundle_file}" -C "${extracted}"
  bundle_dir=${extracted}
fi
manifest_sha256=$(sha256_file "${manifest}")

verify_artifact() {
  local file=$1 expected=$2
  [[ -f "${bundle_dir}/${file}" && ! -L "${bundle_dir}/${file}" ]] || die "release artifact is missing or unsafe: ${file}"
  [[ $(sha256_file "${bundle_dir}/${file}") == "${expected}" ]] \
    || die "artifact checksum verification failed: ${file}"
}
verify_artifact "${compose_file}" "${compose_sha256}"
verify_artifact "${backup_file}" "${backup_sha256}"
verify_artifact "${doctor_file}" "${doctor_sha256}"
verify_artifact "${migrate_file}" "${migrate_sha256}"
verify_artifact "${status_file}" "${status_sha256}"
verify_artifact "${update_file}" "${update_sha256}"
verify_artifact "${uninstall_file}" "${uninstall_sha256}"
verify_artifact "${installer_file}" "${installer_sha256}"
verify_artifact "${schema_file}" "${schema_sha256}"
catalog_dir=${bundle_dir}/catalog
for required_catalog in scenarios targets/manifests profiles; do
  [[ -d ${catalog_dir}/${required_catalog} && ! -L ${catalog_dir}/${required_catalog} ]] \
    || die "release bundle is missing required runtime catalog: ${required_catalog}"
done
[[ -z $(find "${catalog_dir}" -type l -print -quit) ]] \
  || die "release runtime catalog contains a symbolic link"
unexpected_catalog_entry=$(find "${catalog_dir}" -mindepth 1 ! -type d ! -type f -print -quit)
[[ -z ${unexpected_catalog_entry} ]] \
  || die "release runtime catalog contains an unsupported entry"
while IFS= read -r catalog_file; do
  catalog_relative=${catalog_file#"${catalog_dir}/"}
  [[ ${catalog_relative} =~ ^scenarios/[A-Za-z0-9._-]+\.yaml$ \
    || ${catalog_relative} =~ ^targets/manifests/[A-Za-z0-9._-]+\.yaml$ \
    || ${catalog_relative} =~ ^profiles/[A-Za-z0-9._-]+/profile\.yaml$ ]] \
    || die "release runtime catalog contains an unsafe file: ${catalog_relative}"
done < <(find "${catalog_dir}" -type f | LC_ALL=C sort)
scenario_count=$(find "${catalog_dir}/scenarios" -maxdepth 1 -type f -name '*.yaml' | wc -l | tr -d ' ')
manifest_count=$(find "${catalog_dir}/targets/manifests" -maxdepth 1 -type f -name '*.yaml' | wc -l | tr -d ' ')
profile_count=$(find "${catalog_dir}/profiles" -mindepth 2 -maxdepth 2 -type f -name profile.yaml | wc -l | tr -d ' ')
((scenario_count > 0 && manifest_count > 0 && profile_count > 0)) \
  || die "release runtime catalog is incomplete"
catalog_tree_sha256() {
  local root=$1 catalog_file catalog_relative
  if command -v sha256sum >/dev/null 2>&1; then
    while IFS= read -r catalog_file; do
      catalog_relative=${catalog_file#"${root}/"}
      printf '%s  %s\n' "$(sha256_file "${catalog_file}")" "${catalog_relative}"
    done < <(find "${root}" -type f | LC_ALL=C sort) | sha256sum | awk '{print $1}'
  else
    while IFS= read -r catalog_file; do
      catalog_relative=${catalog_file#"${root}/"}
      printf '%s  %s\n' "$(sha256_file "${catalog_file}")" "${catalog_relative}"
    done < <(find "${root}" -type f | LC_ALL=C sort) | shasum -a 256 | awk '{print $1}'
  fi
}
actual_catalog_sha256=$(catalog_tree_sha256 "${catalog_dir}")
[[ ${actual_catalog_sha256} == "${catalog_sha256}" ]] \
  || die "release runtime catalog checksum verification failed"
[[ "${prepare_only}" == true ]] || need docker

assert_safe_runtime_catalog_paths() {
  local relative path
  [[ -d ${runtime_root} && ! -L ${runtime_root} ]] \
    || die "runtime root is not a real directory"
  for relative in state state/scenarios state/targets state/targets/manifests state/profiles; do
    path=${runtime_root}/${relative}
    if [[ -e ${path} || -L ${path} ]]; then
      [[ -d ${path} && ! -L ${path} ]] \
        || die "runtime catalog path is not a real directory: ${path}"
    fi
  done
}

umask 077
mkdir -p "${install_root}/releases" "${runtime_root}"
assert_safe_runtime_catalog_paths
mkdir -p "${runtime_root}/runs" "${runtime_root}/state" "${runtime_root}/state/launches" "${runtime_root}/state/run-ledger" "${runtime_root}/bundles" "${runtime_root}/controller-state"
assert_safe_runtime_catalog_paths
runtime_gid=$(id -g)
chmod 0770 "${runtime_root}" "${runtime_root}/runs" "${runtime_root}/state" "${runtime_root}/state/launches" "${runtime_root}/state/run-ledger" "${runtime_root}/bundles" "${runtime_root}/controller-state"
ssh_key_path=${runtime_root}/state/ssh_deploy_key
ssh_known_hosts=${runtime_root}/state/ssh_known_hosts
for placeholder in "${ssh_key_path}" "${ssh_known_hosts}"; do
  if [[ -e ${placeholder} || -L ${placeholder} ]]; then
    [[ -f ${placeholder} && ! -L ${placeholder} ]] \
      || die "release SSH placeholder must be a regular file inside the runtime root"
  else
    : >"${placeholder}"
    if [[ ${placeholder} == "${ssh_key_path}" ]]; then
      ssh_key_created=true
    else
      ssh_known_hosts_created=true
    fi
  fi
  chmod 0600 "${placeholder}"
done
release_dir=${install_root}/releases/${release_version}
stage_dir=${install_root}/releases/.${release_version}.stage.$$
rm -rf -- "${stage_dir}"
mkdir -p "${stage_dir}"
cp "${manifest}" "${signature}" "${bundle_dir}/${compose_file}" "${bundle_dir}/${backup_file}" \
  "${bundle_dir}/${doctor_file}" "${bundle_dir}/${migrate_file}" "${bundle_dir}/${status_file}" \
  "${bundle_dir}/${update_file}" "${bundle_dir}/${uninstall_file}" "${bundle_dir}/${schema_file}" "${stage_dir}/"
cp "${public_key}" "${stage_dir}/release-public.pem"
cp "${bundle_dir}/${installer_file}" "${stage_dir}/install.sh"
cp -R "${catalog_dir}" "${stage_dir}/catalog"
chmod 0755 "${stage_dir}/install.sh" "${stage_dir}/${backup_file}" "${stage_dir}/${doctor_file}" \
  "${stage_dir}/${migrate_file}" "${stage_dir}/${status_file}" "${stage_dir}/${update_file}" "${stage_dir}/${uninstall_file}"
chmod 0444 "${stage_dir}/release-public.pem"
if [[ -e "${release_dir}" ]]; then
  [[ -f "${release_dir}/release-manifest.json" ]] || die "existing immutable release directory is incomplete"
  [[ $(sha256_file "${release_dir}/release-manifest.json") == $(sha256_file "${manifest}") ]] \
    || die "existing immutable release ${release_version} differs from the signed bundle"
  [[ -d ${release_dir}/catalog && ! -L ${release_dir}/catalog \
    && $(catalog_tree_sha256 "${release_dir}/catalog") == "${catalog_sha256}" ]] \
    || die "existing immutable release ${release_version} has a modified runtime catalog"
  rm -rf -- "${stage_dir}"
else
  mv "${stage_dir}" "${release_dir}"
fi

catalog_backup=${tmp_dir}/runtime-catalog-backup.tar
catalog_backup_paths=()
restore_runtime_catalog() {
  local relative
  catalog_mutated=false
  assert_safe_runtime_catalog_paths
  for relative in state/scenarios state/targets/manifests state/profiles; do
    rm -rf -- "${runtime_root}/${relative}"
  done
  if [[ -f ${catalog_backup} ]]; then
    tar -xf "${catalog_backup}" -C "${runtime_root}"
  fi
}
seed_runtime_catalog() {
  local source destination profile_dir profile_id temporary
  assert_safe_runtime_catalog_paths
  for profile_dir in "${release_dir}/catalog/profiles"/*; do
    [[ -d ${profile_dir} && ! -L ${profile_dir} && -f ${profile_dir}/profile.yaml \
      && ! -L ${profile_dir}/profile.yaml ]] || continue
    profile_id=${profile_dir##*/}
    [[ ${profile_id} =~ ^[A-Za-z0-9._-]+$ ]] || die "release profile has an unsafe identity"
    if [[ -e ${runtime_root}/state/profiles/${profile_id} \
      || -L ${runtime_root}/state/profiles/${profile_id} ]]; then
      [[ -d ${runtime_root}/state/profiles/${profile_id} \
        && ! -L ${runtime_root}/state/profiles/${profile_id} ]] \
        || die "runtime profile path is not a real directory: ${profile_id}"
    fi
  done
  catalog_mutated=true
  mkdir -p "${runtime_root}/state/scenarios" \
    "${runtime_root}/state/targets/manifests" \
    "${runtime_root}/state/profiles"
  assert_safe_runtime_catalog_paths
  for source in "${release_dir}/catalog/scenarios"/*.yaml; do
    [[ -f ${source} && ! -L ${source} ]] || continue
    destination=${runtime_root}/state/scenarios/${source##*/}
    temporary=${destination}.dwarf-seed.$$
    cp "${source}" "${temporary}"
    chmod 0644 "${temporary}"
    mv "${temporary}" "${destination}"
  done
  for source in "${release_dir}/catalog/targets/manifests"/*.yaml; do
    [[ -f ${source} && ! -L ${source} ]] || continue
    destination=${runtime_root}/state/targets/manifests/${source##*/}
    temporary=${destination}.dwarf-seed.$$
    cp "${source}" "${temporary}"
    chmod 0644 "${temporary}"
    mv "${temporary}" "${destination}"
  done
  for profile_dir in "${release_dir}/catalog/profiles"/*; do
    [[ -d ${profile_dir} && ! -L ${profile_dir} && -f ${profile_dir}/profile.yaml \
      && ! -L ${profile_dir}/profile.yaml ]] || continue
    profile_id=${profile_dir##*/}
    [[ ${profile_id} =~ ^[A-Za-z0-9._-]+$ ]] || die "release profile has an unsafe identity"
    if [[ -e ${runtime_root}/state/profiles/${profile_id} \
      || -L ${runtime_root}/state/profiles/${profile_id} ]]; then
      [[ -d ${runtime_root}/state/profiles/${profile_id} \
        && ! -L ${runtime_root}/state/profiles/${profile_id} ]] \
        || die "runtime profile path is not a real directory: ${profile_id}"
    fi
    mkdir -p "${runtime_root}/state/profiles/${profile_id}"
    destination=${runtime_root}/state/profiles/${profile_id}/profile.yaml
    temporary=${destination}.dwarf-seed.$$
    cp "${profile_dir}/profile.yaml" "${temporary}"
    chmod 0644 "${temporary}"
    mv "${temporary}" "${destination}"
  done
}

env_get() {
  [[ -f "${install_root}/.env" ]] || return 0
  sed -n "s/^$1=//p" "${install_root}/.env" | tail -n 1
}
random_token() { openssl rand -base64 48 | tr -d '\r\n'; }
dashboard_token=$(env_get ADA2_DWARF_TOKEN)
controller_token=$(env_get DWARF_CONTROLLER_TOKEN)
installation_id=$(env_get DWARF_INSTALLATION_ID)
[[ -n "${dashboard_token}" ]] || dashboard_token=$(random_token)
[[ -n "${controller_token}" ]] || controller_token=$(random_token)
[[ -n "${installation_id}" ]] || installation_id=install-$(openssl rand -hex 16)
existing_dashboard_bind=$(env_get DWARF_DASHBOARD_BIND)
existing_dashboard_port=$(env_get DWARF_DASHBOARD_PORT)
existing_compose_project_name=$(env_get DWARF_COMPOSE_PROJECT_NAME)
existing_dashboard_container_name=$(env_get DWARF_CONTAINER_NAME)
existing_controller_container_name=$(env_get DWARF_CONTROLLER_CONTAINER_NAME)
existing_network_name=$(env_get DWARF_NETWORK_NAME)
existing_network_subnet=$(env_get DWARF_NETWORK_SUBNET)
dashboard_port=${requested_dashboard_port:-${existing_dashboard_port}}
dashboard_port=${dashboard_port:-8787}
[[ ${dashboard_port} =~ ^[0-9]+$ ]] \
  && ((10#${dashboard_port} >= 1 && 10#${dashboard_port} <= 65535)) \
  || die "dashboard port must be an integer from 1 to 65535"
identity_suffix=${installation_id#install-}
identity_suffix=${identity_suffix:0:12}
[[ ${identity_suffix} =~ ^[0-9a-f]{12}$ ]] || die "installation identity cannot form isolated runtime names"
if [[ -f "${install_root}/install-receipt" ]]; then
  receipt_optional() { sed -n "s/^$1=//p" "${install_root}/install-receipt" | tail -n 1; }
  recorded_bind=$(receipt_optional dashboard_bind)
  recorded_port=$(receipt_optional dashboard_port)
  recorded_project=$(receipt_optional compose_project_name)
  recorded_dashboard_name=$(receipt_optional dashboard_container_name)
  recorded_controller_name=$(receipt_optional controller_container_name)
  recorded_network_name=$(receipt_optional network_name)
  recorded_network_subnet=$(receipt_optional network_subnet)
  if [[ -n ${recorded_project}${recorded_dashboard_name}${recorded_controller_name}${recorded_network_name}${recorded_network_subnet} ]]; then
    [[ -n ${recorded_bind} && -n ${recorded_port} && -n ${recorded_project} \
      && -n ${recorded_dashboard_name} && -n ${recorded_controller_name} \
      && -n ${recorded_network_name} && -n ${recorded_network_subnet} \
      && ${existing_dashboard_bind} == "${recorded_bind}" \
      && ${existing_dashboard_port} == "${recorded_port}" \
      && ${existing_compose_project_name} == "${recorded_project}" \
      && ${existing_dashboard_container_name} == "${recorded_dashboard_name}" \
      && ${existing_controller_container_name} == "${recorded_controller_name}" \
      && ${existing_network_name} == "${recorded_network_name}" \
      && ${existing_network_subnet} == "${recorded_network_subnet}" ]] \
      || die "existing runtime isolation identity differs from its receipt"
  else
    [[ -z ${existing_compose_project_name}${existing_dashboard_container_name}${existing_controller_container_name}${existing_network_name}${existing_network_subnet} \
      && ${existing_dashboard_bind:-127.0.0.1} == 127.0.0.1 \
      && ${existing_dashboard_port:-8787} == 8787 ]] \
      || die "legacy installation has unreceipted runtime isolation settings"
    recorded_project=dwarf
    recorded_dashboard_name=dwarf-fw
    recorded_controller_name=dwarf-controller
    recorded_network_name=dwarf_default
    recorded_network_subnet=10.201.0.0/24
  fi
  compose_project_name=${recorded_project}
  dashboard_container_name=${recorded_dashboard_name}
  controller_container_name=${recorded_controller_name}
  network_name=${recorded_network_name}
  network_subnet=${recorded_network_subnet}
else
  compose_project_name=dwarf-${identity_suffix}
  dashboard_container_name=dwarf-fw-${identity_suffix}
  controller_container_name=dwarf-controller-${identity_suffix}
  network_name=dwarf-${identity_suffix}-network
  subnet_octet_two=$((64 + 16#${identity_suffix:0:2} % 128))
  subnet_octet_three=$((1 + 16#${identity_suffix:2:2} % 254))
  network_subnet=10.${subnet_octet_two}.${subnet_octet_three}.0/24
fi
[[ ${compose_project_name} =~ ^[a-z0-9][a-z0-9_-]{0,62}$ \
  && ${dashboard_container_name} =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}$ \
  && ${controller_container_name} =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}$ \
  && ${network_name} =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}$ \
  && ${network_subnet} =~ ^10\.([0-9]{1,3}\.){2}0/24$ ]] \
  || die "installed runtime isolation identity is invalid"
if [[ -f "${install_root}/install-receipt" ]]; then
  recorded_runtime=$(sed -n 's/^runtime_root=//p' "${install_root}/install-receipt" | tail -n 1)
  recorded_identity=$(sed -n 's/^installation_id=//p' "${install_root}/install-receipt" | tail -n 1)
  [[ -n ${recorded_runtime} && ${recorded_runtime} == "${runtime_root}" ]] \
    || die "requested runtime root does not match the installation receipt"
  [[ -n ${recorded_identity} && ${recorded_identity} == "${installation_id}" ]] \
    || die "installation identity does not match its receipt"
  [[ -f ${runtime_root}/.dwarf-installation-id && ! -L ${runtime_root}/.dwarf-installation-id \
    && $(tr -d '\r\n' <"${runtime_root}/.dwarf-installation-id") == "${installation_id}" ]] \
    || die "runtime ownership marker does not match the installation receipt"
fi
runtime_marker=${runtime_root}/.dwarf-installation-id
marker_backup=${tmp_dir}/previous-installation-marker
schema_marker=${runtime_root}/.dwarf-data-schema
schema_backup=${tmp_dir}/previous-data-schema
if [[ -f "${runtime_marker}" ]]; then
  [[ $(tr -d '\r\n' <"${runtime_marker}") == "${installation_id}" ]] \
    || die "runtime root is owned by a different DWARF installation"
  cp "${runtime_marker}" "${marker_backup}"
fi
[[ -f "${schema_marker}" ]] && cp "${schema_marker}" "${schema_backup}"
pre_migration_backup=
if [[ ${prepare_only} == false && -f "${install_root}/install-receipt" ]]; then
  current_schema=
  [[ -f "${schema_marker}" ]] && current_schema=$(tr -d '\r\n' <"${schema_marker}")
  if [[ ${current_schema} != "${data_schema_version}" ]]; then
    backup_receipt=$(DWARF_INSTALL_ROOT=${install_root} bash "${release_dir}/${backup_file}") \
      || die "pre-migration backup failed; runtime data was not changed"
    pre_migration_backup=$(printf '%s\n' "${backup_receipt}" | sed -n 's/^backup=//p')
    pre_migration_sha=$(printf '%s\n' "${backup_receipt}" | sed -n 's/^sha256=//p')
    [[ ${pre_migration_backup} == "${install_root}/backups/"* \
      && -f ${pre_migration_backup} && ${pre_migration_sha} =~ ^[0-9a-f]{64}$ \
      && $(sha256_file "${pre_migration_backup}") == "${pre_migration_sha}" ]] \
      || die "pre-migration backup receipt did not verify"
  fi
fi
for relative in state/scenarios state/targets/manifests state/profiles; do
  destination=${runtime_root}/${relative}
  if [[ -e ${destination} || -L ${destination} ]]; then
    [[ -d ${destination} && ! -L ${destination} ]] \
      || die "runtime catalog path is not a real directory: ${destination}"
    catalog_backup_paths+=("${relative}")
  fi
done
assert_safe_runtime_catalog_paths
if ((${#catalog_backup_paths[@]})); then
  tar -cf "${catalog_backup}" -C "${runtime_root}" "${catalog_backup_paths[@]}"
fi
seed_runtime_catalog
printf '%s\n' "${installation_id}" >"${runtime_marker}"
chmod 0600 "${runtime_marker}"

docker_socket=/var/run/docker.sock
if [[ "${prepare_only}" == false ]] && command -v docker >/dev/null 2>&1; then
  context_socket=$(docker context inspect --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null || true)
  case "${context_socket}" in unix://*) docker_socket=${context_socket#unix://} ;; esac
fi
docker_gid=0
if [[ ${platform} == linux-amd64 && -S "${docker_socket}" ]]; then
  docker_gid=$(stat -c '%g' "${docker_socket}" 2>/dev/null || stat -f '%g' "${docker_socket}" 2>/dev/null || printf 0)
fi
host_os=${platform%-*}
host_arch=${platform#*-}
available_image_digests=${golden_amaru_image##*@},${golden_cardano_image##*@}

env_backup=${tmp_dir}/previous.env
receipt_backup=${tmp_dir}/previous-receipt
[[ -f "${install_root}/.env" ]] && cp "${install_root}/.env" "${env_backup}"
[[ -f "${install_root}/install-receipt" ]] && cp "${install_root}/install-receipt" "${receipt_backup}"
env_tmp=${install_root}/.env.tmp.$$
{
  printf 'DWARF_IMAGE=%s\n' "${dashboard_image}"
  printf 'DWARF_CONTROLLER_IMAGE=%s\n' "${controller_image}"
  printf 'DWARF_GOLDEN_IMAGES=%s,%s\n' "${golden_amaru_image}" "${golden_cardano_image}"
  printf 'DWARF_ARTIFACT_PACKAGE_AMARU_BASE=%s\n' "${golden_amaru_image}"
  printf 'DWARF_ARTIFACT_PACKAGE_CARDANO_BASE=%s\n' "${golden_cardano_image}"
  printf 'DWARF_HOST_OS=%s\n' "${host_os}"
  printf 'DWARF_HOST_ARCH=%s\n' "${host_arch}"
  printf 'DWARF_AVAILABLE_IMAGE_DIGESTS=%s\n' "${available_image_digests}"
  printf 'DWARF_AVAILABLE_CREDENTIALS=\n'
  printf 'DWARF_SOURCE_REVISION=%s\n' "${release_revision}"
  printf 'DWARF_CATALOG_REVISION=%s\n' "${release_revision}"
  printf 'DWARF_DASHBOARD_BIND=127.0.0.1\n'
  printf 'DWARF_DASHBOARD_PORT=%s\n' "${dashboard_port}"
  printf 'DWARF_COMPOSE_PROJECT_NAME=%s\n' "${compose_project_name}"
  printf 'DWARF_CONTAINER_NAME=%s\n' "${dashboard_container_name}"
  printf 'DWARF_CONTROLLER_CONTAINER_NAME=%s\n' "${controller_container_name}"
  printf 'DWARF_NETWORK_NAME=%s\n' "${network_name}"
  printf 'DWARF_NETWORK_SUBNET=%s\n' "${network_subnet}"
  printf 'DWARF_RUNTIME_ROOT=%s\n' "${runtime_root}"
  printf 'DWARF_SSH_KEY_PATH=%s\n' "${ssh_key_path}"
  printf 'DWARF_SSH_KNOWN_HOSTS=%s\n' "${ssh_known_hosts}"
  printf 'DWARF_DOCKER_SOCKET_PATH=%s\n' "${docker_socket}"
  printf 'DWARF_DOCKER_GID=%s\n' "${docker_gid}"
  printf 'DWARF_RUNTIME_GID=%s\n' "${runtime_gid}"
  printf 'DWARF_INSTALLATION_ID=%s\n' "${installation_id}"
  printf 'ADA2_DWARF_TOKEN=%s\n' "${dashboard_token}"
  printf 'DWARF_CONTROLLER_TOKEN=%s\n' "${controller_token}"
} >"${env_tmp}"
chmod 0600 "${env_tmp}"
mv "${env_tmp}" "${install_root}/.env"

receipt_tmp=${install_root}/install-receipt.tmp.$$
{
  printf 'receipt_version=1\ninstallation_id=%s\n' "${installation_id}"
  printf 'install_root=%s\nruntime_root=%s\nrelease_version=%s\nrelease_revision=%s\n' \
    "${install_root}" "${runtime_root}" "${release_version}" "${release_revision}"
  printf 'dashboard_bind=127.0.0.1\ndashboard_port=%s\n' "${dashboard_port}"
  printf 'compose_project_name=%s\ndashboard_container_name=%s\ncontroller_container_name=%s\n' \
    "${compose_project_name}" "${dashboard_container_name}" "${controller_container_name}"
  printf 'network_name=%s\nnetwork_subnet=%s\n' "${network_name}" "${network_subnet}"
  printf 'data_schema_version=%s\nmanifest_sha256=%s\nbundle_sha256=%s\ncatalog_sha256=%s\n' \
    "${data_schema_version}" "${manifest_sha256}" "${bundle_sha256}" "${catalog_sha256}"
  [[ -z ${pre_migration_backup} ]] || printf 'pre_migration_backup=%s\n' "${pre_migration_backup}"
} >"${receipt_tmp}"
chmod 0600 "${receipt_tmp}"
mv "${receipt_tmp}" "${install_root}/install-receipt"

previous=
[[ -L "${install_root}/current" ]] && previous=$(readlink "${install_root}/current")
ln -sfn "releases/${release_version}" "${install_root}/current"

if [[ "${prepare_only}" == true ]]; then
  installation_committed=true
  printf 'Verified and staged DWARF %s at %s\n' "${release_version}" "${release_dir}"
  exit 0
fi

compose=(docker compose --project-directory "${install_root}" --env-file "${install_root}/.env" -f "${install_root}/current/${compose_file}")
compose_attempted=false
deploy_release() {
  local -a doctor_args=(--preflight)
  [[ -f ${receipt_backup} ]] || doctor_args+=(--fresh-install)
  DWARF_INSTALL_ROOT=${install_root} bash "${release_dir}/${doctor_file}" "${doctor_args[@]}" || return 1
  local image
  for image in "${dashboard_image}" "${controller_image}" "${golden_amaru_image}" "${golden_cardano_image}"; do
    docker pull "${image}" || return 1
  done
  DWARF_INSTALL_ROOT=${install_root} DWARF_TARGET_DATA_SCHEMA_VERSION=${data_schema_version} \
    bash "${release_dir}/${migrate_file}" || return 1
  compose_attempted=true
  "${compose[@]}" up -d --wait --wait-timeout 180 || return 1
  DWARF_INSTALL_ROOT=${install_root} bash "${release_dir}/${status_file}" --quiet || return 1
}
if ! deploy_release; then
  if [[ ${compose_attempted} == true ]]; then
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -f ${env_backup} ]]; then cp "${env_backup}" "${install_root}/.env"; else rm -f "${install_root}/.env"; fi
  if [[ -f ${receipt_backup} ]]; then cp "${receipt_backup}" "${install_root}/install-receipt"; else rm -f "${install_root}/install-receipt"; fi
  if [[ -f ${marker_backup} ]]; then cp "${marker_backup}" "${runtime_marker}"; else rm -f "${runtime_marker}"; fi
  if [[ -f ${schema_backup} ]]; then cp "${schema_backup}" "${schema_marker}"; else rm -f "${schema_marker}"; fi
  [[ ${ssh_key_created} == false ]] || rm -f "${ssh_key_path}"
  [[ ${ssh_known_hosts_created} == false ]] || rm -f "${ssh_known_hosts}"
  restore_runtime_catalog
  if [[ -n "${previous}" && -d "${install_root}/${previous}" ]]; then
    ln -sfn "${previous}" "${install_root}/current"
    if "${compose[@]}" up -d --wait --wait-timeout 180; then
      die "release activation failed; rollback restored and restarted ${previous}"
    fi
    die "release activation failed; rollback selected ${previous}, but its stack did not restart"
  fi
  rm -f "${install_root}/current"
  die "release activation failed; no previous release was available for rollback"
fi

installation_committed=true
printf 'DWARF %s is ready: http://127.0.0.1:%s/\n' "${release_version}" "${dashboard_port}"
printf 'Credentials are stored with mode 0600 in %s/.env\n' "${install_root}"
printf 'Status: DWARF_INSTALL_ROOT=%q bash %q\n' "${install_root}" "${install_root}/current/${status_file}"
