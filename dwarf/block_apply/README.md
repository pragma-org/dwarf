# dwarf/block_apply — forged-block bridge scripts (tracked)

Formalized from the campaign forge-work; invoked by the block-apply bridge primitives
(dwarf/profile_manager/block_apply_primitives.py), registered in dwarf/primitives/registry.json.

## Scripts
- build_block_segments.py  — any single tx .tx -> block body segments [[tx_body],[tx_wits],aux,invalid] (invalid=[0] iff is_valid=false)
- build_stakeaddr_block.py — stake-addr 2-tx create+spend body (tx1 makes a 0xe0 output, tx2 spends it -> inputs.rs:122 on apply)
- build_certphantom.py      — StakeDelegation-for-unregistered-credential body
- build_collateral.py       — no-script tx + unwitnessed FOREIGN collateral body (uses a live foreign UTxO)
- build_l3_block.py         — is_valid=false donation body
- assemble_blockjson.py     — forged header + body -> block.json (race-guarded: header.body_hash == body)
- repoint-amaru-pair1.sh    — reset amaru to a fresh tip-1199 store + repoint it to dial the serve
- block_apply_differential.sh — repoint + watch amaru for adopt / block-fetch / apply / crash; records the verdict

## External dependencies (not in-tree)
- forge_block: forced-nonce VRF+KES header forge needs cardano-crypto (cod-forge tool); feeds forge-result.json.
- serve_crafted_block: tx-submission/block-fetch responder that answers amaru's chain-sync+block-fetch
  (wire runtime_serve_crafted_block), writes serve-addr.txt (host-only) + ACTIVE-BODY.txt (<label> <point_hash>).

## Runtime work-dir
The scripts operate on a per-run work-dir (campaign used /home/nigel/forge-work/pathb/outputs/forged-block/);
parameterize via the primitives' output_dir. amaru pair1: host tmux binary, submit :3210, log /tmp/amaru-pair1.log.

## Hard lessons (baked into the harness)
- ALWAYS pre-flight a hand-built/fixture tx against the LIVE UTxO set before forging (fixture txs from prior
  re-bakes reference absent UTxOs -> both nodes unknown-input, proves nothing).
- Race guard checks block.json point_hash == ACTIVE-BODY.txt (serialize forge<->run); it does NOT catch a
  semantically-broken body — that's what pre-flight is for.
- serve-addr is HOST-ONLY (the repoint appends :3001); amaru dials its upstream :3001 as the chain-sync initiator.
