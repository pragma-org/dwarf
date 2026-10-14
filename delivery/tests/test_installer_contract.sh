#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)

required=(
  delivery/install.sh
  delivery/dwarf-release-public.pem
  delivery/release-manifest.schema.json
  delivery/scripts/backup.sh
  delivery/scripts/doctor.sh
  delivery/scripts/migrate.sh
  delivery/scripts/release-status.sh
  delivery/scripts/update.sh
  delivery/scripts/uninstall.sh
)
for relative in "${required[@]}"; do
  test -f "${ROOT}/${relative}" || { echo "missing ${relative}" >&2; exit 1; }
done
grep -q 'public_key=delivery/dwarf-release-public.pem' "${ROOT}/.github/workflows/dwarf-release.yml"
grep -q 'derived_public_key' "${ROOT}/.github/workflows/dwarf-release.yml"
grep -q 'bash delivery/tests/test_installer_contract.sh' "${ROOT}/.github/workflows/dwarf-release.yml"
grep -q 'Prove configured golden images are multi-architecture' "${ROOT}/.github/workflows/dwarf-release.yml"
grep -q '"${GOLDEN_AMARU_IMAGE}" "${GOLDEN_CARDANO_IMAGE}"' "${ROOT}/.github/workflows/dwarf-release.yml"
grep -q '"minimum_installer_version": "1.2.0"' "${ROOT}/.github/workflows/dwarf-release.yml"
grep -Fq 'trap cleanup EXIT' "${ROOT}/delivery/install.sh"
grep -Fq "trap 'exit 129' HUP" "${ROOT}/delivery/install.sh"
grep -Fq "trap 'exit 130' INT" "${ROOT}/delivery/install.sh"
grep -Fq "trap 'exit 143' TERM" "${ROOT}/delivery/install.sh"
grep -q 'DWARF_RELEASE_PUBLIC_KEY_NOT_CONFIGURED' "${ROOT}/delivery/dwarf-release-public.pem"
grep -q 'inventory.get' "${ROOT}/delivery/scripts/release-status.sh"
grep -q 'Authorization.*Bearer' "${ROOT}/delivery/scripts/release-status.sh"
bash -n "${ROOT}/delivery/install.sh" \
  "${ROOT}/delivery/scripts/backup.sh" \
  "${ROOT}/delivery/scripts/doctor.sh" \
  "${ROOT}/delivery/scripts/migrate.sh" \
  "${ROOT}/delivery/scripts/release-status.sh" \
  "${ROOT}/delivery/scripts/update.sh" \
  "${ROOT}/delivery/scripts/uninstall.sh"

test "$(DWARF_TEST_UNAME_S=Linux DWARF_TEST_UNAME_M=x86_64 bash "${ROOT}/delivery/install.sh" --detect-platform)" = linux-amd64
test "$(DWARF_TEST_UNAME_S=Darwin DWARF_TEST_UNAME_M=arm64 bash "${ROOT}/delivery/install.sh" --detect-platform)" = darwin-arm64
if DWARF_TEST_UNAME_S=Linux DWARF_TEST_UNAME_M=aarch64 bash "${ROOT}/delivery/install.sh" --detect-platform >/dev/null 2>&1; then
  echo "installer accepted unsupported Linux arm64" >&2
  exit 1
fi

tmp=$(mktemp -d)
socket_pid=
cleanup() {
  [[ -z ${socket_pid} ]] || kill "${socket_pid}" 2>/dev/null || true
  rm -rf -- "${tmp}"
}
trap cleanup EXIT HUP INT TERM

bundle="${tmp}/bundle"
mkdir -p "${bundle}"
cp "${ROOT}/delivery/docker-compose.dwarf.yml" "${bundle}/docker-compose.dwarf.yml"
cp "${ROOT}/delivery/scripts/backup.sh" "${bundle}/backup.sh"
cp "${ROOT}/delivery/scripts/doctor.sh" "${bundle}/doctor.sh"
cp "${ROOT}/delivery/scripts/migrate.sh" "${bundle}/migrate.sh"
cp "${ROOT}/delivery/scripts/release-status.sh" "${bundle}/status.sh"
cp "${ROOT}/delivery/scripts/update.sh" "${bundle}/update.sh"
cp "${ROOT}/delivery/scripts/uninstall.sh" "${bundle}/uninstall.sh"
cp "${ROOT}/delivery/install.sh" "${bundle}/install.sh"
cp "${ROOT}/delivery/release-manifest.schema.json" "${bundle}/release-manifest.schema.json"
mkdir -p "${bundle}/catalog/scenarios" \
  "${bundle}/catalog/targets/manifests" \
  "${bundle}/catalog/profiles/test-profile"
printf 'name: signed-scenario\n' >"${bundle}/catalog/scenarios/signed-scenario.yaml"
printf 'name: signed-manifest\n' >"${bundle}/catalog/targets/manifests/signed-manifest.yaml"
printf 'id: test-profile\n' >"${bundle}/catalog/profiles/test-profile/profile.yaml"

