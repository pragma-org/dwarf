#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
COMPOSE_FILE="${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"

if [[ -f "${PACKAGE_ROOT}/.env" ]]; then
  # Match Compose's precedence: values explicitly exported by the caller win
  # over the optional env file, while the env file fills missing values.
  dwarf_caller_exports=$(export -p)
  set -a
  # shellcheck source=/dev/null
  source "${PACKAGE_ROOT}/.env"
  set +a
  eval "${dwarf_caller_exports}"
  unset dwarf_caller_exports
fi

DWARF_IMAGE=${DWARF_IMAGE:-dwarf/framework:current}
DWARF_CONTROLLER_IMAGE=${DWARF_CONTROLLER_IMAGE:-dwarf/controller:current}
DWARF_CONTAINER_NAME=${DWARF_CONTAINER_NAME:-dwarf-fw}
DWARF_DASHBOARD_BIND=${DWARF_DASHBOARD_BIND:-127.0.0.1}
DWARF_DASHBOARD_PORT=${DWARF_DASHBOARD_PORT:-8787}
DWARF_RUNTIME_ROOT=${DWARF_RUNTIME_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf}
DWARF_CONTROLLER_TOKEN=${DWARF_CONTROLLER_TOKEN:-}
DWARF_INSTALLATION_ID=${DWARF_INSTALLATION_ID:-}
DWARF_DOCKER_GID=${DWARF_DOCKER_GID:-}
DWARF_RUNTIME_GID=${DWARF_RUNTIME_GID:-$(id -g)}
DWARF_DOCKER_SOCKET_PATH=${DWARF_DOCKER_SOCKET_PATH:-/var/run/docker.sock}
DWARF_GOLDEN_IMAGES=${DWARF_GOLDEN_IMAGES:-ghcr.io/pragma-org/amaru@sha256:2176f12085219a4052be17dca89742954897eb9a5321554c799d0458913c9c60,ghcr.io/pragma-org/amaru@sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e,ghcr.io/intersectmbo/cardano-node@sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f,ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c}
DWARF_ARTIFACT_PACKAGE_AMARU_BASE=${DWARF_ARTIFACT_PACKAGE_AMARU_BASE:-ghcr.io/pragma-org/amaru@sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e}
DWARF_ARTIFACT_PACKAGE_CARDANO_BASE=${DWARF_ARTIFACT_PACKAGE_CARDANO_BASE:-ghcr.io/intersectmbo/cardano-node@sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f}
case "${DWARF_HOST_OS:-$(uname -s)}" in
  Darwin|darwin) DWARF_HOST_OS=darwin ;;
  Linux|linux) DWARF_HOST_OS=linux ;;
  *) DWARF_HOST_OS=$(uname -s | tr '[:upper:]' '[:lower:]') ;;
esac
case "${DWARF_HOST_ARCH:-$(uname -m)}" in
  aarch64|arm64) DWARF_HOST_ARCH=arm64 ;;
  amd64|x86_64) DWARF_HOST_ARCH=amd64 ;;
  *) DWARF_HOST_ARCH=$(uname -m | tr '[:upper:]' '[:lower:]') ;;
esac
if [[ -z "${DWARF_AVAILABLE_IMAGE_DIGESTS:-}" ]]; then
  IFS=',' read -r -a dwarf_golden_images <<<"${DWARF_GOLDEN_IMAGES}"
  DWARF_AVAILABLE_IMAGE_DIGESTS=
  for dwarf_golden_image in "${dwarf_golden_images[@]}"; do
    [[ "${dwarf_golden_image}" == *@sha256:* ]] || continue
    if [[ -n "${DWARF_AVAILABLE_IMAGE_DIGESTS}" ]]; then
      DWARF_AVAILABLE_IMAGE_DIGESTS+=,
    fi
    DWARF_AVAILABLE_IMAGE_DIGESTS+="${dwarf_golden_image##*@}"
  done
fi
DWARF_AVAILABLE_CREDENTIALS=${DWARF_AVAILABLE_CREDENTIALS:-}
if [[ -z "${DWARF_SOURCE_REVISION:-}" ]]; then
  if git -C "${PACKAGE_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
    DWARF_SOURCE_REVISION=$(git -C "${PACKAGE_ROOT}" rev-parse HEAD)
  else
    DWARF_SOURCE_REVISION=unknown
  fi
