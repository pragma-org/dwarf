#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-doctor
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
preflight=false
fresh_install=false
while (($#)); do
  case "$1" in
    --preflight) preflight=true ;;
    --fresh-install) fresh_install=true ;;
    *) echo "Usage: doctor.sh [--preflight] [--fresh-install]" >&2; exit 2 ;;
  esac
  shift
done

failures=0
ok() { printf 'OK: %s\n' "$*"; }
bad() { printf 'ERROR: %s\n' "$*" >&2; failures=$((failures + 1)); }
env_get() { sed -n "s/^$1=//p" "${install_root}/.env" 2>/dev/null | tail -n 1; }

os=${DWARF_TEST_UNAME_S:-$(uname -s)}
arch=${DWARF_TEST_UNAME_M:-$(uname -m)}
case "${os}:${arch}" in
  Linux:x86_64|Linux:amd64|Darwin:arm64|Darwin:aarch64) ok "supported platform ${os}/${arch}" ;;
  *) bad "unsupported platform ${os}/${arch}; supported: Linux amd64, macOS arm64" ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  bad "Docker CLI is not installed. Install Docker Engine (Linux) or Docker Desktop (macOS)."
elif ! docker compose version >/dev/null 2>&1; then
  bad "Docker Compose v2 is unavailable. Install/enable the docker compose plugin."
elif ! docker info >/dev/null 2>&1; then
  if [[ ${os} == Darwin ]]; then
    bad "Docker is not running. Start Docker Desktop (run: open -a Docker), wait until ready, then rerun."
  else
    bad "Docker is not running or is inaccessible. Run: sudo systemctl start docker; then verify: docker info"
  fi
else
  ok "Docker Engine and Compose are responding"
fi

if [[ ! -f "${install_root}/.env" ]]; then
  bad "installation environment is missing: ${install_root}/.env"
else
  mode=$(stat -c '%a' "${install_root}/.env" 2>/dev/null || stat -f '%Lp' "${install_root}/.env" 2>/dev/null || printf unknown)
  if [[ ${mode} == 600 ]]; then ok "credential environment permissions are 0600"; else bad "credential environment must be mode 0600 (found ${mode})"; fi
  socket=$(env_get DWARF_DOCKER_SOCKET_PATH)
  [[ -n ${socket} ]] || socket=/var/run/docker.sock
  if [[ -S ${socket} ]]; then ok "Docker socket exists at ${socket}"; else bad "Docker socket is unavailable at ${socket}"; fi
  runtime=$(env_get DWARF_RUNTIME_ROOT)
  if [[ -n ${runtime} && -d ${runtime} && -w ${runtime} ]]; then ok "runtime root is writable: ${runtime}"; else bad "runtime root is absent or not writable: ${runtime:-unset}"; fi
  for key in DWARF_IMAGE DWARF_CONTROLLER_IMAGE; do
    image=$(env_get "${key}")
    if [[ ${image} =~ @sha256:[0-9a-f]{64}$ ]]; then ok "${key} is digest pinned"; else bad "${key} is not digest pinned"; fi
  done
fi

if [[ -L "${install_root}/current" && -f "${install_root}/current/docker-compose.dwarf.yml" ]]; then
  ok "active release link and Compose bundle are present"
elif [[ ${preflight} == false ]]; then
  bad "active release link or Compose bundle is missing"
fi

if [[ ${preflight} == true && ${fresh_install} == true ]]; then
  port=$(env_get DWARF_DASHBOARD_PORT)
  port=${port:-8787}
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    bad "dashboard port ${port} is already in use; stop that listener or choose another port"
  else
    ok "dashboard port ${port} is available"
  fi
fi

if [[ -d ${install_root} ]]; then
  free_kb=$(df -Pk "${install_root}" 2>/dev/null | awk 'NR==2 {print $4}')
  if [[ ${free_kb:-0} -ge 1048576 ]]; then ok "at least 1 GiB filesystem space is free"; else bad "less than 1 GiB filesystem space is free"; fi
fi

if ((failures)); then
  printf '%s: %d check(s) failed\n' "${PROGRAM}" "${failures}" >&2
  exit 1
fi
printf '%s: all checks passed\n' "${PROGRAM}"