sha() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
catalog_sha() {
  local catalog_root=$1 file relative
  if command -v sha256sum >/dev/null 2>&1; then
    while IFS= read -r file; do
      relative=${file#"${catalog_root}/"}
      printf '%s  %s\n' "$(sha "${file}")" "${relative}"
    done < <(find "${catalog_root}" -type f | LC_ALL=C sort) | sha256sum | awk '{print $1}'
  else
    while IFS= read -r file; do
      relative=${file#"${catalog_root}/"}
      printf '%s  %s\n' "$(sha "${file}")" "${relative}"
    done < <(find "${catalog_root}" -type f | LC_ALL=C sort) | shasum -a 256 | awk '{print $1}'
  fi
}
tar -czf "${tmp}/dwarf-v9.0.0-test.tar.gz" -C "${bundle}" \
  docker-compose.dwarf.yml backup.sh doctor.sh migrate.sh status.sh update.sh uninstall.sh install.sh release-manifest.schema.json catalog
cp "${tmp}/dwarf-v9.0.0-test.tar.gz" "${bundle}/"
cat >"${bundle}/release-manifest.json" <<EOF
{
  "schema_version": "1",
  "release_version": "v9.0.0-test",
  "release_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "released_at": "2026-10-08T00:00:00Z",
  "supported_platforms": ["linux-amd64", "darwin-arm64"],
  "data_schema_version": 1,
  "minimum_installer_version": "1.0.0",
  "dashboard_image": "registry.example:55000/pragma-org/dwarf@sha256:$(printf 'a%.0s' {1..64})",
  "controller_image": "ghcr.io/pragma-org/dwarf-controller@sha256:$(printf 'b%.0s' {1..64})",
  "golden_amaru_image": "ghcr.io/pragma-org/amaru@sha256:$(printf 'c%.0s' {1..64})",
  "golden_cardano_image": "ghcr.io/intersectmbo/cardano-node@sha256:$(printf 'd%.0s' {1..64})",
  "bundle_file": "dwarf-v9.0.0-test.tar.gz",
  "bundle_sha256": "$(sha "${bundle}/dwarf-v9.0.0-test.tar.gz")",
  "catalog_sha256": "$(catalog_sha "${bundle}/catalog")",
  "installer_file": "install.sh",
  "installer_sha256": "$(sha "${bundle}/install.sh")",
  "schema_file": "release-manifest.schema.json",
  "schema_sha256": "$(sha "${bundle}/release-manifest.schema.json")",
  "compose_file": "docker-compose.dwarf.yml",
  "compose_sha256": "$(sha "${bundle}/docker-compose.dwarf.yml")",
  "backup_file": "backup.sh",
  "backup_sha256": "$(sha "${bundle}/backup.sh")",
  "doctor_file": "doctor.sh",
  "doctor_sha256": "$(sha "${bundle}/doctor.sh")",
  "migrate_file": "migrate.sh",
  "migrate_sha256": "$(sha "${bundle}/migrate.sh")",
  "status_file": "status.sh",
  "status_sha256": "$(sha "${bundle}/status.sh")",
  "update_file": "update.sh",
  "update_sha256": "$(sha "${bundle}/update.sh")",
  "uninstall_file": "uninstall.sh",
  "uninstall_sha256": "$(sha "${bundle}/uninstall.sh")"
}
EOF
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${tmp}/private.pem" >/dev/null 2>&1
openssl pkey -in "${tmp}/private.pem" -pubout -out "${tmp}/public.pem" >/dev/null 2>&1
openssl dgst -sha256 -sign "${tmp}/private.pem" -out "${bundle}/release-manifest.sig" "${bundle}/release-manifest.json"
unowned_runtime="${tmp}/unowned-runtime"
mkdir -p "${unowned_runtime}/legacy"
printf 'do-not-merge\n' >"${unowned_runtime}/legacy/sentinel"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${tmp}/unowned-install" --runtime-root "${unowned_runtime}" \
  --prepare-only >"${tmp}/unowned-runtime.out" 2>&1; then
  echo "installer silently adopted a nonempty unowned runtime root" >&2
  exit 1
fi
grep -q 'nonempty runtime root is not owned' "${tmp}/unowned-runtime.out"
test -f "${unowned_runtime}/legacy/sentinel"
test ! -e "${unowned_runtime}/.dwarf-installation-id"
unowned_install="${tmp}/unowned-install-state"
mkdir -p "${unowned_install}"
printf 'ADA2_DWARF_TOKEN=attacker-selected\n' >"${unowned_install}/.env"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${unowned_install}" --runtime-root "${tmp}/unowned-install-runtime" \
  --prepare-only >"${tmp}/unowned-install.out" 2>&1; then
  echo "installer adopted unreceipted credentials from an existing install root" >&2
  exit 1
fi
grep -q 'unreceipted install state' "${tmp}/unowned-install.out"
grep -qx 'ADA2_DWARF_TOKEN=attacker-selected' "${unowned_install}/.env"
post_placeholder_install="${tmp}/post-placeholder-install"
post_placeholder_runtime="${tmp}/post-placeholder-runtime"
mkdir -p "${post_placeholder_install}/releases/v9.0.0-test"
printf 'not-the-signed-manifest\n' >"${post_placeholder_install}/releases/v9.0.0-test/release-manifest.json"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${post_placeholder_install}" --runtime-root "${post_placeholder_runtime}" \
  --prepare-only >"${tmp}/post-placeholder.out" 2>&1; then
  echo "installer accepted a mismatched immutable release" >&2
  exit 1
fi
grep -q 'existing immutable release.*differs' "${tmp}/post-placeholder.out"
test ! -e "${post_placeholder_runtime}/state/ssh_deploy_key"
test ! -e "${post_placeholder_runtime}/state/ssh_known_hosts"
rm -rf -- "${post_placeholder_install}/releases/v9.0.0-test"
DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${post_placeholder_install}" --runtime-root "${post_placeholder_runtime}" \
  --prepare-only >/dev/null
if python3 -c 'import jsonschema' >/dev/null 2>&1; then
  python3 - "${ROOT}/delivery/release-manifest.schema.json" "${bundle}/release-manifest.json" <<'PY'
import json
import sys
from jsonschema import Draft202012Validator, FormatChecker

with open(sys.argv[1], encoding="utf-8") as handle:
    schema = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    manifest = json.load(handle)
Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
PY
fi

install_one="${tmp}/install-one"
runtime_one="${tmp}/runtime-one"
DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${install_one}" --runtime-root "${runtime_one}" --port 18787 --prepare-only
cmp "${bundle}/catalog/scenarios/signed-scenario.yaml" \
  "${runtime_one}/state/scenarios/signed-scenario.yaml"
cmp "${bundle}/catalog/targets/manifests/signed-manifest.yaml" \
  "${runtime_one}/state/targets/manifests/signed-manifest.yaml"
cmp "${bundle}/catalog/profiles/test-profile/profile.yaml" \
  "${runtime_one}/state/profiles/test-profile/profile.yaml"
grep -qx 'DWARF_DASHBOARD_BIND=127.0.0.1' "${install_one}/.env"
grep -qx 'DWARF_DASHBOARD_PORT=18787' "${install_one}/.env"
darwin_install="${tmp}/darwin-install"
darwin_runtime="${tmp}/darwin-runtime"
DWARF_TEST_UNAME_S=Darwin DWARF_TEST_UNAME_M=arm64 DWARF_INSTALLER_TEST_MODE=1 \
  bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" \
    --test-public-key "${tmp}/public.pem" --install-root "${darwin_install}" \
    --runtime-root "${darwin_runtime}" --prepare-only >/dev/null
grep -qx 'DWARF_HOST_OS=darwin' "${darwin_install}/.env"
grep -qx 'DWARF_HOST_ARCH=arm64' "${darwin_install}/.env"
grep -qx 'DWARF_DOCKER_GID=0' "${darwin_install}/.env"
grep -Eq '^DWARF_COMPOSE_PROJECT_NAME=dwarf-[0-9a-f]{12}$' "${install_one}/.env"
grep -Eq '^DWARF_CONTAINER_NAME=dwarf-fw-[0-9a-f]{12}$' "${install_one}/.env"
grep -Eq '^DWARF_CONTROLLER_CONTAINER_NAME=dwarf-controller-[0-9a-f]{12}$' "${install_one}/.env"
grep -Eq '^DWARF_NETWORK_SUBNET=10\.[0-9]{1,3}\.[0-9]{1,3}\.0/24$' "${install_one}/.env"
grep -qx "DWARF_SSH_KEY_PATH=${runtime_one}/state/ssh_deploy_key" "${install_one}/.env"
grep -qx "DWARF_SSH_KNOWN_HOSTS=${runtime_one}/state/ssh_known_hosts" "${install_one}/.env"
test -f "${runtime_one}/state/ssh_deploy_key"
test ! -s "${runtime_one}/state/ssh_deploy_key"
test -f "${runtime_one}/state/ssh_known_hosts"
test ! -s "${runtime_one}/state/ssh_known_hosts"
grep -qx 'dashboard_port=18787' "${install_one}/install-receipt"
grep -Eq '^compose_project_name=dwarf-[0-9a-f]{12}$' "${install_one}/install-receipt"
grep -Eq '^dashboard_container_name=dwarf-fw-[0-9a-f]{12}$' "${install_one}/install-receipt"
grep -Eq '^controller_container_name=dwarf-controller-[0-9a-f]{12}$' "${install_one}/install-receipt"
grep -Eq '^network_subnet=10\.[0-9]{1,3}\.[0-9]{1,3}\.0/24$' "${install_one}/install-receipt"
grep -qx 'data_schema_version=1' "${install_one}/install-receipt"
grep -Eq '^manifest_sha256=[0-9a-f]{64}$' "${install_one}/install-receipt"
grep -qx "bundle_sha256=$(sha "${bundle}/dwarf-v9.0.0-test.tar.gz")" "${install_one}/install-receipt"
grep -qx "catalog_sha256=$(catalog_sha "${bundle}/catalog")" "${install_one}/install-receipt"
token_one=$(sed -n 's/^DWARF_CONTROLLER_TOKEN=//p' "${install_one}/.env")
dashboard_token_one=$(sed -n 's/^ADA2_DWARF_TOKEN=//p' "${install_one}/.env")
test "${#token_one}" -ge 43
test "${token_one}" != dwarf
test "${#dashboard_token_one}" -ge 43
test "${dashboard_token_one}" != dwarf
cp "${install_one}/current/catalog/scenarios/signed-scenario.yaml" \
  "${tmp}/installed-scenario-good.yaml"
printf 'name: modified-installed-release\n' \
  >"${install_one}/current/catalog/scenarios/signed-scenario.yaml"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${install_one}" --runtime-root "${runtime_one}" \
  --prepare-only >"${tmp}/modified-installed-catalog.out" 2>&1; then
  echo "installer trusted a modified installed release catalog" >&2
  exit 1
fi
grep -q 'modified runtime catalog' "${tmp}/modified-installed-catalog.out"
mv "${tmp}/installed-scenario-good.yaml" \
  "${install_one}/current/catalog/scenarios/signed-scenario.yaml"
DWARF_INSTALL_ROOT="${install_one}" DWARF_TARGET_DATA_SCHEMA_VERSION=1 \
  bash "${install_one}/current/migrate.sh" >/dev/null
DWARF_INSTALL_ROOT="${install_one}" DWARF_TARGET_DATA_SCHEMA_VERSION=1 \
  bash "${install_one}/current/migrate.sh" >/dev/null
test "$(cat "${runtime_one}/.dwarf-data-schema")" = 1
mkdir "${runtime_one}/.dwarf-migration.lock"
printf '%s %s\n' "$(hostname)" 99999999 >"${runtime_one}/.dwarf-migration.lock/owner"
DWARF_INSTALL_ROOT="${install_one}" DWARF_TARGET_DATA_SCHEMA_VERSION=1 \
  bash "${install_one}/current/migrate.sh" >/dev/null
test ! -e "${runtime_one}/.dwarf-migration.lock"
printf 'preserve-me\n' >"${runtime_one}/state/backup-sentinel"
backup_path=${tmp}/manual-backup.tar.gz
DWARF_INSTALL_ROOT="${install_one}" bash "${install_one}/current/backup.sh" \
  --output "${backup_path}" >/dev/null
test -f "${backup_path}"
test -f "${tmp}/manual-backup.json"
tar -tzf "${backup_path}" | grep -q '/state/backup-sentinel$'
expected_backup_sha=$(sha "${backup_path}")
grep -q "\"sha256\":\"${expected_backup_sha}\"" "${tmp}/manual-backup.json"

install_two="${tmp}/install-two"
runtime_two="${tmp}/runtime-two"
DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${install_two}" --runtime-root "${runtime_two}" --prepare-only
token_two=$(sed -n 's/^DWARF_CONTROLLER_TOKEN=//p' "${install_two}/.env")
dashboard_token_two=$(sed -n 's/^ADA2_DWARF_TOKEN=//p' "${install_two}/.env")
test "${token_one}" != "${token_two}"
test "${dashboard_token_one}" != "${dashboard_token_two}"
test "$(sed -n 's/^DWARF_COMPOSE_PROJECT_NAME=//p' "${install_one}/.env")" != \
  "$(sed -n 's/^DWARF_COMPOSE_PROJECT_NAME=//p' "${install_two}/.env")"
test "$(sed -n 's/^DWARF_CONTAINER_NAME=//p' "${install_one}/.env")" != \
  "$(sed -n 's/^DWARF_CONTAINER_NAME=//p' "${install_two}/.env")"

mv "${runtime_two}/state" "${runtime_two}/state-real"
mkdir -p "${tmp}/external-state"
printf 'preserve-external-state\n' >"${tmp}/external-state/sentinel"
ln -s "${tmp}/external-state" "${runtime_two}/state"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${install_two}" --runtime-root "${runtime_two}" --prepare-only \
  >"${tmp}/symlinked-state.out" 2>&1; then
  echo "installer followed a symlinked runtime state ancestor" >&2
  exit 1
fi
grep -q 'runtime catalog path is not a real directory' "${tmp}/symlinked-state.out"
grep -qx 'preserve-external-state' "${tmp}/external-state/sentinel"
rm "${runtime_two}/state"
mv "${runtime_two}/state-real" "${runtime_two}/state"

mv "${runtime_two}/state/profiles/test-profile" \
  "${runtime_two}/state/profiles/test-profile-real"
mkdir -p "${tmp}/external-profile"
printf 'preserve-external-profile\n' >"${tmp}/external-profile/profile.yaml"
ln -s "${tmp}/external-profile" "${runtime_two}/state/profiles/test-profile"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${install_two}" --runtime-root "${runtime_two}" --prepare-only \
  >"${tmp}/symlinked-profile.out" 2>&1; then
  echo "installer followed a symlinked runtime profile directory" >&2
  exit 1
fi
grep -q 'runtime profile path is not a real directory' "${tmp}/symlinked-profile.out"
grep -qx 'preserve-external-profile' "${tmp}/external-profile/profile.yaml"
rm "${runtime_two}/state/profiles/test-profile"
mv "${runtime_two}/state/profiles/test-profile-real" \
  "${runtime_two}/state/profiles/test-profile"

if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${tmp}/bad-port-install" --runtime-root "${tmp}/bad-port-runtime" \
  --port 70000 --prepare-only >"${tmp}/bad-port.out" 2>&1; then
  echo "installer accepted an invalid dashboard port" >&2
  exit 1
fi
grep -q 'dashboard port must be an integer from 1 to 65535' "${tmp}/bad-port.out"

release_server="${tmp}/releases/v9.0.0-test"
mkdir -p "${release_server}"
cp "${bundle}/release-manifest.json" "${bundle}/release-manifest.sig" \
  "${bundle}/dwarf-v9.0.0-test.tar.gz" "${release_server}/"
DWARF_INSTALLER_TEST_MODE=1 DWARF_RELEASE_BASE_URL="file://${tmp}/releases" \
  bash "${ROOT}/delivery/install.sh" --version v9.0.0-test \
    --test-public-key "${tmp}/public.pem" --install-root "${tmp}/download-install" \
    --runtime-root "${tmp}/download-runtime" --prepare-only
test -f "${tmp}/download-install/current/docker-compose.dwarf.yml"
test -x "${tmp}/download-install/current/backup.sh"
test -x "${tmp}/download-install/current/migrate.sh"
test -x "${tmp}/download-install/current/status.sh"

bundle_two="${tmp}/bundle-two"
mkdir -p "${bundle_two}"
cp -R "${bundle}/"* "${bundle_two}/"
sed -e 's/v9\.0\.0-test/v9.0.1-test/g' \
  -e '/"release_revision"/ s/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee/' \
  "${bundle}/release-manifest.json" >"${bundle_two}/release-manifest.json"
openssl dgst -sha256 -sign "${tmp}/private.pem" -out "${bundle_two}/release-manifest.sig" "${bundle_two}/release-manifest.json"

fake_bin="${tmp}/bin"
mkdir -p "${fake_bin}"
cat >"${fake_bin}/docker" <<'EOF'
#!/usr/bin/env sh
env_get() { sed -n "s/^$1=//p" "${DWARF_INSTALL_ROOT}/.env" 2>/dev/null | tail -n 1; }
dashboard_name=$(env_get DWARF_CONTAINER_NAME)
controller_name=$(env_get DWARF_CONTROLLER_CONTAINER_NAME)
network_name=$(env_get DWARF_NETWORK_NAME)
dashboard_name=${dashboard_name:-dwarf-fw}
controller_name=${controller_name:-dwarf-controller}
network_name=${network_name:-dwarf_default}
compose_project=$(env_get DWARF_COMPOSE_PROJECT_NAME)
network_subnet=$(env_get DWARF_NETWORK_SUBNET)
compose_project=${compose_project:-dwarf}
network_subnet=${network_subnet:-10.201.0.0/24}
case " $* " in
  *" compose version "*) exit 0 ;;
  *" context inspect "*) printf 'unix://%s\n' "${DWARF_FAKE_SOCKET}"; exit 0 ;;
  *" info "*) [ "${DWARF_FAKE_MODE:-}" = doctor-failure ] && exit 1; exit 0 ;;
  *" pull "*) [ "${DWARF_FAKE_MODE:-}" = pull-failure ] && exit 1; exit 0 ;;
  *" inspect ${dashboard_name} "*"{{.Config.Image}}"*) printf '%s\n' "registry.example:55000/pragma-org/dwarf@sha256:$(printf 'a%.0s' $(seq 1 64))"; exit 0 ;;
  *" inspect ${controller_name} "*"{{.Config.Image}}"*) printf '%s\n' "ghcr.io/pragma-org/dwarf-controller@sha256:$(printf 'b%.0s' $(seq 1 64))"; exit 0 ;;
  *" inspect ${dashboard_name} "*"io.dwarf.installation"*|*" inspect ${controller_name} "*"io.dwarf.installation"*)
    [ "${DWARF_FAKE_MODE:-}" = identity-failure ] && printf 'wrong-installation\n' || env_get DWARF_INSTALLATION_ID
    exit 0 ;;
  *" inspect ${dashboard_name} "*"com.docker.compose.project"*|*" inspect ${controller_name} "*"com.docker.compose.project"*) printf '%s\n' "${compose_project}"; exit 0 ;;
  *" inspect ${dashboard_name} "*"NetworkSettings.Networks"*|*" inspect ${controller_name} "*"NetworkSettings.Networks"*)
    [ "${DWARF_FAKE_MODE:-}" = network-attachment-failure ] || printf 'attached\n'
    exit 0 ;;
  *" inspect ${dashboard_name} "*"HostConfig.PortBindings"*)
    [ "${DWARF_FAKE_MODE:-}" = live-port-failure ] && printf '2\n' || printf '1\n'
    exit 0 ;;
  *" inspect ${controller_name} "*"HostConfig.PortBindings"*)
    [ "${DWARF_FAKE_MODE:-}" = controller-port-failure ] && printf '1\n' || printf '0\n'
    exit 0 ;;
  *" inspect ${dashboard_name} "*"8787/tcp"*)
    if [ "${DWARF_FAKE_MODE:-}" = live-port-failure ]; then printf '2:0.0.0.0:9999\n';
    else printf '1:%s:%s\n' "$(env_get DWARF_DASHBOARD_BIND)" "$(env_get DWARF_DASHBOARD_PORT)"; fi
    exit 0 ;;
  *" network inspect ${network_name} "*"com.docker.compose.project"*) printf '%s\n' "${compose_project}"; exit 0 ;;
  *" network inspect ${network_name} "*".Subnet"*) printf '%s\n' "${network_subnet}"; exit 0 ;;
  *" exec -i ${dashboard_name} python3 "*) [ "${DWARF_FAKE_MODE:-}" = status-failure ] && exit 1; printf '{"status":"ready"}\n'; exit 0 ;;
  *" compose "*" up "*)
    [ -z "${DWARF_FAKE_UP_FILE:-}" ] || touch "${DWARF_FAKE_UP_FILE}"
    if [ -f "${DWARF_FAKE_FAIL_FILE:-}" ]; then rm -f "${DWARF_FAKE_FAIL_FILE}"; exit 1; fi
    exit 0
    ;;
  *" compose "*" stop "*) [ -z "${DWARF_FAKE_STOP_FILE:-}" ] || touch "${DWARF_FAKE_STOP_FILE}"; exit 0 ;;
  *" compose "*" down "*) [ -z "${DWARF_FAKE_DOWN_FILE:-}" ] || touch "${DWARF_FAKE_DOWN_FILE}"; exit 0 ;;
  *" compose "*" ps "*"--services"*) [ "${DWARF_FAKE_WRITERS:-0}" = 1 ] && printf 'dwarf-fw\ndwarf-controller\n'; exit 0 ;;
  *" ps -q "*"io.dwarf.installation"*)
    case " $* " in
      *"label=io.dwarf.managed=true"*) [ "${DWARF_FAKE_MANAGED_RUNNING:-0}" = 1 ] && printf 'running-topology\n' ;;
      *) printf 'running-dashboard-and-controller\n' ;;
    esac
    exit 0 ;;
  *" ps "*) exit 0 ;;