fi
DWARF_CATALOG_REVISION=${DWARF_CATALOG_REVISION:-${DWARF_SOURCE_REVISION}}
if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
  if git -C "${PACKAGE_ROOT}" log -1 --format=%ct >/dev/null 2>&1; then
    SOURCE_DATE_EPOCH=$(git -C "${PACKAGE_ROOT}" log -1 --format=%ct)
  else
    SOURCE_DATE_EPOCH=0
  fi
fi
ADA2_DWARF_TOKEN=${ADA2_DWARF_TOKEN:-}
DWARF_MOOG_BOOTSTRAP=${DWARF_MOOG_BOOTSTRAP:-off}
DWARF_MOOG_BOOTSTRAP_APPROVE=${DWARF_MOOG_BOOTSTRAP_APPROVE:-0}
# SSH key / known_hosts mounted read-only into the container for optional
# substrate fan-out. Default to placeholder files under the runtime root
# (created by ensure_runtime_dirs) so a fresh install never bind-mounts a
# missing host path. Override to real files only when SSH fan-out is needed.
DWARF_SSH_KEY_PATH=${DWARF_SSH_KEY_PATH:-${DWARF_RUNTIME_ROOT}/state/ssh_deploy_key}
DWARF_SSH_KNOWN_HOSTS=${DWARF_SSH_KNOWN_HOSTS:-${DWARF_RUNTIME_ROOT}/state/ssh_known_hosts}

export DWARF_IMAGE
export DWARF_CONTROLLER_IMAGE
export DWARF_CONTAINER_NAME
export DWARF_DASHBOARD_BIND
export DWARF_DASHBOARD_PORT
export DWARF_RUNTIME_ROOT
export DWARF_CONTROLLER_TOKEN
export DWARF_INSTALLATION_ID
export DWARF_DOCKER_GID
export DWARF_RUNTIME_GID
export DWARF_DOCKER_SOCKET_PATH
export DWARF_GOLDEN_IMAGES
export DWARF_ARTIFACT_PACKAGE_AMARU_BASE
export DWARF_ARTIFACT_PACKAGE_CARDANO_BASE
export DWARF_HOST_OS
export DWARF_HOST_ARCH
export DWARF_AVAILABLE_IMAGE_DIGESTS
export DWARF_AVAILABLE_CREDENTIALS
export DWARF_SOURCE_REVISION
export DWARF_CATALOG_REVISION
export SOURCE_DATE_EPOCH
export ADA2_DWARF_TOKEN
export DWARF_MOOG_BOOTSTRAP
export DWARF_MOOG_BOOTSTRAP_APPROVE
export DWARF_SSH_KEY_PATH
export DWARF_SSH_KNOWN_HOSTS

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

require_docker() {
  require_cmd docker
  docker version >/dev/null
  docker compose version >/dev/null
}

ensure_golden_images() {
  local image
  local images=()
  # Validate the whole list before downloading anything; controller readiness
  # requires these exact target identities, not mutable version tags.
  if [[ -z "${DWARF_GOLDEN_IMAGES}" || "${DWARF_GOLDEN_IMAGES}" == ,* ||
        "${DWARF_GOLDEN_IMAGES}" == *, || "${DWARF_GOLDEN_IMAGES}" == *,,* ]]; then
    echo "DWARF_GOLDEN_IMAGES must contain a non-empty list of digest-pinned target images" >&2
    return 1
  fi
  IFS=, read -r -a images <<< "${DWARF_GOLDEN_IMAGES}"
  for image in "${images[@]}"; do
    if [[ ! "${image}" =~ ^[^[:space:]@]+@sha256:[a-f0-9]{64}$ ]]; then
      echo "target image must be digest-pinned: ${image}" >&2
      return 1
    fi
  done
  echo "Checking pinned target images required by the local controller"
  for image in "${images[@]}"; do
    if docker image inspect "${image}" >/dev/null 2>&1; then
      echo "    available: ${image}"
      continue
    fi
    echo "    pulling missing target: ${image}"
    if ! docker pull "${image}"; then
      echo "failed to pull required target image: ${image}; containers were not started" >&2
      return 1
    fi
    if ! docker image inspect "${image}" >/dev/null 2>&1; then
      echo "required target image still missing after pull: ${image}; containers were not started" >&2
      return 1
    fi
  done
}

