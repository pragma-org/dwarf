"""Curated examples gallery for /learn/examples (slice 1 of dispatch 8).

Hand-picked scenario list spanning the family axis (honest baseline,
byzantine peer, chainsync fault, blockfetch fault, txsubmission
pressure, resource abuse, multi-host, docker mode, snapshot recovery,
serdes/cbor codec). The annotation prose lives in code; the YAML is
read from disk so the page can never go stale relative to the
canonical scenario file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _scenarios_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_SCENARIOS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "scenarios"


# (scenario_filename, family, demonstrates, expected_outcome)
_EXAMPLES: list[tuple[str, str, str, str]] = [
    # ---- Honest baseline / multi-node observation ----
    (
        "runtime-substrate-honest-baseline-example-smoke.yaml",
        "honest baseline",
        "Compose a 3-node substrate (two cardano-node honest peers + one Amaru honest peer) and observe that all three nodes converge on the same chain tip during a 10-second window. The reference shape every other substrate scenario diffs against.",
        "All assertions pass · multi-node observation tile reads CONVERGED",
    ),
    (
        "runtime-substrate-honest-baseline-docker-mode-example-smoke.yaml",
        "docker compose mode",
        "Same honest-baseline topology, composed via Docker containers (compose_mode=docker) instead of native processes (compose_mode=host). Demonstrates that the substrate primitive is mode-agnostic — observation tiles look identical regardless of compose mode.",
        "Compose tile mode=docker · multi-node-observation matches host-mode baseline",
    ),
    (
        "runtime-substrate-multihost-honest-baseline-example-smoke.yaml",
        "multi-host (2-host)",
        "Same honest-baseline topology fanned out across two SSH hosts. compose_mode=multi-host provisions per-host nodes, observes them through their own SSH channels, and folds per-host telemetry back into one bundle.",
        "Three nodes split 2+1 across hosts · single converged tip",
    ),

    # ---- Byzantine peer ----
    (
        "runtime-byzantine-peer-example-smoke.yaml",
        "byzantine peer · calibration",
        "TCP proxy in front of node3's upstream connection. The proxy intercepts the chainsync stream and (in this smoke variant) lets it through unmodified — calibration baseline for the byzantine harness, not a real attack.",
        "0 intercepted · 0 mutated segments · all nodes still converge",
    ),
    (
        "runtime-substrate-byzantine-blockfetch-example-smoke.yaml",
        "byzantine peer · blockfetch mutation",
        "Byzantine proxy mutates blockfetch responses on the wire. The honest peers reject the malformed blocks via the protocol's hash-chain check; the byzantine link gets dropped. Demonstrates rejection without isolation cascade.",
        "intercepted_segments > 0 · honest nodes converge · byzantine link dropped",
    ),

    # ---- ChainSync faults ----
    (
        "runtime-substrate-chainsync-parent-discontinuity-example-smoke.yaml",
        "chainsync · parent discontinuity",
        "Inject headers whose ``prevHash`` doesn't match the immediate parent's hash. Spec-defined reject path: receivers must drop the offending header AND the connection. Asserts the parent-pointer invariant.",
        "Receiver drops connection · no fork accepted · honest substrate intact",
    ),
    (
        "runtime-substrate-chainsync-nonincrementing-height-example-smoke.yaml",
        "chainsync · non-incrementing height",
        "Adversarial peer announces a header whose blockNo does not equal parent.blockNo + 1. Asserts the strict monotonic-height rule the consensus layer relies on for fork-choice.",
        "Header rejected · adversarial peer disconnected",
    ),
    (
        "runtime-substrate-chainsync-nonmonotonic-slot-example-smoke.yaml",
        "chainsync · non-monotonic slot",
        "Header arrives with slotNo ≤ parent.slotNo. Slot-monotonicity is a Praos invariant; the implementation must reject the regression.",
        "Header rejected · slot ordering preserved",
    ),

    # ---- BlockFetch faults ----
    (
        "runtime-substrate-blockfetch-invalid-range-example-smoke.yaml",
        "blockfetch · invalid range",
        "Client requests a (from, to) block range whose endpoints don't lie on the same chain. Server must reject with ``MsgNoBlocks``. Verifies range-validity check before any blocks ship.",
        "Server returns NoBlocks · no malformed-range responses",
    ),
    (
        "runtime-substrate-blockfetch-range-pressure-example-smoke.yaml",
        "blockfetch · range pressure",
        "Open many concurrent BlockRequestRange streams from one peer to a single server, each covering a large window. Stresses the server's per-peer concurrency cap and queue scheduling.",
        "Concurrency cap honored · no per-peer state leakage · honest peers still served",
    ),
    (
        "runtime-substrate-blockfetch-invalid-block-cbor-example-smoke.yaml",
        "blockfetch · invalid block CBOR",
        "Server returns a Block whose CBOR body is structurally malformed (truncated, wrong major type, etc.). Receiver must reject the block + drop the peer; no half-applied state.",
        "Block rejected · peer dropped · no partial-apply leakage",
    ),
    (
        "runtime-substrate-blockfetch-range-mismatch-example-smoke.yaml",
        "blockfetch · range mismatch",
        "Server ships blocks that don't fall in the requested range (sneaks one off-window block in). Client must detect via point check and reject.",
        "Range mismatch detected · blocks discarded",
    ),
    (
        "runtime-substrate-blockfetch-continuity-failure-example-smoke.yaml",
        "blockfetch · continuity failure",
        "Server returns blocks that aren't a contiguous chain (gap or fork in the middle of the range). Client's continuity check must fire.",
        "Continuity check fires · blocks rejected",
    ),

    # ---- TxSubmission ----
    (
        "runtime-substrate-txsubmission-window-pressure-example-smoke.yaml",
        "txsubmission · window pressure",
        "Push the per-peer txSubmissionAcknowledged window to its ceiling — submit more txIds than the protocol's declared backlog and verify the server applies back-pressure rather than accepting unlimited inflight requests.",
        "Window cap respected · no unbounded inflight queue",
    ),
    (
        "runtime-substrate-txsubmission-batch-pressure-example-smoke.yaml",
        "txsubmission · batch pressure",
        "Adversarial client sends batches at the spec's maximum size, repeatedly. Server's per-peer batch handling must stay bounded under sustained load.",
        "Batch handling bounded · no per-peer memory growth",
    ),
    (
        "runtime-substrate-txsubmission-unexpected-body-example-smoke.yaml",
        "txsubmission · unexpected body",
        "Reply contains a transaction body the client didn't request (txId not in the prior MsgRequestTxs list). Spec-required reject path.",
        "Reply rejected · peer dropped",
    ),
    (
        "runtime-substrate-mempool-failure-containment-example-smoke.yaml",
        "txsubmission · mempool failure containment",
        "Inject a mempool-application failure (insufficient fee / phase-2 script failure) and assert the failure is contained to that specific transaction — neighbouring valid txs in the same batch still flow.",
        "Failed tx isolated · valid txs still applied",
    ),

    # ---- Resource abuse / impairment ----
    (
        "runtime-substrate-resource-disk-full-during-sync-example-smoke.yaml",
        "resource abuse · disk full",
        "Fill the data-dir to 100% mid-sync and observe the node's recovery path. Spec: must not corrupt prior state; must surface a structured 'disk full' error.",
        "Structured error surfaced · prior state intact · post-restart recovery",
    ),
    (
        "runtime-substrate-resource-slow-loris-chainsync-example-smoke.yaml",
        "resource abuse · slow loris",
        "Slow-Loris on chainsync: open a connection, send headers byte-by-byte forever. Server's idle/per-byte timers must reap the connection.",
        "Idle timer fires · connection dropped · peer slot freed",
    ),
    (
        "runtime-substrate-resource-sync-bandwidth-throttle-example-smoke.yaml",
        "resource abuse · bandwidth throttle",
        "Throttle the upstream pipe to a bandwidth ceiling that's below the sync rate the node needs. Catch-up time must extend gracefully without timeout-cascading the rest of the substrate.",
        "Sync slows linearly · no timeout cascade · downstream peers unaffected",
    ),
    (
        "runtime-substrate-network-impairment-example-smoke.yaml",
        "network impairment · jitter / loss",
        "Inject jitter and packet loss between two nodes. Mini-protocol-level retries + KeepAlive must hold the link together.",
        "Link stays up · KeepAlive recovery fires · convergence after impairment lifted",
    ),
    (
        "m3-runtime-node2-node3-partition-rejoin.yaml",
        "network impairment · partition rejoin",
        "Hard-partition node2 from node3 for N seconds, lift the partition, observe rejoin. Asserts the network governor reopens hot/warm paths and chainsync reconciles.",
        "Partition fires · post-rejoin tip_group_count == 1",
    ),

    # ---- Snapshot recovery ----
    (
        "runtime-substrate-snapshot-restore-recovers-example-smoke.yaml",
        "snapshot recovery",
        "Capture a clean snapshot, deliberately corrupt one node's data dir, restore from the snapshot, assert chainsync re-converges. Validates the framework's snapshot/restore primitive end-to-end.",
        "Snapshot tile present · post-restore tip_group_count == 1",
    ),
    (
        "runtime-substrate-snapshot-corruption-detected-example-smoke.yaml",
        "snapshot recovery · corruption detected",
        "Pre-corrupt a node's chain DB before bringing it up; framework detects the corruption signature and triggers automatic snapshot restore.",
        "Corruption detected · auto-restore fires · node returns clean",
    ),

    # ---- Hard-fork / era ----
    (
        "runtime-substrate-era-transition-example-smoke.yaml",
        "hard-fork era transition",
        "Force a babbage→conway HF boundary at slot 500. Assert pre-HF rules observed match babbage and post-HF rules match conway, and all nodes report the same protocol version on the post side.",
        "Era transition tile reads babbage → conway · HF boundary CONVERGED",
    ),
    (
        "runtime-substrate-stake-snapshot-boundary-example-smoke.yaml",
        "hard-fork · stake-snapshot boundary",
        "Force the epoch-boundary stake-snapshot to roll over while the substrate is mid-sync. Tests that snapshot rollover doesn't disrupt active mini-protocol streams.",
        "Snapshot rolls cleanly · streams stay healthy",
    ),

    # ---- Serdes / CBOR codec ----
    (
        "runtime-substrate-serdes-blockfetch-invalid-block-cbor-example-smoke.yaml",
        "serdes · blockfetch invalid CBOR",
        "Send a block whose CBOR body is structurally malformed via the substrate-level serdes harness — exercises the parser surface end-to-end through a live blockfetch session, not just a unit-test fuzz.",
        "Malformed block rejected · receiving node stays healthy",
    ),
    (
        "runtime-substrate-serdes-txsubmission-unexpected-body-example-smoke.yaml",
        "serdes · txsubmission unexpected body",
        "Substrate-level test of the txsubmission codec rejecting a body the client didn't ask for — same property as the smaller-scope txsubmission scenario but proven at the live-substrate level.",
        "Body rejected on wire · no peer poisoning",
    ),
    (
        "amaru-cbor-block-fuzz-structured.yaml",
        "serdes · structured CBOR fuzzing",
        "Structured CBOR block fuzzing — use the spec-grammar to generate type-correct CBOR bodies and feed them into the block parser. Catches deeper structural issues that random byte fuzzing misses.",
        "Structured corpus advances bitmap · differential parse equality vs cardano-node",
    ),
    (
        "amaru-cargo-fuzz-blockfetch-aflpp-smoke.yaml",
        "serdes · AFL++ smoke",
        "AFL++ cargo-fuzz harness against Amaru's blockfetch parser. Smoke variant runs 60s with the existing corpus to detect ASAN-clean crashes only.",
        "No crashes / hangs in the smoke window · bitmap coverage advances",
    ),
    (
        "cardano-node-cov-applyblock-aflpp-smoke.yaml",
        "native coverage · applyblock ledger rules",
        "Native coverage-guided AFL++ over a SanitizerCoverage-instrumented cardano-node. The applyblock surface decodes a Conway Tx and runs the full BBODY -> LEDGERS -> per-tx LEDGER state-transition system over a genesis-initialised NewEpochState — the deepest fuzz surface, reaching the real ledger rules, not just the decoder. An 8h campaign across nine surfaces ran ~20.5M executions with 0 crashes.",
        "aflpp_smoke_exit_clean passes · native edge coverage advances · 0 crashes",
    ),

    # ---- Compound (multiple fault families together) ----
    (
        "runtime-substrate-compound-eclipse-recovery-example-smoke.yaml",
        "compound · eclipse + recovery",
        "Eclipse one node from honest peers (peer-list capture), then recover via the peer governor's hot/warm/cold rotation. Asserts the governor's recovery path actually exits eclipse.",
        "Eclipse fires · governor rotation triggers recovery · convergence restored",
    ),
    (
        "runtime-substrate-compound-hf-txsubmission-example-smoke.yaml",
        "compound · HF + tx pressure",
        "Run txsubmission window pressure ACROSS the HF boundary — the protocol version flips mid-stream. Asserts no txs are lost / double-applied during the version flip.",
        "No tx loss · post-HF version observed · streams healthy",
    ),
    (
        "runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke.yaml",
        "compound · stake-snapshot + HF",
        "Stake-snapshot rollover collides with HF boundary at the same epoch boundary. Both subsystems must complete cleanly without deadlock.",
        "Both rollovers complete · no deadlock · convergence preserved",
    ),
    (
        "runtime-substrate-compound-parser-overlay-forging-example-smoke.yaml",
        "compound · parser + forging overlay",
        "Forging-overlay scenario where adversarial blocks have malformed CBOR bodies. Tests both the parser's reject path AND the forging-overlay's slot-leader check at once.",
        "Adversarial blocks rejected · honest forging unaffected",
    ),

    # ---- Large-N substrate ----
    (
        "runtime-substrate-large-10-node-honest-mesh-example-smoke.yaml",
        "large-N · 10-node honest mesh",
        "Compose a 10-node honest substrate. Tests that the framework's compose primitive scales past the 3-node common case; observation tiles surface 10 per-node tip counts.",
        "10 nodes converged · single tip group",
    ),
    (
        "runtime-substrate-large-20-node-honest-mesh-example-smoke.yaml",
        "large-N · 20-node honest mesh",
        "20-node honest mesh — pushes the framework's compose primitive into the regime where peer-governor hot/warm/cold rotation actually matters.",
        "20 nodes converged · governor rotates without disconnecting honest links",
    ),
    (
        "runtime-substrate-large-20-node-eclipse-example-smoke.yaml",
        "large-N · 20-node eclipse",
        "20-node mesh with one node eclipsed via peer-list capture. Demonstrates the eclipse property scales — at 20 nodes the eclipsed node shouldn't recover trivially.",
        "Eclipse confirmed · governor cannot escape until honest peer is offered",
    ),
]


def list_examples() -> list[dict[str, Any]]:
    """Return the curated examples with the on-disk YAML body inlined.
    Missing files are dropped silently (the page renders the rest)."""
    base = _scenarios_dir()
    out: list[dict[str, Any]] = []
    for filename, family, demonstrates, expected in _EXAMPLES:
        path = base / filename
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        scenario_id = filename[:-len(".yaml")] if filename.endswith(".yaml") else filename
        out.append({
            "filename": filename,
            "scenario_id": scenario_id,
            "family": family,
            "demonstrates": demonstrates,
            "expected": expected,
            "body": body,
            "size_bytes": len(body),
            "scenarios_url": f"/operate/scenarios#{scenario_id}",
            "cli_command": f"cardano-profile scenario run dwarf/scenarios/{filename}",
        })
    return out


def corpus_management_examples() -> list[dict[str, str]]:
    """Small, executable-shaped examples for the corpus Learn reference."""
    return [
        {
            "title": "Inspect and export",
            "description": "Browse hashes and bounded previews, then preserve the exact configured corpus set.",
            "example": "GET /operate/corpora\nGET /api/corpora/export",
        },
        {
            "title": "Import exact bytes",
            "description": "The runtime overlay accepts a safe filename and base64-encoded bytes after the dashboard token gate.",
            "example": 'POST /api/corpora/runtime--overlay/actions?token=…\n{"action":"import","filename":"seed.cbor","content_base64":"…"}',
        },
        {
            "title": "Promote a testcase",
            "description": "Copy a retained testcase artifact into the runtime overlay without altering its source run.",
            "example": 'POST /api/corpora/runtime--overlay/actions?token=…\n{"action":"promote","case_id":"tc-…"}',
        },
    ]


def grammar_management_examples() -> list[dict[str, str]]:
    """Concrete read and mutation shapes for generation grammar assets."""
    return [
        {
            "title": "Inspect exact sources",
            "description": "Browse the validated structure and parsed token bytes, or export the complete explicit inventory.",
            "example": "GET /operate/grammars\nGET /api/grammars/export",
        },
        {
            "title": "Clone before editing",
            "description": "Copy one shipped source pair into the explicit writable runtime root; the repository source remains immutable.",
            "example": 'POST /api/grammars/<repository-id>/actions?token=…\n{"action":"clone"}',
        },
        {
            "title": "Validate and save a runtime clone",
            "description": "Both structure and dictionary must validate before either runtime file is replaced.",
            "example": 'POST /api/grammars/<runtime-id>/actions?token=…\n{"action":"save","structure":{"format":"cbor","target":"…","decoder_entrypoint":"…","shape":{"type":"array"}},"dictionary":"\\"\\\\x82\\"\\n"}',
        },
    ]


def asset_catalog_examples() -> list[dict[str, str]]:
    """Return one concrete, source-anchored example for every asset catalog.

    These records explain boundaries and relationships; they are not a second
    inventory. Live counts and object rows continue to come from the catalog
    loaders used by Operate.
    """
    return [
        {
            "catalog": "primitives",
            "object": "cbor_fuzz_target",
            "purpose": "A typed load action that drives a registered decode target with generated bytes.",
            "schema": "dwarf/primitives/load/cbor_fuzz_target.schema.json",
            "lifecycle": "Register code and schema, select it in a scenario, execute it, then retain its events and assertions in the run bundle.",
            "source_of_truth": "dwarf/primitives/registry.json",
            "example_source": "dwarf/primitives/registry.json",
            "mutability": "Repository primitives are read-only in the catalog; New primitive scaffolds a reviewed code change and requires a rebuild.",
            "relationships": "Referenced by scenarios; may drive targets and consume corpora or generation grammars; plugins may declare additional primitives.",
            "operate_url": "/operate/primitives",
            "learn_url": "/learn/primitives",
            "example": 'load:\n  - primitive: cbor_fuzz_target\n    target_id: amaru-cbor-decode-block\n    iterations: 1000',
        },
        {
            "catalog": "profile-templates",
            "object": "mixed-minimal",
            "purpose": "A reusable topology scaffold for creating a named mixed Cardano-node and Amaru deployment profile.",
            "schema": "dwarf/spec/v1/profile.schema.json",
            "lifecycle": "Inspect or download the shipped template, clone it through New profile, validate the resulting profile, then deploy that profile when a scenario needs it.",
            "source_of_truth": "dwarf/profiles/templates/mixed-minimal.yaml",
            "example_source": "dwarf/profiles/templates/mixed-minimal.yaml",
            "mutability": "Shipped templates are immutable; cloned profiles are editable through the structured or raw editor.",
            "relationships": "Creates profiles; profiles supply topology and runtime configuration to devnet scenarios.",
            "operate_url": "/operate/profile-templates",
            "learn_url": "/learn/profile-templates",
            "example": 'profile new --template mixed-minimal --name my-mixed-devnet',
        },
        {
            "catalog": "testcases",
            "object": "retained scenario candidate",
            "purpose": "A provenance-bearing record for one interesting input or run outcome that can be replayed, minimized, compared, promoted, or closed.",
            "schema": "profile_manager.testcase_lifecycle ingestion and state-record contract",
            "lifecycle": "Ingest from retained evidence, triage, replay or minimize, compare implementations when applicable, then promote to regression material or close with a reason.",
            "source_of_truth": "dwarf/state/testcases/ plus the referenced immutable run artifact",
            "example_source": "dwarf/profile_manager/testcase_lifecycle.py",
            "mutability": "Lifecycle metadata is mutable through guarded POST actions; the referenced run artifact is never rewritten.",
            "relationships": "Belongs to a testcase bucket; references its source scenario, run, target, artifact, and any promoted corpus input.",
            "operate_url": "/operate/testcases",
            "learn_url": "/learn/testcases",
            "example": '{"case_id":"tc-…","source_run_id":"…","classification":"crash","replay_state":"pending"}',
        },
        {
            "catalog": "testcase-buckets",
            "object": "implementation + classification bucket",
            "purpose": "A derived triage grouping for testcase records that share implementation and classification signals.",
            "schema": "profile_manager.data.operate_testcases.testcase_bucket_rows",
            "lifecycle": "Recomputed from testcase state whenever the catalog is read; it changes only as underlying testcase records change.",
            "source_of_truth": "Derived from dwarf/state/testcases/; no separate bucket file exists.",
            "example_source": "dwarf/profile_manager/testcase_lifecycle.py",
            "mutability": "Read-only derived view; act on the member testcases instead.",
            "relationships": "Aggregates testcase members and exposes their replay, compare, minimization, and promotion state.",
            "operate_url": "/operate/testcase-buckets",
            "learn_url": "/learn/testcases",
            "example": 'bucket: implementation=amaru · classification=runtime-anomaly · cases=3',
        },
        {
            "catalog": "corpora",
            "object": "amaru-cargo-fuzz-chainsync",
            "purpose": "An exact-byte input set used to seed, resume, minimize, or regress a fuzzing campaign.",
            "schema": "dwarf/spec/v1/corpus-record.schema.json",
            "lifecycle": "Seed or generate bytes, retain queue/crash artifacts, minimize useful cases, promote reviewed cases, then reuse them for regression.",
            "source_of_truth": "dwarf/corpora/ for shipped inputs and dwarf/state/corpora/ for the writable runtime overlay",
            "example_source": "dwarf/corpora/amaru-cargo-fuzz-chainsync/seeds/seed-001-request-next.cbor",
            "mutability": "Repository corpora are read-only; the runtime overlay permits token-gated import, promotion, and recoverable trash actions.",
            "relationships": "Consumed by primitives and targets; may receive promoted testcase artifacts; may reference a generation grammar.",
            "operate_url": "/operate/corpora",
            "learn_url": "/learn/corpora",
            "example": 'dwarf/corpora/amaru-cargo-fuzz-chainsync/seeds/seed-001-request-next.cbor',
        },
        {
            "catalog": "grammars",
            "object": "amaru-cargo-fuzz-chainsync",
            "purpose": "A validated input-shape document paired with finite mutation tokens for structure-aware generation.",
            "schema": "dwarf/spec/v1/generation-structure.schema.json plus the mutation-dictionary parser",
            "lifecycle": "Inspect the shipped pair, clone it to the runtime overlay, validate both files together, then save and reference the clone.",
            "source_of_truth": "dwarf/grammars/<id>/{structure.json,dict.txt} or dwarf/state/grammars/<id>/ for a runtime clone",
            "example_source": "dwarf/grammars/amaru-cargo-fuzz-chainsync/structure.json",
            "mutability": "Repository grammars are read-only; only validated runtime clones are editable.",
            "relationships": "Referenced explicitly by generating primitives, targets, or corpus metadata; it does not execute a workload by itself.",
            "operate_url": "/operate/grammars",
            "learn_url": "/learn/grammars",
            "example": 'dwarf/grammars/amaru-cargo-fuzz-chainsync/{structure.json,dict.txt}',
        },
        {
            "catalog": "risk-packages",
            "object": "package-a",
            "purpose": "A planning record that groups candidate-risk IDs, evidence references, blockers, compatibility, and bounded follow-up work.",
            "schema": "dwarf/spec/v1/risk-work-package.schema.json",
            "lifecycle": "Author and validate the package, execute referenced work separately, attach evidence references, and update blockers or status without promoting a finding implicitly.",
            "source_of_truth": "dwarf/evidence-packages/package-a.yaml",
            "example_source": "dwarf/evidence-packages/package-a.yaml",
            "mutability": "Repository records are read-only in Operate and change through reviewed source control.",
            "relationships": "References candidate risks, scenarios, profiles, and evidence; it is neither a run bundle nor a confirmed vulnerability.",
            "operate_url": "/operate/risk-packages",
            "learn_url": "/learn/risk-packages",
            "example": 'id: package-a\ncandidate_ids: [CR-M1-DRAFT-001]\nevidence_paths: […]',
        },
        {
            "catalog": "plugins",
            "object": "hello-dwarf manifest (documentation example)",
            "purpose": "A statically inspectable extension manifest that can declare a primitive registry and, optionally, a runtime entrypoint.",
            "schema": "plugin.json contract documented by profile_manager.plugin_loader",
            "lifecycle": "Place the plugin under an explicit plugin root, inventory its manifest without execution, review collisions and trust boundaries, then restart DWARF to load an approved runtime entrypoint.",
            "source_of_truth": "The schema-valid example in /learn/plugin-authoring; installed plugins use plugin.json plus their explicitly declared registry and entrypoint files",
            "example_source": "dwarf/dashboard/templates/learn/plugin_authoring_guide.j2",
            "mutability": "Read-only inventory and export; plugin authoring happens outside the dashboard and execution is never sandboxed.",
            "relationships": "Declares primitives and their schemas/executors; those primitives may then be referenced by scenarios.",
            "operate_url": "/operate/plugins",
            "learn_url": "/learn/plugin-authoring",
            "example": '{"plugin_id":"hello-dwarf","dwarf_api_version":"v1","registry":"registry.json"}',
        },
    ]