esac
exit 0
EOF
chmod +x "${fake_bin}/docker"
cat >"${fake_bin}/lsof" <<'EOF'
#!/usr/bin/env sh
exit 1
EOF
chmod +x "${fake_bin}/lsof"
cat >"${fake_bin}/df" <<'EOF'
#!/usr/bin/env sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf 'installer-test 4194304 1024 4193280 1%% /\n'
EOF
chmod +x "${fake_bin}/df"
fake_socket="${tmp}/docker.sock"
python3 - "${fake_socket}" <<'PY' &
import os
import socket
import sys
import time

path = sys.argv[1]
try:
    os.unlink(path)
except FileNotFoundError:
    pass
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
time.sleep(60)
PY
socket_pid=$!
for _ in 1 2 3 4 5; do [[ -S ${fake_socket} ]] && break; sleep 0.1; done

printf 'name: allowed-local-extension\n' >"${runtime_one}/state/scenarios/local-extension.yaml"
PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${install_one}" bash "${install_one}/current/status.sh" --quiet
mv "${runtime_one}/state/targets/manifests/signed-manifest.yaml" \
  "${tmp}/signed-manifest-missing.yaml"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${install_one}" bash "${install_one}/current/status.sh" --quiet \
    >"${tmp}/missing-runtime-manifest.out" 2>&1; then
  echo "status accepted a missing signed target manifest" >&2
  exit 1