ensure_package_layout() {
  local required=(
    "${PACKAGE_ROOT}/dwarf/cardano-profile"
    "${PACKAGE_ROOT}/dwarf/profile_manager"
    "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"
    "${PACKAGE_ROOT}/infrastructure/docker/dwarf-controller.Dockerfile"
    "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw-entrypoint.sh"
    "${PACKAGE_ROOT}/infrastructure/docker/requirements-framework.txt"
    "${COMPOSE_FILE}"
  )

  for path in "${required[@]}"; do
    test -e "$path" || {
      echo "missing package component: $path" >&2
      exit 1
    }
  done
}

prepare_profile_catalog() {
  python3 - "${DWARF_RUNTIME_ROOT}/state/profiles" <<'PY'
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
if root.is_symlink():
    raise SystemExit("profile catalog root must not be a symlink")
root.mkdir(parents=True, exist_ok=True)
nofollow = getattr(os, "O_NOFOLLOW", 0)
root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow)
try:
    lock_fd = os.open(
        ".dwarf-catalog.lock",
        os.O_RDWR | os.O_CREAT | nofollow,
        0o660,
        dir_fd=root_fd,
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise SystemExit("profile catalog lock must be a regular file")
        os.fchmod(lock_fd, 0o660)
    finally:
        os.close(lock_fd)
finally:
    os.close(root_fd)
PY
}

ensure_runtime_dirs() {
  mkdir -p "${DWARF_RUNTIME_ROOT}/runs" "${DWARF_RUNTIME_ROOT}/state" "${DWARF_RUNTIME_ROOT}/state/launches" "${DWARF_RUNTIME_ROOT}/state/run-ledger" "${DWARF_RUNTIME_ROOT}/state/measurement-targets" "${DWARF_RUNTIME_ROOT}/state/measurements" "${DWARF_RUNTIME_ROOT}/state/measurement-profiles" "${DWARF_RUNTIME_ROOT}/state/scenarios" "${DWARF_RUNTIME_ROOT}/state/version-catalog" "${DWARF_RUNTIME_ROOT}/state/version-imports" "${DWARF_RUNTIME_ROOT}/bundles" "${DWARF_RUNTIME_ROOT}/controller-state"
  prepare_profile_catalog
  # The framework container runs as uid 1000 and writes run bundles + state into
  # these bind-mounted dirs. Make them writable regardless of which host uid owns
  # them (a fresh clone's dirs, or dirs Docker auto-created as root). Best-effort:
  # ignore if the current user may not chmod pre-existing dirs.
  chmod 0770 "${DWARF_RUNTIME_ROOT}" "${DWARF_RUNTIME_ROOT}/runs" "${DWARF_RUNTIME_ROOT}/state" "${DWARF_RUNTIME_ROOT}/state/launches" "${DWARF_RUNTIME_ROOT}/state/run-ledger" "${DWARF_RUNTIME_ROOT}/state/measurement-targets" "${DWARF_RUNTIME_ROOT}/state/measurements" "${DWARF_RUNTIME_ROOT}/state/measurement-profiles" "${DWARF_RUNTIME_ROOT}/state/profiles" "${DWARF_RUNTIME_ROOT}/state/scenarios" "${DWARF_RUNTIME_ROOT}/state/version-catalog" "${DWARF_RUNTIME_ROOT}/state/version-imports" "${DWARF_RUNTIME_ROOT}/bundles" "${DWARF_RUNTIME_ROOT}/controller-state" 2>/dev/null || true
  # Placeholder SSH files so the read-only key/known_hosts mounts always have an
  # existing source on a fresh install (empty = no fan-out configured, harmless).
  [[ -e "${DWARF_SSH_KNOWN_HOSTS}" ]] || : > "${DWARF_SSH_KNOWN_HOSTS}"
  if [[ ! -e "${DWARF_SSH_KEY_PATH}" ]]; then
    : > "${DWARF_SSH_KEY_PATH}"
    chmod 600 "${DWARF_SSH_KEY_PATH}"
  fi
}

