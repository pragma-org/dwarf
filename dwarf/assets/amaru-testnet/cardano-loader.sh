#!/usr/bin/env bash
set -vxo pipefail

export PATH=/usr/local/bin:$PATH
BOOTSTRAP_TARGET_EPOCH=3
# The immutable producer requires an immutable tip in epoch >= 3.  At the
# former epoch-3 midpoint, the pinned synthesizer's immutable tip was still in
# epoch 2.  Three quarters through epoch 3 is the audited exportable point and
# leaves one quarter of the epoch for this live ledger test.  It deliberately
# avoids the known epoch-transition active-nonce path; it does not fix or test
# that path.
BOOTSTRAP_TARGET_EPOCH_FRACTION_NUMERATOR=3
BOOTSTRAP_TARGET_EPOCH_FRACTION_DENOMINATOR=4
SYNTHESIS_SLOT=""

write_file() {
    local tmp_file="${1}_$(tr </dev/urandom -dc A-Za-z0-9 | head -c16)"
    cat >"${tmp_file}"
    mv --force "${tmp_file}" "${1}"
}

copy_config() {
    POOL_ID=$1
    cp -fr /data/p${POOL_ID}-config/configs/* /configs/${POOL_ID}/
    chmod 0600 /configs/${POOL_ID}/keys/*
}

copy_database() {
    POOL_ID=$1
    PROTOCOL_MAGIC=$(jq .protocolConsts.protocolMagic /configs/${POOL_ID}/configs/byron-genesis.json)
    cp -fr /data/generated/db/* /state/${POOL_ID}/
    echo -n $PROTOCOL_MAGIC > /state/${POOL_ID}/protocolMagicId
}

config_topology_json() {
    VALENCY=2
    local num_pools=$2
    local i prev next
    for ((i=1; i<=num_pools; i++)); do
        prev=$((i - 1))
        if [ $prev -eq 0 ]; then prev=$num_pools; fi
        next=$((i + 1))
        if [ $next -gt $num_pools ]; then next=1; fi
        cat <<EOF > "/configs/$i/configs/topology.json"
{
  "localRoots": [
    {
      "accessPoints": [
        {"address": "p${prev}.example", "port": 3001},
        {"address": "p${next}.example", "port": 3001}
      ],
      "advertise": true,
      "trustable": true,
      "valency": ${VALENCY}
    }
  ],
  "publicRoots": [],
  "useLedgerAfterSlot": 0
}
EOF
    done
}

set_start_time() {
    SHELLEY_GENESIS_JSON="$1/configs/shelley-genesis.json"
    BYRON_GENESIS_JSON="$1/configs/byron-genesis.json"
    epoch_length=$(jq -er '.epochLength | select(type == "number" and . > 1)' "${SHELLEY_GENESIS_JSON}")
    target_slot=$((
        epoch_length * BOOTSTRAP_TARGET_EPOCH
        + epoch_length * BOOTSTRAP_TARGET_EPOCH_FRACTION_NUMERATOR
            / BOOTSTRAP_TARGET_EPOCH_FRACTION_DENOMINATOR
    ))
    if [[ -n "${SYNTHESIS_SLOT}" && "${SYNTHESIS_SLOT}" != "${target_slot}" ]]; then
        echo "inconsistent epoch length across bootstrap pool configurations" >&2
        exit 1
    fi
    SYNTHESIS_SLOT=${target_slot}
    SYSTEM_START_UNIX=$(($(date +%s) - SYNTHESIS_SLOT))
    SYSTEM_START_ISO="$(date -d @${SYSTEM_START_UNIX} '+%Y-%m-%dT%H:%M:00Z')"
    jq ".systemStart = \"${SYSTEM_START_ISO}\"" "${SHELLEY_GENESIS_JSON}" | write_file "${SHELLEY_GENESIS_JSON}"
    jq ".startTime = ${SYSTEM_START_UNIX}" "${BYRON_GENESIS_JSON}" | write_file "${BYRON_GENESIS_JSON}"
}

pools=$(ls -d /configs/*)
number_of_pools=$(ls -d /configs/* | wc -l)
echo "number_of_pools: $number_of_pools"
for pool in $pools; do
  pool_ix=$(echo "$pool" | awk -F '/' '{print $3}')
  echo "configure pool: $pool ($pool_ix)"
  copy_config "$pool_ix"
  config_topology_json "$pool_ix" "$number_of_pools"
  set_start_time "$pool"
done
: "${SYNTHESIS_SLOT:?bootstrap synthesis slot was not derived}"

# The pinned bootstrap config retains five genesis pools even when the runtime
# materializes only one producer.  Keep the synthesized private genesis aligned
# with the producer set that can actually forge; this is a coherence hardening,
# not proof that sparse leadership caused any particular observed stall.
if [[ "$number_of_pools" -eq 1 ]]; then
  /data/apply-single-producer-stake.sh \
    /data/generated/single-producer-stake-applied.json \
    /configs || exit 1
fi

/data/apply-workload-funding.sh \
  /data/workload-funding.json \
  /data/generated/workload-funding-applied.json \
  /configs

if [[ -f /data/governance-guardrails.json ]]; then
  /data/apply-governance-guardrails.sh \
    /data/governance-guardrails.json \
    /data/generated/governance-guardrails-applied.json \
    /configs
fi

if [[ -d /data/generated/db ]]; then
    echo "Generated DB exists, archiving it"
    mv /data/generated/db /data/generated/db.old
fi

pushd /data/generated
( echo "["; for i in $(seq 1 "$number_of_pools"); do
                 out="["
                 out="${out}$(cat /configs/$i/keys/opcert.cert)"
                 out="${out},$(cat /configs/$i/keys/vrf.skey)"
                 out="${out},$(cat /configs/$i/keys/kes.skey)]"
                 echo $out
                 [[ $i -ne 5 ]] && echo ","
             done; echo "]" ) > bulk.json

db-synthesizer --config /configs/1/configs/config.json --bulk-credentials-file bulk.json -s "${SYNTHESIS_SLOT}" --db db
popd

for pool in $pools; do
  pool_ix=$(echo "$pool" | awk -F '/' '{print $3}')
  echo "copy db for pool: $pool ($pool_ix)"
  copy_database "$pool_ix"
done