fi
grep -q 'active runtime catalog differs from signed base: targets/manifests/signed-manifest.yaml' \
  "${tmp}/missing-runtime-manifest.out"
mv "${tmp}/signed-manifest-missing.yaml" \
  "${runtime_one}/state/targets/manifests/signed-manifest.yaml"
cp "${runtime_one}/state/profiles/test-profile/profile.yaml" "${tmp}/signed-profile-good.yaml"
printf 'id: modified-profile\n' >"${runtime_one}/state/profiles/test-profile/profile.yaml"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${install_one}" bash "${install_one}/current/status.sh" --quiet \
    >"${tmp}/modified-runtime-profile.out" 2>&1; then
  echo "status accepted a modified signed profile" >&2
  exit 1
fi
grep -q 'active runtime catalog differs from signed base: profiles/test-profile/profile.yaml' \
  "${tmp}/modified-runtime-profile.out"
mv "${tmp}/signed-profile-good.yaml" \
  "${runtime_one}/state/profiles/test-profile/profile.yaml"

# Reproduce the exact isolation shape written by the released 1.1 installer:
# bind/port exist, while project/container/network fields are absent from both
# .env and receipt. The 1.2 status artifact must attest those legacy defaults,
# then the 1.2 installer must migrate them into explicit receipt-bound fields.
bridge_install="${tmp}/bridge-install"
bridge_runtime="${tmp}/bridge-runtime"
DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${bridge_install}" --runtime-root "${bridge_runtime}" --prepare-only >/dev/null
sed -e '/^DWARF_COMPOSE_PROJECT_NAME=/d' -e '/^DWARF_CONTAINER_NAME=/d' \
  -e '/^DWARF_CONTROLLER_CONTAINER_NAME=/d' -e '/^DWARF_NETWORK_NAME=/d' \
  -e '/^DWARF_NETWORK_SUBNET=/d' \
  "${bridge_install}/.env" >"${tmp}/bridge.env"