load_or_create_private_token() {
  local token_path=$1
  python3 - "${token_path}" <<'PY'
import os
import secrets
import stat
import sys
import tempfile

path = os.path.abspath(sys.argv[1])
parent = os.path.dirname(path)
name = os.path.basename(path)
nofollow = getattr(os, "O_NOFOLLOW", 0)
nonblock = getattr(os, "O_NONBLOCK", 0)

if not os.path.lexists(path):
    fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=parent)
    try:
        token = (secrets.token_urlsafe(48) + "\n").encode("ascii")
        os.fchmod(fd, 0o600)
        os.write(fd, token)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

try:
    fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
except OSError as error:
    raise SystemExit(f"private credential is not a safe regular file: {path}: {error}")
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"private credential is not a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise SystemExit(f"private credential is not owned by the installing user: {path}")
    os.fchmod(fd, 0o600)
    if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
        raise SystemExit(f"private credential permissions are not 0600: {path}")
    chunks = []
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
        if sum(map(len, chunks)) > 4096:
            raise SystemExit(f"private credential is unexpectedly large: {path}")
finally:
    os.close(fd)

try:
    value = b"".join(chunks).decode("ascii").strip()
except UnicodeDecodeError:
    raise SystemExit(f"private credential is not ASCII: {path}")
if len(value) < 48 or any(character.isspace() for character in value):
    raise SystemExit(f"private credential is empty or malformed: {path}")
print(value)
PY
}

load_existing_private_token() {
  local token_path=$1
  python3 - "${token_path}" <<'PY'
import os
import stat
import sys

path = os.path.abspath(sys.argv[1])
nofollow = getattr(os, "O_NOFOLLOW", 0)
nonblock = getattr(os, "O_NONBLOCK", 0)
try:
    fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
except OSError as error:
    raise SystemExit(f"persisted credential is not a safe regular file: {path}: {error}")
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"persisted credential is not a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise SystemExit(f"persisted credential is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SystemExit(f"persisted credential permissions are not 0600: {path}")
    chunks = []
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
        if sum(map(len, chunks)) > 4096:
            raise SystemExit(f"persisted credential is unexpectedly large: {path}")
finally:
    os.close(fd)

try:
    value = b"".join(chunks).decode("ascii").strip()
except UnicodeDecodeError:
    raise SystemExit(f"persisted credential is not ASCII: {path}")
if len(value) < 48 or any(character.isspace() for character in value):
    raise SystemExit(f"persisted credential is empty or malformed: {path}")
print(value)
PY
}

load_existing_installation_id() {
  local identity_path=$1
  python3 - "${identity_path}" <<'PY'
import os
import re
import stat
import sys

path = os.path.abspath(sys.argv[1])
nofollow = getattr(os, "O_NOFOLLOW", 0)
nonblock = getattr(os, "O_NONBLOCK", 0)
try:
    fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
except OSError as error:
    raise SystemExit(f"persisted installation ID is not a safe regular file: {path}: {error}")
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"persisted installation ID is not a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise SystemExit(f"persisted installation ID is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SystemExit(f"persisted installation ID permissions are not 0600: {path}")
    value_bytes = os.read(fd, 257)
    if len(value_bytes) > 256 or os.read(fd, 1):
        raise SystemExit(f"persisted installation ID is unexpectedly large: {path}")
finally:
    os.close(fd)

try:
    value = value_bytes.decode("ascii").strip()
except UnicodeDecodeError:
    raise SystemExit(f"persisted installation ID is not ASCII: {path}")
if not re.fullmatch(r"install-[0-9a-f]{16}", value):
    raise SystemExit(f"persisted installation ID is malformed: {path}")
print(value)
PY
}

