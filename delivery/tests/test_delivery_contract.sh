#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)

required_files=(
  "README.md"
  "INSTALL.md"
  "OPERATIONS.md"
  "docs/index.html"
  "delivery/docker-compose.dwarf.yml"
  "delivery/scripts/common.sh"
  "delivery/scripts/install.sh"
  "delivery/scripts/build-image.sh"
  "delivery/scripts/deploy.sh"
  "delivery/scripts/status.sh"
  "delivery/scripts/undeploy.sh"
  "delivery/scripts/uninstall.sh"
  "infrastructure/docker/dwarf-fw.Dockerfile"
  "infrastructure/docker/dwarf-controller.Dockerfile"
  "infrastructure/docker/dwarf-fw-entrypoint.sh"
  "dwarf/cardano-profile"
  "dwarf/scenarios/README.md"
)

forbidden_paths=(
  "agent"
  "dist"
  "dwarf/fuzz-tests"
  "dwarf/tests"
  "dwarf/docs/fuzz-framework-pattern-mapping.md"
  "dwarf/docs/production-target.md"
  "dwarf/docs/web-ui.md"
  "dwarf/corpora/m3"
  "dwarf/corpora/plutus-phase2"
  "dwarf/corpora/crash-triage-example"
  "dwarf/corpora/differential-rule-harness-example"
  "OPERATE-CHEATSHEET.md"
  "dwarf-client-showcase.html"
  "dwarf-operate-cheatsheet.html"
  "dwarf/scripts/README.md"
  "dwarf/scripts/deploy-dashboard.sh"
  "dwarf-uptodatemay"
  "dwarf-v55"
  "infrastructure/cardano-box"
  "infrastructure/docker/amaru-0.1.2.Dockerfile"
  "infrastructure/docker/cardano-node-10.7.1.Dockerfile"
  "infrastructure/docker/docker-compose.yml"
  "infrastructure/docker/IMAGE-SIGNING.md"
  "infrastructure/docker/REPRODUCIBLE-BUILD.md"
)

required_executables=(
  "delivery/scripts/install.sh"
  "delivery/scripts/build-image.sh"
  "delivery/scripts/deploy.sh"
  "delivery/scripts/status.sh"
  "delivery/scripts/undeploy.sh"
  "delivery/scripts/uninstall.sh"
)

for path in "${required_files[@]}"; do
  test -e "${PACKAGE_ROOT}/${path}" || {
    echo "missing required file: ${path}" >&2
    exit 1
  }
done

for path in "${forbidden_paths[@]}"; do
  test ! -e "${PACKAGE_ROOT}/${path}" || {
    echo "non-delivery path should not be packaged: ${path}" >&2
    exit 1
  }
done

for path in "${required_executables[@]}"; do
  test -x "${PACKAGE_ROOT}/${path}" || {
    echo "not executable: ${path}" >&2
    exit 1
  }
done

for script in "${required_executables[@]}" "delivery/scripts/common.sh"; do
  bash -n "${PACKAGE_ROOT}/${script}"
done

if grep -Fq '?token=${ADA2_DWARF_TOKEN}' "${PACKAGE_ROOT}/delivery/scripts/install.sh"; then
  echo "source installer must not print the private dashboard token" >&2
  exit 1
fi

test_runtime_root=$(mktemp -d)
trap 'rm -rf "${test_runtime_root}"' EXIT
(
  unset ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN DWARF_INSTALLATION_ID
  # shellcheck source=../scripts/common.sh
  source "${PACKAGE_ROOT}/delivery/scripts/common.sh"
  DWARF_RUNTIME_ROOT="${test_runtime_root}"
  DWARF_SSH_KEY_PATH="${test_runtime_root}/state/ssh_deploy_key"
  DWARF_SSH_KNOWN_HOSTS="${test_runtime_root}/state/ssh_known_hosts"
  ensure_runtime_dirs
  ensure_controller_identity
  test "${DWARF_DASHBOARD_BIND}" = 127.0.0.1
  test "${ADA2_DWARF_TOKEN}" != dwarf
  test "${#ADA2_DWARF_TOKEN}" -ge 48
  test "${#DWARF_CONTROLLER_TOKEN}" -ge 48
  test -s "${test_runtime_root}/state/dashboard-token"
  test -s "${test_runtime_root}/state/controller-token"
  test -s "${test_runtime_root}/state/installation-id"
  seed_example_runs
  seed_example_bundles
  # Public source installs start without private or pre-recorded operator runs.
  test -z "$(ls -A "${test_runtime_root}/runs")"
  test -z "$(ls -A "${test_runtime_root}/bundles")"
)