mv "${tmp}/bridge.env" "${bridge_install}/.env"
chmod 0600 "${bridge_install}/.env"
sed -e '/^dashboard_bind=/d' -e '/^dashboard_port=/d' -e '/^compose_project_name=/d' \
  -e '/^dashboard_container_name=/d' -e '/^controller_container_name=/d' \
  -e '/^network_name=/d' -e '/^network_subnet=/d' \
  "${bridge_install}/install-receipt" >"${tmp}/bridge.receipt"
mv "${tmp}/bridge.receipt" "${bridge_install}/install-receipt"
chmod 0600 "${bridge_install}/install-receipt"
DWARF_INSTALL_ROOT="${bridge_install}" DWARF_TARGET_DATA_SCHEMA_VERSION=1 \
  bash "${bridge_install}/current/migrate.sh" >/dev/null
PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${bridge_install}" bash "${bridge_install}/current/status.sh" --quiet
PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" \
    --test-public-key "${tmp}/public.pem" --install-root "${bridge_install}" \
    --runtime-root "${bridge_runtime}" >/dev/null
grep -qx 'compose_project_name=dwarf' "${bridge_install}/install-receipt"
grep -qx 'dashboard_container_name=dwarf-fw' "${bridge_install}/install-receipt"
grep -qx 'controller_container_name=dwarf-controller' "${bridge_install}/install-receipt"
grep -qx 'network_name=dwarf_default' "${bridge_install}/install-receipt"
grep -qx 'network_subnet=10.201.0.0/24' "${bridge_install}/install-receipt"

cp "${install_one}/.env" "${tmp}/install-one-before-tamper.env"
sed 's/^DWARF_CONTAINER_NAME=.*/DWARF_CONTAINER_NAME=foreign-dashboard/' \
  "${tmp}/install-one-before-tamper.env" >"${install_one}/.env"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${install_one}" bash "${install_one}/current/update.sh" \
    --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
    >"${tmp}/tampered-update.out" 2>&1; then
  echo "update adopted an isolation identity not anchored to the existing receipt" >&2
  exit 1
fi
grep -q 'existing runtime isolation identity differs from its receipt' "${tmp}/tampered-update.out"
mv "${tmp}/install-one-before-tamper.env" "${install_one}/.env"

