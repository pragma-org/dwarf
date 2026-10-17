#!/usr/bin/env bash
set -euo pipefail

request_path=$1
evidence_path=$2
configs_root=$3

credentials=$(jq -cer '
  if (.credentials | type) == "array" then .credentials
  else [{id: "utxo1", address, address_base16, verification_key}]
  end
  | select(length > 0)
' "$request_path")
credential_count=$(jq -er 'length' <<<"$credentials")
for index in $(seq 0 $((credential_count - 1))); do
    credential=$(jq -cer ".[$index]" <<<"$credentials")
    id=$(jq -er '.id' <<<"$credential")
    address=$(jq -er '.address' <<<"$credential")
    address_base16=$(jq -er '.address_base16' <<<"$credential")
    verification_key=$(jq -er '.verification_key' <<<"$credential")
    if [[ ! "$id" =~ ^utxo[1-9][0-9]*$ ]] ||
       [[ ! "$address" =~ ^addr_test1 ]] ||
       [[ ! "$address_base16" =~ ^60[0-9a-f]{56}$ ]] ||
       [[ "$verification_key" != "utxo-keys/${id}/utxo.vkey" ]]; then
        echo "invalid generated workload funding request" >&2
        exit 1
    fi
done

genesis_files=("$configs_root"/*/configs/shelley-genesis.json)
if [[ ! -f "${genesis_files[0]}" ]]; then
    echo "pinned loader config has no Shelley genesis" >&2
    exit 1
fi

first_genesis=${genesis_files[0]}
before_funds=$(jq -cer '.initialFunds' "$first_genesis")
allocation=$(jq -er '[.initialFunds[] | select(type == "number" and . > 0)] | max' "$first_genesis")
missing_count=$(jq -nr \
    --argjson credentials "$credentials" \
    --argjson before_funds "$before_funds" '
      $credentials
      | map(select($before_funds[.address_base16] == null))
      | length
    ')
total=$(jq -er '[.initialFunds[]] | add' "$first_genesis")
max_supply=$(jq -er '.maxLovelaceSupply' "$first_genesis")
if (( total + allocation * missing_count > max_supply )); then
    echo "generated workload funding exceeds the Shelley supply" >&2
    exit 1
fi

for genesis in "${genesis_files[@]}"; do
    tmp="${genesis}.workload-funding.tmp"
    jq --argjson credentials "$credentials" --argjson allocation "$allocation" '
      reduce $credentials[] as $credential
        (. ; .initialFunds[$credential.address_base16] =
          (.initialFunds[$credential.address_base16] // $allocation))
    ' "$genesis" >"$tmp"
    mv -f "$tmp" "$genesis"
done

genesis_sha256=$(sha256sum "$first_genesis" | awk '{print $1}')
for genesis in "${genesis_files[@]}"; do
    [[ "$(sha256sum "$genesis" | awk '{print $1}')" == "$genesis_sha256" ]] || {
        echo "staged loader Shelley genesis files differ" >&2
        exit 1
    }
done

tmp="${evidence_path}.tmp"
jq -n \
    --argjson credentials "$credentials" \
    --argjson before_funds "$before_funds" \
    --slurpfile genesis "$first_genesis" '
      [$credentials[] as $credential
        | $credential + {
            lovelace: $genesis[0].initialFunds[$credential.address_base16],
            materialized: ($before_funds[$credential.address_base16] == null)
          }]
    ' >"${tmp}.credentials"
evidence_credentials=$(cat "${tmp}.credentials")
rm -f "${tmp}.credentials"
primary=$(jq -cer '.[0]' <<<"$evidence_credentials")
jq -n \
    --argjson primary "$primary" \
    --argjson credentials "$evidence_credentials" \
    --arg shelley_genesis_sha256 "$genesis_sha256" \
    '$primary + {credentials: $credentials,
      shelley_genesis_sha256: $shelley_genesis_sha256}' >"$tmp"
mv -f "$tmp" "$evidence_path"
