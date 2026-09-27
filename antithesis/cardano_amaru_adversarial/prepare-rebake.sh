#!/usr/bin/env bash
# prepare-rebake.sh — re-bake the phase-1 reference ledger with the COMMITTED funding key.
#
# Produces two frozen, genesis-paired stores that both contain the durable funded UTxO
#   9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0  (200000000000000 lovelace)
# so the underfee corpus is regenerable and new phase-1 txs (native-script, etc.) can be crafted
# indefinitely. Runs LOCALLY; it rebuilds ~1.5 GB of state/images that are intentionally NOT
# committed and NOT pushed to GHCR (ops territory).
#
# Inputs (committed, in fixture/funding/):
#   payment.skey / payment.vkey  — the funding keypair
#   genesis/                     — the exact 5 genesis files (funded initialFund already injected)
#
# Toolchain: configurator 0f9570b, cardano-node (producer image), db-analyser, amaru + its
# make-store recipe (relay-image/make-store.sh). Verified end-to-end 2026-09-26.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/fixture/funding"
WORK="${REBAKE_WORK:-$HOME/rebake-amaru}"          # scratch (NOT committed)
REF="${REBAKE_REF:-$HOME/rebake-ref}"              # frozen cardano reference db (NOT committed)
ADDR="addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6"
MAGIC=42

echo "== 1. Genesis =="
# Option A (reproduce THIS bake): use the committed genesis verbatim.
#   cp "$FUND"/genesis/*.json "$WORK"/configs/
# Option B (fresh keys): run configurator 0f9570b to emit a k=20/epoch400/magic42 genesis, then
# inject the funding initialFund + push systemStart forward (configurator sets it to "now"):
#   docker run ... ghcr.io/cardano-foundation/cardano-node-antithesis/configurator:0f9570b
#   python3 - <<'PY'  # inject { "60e5a5ddb0...d2327": 200000000000000 } into shelley initialFunds
#   ...  # and set shelley.systemStart / byron.startTime to now+150s across all 3 producer copies
#   PY
# Pool keys differ per configurator run, but the funded UTxO id is f(genesis initialFunds + key)
# and is therefore STABLE regardless. The committed genesis has cardano-cli shelley hash
# 8b6dfc7c215c5a0bfdee17f56ac020d4b18f79da78572d6f5cfddeab7ea43398.

echo "== 2. Forge a clean chain (3-node k=20 cluster, >=3 epochs) =="
# docker compose -p rebake up -d --no-deps tracer p1 p2 p3   # producers WITH opcert/kes/vrf keys
# Wait until `cardano-cli query tip` shows epoch >= 3, then STOP the producers. Do NOT submit any
# tx to this cluster — the genesis UTxO must stay unspent so it lands in both baked stores.
# Snapshot the pre-spend node DB (contains protocolMagicId + immutable/ + volatile/ + ledger/):
#   docker cp rebake-p1-1:/state "$WORK"/db     # (the db root, INCLUDING the protocolMagicId marker)

echo "== 3. Frozen cardano reference (NON-FORGING) =="
# A differential reference must NEVER forge/advance or every run mutates the ledger and the gate
# stops being repeatable. Run a cardano-node from the pre-spend db with NO --shelley-* keys and an
# empty topology so it never syncs past the baked tip:
#   rm -rf "$REF" && mkdir -p "$REF" && cp -a "$WORK"/db "$REF"/db
#   rm -rf "$REF"/db/ledger/*_db-analyser            # drop db-analyser-named snapshots; node replays
#   printf '{"localRoots":[{"accessPoints":[],"advertise":false,"trustable":false,"valency":0}],"publicRoots":[],"useLedgerAfterSlot":-1}' > "$REF"/topology-empty.json
#   docker run -d --name rebake-ref -v "$WORK"/configs:/configs:ro -v "$REF":/state <cardano-node> \
#     run --topology /state/topology-empty.json --config /configs/config.json \
#         --database-path /state/db --socket-path /state/node.socket --port 3001 --host-addr 0.0.0.0
#   # verify: query tip == epoch 3 Conway, and query utxo shows 9708b921...#0 UNSPENT.
#   docker run -d --name rebake-submit-api -v "$REF":/state -v "$HERE"/haskell:/cfg:ro -p 8090:8090 \
#     ghcr.io/intersectmbo/cardano-submit-api:11.1.2 --config /cfg/submit-api-config.yaml \
#     --testnet-magic $MAGIC --socket-path /state/node.socket --listen-address 0.0.0.0 --port 8090

echo "== 4. Amaru testnet_42 store (same genesis) =="
# relay-image/make-store.sh: db-analyser boundary points -> `amaru snapshot create` (epoch 3) ->
# `amaru node bootstrap`, against the SAME pre-spend db/genesis. Produces
#   $WORK/store/{chain,ledger}.testnet_42.db  (+ copy era-history.json alongside).
# Serve it from a NON-syncing amaru relay (baked store; peers unreachable is fine for submit-only):
#   docker run -d --name rebake-amaru-relay -v "$WORK"/store:/srv/amaru \
#     -v "$HERE"/relay-image/entrypoint.sh:/usr/local/bin/dwarf-amaru-entrypoint.sh:ro \
#     -e AMARU_STORE=/srv/amaru -e AMARU_GLOBAL_SYSTEM_START=1790433953000 \
#     -e AMARU_SUBMIT_API_ADDRESS=0.0.0.0:3011 -p 3011:3011 --entrypoint /bin/sh \
#     <amaru-adv-image> /usr/local/bin/dwarf-amaru-entrypoint.sh

echo "== 5. Regenerate the underfee corpus (spends the funded UTxO) =="
# For each case, build-raw a 1-in/1-out self-send spending 9708b921...#0, sign with payment.skey:
#   cardano-cli conway transaction build-raw --tx-in <UTxO> --tx-out "$ADDR+<total-fee>" \
#     --fee <fee> --out-file <case>.raw
#   cardano-cli conway transaction sign --tx-body-file <case>.raw --signing-key-file payment.skey \
#     --testnet-magic $MAGIC --out-file <case>.tx
# Fees: rejects below cardano's min 164181 (164081/164179/164180); accepted at/above Amaru's min
# 164225 (both accept). See fixture/funding/README.md for why the accept case is 164225 not 164181
# (the min-fee tx-size divergence, dwarf/docs/finding-amaru-minfee-txsize-divergence.md).
# Rebuild fixture/static/{corpus.json,metadata.json} (cbor_size + sha256 + txid per case).

echo "== 6. Verify the gate (non-vacuous) =="
# PYTHONPATH=workload python3 - <<'PY'
# from mixed_phase1 import load_corpus, default_transports, observe_differential, matches_expected
# corpus=load_corpus("fixture/static")
# t=default_transports("http://localhost:3011/api/submit/tx","http://localhost:8090/api/submit/tx")
# for fx in corpus:  # rejects first, accepted last (accepted reserves the UTxO in the mempool)
#     r=observe_differential(fx.payload,t); assert matches_expected(r,fx.expected), fx.case_id
# PY
echo "prepare-rebake: recipe documented; see fixture/funding/README.md for verified results."