legacy_install="${tmp}/legacy-install"
legacy_runtime="${tmp}/legacy-runtime"
DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${legacy_install}" --runtime-root "${legacy_runtime}" --prepare-only >/dev/null
printf 'legacy-data\n' >"${legacy_runtime}/state/legacy-sentinel"
PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" \
    --test-public-key "${tmp}/public.pem" --install-root "${legacy_install}" \
    --runtime-root "${legacy_runtime}" >/dev/null
test -f "${legacy_runtime}/state/legacy-sentinel"
test "$(find "${legacy_install}/backups" -type f -name '*.tar.gz' | wc -l | tr -d '[:space:]')" = 1
grep -q '^pre_migration_backup=' "${legacy_install}/install-receipt"

fail_once="${tmp}/fail-deploy-once"
cp "${install_one}/.env" "${tmp}/install-one-before-update.env"
printf 'name: locally-modified-before-update\n' >"${runtime_one}/state/scenarios/signed-scenario.yaml"
cp "${runtime_one}/state/scenarios/signed-scenario.yaml" "${tmp}/scenario-before-update.yaml"
touch "${fail_once}"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_FAIL_FILE="${fail_once}" DWARF_INSTALL_ROOT="${install_one}" \
  bash "${install_one}/current/update.sh" --bundle-dir "${bundle_two}" \
    --test-public-key "${tmp}/public.pem" >"${tmp}/update.out" 2>&1; then
  echo "update accepted an induced deployment failure" >&2
  exit 1
fi
grep -qi rollback "${tmp}/update.out"
test "$(readlink "${install_one}/current")" = releases/v9.0.0-test
grep -qx 'release_version=v9.0.0-test' "${install_one}/install-receipt"
grep -qx "DWARF_IMAGE=registry.example:55000/pragma-org/dwarf@sha256:$(printf 'a%.0s' {1..64})" "${install_one}/.env"
cmp -s "${tmp}/install-one-before-update.env" "${install_one}/.env"
cmp -s "${tmp}/scenario-before-update.yaml" \
  "${runtime_one}/state/scenarios/signed-scenario.yaml"

for failure_mode in doctor-failure pull-failure; do
  if PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
    DWARF_FAKE_MODE="${failure_mode}" DWARF_INSTALL_ROOT="${install_one}" \
    bash "${install_one}/current/update.sh" --bundle-dir "${bundle_two}" \
      --test-public-key "${tmp}/public.pem" >"${tmp}/${failure_mode}.out" 2>&1; then
    echo "update accepted induced ${failure_mode}" >&2
    exit 1
  fi
  test "$(readlink "${install_one}/current")" = releases/v9.0.0-test
  grep -qx 'release_version=v9.0.0-test' "${install_one}/install-receipt"
  cmp -s "${tmp}/install-one-before-update.env" "${install_one}/.env"
done

PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${install_one}" \
  bash "${install_one}/current/update.sh" --bundle-dir "${bundle_two}" \
    --test-public-key "${tmp}/public.pem" >/dev/null
test "$(readlink "${install_one}/current")" = releases/v9.0.1-test
grep -qx 'release_version=v9.0.1-test' "${install_one}/install-receipt"
grep -qx 'DWARF_DASHBOARD_PORT=18787' "${install_one}/.env"
for key in DWARF_COMPOSE_PROJECT_NAME DWARF_CONTAINER_NAME DWARF_CONTROLLER_CONTAINER_NAME DWARF_NETWORK_NAME DWARF_NETWORK_SUBNET; do
  test "$(sed -n "s/^${key}=//p" "${install_one}/.env")" = \
    "$(sed -n "s/^${key}=//p" "${tmp}/install-one-before-update.env")"
done
test -f "${runtime_one}/state/backup-sentinel"

fresh_install="${tmp}/fresh-failure-install"
fresh_runtime="${tmp}/fresh-failure-runtime"
missing_docker_install="${tmp}/missing-docker-install"
missing_docker_runtime="${tmp}/missing-docker-runtime"
if DWARF_INSTALLER_TEST_MODE=1 DWARF_TEST_MISSING_COMMAND=docker bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${missing_docker_install}" --runtime-root "${missing_docker_runtime}" \
  >"${tmp}/missing-docker.out" 2>&1; then
  echo "fresh install accepted a missing Docker CLI" >&2
  exit 1
fi
grep -q 'missing required command: docker' "${tmp}/missing-docker.out"
test ! -e "${missing_docker_runtime}/.dwarf-installation-id"
test ! -e "${missing_docker_install}/current"
PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" \
    --test-public-key "${tmp}/public.pem" --install-root "${missing_docker_install}" \
    --runtime-root "${missing_docker_runtime}" >/dev/null
test "$(readlink "${missing_docker_install}/current")" = releases/v9.0.0-test
PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_MODE=identity-failure DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet >"${tmp}/identity-status.out" 2>&1; then
  echo "status accepted a container owned by a different installation" >&2
  exit 1
fi
grep -q 'container runtime identity differs' "${tmp}/identity-status.out"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_MODE=live-port-failure DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet >"${tmp}/live-port-status.out" 2>&1; then
  echo "status accepted a dashboard port binding that differs from the receipt" >&2
  exit 1
fi
grep -q 'dashboard live port binding differs' "${tmp}/live-port-status.out"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_MODE=controller-port-failure DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet >"${tmp}/controller-port-status.out" 2>&1; then
  echo "status accepted a published controller host port" >&2
  exit 1
fi
grep -q 'dashboard live port binding differs' "${tmp}/controller-port-status.out"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_MODE=network-attachment-failure DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet >"${tmp}/network-attachment-status.out" 2>&1; then
  echo "status accepted a container detached from the receipted network" >&2
  exit 1
fi
grep -q 'container is not attached to the receipted installation network' "${tmp}/network-attachment-status.out"
cp "${missing_docker_install}/.env" "${tmp}/valid.env"
sed 's/^DWARF_DASHBOARD_PORT=.*/DWARF_DASHBOARD_PORT=18888/' \
  "${tmp}/valid.env" >"${missing_docker_install}/.env"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet >"${tmp}/tampered-runtime-identity.out" 2>&1; then
  echo "status accepted runtime isolation identity not anchored to the receipt" >&2
  exit 1
fi
grep -q 'runtime isolation identity differs' "${tmp}/tampered-runtime-identity.out"
cp "${tmp}/valid.env" "${missing_docker_install}/.env"
sed "s/sha256:$(printf 'a%.0s' {1..64})/sha256:$(printf 'f%.0s' {1..64})/" \
  "${tmp}/valid.env" >"${missing_docker_install}/.env"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/status.sh" --quiet >"${tmp}/tampered-status.out" 2>&1; then
  echo "status accepted image identity not anchored to the signed manifest" >&2
  exit 1
fi
mv "${tmp}/valid.env" "${missing_docker_install}/.env"