# Read-only commands must reuse the exact identity install/deploy persisted.
# Unlike ensure_controller_identity, this helper never creates credentials or
# an installation ID and refuses an environment that names another install.
load_persisted_identity() {
  local state="${DWARF_RUNTIME_ROOT}/state"
  local installation_file="${state}/installation-id"
  local persisted_dashboard persisted_controller persisted_installation path
  for path in "${state}/dashboard-token" "${state}/controller-token" "${installation_file}"; do
    if [[ ! -e "${path}" && ! -L "${path}" ]]; then
      echo "persisted identity is missing: ${path}; run install.sh or deploy.sh first" >&2
      return 1
    fi
  done
  persisted_dashboard=$(load_existing_private_token "${state}/dashboard-token") || return 1
  persisted_controller=$(load_existing_private_token "${state}/controller-token") || return 1
  persisted_installation=$(load_existing_installation_id "${installation_file}") || return 1
  if [[ -n "${ADA2_DWARF_TOKEN}" && "${ADA2_DWARF_TOKEN}" != "${persisted_dashboard}" ]]; then
    echo "dashboard credential conflicts with persisted identity; refusing to continue" >&2
    return 1
  fi
  if [[ -n "${DWARF_CONTROLLER_TOKEN}" && "${DWARF_CONTROLLER_TOKEN}" != "${persisted_controller}" ]]; then
    echo "controller credential conflicts with persisted identity; refusing to continue" >&2
    return 1
  fi
  if [[ -n "${DWARF_INSTALLATION_ID}" && "${DWARF_INSTALLATION_ID}" != "${persisted_installation}" ]]; then
    echo "DWARF_INSTALLATION_ID conflicts with persisted ${installation_file}; refusing to continue" >&2
    return 1
  fi
  ADA2_DWARF_TOKEN=${persisted_dashboard}
  DWARF_CONTROLLER_TOKEN=${persisted_controller}
  DWARF_INSTALLATION_ID=${persisted_installation}
  export ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN DWARF_INSTALLATION_ID
}

ensure_controller_identity() {
  local dashboard_token_file="${DWARF_RUNTIME_ROOT}/state/dashboard-token"
  local token_file="${DWARF_RUNTIME_ROOT}/state/controller-token"
  local installation_file="${DWARF_RUNTIME_ROOT}/state/installation-id"
  if [[ -z "${ADA2_DWARF_TOKEN}" ]]; then
    ADA2_DWARF_TOKEN=$(load_or_create_private_token "${dashboard_token_file}") || return 1
  fi
  if [[ -z "${DWARF_CONTROLLER_TOKEN}" ]]; then
    DWARF_CONTROLLER_TOKEN=$(load_or_create_private_token "${token_file}") || return 1
  fi
  if [[ -z "${DWARF_INSTALLATION_ID}" ]]; then
    if [[ ! -s "${installation_file}" ]]; then
      python3 -c 'import secrets,sys; print("install-" + secrets.token_hex(8), file=sys.stdout)' >"${installation_file}"
      chmod 0600 "${installation_file}"
    fi
    DWARF_INSTALLATION_ID=$(tr -d '\r\n' <"${installation_file}")
  fi
  if [[ -z "${DWARF_DOCKER_GID}" && -S /var/run/docker.sock ]]; then
    DWARF_DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || stat -f '%g' /var/run/docker.sock)
  fi
  DWARF_DOCKER_GID=${DWARF_DOCKER_GID:-0}
  export ADA2_DWARF_TOKEN DWARF_CONTROLLER_TOKEN DWARF_INSTALLATION_ID DWARF_DOCKER_GID
}

enable_existing_control_channel() {
  # A non-empty staged key is created only by control-channel provisioning.
  # Preserve shim mode when deploy.sh recreates the dashboard directly, just
  # as install.sh already does on a later install without --control-channel.
  if [[ "${ADA2_DWARF_CONTROL_SHIM:-}" != 1 && -s "${DWARF_SSH_KEY_PATH}" ]]; then
    export ADA2_DWARF_CONTROL_SHIM=1
  fi
}

