#!/usr/bin/env bash
set -euo pipefail

request_path=$1
evidence_path=$2
configs_root=$3

fixture=$(jq -er '.fixture' "$request_path")
expected_source_script_hash=$(jq -er '.expected_source_script_hash' "$request_path")
effective_script_hash=$(jq -er '.effective_script_hash' "$request_path")
script_sha256=$(jq -er '.script_sha256' "$request_path")
if [[ "$fixture" != "always-succeeds-v3" ]] ||
   [[ ! "$expected_source_script_hash" =~ ^[0-9a-f]{56}$ ]] ||
   [[ ! "$effective_script_hash" =~ ^[0-9a-f]{56}$ ]] ||
   [[ ! "$script_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "invalid governance guardrails request" >&2
    exit 1
fi

genesis_files=("$configs_root"/*/configs/conway-genesis.json)
if [[ ! -f "${genesis_files[0]}" ]]; then
    echo "pinned loader config has no Conway genesis" >&2
    exit 1
fi

for genesis in "${genesis_files[@]}"; do
    observed=$(jq -er '.constitution.script' "$genesis")
    if [[ "$observed" != "$expected_source_script_hash" ]]; then
        echo "pinned Conway genesis constitution differs from reviewed fixture source" >&2
        exit 1
    fi
    tmp="${genesis}.governance-guardrails.tmp"
    jq --arg script "$effective_script_hash" \
        '.constitution.script = $script' "$genesis" >"$tmp"
    mv -f "$tmp" "$genesis"
done

first_genesis=${genesis_files[0]}
genesis_sha256=$(sha256sum "$first_genesis" | awk '{print $1}')
for genesis in "${genesis_files[@]}"; do
    [[ "$(jq -er '.constitution.script' "$genesis")" == "$effective_script_hash" ]] || {
        echo "staged Conway genesis omitted governance guardrails fixture" >&2
        exit 1
    }
    [[ "$(sha256sum "$genesis" | awk '{print $1}')" == "$genesis_sha256" ]] || {
        echo "staged loader Conway genesis files differ" >&2
        exit 1
    }
done

tmp="${evidence_path}.tmp"
jq -n \
    --arg fixture "$fixture" \
    --arg prior_script_hash "$expected_source_script_hash" \
    --arg effective_script_hash "$effective_script_hash" \
    --arg script_sha256 "$script_sha256" \
    --arg conway_genesis_sha256 "$genesis_sha256" \
    --argjson config_count "${#genesis_files[@]}" \
    '{fixture: $fixture, prior_script_hash: $prior_script_hash,
      effective_script_hash: $effective_script_hash,
      script_sha256: $script_sha256,
      conway_genesis_sha256: $conway_genesis_sha256,
      config_count: $config_count}' >"$tmp"
mv -f "$tmp" "$evidence_path"