backup_stop="${tmp}/backup-stop"
backup_restart="${tmp}/backup-restart"
PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" DWARF_FAKE_WRITERS=1 \
  DWARF_FAKE_STOP_FILE="${backup_stop}" DWARF_FAKE_UP_FILE="${backup_restart}" \
  DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/backup.sh" \
    --output "${tmp}/quiesced-backup.tar.gz" >/dev/null
test -f "${backup_stop}"
test -f "${backup_restart}"
rm -f "${backup_stop}" "${backup_restart}"
cat >"${fake_bin}/tar" <<'EOF'
#!/usr/bin/env sh
exit 1
EOF
chmod +x "${fake_bin}/tar"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_SOCKET="${fake_socket}" DWARF_FAKE_WRITERS=1 \
  DWARF_FAKE_STOP_FILE="${backup_stop}" DWARF_FAKE_UP_FILE="${backup_restart}" \
  DWARF_INSTALL_ROOT="${missing_docker_install}" \
  bash "${missing_docker_install}/current/backup.sh" \
    --output "${tmp}/failed-backup.tar.gz" >/dev/null 2>&1; then
  echo "backup accepted an induced archive failure" >&2
  exit 1
fi
test -f "${backup_stop}"
test -f "${backup_restart}"
rm -f "${fake_bin}/tar"

fresh_fail_once="${tmp}/fresh-fail-deploy-once"
touch "${fresh_fail_once}"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_FAIL_FILE="${fresh_fail_once}" bash "${ROOT}/delivery/install.sh" \
    --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
    --install-root "${fresh_install}" --runtime-root "${fresh_runtime}" >"${tmp}/fresh-failure.out" 2>&1; then
  echo "fresh install accepted an induced deployment failure" >&2
  exit 1
fi
test ! -e "${fresh_install}/current"
test ! -e "${fresh_install}/.env"
test ! -e "${fresh_install}/install-receipt"
test ! -e "${fresh_runtime}/.dwarf-installation-id"
test ! -e "${fresh_runtime}/.dwarf-data-schema"
test ! -e "${fresh_runtime}/state/ssh_deploy_key"
test ! -e "${fresh_runtime}/state/ssh_known_hosts"
test ! -e "${fresh_runtime}/state/scenarios"
test ! -e "${fresh_runtime}/state/targets/manifests"
test ! -e "${fresh_runtime}/state/profiles"
PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" \
    --test-public-key "${tmp}/public.pem" --install-root "${fresh_install}" \
    --runtime-root "${fresh_runtime}" >/dev/null
test "$(readlink "${fresh_install}/current")" = releases/v9.0.0-test

semantic_fail_install="${tmp}/semantic-fail-install"
semantic_fail_runtime="${tmp}/semantic-fail-runtime"
semantic_down="${tmp}/semantic-fail-down"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALLER_TEST_MODE=1 DWARF_FAKE_SOCKET="${fake_socket}" \
  DWARF_FAKE_MODE=status-failure DWARF_FAKE_DOWN_FILE="${semantic_down}" \
  bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" \
    --test-public-key "${tmp}/public.pem" --install-root "${semantic_fail_install}" \
    --runtime-root "${semantic_fail_runtime}" >"${tmp}/semantic-failure.out" 2>&1; then
  echo "fresh install accepted failed semantic readiness" >&2
  exit 1
fi
test -f "${semantic_down}"
test ! -e "${semantic_fail_install}/current"
test ! -e "${semantic_fail_install}/.env"
test ! -e "${semantic_fail_install}/install-receipt"
test ! -e "${semantic_fail_runtime}/.dwarf-installation-id"
test ! -e "${semantic_fail_runtime}/.dwarf-data-schema"

cp "${bundle}/catalog/scenarios/signed-scenario.yaml" "${tmp}/signed-scenario-good.yaml"
printf 'name: tampered-after-signing\n' >"${bundle}/catalog/scenarios/signed-scenario.yaml"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" \
  --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" \
  --install-root "${tmp}/bad-catalog" --runtime-root "${tmp}/bad-catalog-runtime" \
  --prepare-only >"${tmp}/catalog-checksum.out" 2>&1; then
  echo "installer accepted a runtime catalog changed after signing" >&2
  exit 1
fi
grep -q 'runtime catalog checksum verification failed' "${tmp}/catalog-checksum.out"
mv "${tmp}/signed-scenario-good.yaml" "${bundle}/catalog/scenarios/signed-scenario.yaml"

cp "${bundle}/release-manifest.json" "${tmp}/manifest-good.json"
printf '\n' >>"${bundle}/docker-compose.dwarf.yml"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" --install-root "${tmp}/bad-checksum" --runtime-root "${tmp}/bad-runtime" --prepare-only >"${tmp}/checksum.out" 2>&1; then
  echo "installer accepted a bad artifact checksum" >&2
  exit 1
fi
grep -qi checksum "${tmp}/checksum.out"
mv "${tmp}/manifest-good.json" "${bundle}/release-manifest.json"
printf 'bad' >"${bundle}/release-manifest.sig"
if DWARF_INSTALLER_TEST_MODE=1 bash "${ROOT}/delivery/install.sh" --bundle-dir "${bundle}" --test-public-key "${tmp}/public.pem" --install-root "${tmp}/bad-signature" --runtime-root "${tmp}/bad-runtime-two" --prepare-only >"${tmp}/signature.out" 2>&1; then
  echo "installer accepted a bad manifest signature" >&2
  exit 1
fi
grep -qi signature "${tmp}/signature.out"

cat >"${fake_bin}/docker" <<'EOF'
#!/usr/bin/env sh
if [ "$1 $2" = "compose version" ]; then exit 0; fi
if [ "$1" = info ]; then echo 'Cannot connect to the Docker daemon' >&2; exit 1; fi
exit 0
EOF
chmod +x "${fake_bin}/docker"
if PATH="${fake_bin}:${PATH}" DWARF_TEST_UNAME_S=Darwin DWARF_TEST_UNAME_M=arm64 DWARF_INSTALL_ROOT="${install_one}" bash "${ROOT}/delivery/scripts/doctor.sh" >"${tmp}/doctor-macos.out" 2>&1; then
  echo "doctor accepted a stopped Docker daemon" >&2
  exit 1
fi
grep -q 'Start Docker Desktop' "${tmp}/doctor-macos.out"
if PATH="${fake_bin}:${PATH}" DWARF_TEST_UNAME_S=Linux DWARF_TEST_UNAME_M=x86_64 DWARF_INSTALL_ROOT="${install_one}" bash "${ROOT}/delivery/scripts/doctor.sh" >"${tmp}/doctor-linux.out" 2>&1; then
  echo "doctor accepted a stopped Docker daemon" >&2
  exit 1
