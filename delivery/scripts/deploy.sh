#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ensure_package_layout
ensure_runtime_dirs
ensure_controller_identity
enable_existing_control_channel
seed_example_runs
seed_example_bundles
seed_scenarios
seed_manifests
seed_profiles
seed_measurements
seed_measurement_profiles
require_docker

docker image inspect "${DWARF_IMAGE}" >/dev/null || {
  echo "image not found: ${DWARF_IMAGE}" >&2
  echo "run bash delivery/scripts/build-image.sh first" >&2
  exit 1
}
docker image inspect "${DWARF_CONTROLLER_IMAGE}" >/dev/null || {
  echo "image not found: ${DWARF_CONTROLLER_IMAGE}" >&2
  echo "run bash delivery/scripts/build-image.sh first" >&2
  exit 1
}

ensure_golden_images

echo "Starting local controller and dashboard (${DWARF_CONTAINER_NAME})"
compose up -d --no-build --force-recreate dwarf-controller dwarf-fw

echo "Waiting for dashboard readiness"
ready=false
for _ in $(seq 1 30); do
  if docker exec -i "${DWARF_CONTAINER_NAME}" python3 - <<'PY' >/dev/null 2>&1
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8787/api/status", timeout=20) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
  then
    ready=true
    break
  fi
  sleep 1
done

if [[ "${ready}" != true ]]; then
  echo "dashboard did not become ready: http://${DWARF_DASHBOARD_BIND}:${DWARF_DASHBOARD_PORT}/api/status" >&2
  docker logs "${DWARF_CONTAINER_NAME}" --tail 80 >&2 || true
  exit 1
fi

echo "Checking dashboard/controller revision compatibility"
if ! docker exec -i "${DWARF_CONTAINER_NAME}" python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8787/api/status", timeout=20) as response:
    dashboard = json.load(response)
with urllib.request.urlopen("http://dwarf-controller:8790/healthz", timeout=20) as response:
    controller = json.load(response)
dashboard_revision = (dashboard.get("runtime_identity") or {}).get("release_revision")
controller_revision = controller.get("release_revision")
if not dashboard_revision or dashboard_revision != controller_revision:
    raise SystemExit(
        f"revision mismatch: dashboard={dashboard_revision!r} controller={controller_revision!r}"
    )
PY
then
  echo "dashboard/controller revision compatibility check failed" >&2
  docker logs "${DWARF_CONTAINER_NAME}" --tail 80 >&2 || true
  docker logs "${DWARF_CONTROLLER_CONTAINER_NAME:-dwarf-controller}" --tail 80 >&2 || true
  exit 1
fi

optional_moog_bootstrap
compose ps