seed_example_runs() {
  local source_runs="${PACKAGE_ROOT}/dwarf/runs"
  local source_bundles="${PACKAGE_ROOT}/dwarf/bundles"
  local target_runs="${DWARF_RUNTIME_ROOT}/runs"

  mkdir -p "${target_runs}"

  local run_dir run_id
  if [[ -d "${source_runs}" ]]; then
    for run_dir in "${source_runs}"/*; do
      [[ -d "${run_dir}" ]] || continue
      run_id=$(basename "${run_dir}")
      if [[ ! -e "${target_runs}/${run_id}" ]]; then
        cp -R "${run_dir}" "${target_runs}/${run_id}"
      fi
    done
  fi

  # Public delivery keeps examples as tar.gz bundles rather than duplicating
  # their unpacked run trees. Materialize each missing run on install/deploy so
  # a fresh dashboard has the same inspectable examples without overwriting
  # any runtime evidence already present.
  local bundle_path
  if [[ -d "${source_bundles}" ]]; then
    for bundle_path in "${source_bundles}"/*.tar.gz; do
      [[ -f "${bundle_path}" ]] || continue
      run_id=$(basename "${bundle_path}" .tar.gz)
      [[ -e "${target_runs}/${run_id}" ]] && continue
      if tar -tzf "${bundle_path}" | grep -Evq "^${run_id}(/|$)"; then
        echo "refusing example bundle with unexpected top-level path: ${bundle_path}" >&2
        return 1
      fi
      tar -xzf "${bundle_path}" -C "${target_runs}"
    done
  fi
}

seed_example_bundles() {
  local source_bundles="${PACKAGE_ROOT}/dwarf/bundles"
  local target_bundles="${DWARF_RUNTIME_ROOT}/bundles"

  [[ -d "${source_bundles}" ]] || return 0
  mkdir -p "${target_bundles}"

  local bundle_path bundle_name
  for bundle_path in "${source_bundles}"/*.tar.gz; do
    [[ -f "${bundle_path}" ]] || continue
    bundle_name=$(basename "${bundle_path}")
    if [[ ! -e "${target_bundles}/${bundle_name}" ]]; then
      cp "${bundle_path}" "${target_bundles}/${bundle_name}"
    fi
  done
}

seed_scenarios() {
  # Synchronize packaged scenarios into the writable runtime catalog. Packaged
  # names are authoritative on deploy; runtime-only scenarios are untouched.
  local source_scenarios="${PACKAGE_ROOT}/dwarf/scenarios"
  local target_scenarios="${DWARF_RUNTIME_ROOT}/state/scenarios"

  [[ -d "${source_scenarios}" ]] || return 0
  mkdir -p "${target_scenarios}"

  local scn_path scn_name
  for scn_path in "${source_scenarios}"/*.yaml; do
    [[ -f "${scn_path}" ]] || continue
    scn_name=$(basename "${scn_path}")
    cp "${scn_path}" "${target_scenarios}/${scn_name}"
  done
}

seed_manifests() {
  # Synchronize packaged target manifests; runtime-only manifests are untouched.
  local source_manifests="${PACKAGE_ROOT}/dwarf/targets/manifests"
  local target_manifests="${DWARF_RUNTIME_ROOT}/state/targets/manifests"

  [[ -d "${source_manifests}" ]] || return 0
  mkdir -p "${target_manifests}"

  local m_path m_name
  for m_path in "${source_manifests}"/*.yaml; do
    [[ -f "${m_path}" ]] || continue
    m_name=$(basename "${m_path}")
    cp "${m_path}" "${target_manifests}/${m_name}"
  done
}

seed_profiles() {
  # Synchronize packaged profiles; runtime-only profile directories are untouched.
  local source_profiles="${PACKAGE_ROOT}/dwarf/profiles"
  local target_profiles="${DWARF_RUNTIME_ROOT}/state/profiles"

  [[ -d "${source_profiles}" ]] || return 0
  prepare_profile_catalog
  python3 - "${source_profiles}" "${target_profiles}" <<'PY'
import fcntl
import os
import re
import stat
import sys
import uuid
from pathlib import Path

source_root = Path(sys.argv[1])
target_root = Path(sys.argv[2])
safe_id = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
nofollow = getattr(os, "O_NOFOLLOW", 0)
root_fd = os.open(
    target_root,
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow,
)
try:
    lock_fd = os.open(
        ".dwarf-catalog.lock", os.O_RDWR | nofollow, dir_fd=root_fd
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise SystemExit("profile catalog lock must be a regular file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        for source_dir in sorted(source_root.iterdir(), key=lambda item: item.name):
            source = source_dir / "profile.yaml"
            if (
                not source_dir.is_dir()
                or source_dir.is_symlink()
                or not source.is_file()
                or source.is_symlink()
            ):
                continue
            profile_id = source_dir.name
            if not safe_id.fullmatch(profile_id):
                raise SystemExit("packaged profile id is unsafe")
            try:
                os.mkdir(profile_id, 0o770, dir_fd=root_fd)
            except FileExistsError:
                pass
            profile_fd = os.open(
                profile_id,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow,
                dir_fd=root_fd,
            )
            temporary = f".profile.yaml.{uuid.uuid4().hex}.tmp"
            try:
                try:
                    existing = os.stat(
                        "profile.yaml", dir_fd=profile_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    existing = None
                if existing is not None and not stat.S_ISREG(existing.st_mode):
                    raise SystemExit("runtime profile destination is unsafe")
                output_fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                    0o644,
                    dir_fd=profile_fd,
                )
                try:
                    data = source.read_bytes()
                    view = memoryview(data)
                    while view:
                        written = os.write(output_fd, view)
                        view = view[written:]
                    os.fsync(output_fd)
                finally:
                    os.close(output_fd)
                os.replace(
                    temporary,
                    "profile.yaml",
                    src_dir_fd=profile_fd,
                    dst_dir_fd=profile_fd,
                )
                os.fsync(profile_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=profile_fd)
                except FileNotFoundError:
                    pass
                os.close(profile_fd)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
finally:
    os.close(root_fd)
PY
}

seed_measurements() {
  # Synchronize packaged taps into the writable runtime catalog while keeping
  # locally authored definitions that do not collide with packaged ids.
  local source_measurements="${PACKAGE_ROOT}/dwarf/measurements"
  local target_measurements="${DWARF_RUNTIME_ROOT}/state/measurements"

  [[ -d "${source_measurements}" ]] || return 0
  mkdir -p "${target_measurements}"

  local m_path m_name
  for m_path in "${source_measurements}"/*.yaml; do
    [[ -f "${m_path}" ]] || continue
    m_name=$(basename "${m_path}")
    cp "${m_path}" "${target_measurements}/${m_name}"
  done
}

seed_measurement_profiles() {
  # Measurement profiles follow the same packaged-authoritative/runtime-local
  # merge contract as individual tap definitions.
  local source_profiles="${PACKAGE_ROOT}/dwarf/measurement-profiles"
  local target_profiles="${DWARF_RUNTIME_ROOT}/state/measurement-profiles"

  [[ -d "${source_profiles}" ]] || return 0
  mkdir -p "${target_profiles}"

  local p_path p_name
  for p_path in "${source_profiles}"/*.yaml; do
    [[ -f "${p_path}" ]] || continue
    p_name=$(basename "${p_path}")
    cp "${p_path}" "${target_profiles}/${p_name}"
  done
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

container_running() {
  docker inspect -f '{{.State.Running}}' "${DWARF_CONTAINER_NAME}" 2>/dev/null | grep -qx true
}

optional_moog_bootstrap() {
  case "${DWARF_MOOG_BOOTSTRAP}" in
    off|0|false|no|"")
      return 0
      ;;
    plan)
      echo "Moog bootstrap plan requested (no remote state change)"
      docker exec -i "${DWARF_CONTAINER_NAME}" python3 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog bootstrap --json
      ;;
    approve)
      if [[ "${DWARF_MOOG_BOOTSTRAP_APPROVE}" != "1" ]]; then
        echo "DWARF_MOOG_BOOTSTRAP=approve requires DWARF_MOOG_BOOTSTRAP_APPROVE=1" >&2
        exit 1
      fi
      echo "Moog bootstrap approve requested"
      docker exec -i "${DWARF_CONTAINER_NAME}" python3 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog bootstrap --approve --json
      docker exec -i "${DWARF_CONTAINER_NAME}" python3 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog healthcheck --json
      ;;
    *)
      echo "invalid DWARF_MOOG_BOOTSTRAP value: ${DWARF_MOOG_BOOTSTRAP} (use off, plan, or approve)" >&2
      exit 1
      ;;
  esac
}

print_delivery_config() {
  cat <<EOF
Package root: ${PACKAGE_ROOT}
Compose file: ${COMPOSE_FILE}
Image: ${DWARF_IMAGE}
Container: ${DWARF_CONTAINER_NAME}
Dashboard: ${DWARF_DASHBOARD_BIND}:${DWARF_DASHBOARD_PORT}
Runtime root: ${DWARF_RUNTIME_ROOT}
Moog bootstrap: ${DWARF_MOOG_BOOTSTRAP}
EOF
}