fi
grep -q 'systemctl start docker' "${tmp}/doctor-linux.out"

sentinel="${runtime_one}/sentinel"
touch "${sentinel}"
if DWARF_INSTALL_ROOT="${install_one}" bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" >"${tmp}/purge.out" 2>&1; then
  echo "uninstall accepted purge without exact confirmation" >&2
  exit 1
fi
test -f "${sentinel}"

installation_id=$(sed -n 's/^installation_id=//p' "${install_one}/install-receipt")
if PATH="${fake_bin}:${PATH}" DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >"${tmp}/purge-docker-down.out" 2>&1; then
  echo "uninstall purged while Docker was unavailable" >&2
  exit 1
fi
grep -q 'Docker must be running' "${tmp}/purge-docker-down.out"
test -f "${sentinel}"

cat >"${fake_bin}/docker" <<'EOF'
#!/usr/bin/env sh
if [ -n "${DWARF_FAKE_DOCKER_LOG:-}" ]; then
  printf '%s\n' "$*" >>"${DWARF_FAKE_DOCKER_LOG}"
fi
if [ "${DWARF_FAKE_COMPOSE_UNAVAILABLE:-0}" = 1 ] && [ "$1 $2" = "compose version" ]; then
  exit 1
fi
case " $* " in
  *" compose version "*|*" info "*|*" compose "*" down "*) exit 0 ;;
  *" ps -aq "*) [ "${DWARF_FAKE_MANAGED:-0}" = 1 ] && echo stale-managed-container; exit 0 ;;
esac
exit 0
EOF
chmod +x "${fake_bin}/docker"
if PATH="${fake_bin}:${PATH}" DWARF_FAKE_MANAGED=1 DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >"${tmp}/purge-managed.out" 2>&1; then
  echo "uninstall purged while a stopped managed container remained" >&2
  exit 1
fi
grep -q 'managed topology containers still exist' "${tmp}/purge-managed.out"
test -f "${sentinel}"

fake_docker_log="${tmp}/uninstall-docker.log"
: >"${fake_docker_log}"
PATH="${fake_bin}:${PATH}" DWARF_FAKE_DOCKER_LOG="${fake_docker_log}" \
  DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" >/dev/null
grep -q 'compose .* down --remove-orphans' "${fake_docker_log}"
if grep -q -- '--volumes' "${fake_docker_log}"; then
  echo "preserve-data uninstall removed Compose volume handles" >&2
  exit 1
fi
test -f "${sentinel}"

if [[ $(uname -s) == Darwin && -L /tmp ]]; then
  darwin_shallow_runtime="/tmp/dwarf-uninstall-contract-$$"
  darwin_shallow_install="${tmp}/darwin-shallow-install"
  cp -a "${install_one}" "${darwin_shallow_install}"
  mkdir -p "${darwin_shallow_runtime}"
  printf '%s\n' "${installation_id}" >"${darwin_shallow_runtime}/.dwarf-installation-id"
  touch "${darwin_shallow_runtime}/sentinel"
  sed -i '' \
    -e "s|^install_root=.*|install_root=${darwin_shallow_install}|" \
    -e "s|^runtime_root=.*|runtime_root=${darwin_shallow_runtime}|" \
    "${darwin_shallow_install}/install-receipt"
  PATH="${fake_bin}:${PATH}" DWARF_INSTALL_ROOT="${darwin_shallow_install}" \
    bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${darwin_shallow_runtime}" \
      --confirm "PURGE DWARF ${installation_id}" >/dev/null
  test ! -e "${darwin_shallow_runtime}"
fi

cp "${install_one}/.env" "${tmp}/uninstall-good.env"
sed 's/^DWARF_COMPOSE_PROJECT_NAME=.*/DWARF_COMPOSE_PROJECT_NAME=foreign-project/' \
  "${tmp}/uninstall-good.env" >"${install_one}/.env"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >"${tmp}/purge-project-mismatch.out" 2>&1; then
  echo "uninstall purged with a Compose project identity not anchored to the receipt" >&2
  exit 1
fi
grep -q 'Compose project identity differs from the install receipt' "${tmp}/purge-project-mismatch.out"
test -f "${sentinel}"
mv "${tmp}/uninstall-good.env" "${install_one}/.env"

compose_path="${install_one}/current/docker-compose.dwarf.yml"
cp "${compose_path}" "${tmp}/uninstall-good-compose.yml"
printf '\n# modified before purge\n' >>"${compose_path}"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >"${tmp}/purge-compose-mismatch.out" 2>&1; then
  echo "uninstall purged with a Compose artifact that differs from the signed manifest" >&2
  exit 1
fi
grep -q 'Compose artifact differs from the signed manifest' "${tmp}/purge-compose-mismatch.out"
test -f "${sentinel}"
mv "${tmp}/uninstall-good-compose.yml" "${compose_path}"

mv "${compose_path}" "${tmp}/uninstall-good-compose.yml"
if PATH="${fake_bin}:${PATH}" DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >"${tmp}/purge-compose-missing.out" 2>&1; then
  echo "uninstall purged while the signed Compose artifact was missing" >&2
  exit 1
fi
grep -q 'signed Compose artifact is missing or unsafe' "${tmp}/purge-compose-missing.out"
test -f "${sentinel}"
mv "${tmp}/uninstall-good-compose.yml" "${compose_path}"

if PATH="${fake_bin}:${PATH}" DWARF_FAKE_COMPOSE_UNAVAILABLE=1 \
  DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >"${tmp}/purge-compose-unavailable.out" 2>&1; then
  echo "uninstall purged while Docker Compose was unavailable" >&2
  exit 1
fi
grep -q 'Docker Compose is required for purge' "${tmp}/purge-compose-unavailable.out"
test -f "${sentinel}"

: >"${fake_docker_log}"
PATH="${fake_bin}:${PATH}" DWARF_FAKE_DOCKER_LOG="${fake_docker_log}" \
  DWARF_INSTALL_ROOT="${install_one}" \
  bash "${ROOT}/delivery/scripts/uninstall.sh" --purge "${runtime_one}" \
    --confirm "PURGE DWARF ${installation_id}" >/dev/null
grep -q 'compose .* down --remove-orphans --volumes' "${fake_docker_log}"
test ! -e "${runtime_one}"

grep -q 'rollback' "${ROOT}/delivery/scripts/update.sh"
grep -q 'previous' "${ROOT}/delivery/scripts/update.sh"
grep -q 'DWARF_PURGE_CONFIRMATION' "${ROOT}/delivery/scripts/uninstall.sh"

echo "installer contract ok"
