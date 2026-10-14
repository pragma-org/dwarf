#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-status
install_root=${DWARF_INSTALL_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf-install}
quiet=false
[[ ${1:-} != --quiet ]] || quiet=true

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
receipt=${install_root}/install-receipt
env_file=${install_root}/.env
[[ -f ${receipt} && ! -L ${receipt} && -f ${env_file} && ! -L ${env_file} ]] \
  || die "installed release metadata is missing or unsafe"
field() {
  local file=$1 key=$2 value count
  value=$(sed -n "s/^${key}=//p" "${file}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "${key} is missing or duplicated"
  printf '%s\n' "${value}"
}

[[ $(field "${receipt}" install_root) == "${install_root}" ]] \
  || die "receipt does not belong to this install root"
revision=$(field "${receipt}" release_revision)
release_version=$(field "${receipt}" release_version)
installation_id=$(field "${receipt}" installation_id)
runtime_root=$(field "${receipt}" runtime_root)
receipt_schema=$(field "${receipt}" data_schema_version)
receipt_manifest_sha=$(field "${receipt}" manifest_sha256)
receipt_bundle_sha=$(field "${receipt}" bundle_sha256)
receipt_catalog_sha=$(field "${receipt}" catalog_sha256)
dashboard_image=$(field "${env_file}" DWARF_IMAGE)
controller_image=$(field "${env_file}" DWARF_CONTROLLER_IMAGE)
golden_images=$(field "${env_file}" DWARF_GOLDEN_IMAGES)
dashboard_bind=$(field "${env_file}" DWARF_DASHBOARD_BIND)
dashboard_port=$(field "${env_file}" DWARF_DASHBOARD_PORT)
optional_field() { sed -n "s/^$2=//p" "$1" | tail -n 1; }
compose_project=$(optional_field "${env_file}" DWARF_COMPOSE_PROJECT_NAME)
dashboard_name=$(optional_field "${env_file}" DWARF_CONTAINER_NAME)
controller_name=$(optional_field "${env_file}" DWARF_CONTROLLER_CONTAINER_NAME)
network_name=$(optional_field "${env_file}" DWARF_NETWORK_NAME)
network_subnet=$(optional_field "${env_file}" DWARF_NETWORK_SUBNET)
receipt_bind=$(optional_field "${receipt}" dashboard_bind)
receipt_port=$(optional_field "${receipt}" dashboard_port)
receipt_project=$(optional_field "${receipt}" compose_project_name)
receipt_dashboard_name=$(optional_field "${receipt}" dashboard_container_name)
receipt_controller_name=$(optional_field "${receipt}" controller_container_name)
receipt_network_name=$(optional_field "${receipt}" network_name)
receipt_network_subnet=$(optional_field "${receipt}" network_subnet)
if [[ -z ${compose_project}${dashboard_name}${controller_name}${network_name}${network_subnet} \
  && -z ${receipt_bind}${receipt_port}${receipt_project}${receipt_dashboard_name}${receipt_controller_name}${receipt_network_name}${receipt_network_subnet} ]]; then
  [[ ${dashboard_bind} == 127.0.0.1 && ${dashboard_port} == 8787 ]] \
    || die "legacy runtime isolation identity is not the release default"
  compose_project=dwarf
  dashboard_name=dwarf-fw
  controller_name=dwarf-controller
  network_name=dwarf_default
  network_subnet=10.201.0.0/24
else
  [[ ${dashboard_bind} == 127.0.0.1 && ${dashboard_bind} == "${receipt_bind}" \
    && ${dashboard_port} == "${receipt_port}" && ${compose_project} == "${receipt_project}" \
    && ${dashboard_name} == "${receipt_dashboard_name}" \
    && ${controller_name} == "${receipt_controller_name}" \
    && ${network_name} == "${receipt_network_name}" \
    && ${network_subnet} == "${receipt_network_subnet}" ]] \
    || die "runtime isolation identity differs from the install receipt"
fi

current=${install_root}/current
[[ -L ${current} && $(readlink "${current}") == "releases/${release_version}" ]] \
  || die "active release link differs from the receipt"
manifest=${current}/release-manifest.json
signature=${current}/release-manifest.sig
public_key=${current}/release-public.pem
[[ -f ${manifest} && ! -L ${manifest} && -f ${signature} && ! -L ${signature} \
  && -f ${public_key} && ! -L ${public_key} ]] || die "signed release identity is incomplete"
command -v openssl >/dev/null 2>&1 || die "OpenSSL is not installed"
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
  [[ ${count} == 1 ]] || die "manifest field ${key} is missing or duplicated"
  printf '%s\n' "${value}"
}
manifest_number() {
  local key=$1 value count
  value=$(sed -n "s/^[[:space:]]*\"${key}\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p" "${manifest}")
  count=$(printf '%s\n' "${value}" | awk 'NF {n++} END {print n+0}')
  [[ ${count} == 1 ]] || die "manifest field ${key} is missing or duplicated"
  printf '%s\n' "${value}"
}
manifest_revision=$(manifest_string release_revision)
manifest_bundle_sha=$(manifest_string bundle_sha256)
manifest_catalog_sha=$(manifest_string catalog_sha256)
manifest_schema=$(manifest_number data_schema_version)
manifest_dashboard=$(manifest_string dashboard_image)
manifest_controller=$(manifest_string controller_image)
manifest_amaru=$(manifest_string golden_amaru_image)
manifest_cardano=$(manifest_string golden_cardano_image)
[[ ${revision} == "${manifest_revision}" && ${receipt_bundle_sha} == "${manifest_bundle_sha}" \
  && ${receipt_catalog_sha} == "${manifest_catalog_sha}" \
  && ${receipt_schema} == "${manifest_schema}" ]] || die "receipt identity differs from the signed manifest"
