#!/usr/bin/env bash
set -euo pipefail

evidence_path=$1
configs_root=$2

config_roots=("$configs_root"/*)
if [[ "${#config_roots[@]}" -ne 1 ]] || [[ ! -d "${config_roots[0]}" ]]; then
    echo "single-producer stake normalization requires exactly one materialized producer" >&2
    exit 1
fi

config_root=${config_roots[0]}
cold_key="$config_root/keys/cold.vkey"
genesis="$config_root/configs/shelley-genesis.json"
if [[ ! -f "$cold_key" ]] || [[ ! -f "$genesis" ]]; then
    echo "single-producer stake normalization is missing producer credentials or Shelley genesis" >&2
    exit 1
fi

cold_key_cbor=$(jq -er \
    '.cborHex | select(test("^5820[0-9a-f]{64}$"))' "$cold_key")
cold_key_hex=${cold_key_cbor#5820}
pool_id=$(
    for ((offset = 0; offset < ${#cold_key_hex}; offset += 2)); do
        printf '%b' "\\x${cold_key_hex:offset:2}"
    done | b2sum --length=224 | awk '{print $1}'
)
if [[ ! "$pool_id" =~ ^[0-9a-f]{56}$ ]]; then
    echo "materialized producer pool ID is invalid" >&2
    exit 1
fi

prior_pool_count=$(jq -er '.staking.pools | length | select(. > 0)' "$genesis")
delegation_count=$(jq -er '.staking.stake | length | select(. > 0)' "$genesis")
jq -e --arg pool_id "$pool_id" '.staking.pools[$pool_id] != null' "$genesis" >/dev/null || {
    echo "materialized producer is absent from pinned Shelley genesis" >&2
    exit 1
}

tmp="${genesis}.single-producer-stake.tmp"
jq --arg pool_id "$pool_id" \
    '.staking.pools = {($pool_id): .staking.pools[$pool_id]}
     | .staking.stake |= with_entries(.value = $pool_id)' \
    "$genesis" >"$tmp"
mv -f "$tmp" "$genesis"

[[ "$(jq -er '.staking.pools | length' "$genesis")" == "1" ]] || {
    echo "single-producer Shelley genesis retained ghost pools" >&2
    exit 1
}
jq -e --arg pool_id "$pool_id" \
    'all(.staking.stake[]; . == $pool_id)' "$genesis" >/dev/null || {
    echo "single-producer Shelley genesis retained ghost delegations" >&2
    exit 1
}

tmp="${evidence_path}.tmp"
jq -n \
    --arg materialized_pool_id "$pool_id" \
    --argjson prior_pool_count "$prior_pool_count" \
    --argjson retained_pool_count 1 \
    --argjson delegation_count "$delegation_count" \
    --argjson config_count 1 \
    '{config_count: $config_count, delegation_count: $delegation_count,
      materialized_pool_id: $materialized_pool_id,
      prior_pool_count: $prior_pool_count,
      retained_pool_count: $retained_pool_count}' >"$tmp"
mv -f "$tmp" "$evidence_path"