first_dashboard_token_fingerprint=$(cksum "${test_runtime_root}/state/dashboard-token" | awk '{print $1 ":" $2}')
file_mode() {
  stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"
}
test "$(file_mode "${test_runtime_root}/state/dashboard-token")" = 600
chmod 0640 "${test_runtime_root}/state/dashboard-token"
(
  unset ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN DWARF_INSTALLATION_ID
  # shellcheck source=../scripts/common.sh
  source "${PACKAGE_ROOT}/delivery/scripts/common.sh"
  DWARF_RUNTIME_ROOT="${test_runtime_root}"
  ensure_controller_identity
  test "${ADA2_DWARF_TOKEN}" = "$(tr -d '\r\n' <"${test_runtime_root}/state/dashboard-token")"
)
test "${first_dashboard_token_fingerprint}" = "$(cksum "${test_runtime_root}/state/dashboard-token" | awk '{print $1 ":" $2}')"
test "$(file_mode "${test_runtime_root}/state/dashboard-token")" = 600

symlink_runtime_root=$(mktemp -d)
printf '%s\n' substitution >"${symlink_runtime_root}/substitution"
mkdir -p "${symlink_runtime_root}/state"
ln -s "${symlink_runtime_root}/substitution" "${symlink_runtime_root}/state/dashboard-token"
if (
  unset ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN DWARF_INSTALLATION_ID
  # shellcheck source=../scripts/common.sh
  source "${PACKAGE_ROOT}/delivery/scripts/common.sh"
  DWARF_RUNTIME_ROOT="${symlink_runtime_root}"
  ensure_controller_identity
) >/dev/null 2>&1; then
  echo "source installer accepted a symlinked dashboard credential" >&2
  exit 1
fi
rm -rf "${symlink_runtime_root}"

fifo_runtime_root=$(mktemp -d)
mkdir -p "${fifo_runtime_root}/state"
mkfifo "${fifo_runtime_root}/state/dashboard-token"
if ! PACKAGE_ROOT="${PACKAGE_ROOT}" FIFO_RUNTIME_ROOT="${fifo_runtime_root}" python3 - <<'PY'
import os
import subprocess
import sys