[[ ${dashboard_image} == "${manifest_dashboard}" && ${controller_image} == "${manifest_controller}" \
  && ${golden_images} == "${manifest_amaru},${manifest_cardano}" ]] \
  || die "configured images differ from the signed manifest"
catalog_root=${current}/catalog
[[ -d ${catalog_root} && ! -L ${catalog_root} \
  && -z $(find "${catalog_root}" -type l -print -quit) ]] \
  || die "installed signed catalog is missing or unsafe"
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
[[ $(catalog_tree_sha256 "${catalog_root}") == "${manifest_catalog_sha}" ]] \
  || die "installed release catalog differs from the signed manifest"
for runtime_catalog_path in state state/scenarios state/targets state/targets/manifests state/profiles; do
  [[ -d ${runtime_root}/${runtime_catalog_path} \
    && ! -L ${runtime_root}/${runtime_catalog_path} ]] \
    || die "active runtime catalog path is missing or unsafe: ${runtime_catalog_path}"
done
while IFS= read -r signed_catalog_file; do
  catalog_relative=${signed_catalog_file#"${catalog_root}/"}
  [[ ${catalog_relative} =~ ^scenarios/[A-Za-z0-9._-]+\.yaml$ \
    || ${catalog_relative} =~ ^targets/manifests/[A-Za-z0-9._-]+\.yaml$ \
    || ${catalog_relative} =~ ^profiles/[A-Za-z0-9._-]+/profile\.yaml$ ]] \
    || die "installed signed catalog contains an unsafe path: ${catalog_relative}"
  active_catalog_file=${runtime_root}/state/${catalog_relative}
  [[ -f ${active_catalog_file} && ! -L ${active_catalog_file} \
    && $(sha256_file "${active_catalog_file}") == $(sha256_file "${signed_catalog_file}") ]] \
    || die "active runtime catalog differs from signed base: ${catalog_relative}"
done < <(find "${catalog_root}" -type f | LC_ALL=C sort)
[[ -f ${runtime_root}/.dwarf-data-schema \
  && $(tr -d '\r\n' <"${runtime_root}/.dwarf-data-schema") == "${receipt_schema}" ]] \
  || die "runtime data schema differs from the signed release"
[[ -f ${runtime_root}/.dwarf-installation-id && ! -L ${runtime_root}/.dwarf-installation-id \
  && $(tr -d '\r\n' <"${runtime_root}/.dwarf-installation-id") == "${installation_id}" ]] \
  || die "runtime ownership marker differs from the install receipt"

command -v docker >/dev/null 2>&1 || die "Docker CLI is not installed"
docker info >/dev/null 2>&1 || die "Docker Engine/Desktop is not running"

actual_dashboard=$(docker inspect "${dashboard_name}" --format '{{.Config.Image}}' 2>/dev/null) \
  || die "dashboard container is missing"
actual_controller=$(docker inspect "${controller_name}" --format '{{.Config.Image}}' 2>/dev/null) \
  || die "controller container is missing"
[[ ${actual_dashboard} == "${dashboard_image}" ]] \
  || die "dashboard image identity differs from the signed release"
[[ ${actual_controller} == "${controller_image}" ]] \
  || die "controller image identity differs from the signed release"
for container in "${dashboard_name}" "${controller_name}"; do
  actual_installation=$(docker inspect "${container}" \
    --format '{{ index .Config.Labels "io.dwarf.installation" }}' 2>/dev/null) \
    || die "container ownership labels are unreadable"
  actual_project=$(docker inspect "${container}" \
    --format '{{ index .Config.Labels "com.docker.compose.project" }}' 2>/dev/null) \
    || die "container Compose identity is unreadable"
  [[ ${actual_installation} == "${installation_id}" && ${actual_project} == "${compose_project}" ]] \
    || die "container runtime identity differs from the install receipt"
  attached=$(docker inspect "${container}" \
    --format "{{if index .NetworkSettings.Networks \"${network_name}\"}}attached{{end}}" 2>/dev/null) \
    || die "container network attachment is unreadable"
  [[ ${attached} == attached ]] || die "container is not attached to the receipted installation network"
done
actual_dashboard_binding=$(docker inspect "${dashboard_name}" \
  --format '{{with (index .NetworkSettings.Ports "8787/tcp")}}{{len .}}:{{(index . 0).HostIp}}:{{(index . 0).HostPort}}{{end}}' \
  2>/dev/null) || die "dashboard port binding is unreadable"
dashboard_binding_count=$(docker inspect "${dashboard_name}" \
  --format '{{len .HostConfig.PortBindings}}' 2>/dev/null) \
  || die "dashboard published-port inventory is unreadable"
controller_binding_count=$(docker inspect "${controller_name}" \
  --format '{{len .HostConfig.PortBindings}}' 2>/dev/null) \
  || die "controller published-port inventory is unreadable"
[[ ${actual_dashboard_binding} == "1:${dashboard_bind}:${dashboard_port}" \
  && ${dashboard_binding_count} == 1 && ${controller_binding_count} == 0 ]] \
  || die "dashboard live port binding differs from the install receipt"
actual_network_project=$(docker network inspect "${network_name}" \
  --format '{{ index .Labels "com.docker.compose.project" }}' 2>/dev/null) \
  || die "installation network is missing"
actual_network_subnet=$(docker network inspect "${network_name}" \
  --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null) \
  || die "installation network identity is unreadable"
[[ ${actual_network_project} == "${compose_project}" && ${actual_network_subnet} == "${network_subnet}" ]] \
  || die "installation network identity differs from the install receipt"

payload=$(docker exec -i "${dashboard_name}" python3 - "${revision}" "${installation_id}" <<'PY'
import json
import os
import sys
import uuid
import urllib.request

expected_revision, expected_installation = sys.argv[1:]
with urllib.request.urlopen("http://127.0.0.1:8787/api/status", timeout=20) as response:
    dashboard = json.load(response)
with urllib.request.urlopen("http://dwarf-controller:8790/healthz", timeout=20) as response:
    controller = json.load(response)
controller_token = os.environ.get("DWARF_CONTROLLER_TOKEN") or ""
dashboard_installation = os.environ.get("DWARF_INSTALLATION_ID") or ""
if len(controller_token) < 32:
    raise SystemExit("dashboard controller token is unavailable")
if dashboard_installation != expected_installation:
    raise SystemExit("dashboard installation identity differs from the receipt")
inventory_body = json.dumps({
    "schema_version": "v1",
    "request_id": str(uuid.uuid4()),
    "operation": "inventory.get",
    "timeout_seconds": 10,
}).encode("utf-8")
inventory_request = urllib.request.Request(
    "http://dwarf-controller:8790/v1/requests",
    data=inventory_body,
    headers={
        "Authorization": "Bearer " + controller_token,
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(inventory_request, timeout=20) as response:
    inventory = json.load(response)
identity = dashboard.get("runtime_identity") or {}
readiness = identity.get("readiness") or {}
catalog = identity.get("catalog") or {}
problems = []
if identity.get("attested") is not True:
    problems.append("dashboard release identity is not attested")
if readiness.get("ready") is not True:
    problems.append(f"dashboard semantic readiness is {readiness.get('state')!r}")
if identity.get("release_revision") != expected_revision:
    problems.append("dashboard revision differs from the receipt")
if catalog.get("matches_release") is not True:
    problems.append("dashboard catalog revision differs from the release")
if controller.get("status") != "ok":
    problems.append("controller semantic health is not ok")
if controller.get("release_revision") != expected_revision:
    problems.append("controller revision differs from the receipt")
if controller.get("installation_id") != expected_installation:
    problems.append("controller installation identity differs from the receipt")
if controller.get("revision_compatible") is not True:
    problems.append("dashboard/controller revisions are incompatible")
if inventory.get("installation_id") != expected_installation:
    problems.append("authenticated inventory installation identity differs from the receipt")
managed = inventory.get("managed")
unmanaged = inventory.get("unmanaged")
if not isinstance(managed, list) or not isinstance(unmanaged, list):
    problems.append("authenticated inventory has an invalid resource shape")
elif inventory.get("managed_count") != len(managed):
    problems.append("authenticated inventory managed count is inconsistent")
elif inventory.get("visible_unmanaged_count") != len(unmanaged):
    problems.append("authenticated inventory unmanaged count is inconsistent")
if problems:
    raise SystemExit("; ".join(problems))
print(json.dumps({
    "status": "ready",
    "installation_id": expected_installation,
    "release_revision": expected_revision,
    "dashboard": identity,
    "controller": controller,
    "inventory": inventory,
}, sort_keys=True))
PY
) || die "semantic readiness check failed"
[[ -n ${payload} ]] || die "semantic readiness returned an empty receipt"

if [[ ${quiet} == false ]]; then
  printf 'install_root=%s\nruntime_root=%s\n' "${install_root}" "${runtime_root}"
  printf 'dashboard_url=http://%s:%s/\ncompose_project=%s\nnetwork=%s\n' \
    "${dashboard_bind}" "${dashboard_port}" "${compose_project}" "${network_name}"
  printf 'dashboard_image=%s\ncontroller_image=%s\n' "${dashboard_image}" "${controller_image}"
  printf '%s\n' "${payload}"
fi
