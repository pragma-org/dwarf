#!/usr/bin/env bash
set -euo pipefail

PROGRAM=dwarf-assemble-release-candidate
source_root=
output_dir=
version=
revision=
signing_key=
dashboard_image=
controller_image=
golden_amaru_image=
golden_cardano_image=

die() { printf '%s: %s\n' "${PROGRAM}" "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: assemble-release-candidate.sh \
  --source-root DIR --output-dir DIR --version VERSION --revision COMMIT \
  --signing-key FILE --dashboard-image DIGEST_REF --controller-image DIGEST_REF \
  --golden-amaru-image DIGEST_REF --golden-cardano-image DIGEST_REF

Creates a signed, checksummed, no-checkout release-candidate bundle from the
exact committed Git revision. The supplied key is candidate-only trust material;
this command does not publish images, tags, releases, or repository changes.
EOF
}

while (($#)); do
  case "$1" in
    --source-root) (($# >= 2)) || die "--source-root requires a value"; source_root=$2; shift 2 ;;
    --output-dir) (($# >= 2)) || die "--output-dir requires a value"; output_dir=$2; shift 2 ;;
    --version) (($# >= 2)) || die "--version requires a value"; version=$2; shift 2 ;;
    --revision) (($# >= 2)) || die "--revision requires a value"; revision=$2; shift 2 ;;
    --signing-key) (($# >= 2)) || die "--signing-key requires a value"; signing_key=$2; shift 2 ;;
    --dashboard-image) (($# >= 2)) || die "--dashboard-image requires a value"; dashboard_image=$2; shift 2 ;;
    --controller-image) (($# >= 2)) || die "--controller-image requires a value"; controller_image=$2; shift 2 ;;
    --golden-amaru-image) (($# >= 2)) || die "--golden-amaru-image requires a value"; golden_amaru_image=$2; shift 2 ;;
    --golden-cardano-image) (($# >= 2)) || die "--golden-cardano-image requires a value"; golden_cardano_image=$2; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

for value in source_root output_dir version revision signing_key dashboard_image controller_image golden_amaru_image golden_cardano_image; do
  [[ -n ${!value} ]] || die "missing required option: ${value//_/-}"
done
[[ ${source_root} == /* ]] || die "source root must be an absolute Git worktree path"
[[ -d ${source_root}/.git || -f ${source_root}/.git ]] \
  || die "source root must be a Git worktree"
[[ ${output_dir} == /* && ! -L ${output_dir} ]] || die "output directory must be an absolute non-symlink path"
[[ ${version} =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] \
  || die "version must be an immutable semantic release identifier"
[[ ${revision} =~ ^[0-9a-f]{40}$ ]] || die "revision must be a full lowercase Git commit"
git -C "${source_root}" cat-file -e "${revision}^{commit}" 2>/dev/null \
  || die "revision is not a commit in the source repository"
[[ -f ${signing_key} && ! -L ${signing_key} ]] || die "signing key must be a regular file"
for command in git python3 openssl; do
  command -v "${command}" >/dev/null 2>&1 || die "missing required command: ${command}"
done
python3 -c 'import jsonschema' >/dev/null 2>&1 \
  || die "Python package jsonschema is required to validate the signed manifest"

digest_reference='^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?/[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'
for image in "${dashboard_image}" "${controller_image}" "${golden_amaru_image}" "${golden_cardano_image}"; do
  [[ ${image} =~ ${digest_reference} ]] || die "image is not a digest-pinned OCI reference: ${image}"
done
command -v docker >/dev/null 2>&1 || die "Docker with buildx is required to inspect image indexes"
for image in "${dashboard_image}" "${controller_image}" "${golden_amaru_image}" "${golden_cardano_image}"; do
  raw=$(docker buildx imagetools inspect --raw "${image}") \
    || die "cannot inspect image index: ${image}"
  RAW_INDEX=${raw} python3 - <<'PY' || die "image index lacks Linux amd64 or arm64"
import json
import os

index = json.loads(os.environ["RAW_INDEX"])
platforms = {
    (item.get("platform", {}).get("os"), item.get("platform", {}).get("architecture"))
    for item in index.get("manifests", [])
}
if not {("linux", "amd64"), ("linux", "arm64")} <= platforms:
    raise SystemExit(1)
PY
done
for identity in "dashboard:${dashboard_image}" "controller:${controller_image}"; do
  role=${identity%%:*}
  image=${identity#*:}
  image_config=$(docker buildx imagetools inspect --format '{{json .Image}}' "${image}") \
    || die "cannot inspect ${role} platform image configs"
  EXPECTED_REVISION=${revision} EXPECTED_ROLE=${role} IMAGE_CONFIG=${image_config} \
    python3 - <<'PY' || die "${role} image identity does not match release revision"
import json
import os

images = json.loads(os.environ["IMAGE_CONFIG"])
expected = {
    "org.opencontainers.image.revision": os.environ["EXPECTED_REVISION"],
    "io.dwarf.component.role": os.environ["EXPECTED_ROLE"],
    "io.dwarf.catalog.revision": os.environ["EXPECTED_REVISION"],
}
for platform in ("linux/amd64", "linux/arm64"):
    try:
        labels = images[platform]["config"]["Labels"]
    except (KeyError, TypeError):
        raise SystemExit(1)
    if any(labels.get(key) != value for key, value in expected.items()):
        raise SystemExit(1)
PY
done

if [[ -e ${output_dir} ]]; then
  [[ -d ${output_dir} ]] || die "output path exists and is not a directory"
  [[ -z $(find "${output_dir}" -mindepth 1 -print -quit) ]] \
    || die "output directory must be empty"
else
  mkdir -p "${output_dir}"
fi
umask 077
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/dwarf-release-candidate.XXXXXX")
cleanup() { rm -rf -- "${tmp_dir}"; }
trap cleanup EXIT
payload=${tmp_dir}/payload
staging=${tmp_dir}/dist
mkdir -p "${payload}" "${staging}"

openssl pkey -in "${signing_key}" -check -noout >/dev/null \
  || die "candidate signing key is invalid"
public_key=${staging}/release-candidate-public.pem
openssl pkey -in "${signing_key}" -pubout -out "${public_key}" >/dev/null
chmod 0444 "${public_key}"

extract() {
  local repository_path=$1 destination=$2
  git -C "${source_root}" show "${revision}:${repository_path}" >"${destination}" \
    || die "release artifact is absent from revision: ${repository_path}"
}
extract delivery/install.sh "${tmp_dir}/install.source.sh"
PUBLIC_KEY_PATH=${public_key} SOURCE_INSTALL=${tmp_dir}/install.source.sh OUTPUT_INSTALL=${payload}/install.sh \
  python3 - <<'PY'
import os
from pathlib import Path

source = Path(os.environ["SOURCE_INSTALL"]).read_text(encoding="utf-8")
public_key = Path(os.environ["PUBLIC_KEY_PATH"]).read_text(encoding="utf-8").strip()
placeholder = """-----BEGIN PUBLIC KEY-----
DWARF_RELEASE_PUBLIC_KEY_NOT_CONFIGURED
-----END PUBLIC KEY-----"""
if source.count(placeholder) != 1:
    raise SystemExit("installer public-key placeholder is missing or duplicated")
Path(os.environ["OUTPUT_INSTALL"]).write_text(source.replace(placeholder, public_key), encoding="utf-8")
PY

declare -a artifacts=(
  "delivery/docker-compose.dwarf.yml:docker-compose.dwarf.yml"
  "delivery/scripts/backup.sh:backup.sh"
  "delivery/scripts/doctor.sh:doctor.sh"
  "delivery/scripts/migrate.sh:migrate.sh"
  "delivery/scripts/release-status.sh:status.sh"
  "delivery/scripts/update.sh:update.sh"
  "delivery/scripts/uninstall.sh:uninstall.sh"
  "delivery/release-manifest.schema.json:release-manifest.schema.json"
)
for artifact in "${artifacts[@]}"; do
  extract "${artifact%%:*}" "${payload}/${artifact##*:}"
done
mkdir -p "${payload}/catalog/scenarios" \
  "${payload}/catalog/targets/manifests" \
  "${payload}/catalog/profiles"
scenario_count=0
manifest_count=0
profile_count=0
while IFS= read -r repository_path; do
  case "${repository_path}" in
    dwarf/scenarios/*.yaml)
      destination=${payload}/catalog/scenarios/${repository_path##*/}
      scenario_count=$((scenario_count + 1))
      ;;
    dwarf/targets/manifests/*.yaml)
      destination=${payload}/catalog/targets/manifests/${repository_path##*/}
      manifest_count=$((manifest_count + 1))
      ;;
    dwarf/profiles/*/profile.yaml)
      profile_id=${repository_path#dwarf/profiles/}
      profile_id=${profile_id%/profile.yaml}
      [[ ${profile_id} != */* && ${profile_id} =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "release profile has an unsafe identity: ${repository_path}"
      mkdir -p "${payload}/catalog/profiles/${profile_id}"
      destination=${payload}/catalog/profiles/${profile_id}/profile.yaml
      profile_count=$((profile_count + 1))
      ;;
    *) continue ;;
  esac
  extract "${repository_path}" "${destination}"
done < <(git -C "${source_root}" ls-tree -r --name-only "${revision}" -- \
  dwarf/scenarios dwarf/targets/manifests dwarf/profiles)
((scenario_count > 0 && manifest_count > 0 && profile_count > 0)) \
  || die "release revision does not contain the required runtime catalogs"
cp "${public_key}" "${payload}/release-candidate-public.pem"
chmod 0755 "${payload}/install.sh" "${payload}/backup.sh" "${payload}/doctor.sh" \
  "${payload}/migrate.sh" "${payload}/status.sh" "${payload}/update.sh" "${payload}/uninstall.sh"

source_date_epoch=$(git -C "${source_root}" show -s --format=%ct "${revision}")
bundle_file=dwarf-${version}-${revision}.tar.gz
PAYLOAD=${payload} BUNDLE=${staging}/${bundle_file} SOURCE_DATE_EPOCH=${source_date_epoch} \
  python3 - <<'PY'
import gzip
import os
from pathlib import Path
import tarfile

payload = Path(os.environ["PAYLOAD"])
bundle = Path(os.environ["BUNDLE"])
epoch = int(os.environ["SOURCE_DATE_EPOCH"])
with bundle.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for path in sorted(payload.rglob("*"), key=lambda item: item.relative_to(payload).as_posix()):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise SystemExit(f"unsupported release payload entry: {path}")
                info = archive.gettarinfo(str(path), arcname=path.name)
                info.name = path.relative_to(payload).as_posix()
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                if path.is_file():
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
PY

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
released_at=$(python3 - "${source_date_epoch}" <<'PY'
from datetime import datetime, timezone
import sys
print(datetime.fromtimestamp(int(sys.argv[1]), timezone.utc).isoformat().replace("+00:00", "Z"))
PY
)
export RELEASE_VERSION=${version} RELEASE_REVISION=${revision} RELEASED_AT=${released_at}
export DASHBOARD_IMAGE=${dashboard_image} CONTROLLER_IMAGE=${controller_image}
export GOLDEN_AMARU_IMAGE=${golden_amaru_image} GOLDEN_CARDANO_IMAGE=${golden_cardano_image}
export BUNDLE_FILE=${bundle_file} BUNDLE_SHA256=$(sha256_file "${staging}/${bundle_file}")
catalog_listing() {
  local file relative
  while IFS= read -r file; do
    relative=${file#"${payload}/catalog/"}
    printf '%s  %s\n' "$(sha256_file "${file}")" "${relative}"
  done < <(find "${payload}/catalog" -type f | LC_ALL=C sort)
}
if command -v sha256sum >/dev/null 2>&1; then
  export CATALOG_SHA256=$(catalog_listing | sha256sum | awk '{print $1}')
else
  export CATALOG_SHA256=$(catalog_listing | shasum -a 256 | awk '{print $1}')
fi
export INSTALLER_SHA256=$(sha256_file "${payload}/install.sh")
export SCHEMA_SHA256=$(sha256_file "${payload}/release-manifest.schema.json")
export COMPOSE_SHA256=$(sha256_file "${payload}/docker-compose.dwarf.yml")
export BACKUP_SHA256=$(sha256_file "${payload}/backup.sh")
export DOCTOR_SHA256=$(sha256_file "${payload}/doctor.sh")
export MIGRATE_SHA256=$(sha256_file "${payload}/migrate.sh")
export STATUS_SHA256=$(sha256_file "${payload}/status.sh")
export UPDATE_SHA256=$(sha256_file "${payload}/update.sh")
export UNINSTALL_SHA256=$(sha256_file "${payload}/uninstall.sh")
python3 - "${staging}/release-manifest.json" <<'PY'
import json
import os
from pathlib import Path
import sys

manifest = {
    "schema_version": "1",
    "release_version": os.environ["RELEASE_VERSION"],
    "release_revision": os.environ["RELEASE_REVISION"],
    "released_at": os.environ["RELEASED_AT"],
    "supported_platforms": ["linux-amd64", "darwin-arm64"],
    "data_schema_version": 1,
    "minimum_installer_version": "1.2.0",
    "dashboard_image": os.environ["DASHBOARD_IMAGE"],
    "controller_image": os.environ["CONTROLLER_IMAGE"],
    "golden_amaru_image": os.environ["GOLDEN_AMARU_IMAGE"],
    "golden_cardano_image": os.environ["GOLDEN_CARDANO_IMAGE"],
    "bundle_file": os.environ["BUNDLE_FILE"],
    "bundle_sha256": os.environ["BUNDLE_SHA256"],
    "catalog_sha256": os.environ["CATALOG_SHA256"],
    "installer_file": "install.sh",
    "installer_sha256": os.environ["INSTALLER_SHA256"],
    "schema_file": "release-manifest.schema.json",
    "schema_sha256": os.environ["SCHEMA_SHA256"],
    "compose_file": "docker-compose.dwarf.yml",
    "compose_sha256": os.environ["COMPOSE_SHA256"],
    "backup_file": "backup.sh",
    "backup_sha256": os.environ["BACKUP_SHA256"],
    "doctor_file": "doctor.sh",
    "doctor_sha256": os.environ["DOCTOR_SHA256"],
    "migrate_file": "migrate.sh",
    "migrate_sha256": os.environ["MIGRATE_SHA256"],
    "status_file": "status.sh",
    "status_sha256": os.environ["STATUS_SHA256"],
    "update_file": "update.sh",
    "update_sha256": os.environ["UPDATE_SHA256"],
    "uninstall_file": "uninstall.sh",
    "uninstall_sha256": os.environ["UNINSTALL_SHA256"],
}
Path(sys.argv[1]).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
SCHEMA=${payload}/release-manifest.schema.json MANIFEST=${staging}/release-manifest.json \
  python3 - <<'PY'
import json
import os
from jsonschema import Draft202012Validator, FormatChecker

with open(os.environ["SCHEMA"], encoding="utf-8") as handle:
    schema = json.load(handle)
with open(os.environ["MANIFEST"], encoding="utf-8") as handle:
    manifest = json.load(handle)
Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
PY
openssl dgst -sha256 -sign "${signing_key}" \
  -out "${staging}/release-manifest.sig" "${staging}/release-manifest.json"
openssl dgst -sha256 -verify "${public_key}" -signature "${staging}/release-manifest.sig" \
  "${staging}/release-manifest.json" >/dev/null
cp "${payload}/install.sh" "${staging}/install.sh"
{
  for file in "${bundle_file}" install.sh release-manifest.json release-manifest.sig release-candidate-public.pem; do
    printf '%s  %s\n' "$(sha256_file "${staging}/${file}")" "${file}"
  done
} >"${staging}/SHA256SUMS"
chmod 0444 "${staging}/release-manifest.json" "${staging}/release-manifest.sig" \
  "${staging}/SHA256SUMS"
for artifact in "${staging}"/*; do
  mv "${artifact}" "${output_dir}/"
done
printf 'Assembled signed DWARF release candidate %s from %s at %s\n' \
  "${version}" "${revision}" "${output_dir}"
