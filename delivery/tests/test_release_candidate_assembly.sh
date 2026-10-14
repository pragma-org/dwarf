#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tmp=$(mktemp -d)
cleanup() { rm -rf -- "${tmp}"; }
trap cleanup EXIT

source_root=${ROOT}

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out "${tmp}/candidate-private.pem" >/dev/null 2>&1
mkdir -p "${tmp}/bin"
cat >"${tmp}/bin/docker" <<'EOF'
#!/usr/bin/env sh
if [ "$1 $2 $3 $4" = "buildx imagetools inspect --raw" ]; then
  cat <<'JSON'
{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","platform":{"os":"linux","architecture":"amd64"}},{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platform":{"os":"linux","architecture":"arm64"}}]}
JSON
  exit 0
fi
case " $* " in
  *" buildx imagetools inspect --format "*)
    role=dashboard
    case "$*" in *controller*) role=controller ;; esac
    revision=${DWARF_FAKE_IMAGE_REVISION}
    if [ "${DWARF_FAKE_STALE_IMAGE:-0}" = 1 ] && [ "${role}" = dashboard ]; then
      revision=0000000000000000000000000000000000000000
    fi
    printf '{"linux/amd64":{"config":{"Labels":{"org.opencontainers.image.revision":"%s","io.dwarf.component.role":"%s","io.dwarf.catalog.revision":"%s"}}},"linux/arm64":{"config":{"Labels":{"org.opencontainers.image.revision":"%s","io.dwarf.component.role":"%s","io.dwarf.catalog.revision":"%s"}}}}\n' \
      "${revision}" "${role}" "${revision}" "${revision}" "${role}" "${revision}"
    exit 0
    ;;
esac
exit 1
EOF
chmod +x "${tmp}/bin/docker"

digest_a=$(printf 'a%.0s' {1..64})
digest_b=$(printf 'b%.0s' {1..64})
digest_c=$(printf 'c%.0s' {1..64})
digest_d=$(printf 'd%.0s' {1..64})
revision=$(git -C "${source_root}" rev-parse HEAD)
if ! git -C "${source_root}" diff --quiet HEAD --; then
  revision=$(git -C "${source_root}" stash create 'test exact candidate revision')
  [[ ${revision} =~ ^[0-9a-f]{40}$ ]]
fi
PATH="${tmp}/bin:${PATH}" DWARF_FAKE_IMAGE_REVISION="${revision}" \
  bash "${ROOT}/delivery/scripts/assemble-release-candidate.sh" \
  --source-root "${source_root}" \
  --output-dir "${tmp}/dist" \
  --version v9.0.0-rc.1 \
  --revision "${revision}" \
  --signing-key "${tmp}/candidate-private.pem" \
  --dashboard-image "registry.example/dwarf/dashboard@sha256:${digest_a}" \
  --controller-image "registry.example/dwarf/controller@sha256:${digest_b}" \
  --golden-amaru-image "registry.example/dwarf/amaru@sha256:${digest_c}" \
  --golden-cardano-image "registry.example/dwarf/cardano-node@sha256:${digest_d}" >/dev/null

manifest="${tmp}/dist/release-manifest.json"
signature="${tmp}/dist/release-manifest.sig"
public_key="${tmp}/dist/release-candidate-public.pem"
test -f "${manifest}"
test -f "${signature}"
test -f "${public_key}"
openssl dgst -sha256 -verify "${public_key}" -signature "${signature}" "${manifest}" >/dev/null
grep -q '"release_version": "v9.0.0-rc.1"' "${manifest}"
grep -q "\"release_revision\": \"${revision}\"" "${manifest}"
grep -q '"minimum_installer_version": "1.2.0"' "${manifest}"

bundle=$(python3 - "${manifest}" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["bundle_file"])
PY
)
test -f "${tmp}/dist/${bundle}"
(cd "${tmp}/dist" && shasum -a 256 -c SHA256SUMS >/dev/null)
tar -tzf "${tmp}/dist/${bundle}" | grep -Eq '(^|/)install\.sh$'
mkdir -p "${tmp}/payload"
tar -xzf "${tmp}/dist/${bundle}" -C "${tmp}/payload"
grep -q 'BEGIN PUBLIC KEY' "${tmp}/payload/install.sh"
if grep -q 'DWARF_RELEASE_PUBLIC_KEY_NOT_CONFIGURED' "${tmp}/payload/install.sh"; then
  echo "release candidate retained the fail-closed public-key placeholder" >&2
  exit 1
fi
cmp "${tmp}/payload/release-candidate-public.pem" "${public_key}"
cmp "${tmp}/payload/catalog/scenarios/amaru-cbor-auxiliary-data-fuzz.yaml" \
  "${source_root}/dwarf/scenarios/amaru-cbor-auxiliary-data-fuzz.yaml"
cmp "${tmp}/payload/catalog/targets/manifests/amaru-cbor-decode-block.yaml" \
  "${source_root}/dwarf/targets/manifests/amaru-cbor-decode-block.yaml"
cmp "${tmp}/payload/catalog/profiles/block-apply-adversary/profile.yaml" \
  "${source_root}/dwarf/profiles/block-apply-adversary/profile.yaml"
test "$(find "${tmp}/payload/catalog/scenarios" -maxdepth 1 -type f -name '*.yaml' | wc -l | tr -d ' ')" \
  = "$(find "${source_root}/dwarf/scenarios" -maxdepth 1 -type f -name '*.yaml' | wc -l | tr -d ' ')"
test "$(find "${tmp}/payload/catalog/targets/manifests" -maxdepth 1 -type f -name '*.yaml' | wc -l | tr -d ' ')" \
  = "$(find "${source_root}/dwarf/targets/manifests" -maxdepth 1 -type f -name '*.yaml' | wc -l | tr -d ' ')"
test "$(find "${tmp}/payload/catalog/profiles" -mindepth 2 -maxdepth 2 -type f -name profile.yaml | wc -l | tr -d ' ')" \
  = "$(find "${source_root}/dwarf/profiles" -mindepth 2 -maxdepth 2 -type f -name profile.yaml | wc -l | tr -d ' ')"

if PATH="${tmp}/bin:${PATH}" DWARF_FAKE_IMAGE_REVISION="${revision}" DWARF_FAKE_STALE_IMAGE=1 \
  bash "${ROOT}/delivery/scripts/assemble-release-candidate.sh" \
    --source-root "${source_root}" \
    --output-dir "${tmp}/stale-dist" \
    --version v9.0.0-rc.2 \
    --revision "${revision}" \
    --signing-key "${tmp}/candidate-private.pem" \
    --dashboard-image "registry.example/dwarf/dashboard@sha256:${digest_a}" \
    --controller-image "registry.example/dwarf/controller@sha256:${digest_b}" \
    --golden-amaru-image "registry.example/dwarf/amaru@sha256:${digest_c}" \
    --golden-cardano-image "registry.example/dwarf/cardano-node@sha256:${digest_d}" \
    >"${tmp}/stale.out" 2>&1; then
  echo "release candidate accepted a stale dashboard image revision" >&2
  exit 1
fi
grep -q 'dashboard image identity does not match release revision' "${tmp}/stale.out"
test ! -e "${tmp}/stale-dist" || test -z "$(find "${tmp}/stale-dist" -mindepth 1 -print -quit)"

echo "release candidate assembly contract ok"