script = r'''
set -euo pipefail
unset ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN DWARF_INSTALLATION_ID
source "${PACKAGE_ROOT}/delivery/scripts/common.sh"
DWARF_RUNTIME_ROOT="${FIFO_RUNTIME_ROOT}"
ensure_controller_identity
'''
try:
    result = subprocess.run(
        ["bash", "-c", script],
        env=os.environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(1)
raise SystemExit(0 if result.returncode != 0 else 1)
PY
then
  echo "source installer did not promptly reject a FIFO dashboard credential" >&2
  exit 1
fi
rm -rf "${fifo_runtime_root}"

if grep -Eq 'image:.*:current|DWARF_DASHBOARD_BIND:-0\.0\.0\.0|ADA2_DWARF_TOKEN:-dwarf' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"; then
  echo "release Compose contains mutable images or insecure public defaults" >&2
  exit 1
fi
grep -q 'DWARF_DASHBOARD_BIND:-127.0.0.1' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q "dwarf-fw" "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q "dwarf-controller" "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_DOCKER_SOCKET_PATH:-/var/run/docker.sock' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
if awk '/^  dwarf-fw:/{dashboard=1} /^  dwarf-controller:/{dashboard=0} dashboard && /docker.sock/{found=1} END{exit found ? 0 : 1}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"; then
  echo "dashboard service must not receive the Docker socket" >&2
  exit 1
fi
grep -q 'DWARF_CONTROLLER_URL: http://dwarf-controller:8790' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_HOST_RUNS_DIR: ${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/runs' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_HOST_OS: ${DWARF_HOST_OS:?set DWARF_HOST_OS' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_HOST_ARCH: ${DWARF_HOST_ARCH:?set DWARF_HOST_ARCH' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_AVAILABLE_IMAGE_DIGESTS: ${DWARF_AVAILABLE_IMAGE_DIGESTS:-}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_AVAILABLE_CREDENTIALS: ${DWARF_AVAILABLE_CREDENTIALS:-}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q "printf 'DWARF_HOST_OS=%s" "${PACKAGE_ROOT}/delivery/install.sh"
grep -q "printf 'DWARF_HOST_ARCH=%s" "${PACKAGE_ROOT}/delivery/install.sh"
grep -q "printf 'DWARF_AVAILABLE_IMAGE_DIGESTS=%s" "${PACKAGE_ROOT}/delivery/install.sh"
grep -q 'DWARF_CONTROLLER_HOST_STATE_DIR:' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'dwarf-controller-state:/var/dwarf/controller-state' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'ADA2_DWARF_LAUNCH_ROOT: /var/dwarf/launches' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'ADA2_DWARF_RUN_LEDGER_ROOT: /var/dwarf/run-ledger' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'dwarf-launches:/var/dwarf/launches:ro' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'dwarf-run-ledger:/var/dwarf/run-ledger' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
[[ $(grep -Fc 'ADA2_DWARF_REMOTE_BASE: /var/dwarf/controller-state/controller/topology-runtimes' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml") -eq 2 ]]
grep -q 'ADA2_DWARF_PROFILES_DIR: /var/dwarf/dashboard-state/profiles' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q '${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/profiles:/var/dwarf/dashboard-state/profiles:ro' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'ADA2_DWARF_SCENARIOS_DIR: /var/dwarf/dashboard-state/scenarios' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q '${DWARF_RUNTIME_ROOT:?set DWARF_RUNTIME_ROOT}/state/scenarios:/var/dwarf/dashboard-state/scenarios:ro' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
if awk '/^  dwarf-controller:/{controller=1} /^networks:/{controller=0} controller && /- dwarf-state:/{found=1} END{exit found ? 0 : 1}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"; then
  echo "controller service must not receive the full dashboard state volume" >&2
  exit 1
fi
awk '/^  dwarf-controller:/{controller=1} /^networks:/{controller=0} controller && /- \/var\/dwarf\/state$/{found=1} END{exit found ? 0 : 1}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
if awk '/^  dwarf-fw:/{dashboard=1} /^  dwarf-controller:/{dashboard=0} dashboard && /dwarf-controller-state/{found=1} END{exit found ? 0 : 1}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"; then
  echo "dashboard service must not receive controller-private state" >&2
  exit 1
fi
[[ $(grep -Fc 'DWARF_INSTALLATION_ID: ${DWARF_INSTALLATION_ID:?set DWARF_INSTALLATION_ID}' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml") -eq 2 ]]
grep -q 'DWARF_CONTROLLER_TOKEN' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'DWARF_CONTROLLER_IMAGE' "${PACKAGE_ROOT}/delivery/scripts/build-image.sh"
grep -q 'FROM scratch' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-controller.Dockerfile"
grep -q 'controller image must not inherit the dashboard state volume' "${PACKAGE_ROOT}/delivery/scripts/build-image.sh"
grep -q 'seed_example_runs' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'seed_example_runs' "${PACKAGE_ROOT}/delivery/scripts/install.sh"
grep -q 'seed_example_bundles' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'seed_example_bundles' "${PACKAGE_ROOT}/delivery/scripts/install.sh"
grep -q 'seed_scenarios' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'seed_manifests' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'seed_profiles' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q -- '--force-recreate' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q '/api/status' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'docker exec -i' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'optional_moog_bootstrap' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'DWARF_MOOG_BOOTSTRAP' "${PACKAGE_ROOT}/delivery/scripts/common.sh"
grep -q 'moog bootstrap --approve --json' "${PACKAGE_ROOT}/delivery/scripts/common.sh"
grep -q 'moog healthcheck --json' "${PACKAGE_ROOT}/delivery/scripts/common.sh"
grep -q 'MOOG_GITHUB_PAT' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'MOOG_ANTITHESIS_LAUNCH_URL' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'MOOG_REGISTRY' "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'Acquire::Check-Valid-Until "false";' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"
grep -q 'Acquire::Check-Date "false";' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"
if grep -q '\\\"false\\\"' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"; then
  echo "Dockerfile apt config contains escaped quotes that apt will not parse as intended" >&2
  exit 1
fi
if grep -q 'infrastructure/cardano-box' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"; then
  echo "Dockerfile depends on cardano-box helper files that are not part of the delivery package" >&2
  exit 1
fi
if grep -Eq 'dwarf-v55|dwarf-uptodatemay|Selected audit/research evidence|May continuation notes|Source-of-truth and requirements docs|dist/' "${PACKAGE_ROOT}/README.md"; then
  echo "README still describes non-delivery carryover directories" >&2
  exit 1
fi

manifest_count=$(
  find "${PACKAGE_ROOT}/dwarf/targets/manifests" -type f -name '*.yaml' | wc -l | tr -d '[:space:]'
)
if [[ "${manifest_count}" != "40" ]]; then
  echo "expected 40 shipped target manifests, found ${manifest_count}" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/targets/manifests" -type f -name '*.yaml' \
  ! \( -name '*-cbor-decode-*.yaml' -o -name '*-mini-protocol-decode-*.yaml' -o -name '*-cov-*.yaml' \) \
  | grep -q .; then
  echo "target manifest catalog contains an unsupported manifest family" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/targets" -mindepth 1 -maxdepth 1 -type d \
  ! \( -name 'amaru' -o -name 'cardano-node' -o -name 'manifests' \) \
  | grep -q .; then
  echo "target source tree contains non-M2 top-level harness directories" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/corpora" -mindepth 1 -maxdepth 1 -type d \
  \( -name 'amaru-cardano-differential-cargo-fuzz-*' -o -name 'amaru-cargo-fuzz-ledger-*' \) \
  | grep -q .; then
  echo "corpora contains non-M2 differential or ledger campaign seeds" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/grammars" -mindepth 1 -maxdepth 1 -type d \
  \( -name 'amaru-cardano-differential-cargo-fuzz-*' -o -name 'amaru-cargo-fuzz-ledger-*' \) \
  | grep -q .; then
  echo "grammars contains non-M2 differential or ledger campaign dictionaries" >&2
  exit 1
fi

if grep -R -E '/Users/operator|/home/dwarf|cardano-box|192\.168\.30\.16' "${PACKAGE_ROOT}/dwarf/targets/manifests" "${PACKAGE_ROOT}/dwarf/targets/README.md" "${PACKAGE_ROOT}/dwarf/grammars/README.md"; then
  echo "public target or grammar catalog contains local host paths" >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  DWARF_IMAGE="ghcr.io/pragma-org/dwarf@sha256:$(printf 'a%.0s' {1..64})" \
  DWARF_CONTROLLER_IMAGE="ghcr.io/pragma-org/dwarf-controller@sha256:$(printf 'b%.0s' {1..64})" \
  DWARF_GOLDEN_IMAGES="ghcr.io/pragma-org/amaru@sha256:$(printf 'c%.0s' {1..64})" \
  DWARF_ARTIFACT_PACKAGE_AMARU_BASE="ghcr.io/pragma-org/amaru@sha256:$(printf 'c%.0s' {1..64})" \
  DWARF_ARTIFACT_PACKAGE_CARDANO_BASE="ghcr.io/intersectmbo/cardano-node@sha256:$(printf 'd%.0s' {1..64})" \
  DWARF_HOST_OS=linux DWARF_HOST_ARCH=amd64 \
  DWARF_RUNTIME_ROOT="${test_runtime_root}" \
  DWARF_AVAILABLE_IMAGE_DIGESTS="sha256:$(printf 'c%.0s' {1..64})" \
  DWARF_CONTROLLER_TOKEN=test-controller-token DWARF_INSTALLATION_ID=test-installation \
  ADA2_DWARF_TOKEN=test-dashboard-token \
    docker compose -f "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml" config >/dev/null
fi

echo "delivery contract ok"
