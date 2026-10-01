# Dwarf Primitives Reference

Generated from `dwarf/primitives/registry.json` plus the referenced JSON schemas.

## `aflpp_smoke_exit_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `AflppSmokeExitClean`
- Supports: `cardano-node`
- Runtimes: `library`
- Schema: `primitives/assertion/aflpp_smoke_exit_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_bitmap_cvg` | `number` | `no` | - |
| `min_completed` | `integer` | `no` | - |
| `min_cycles_done` | `integer` | `no` | - |
| `min_execs_done` | `integer` | `no` | - |
| `min_queue_count` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "min_bitmap_cvg": 0.0,
    "min_cycles_done": 1,
    "min_execs_done": 1000,
    "min_queue_count": 12,
    "primitive": "aflpp_smoke_exit_clean"
  },
  "scenario": "cardano-node-cov-applyblock-aflpp-smoke"
}
```

## `all_nodes_responsive`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `AllNodesResponsive`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/all_nodes_responsive.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "all_nodes_responsive"
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `all_nodes_started_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `AllNodesStartedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/all_nodes_started_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |
| `min_node_count` | `integer` | `no` | Minimum number of nodes the compose report must contain before the assertion can pass. |

### Example Invocation

```json
{
  "reference": {
    "min_completed": 1,
    "min_node_count": 5,
    "primitive": "all_nodes_started_clean"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke"
}
```

## `amaru_measurement_boundary_proven`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `AmaruMeasurementBoundaryProven`
- Supports: `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/amaru_measurement_boundary_proven.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_mode` | `string` | `yes` | - |
| `min_attempts_per_case` | `integer` | `yes` | - |
| `min_internal_samples_per_outcome` | `integer` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_mode": "stock",
    "min_attempts_per_case": 40,
    "min_internal_samples_per_outcome": 0,
    "primitive": "amaru_measurement_boundary_proven"
  },
  "scenario": "amaru-measurement-boundary-control-stock"
}
```

## `assemble_blockjson`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.block_apply_primitives`
- Class: `AssembleBlockjson`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/assemble_blockjson.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `active_body_path` | `string` | `no` | - |
| `args` | `array` | `no` | - |
| `forge_result_path` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "forge_result_path": "outputs/forge-block/forge-result.json",
    "output_dir": "outputs/assemble",
    "primitive": "assemble_blockjson"
  },
  "scenario": "ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `big_ledger_peer_quorum_intact`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BigLedgerPeerQuorumIntact`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/big_ledger_peer_quorum_intact.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_expected_matches` | `integer` | `yes` | - |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_expected_matches": 3,
    "primitive": "big_ledger_peer_quorum_intact"
  },
  "scenario": "runtime-substrate-eclipse-topology-example-smoke"
}
```

## `block_application_samples_correlated`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BlockApplicationSamplesCorrelated`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/block_application_samples_correlated.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_samples` | `integer` | `no` | - |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_samples": 30,
    "primitive": "block_application_samples_correlated"
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `block_apply_differential`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.block_apply_primitives`
- Class: `BlockApplyDifferential`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/block_apply_differential.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `expected_outcome` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `serve_addr` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `watch_seconds` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_outcome": "accept_invalid",
    "output_dir": "outputs/block-apply-differential",
    "primitive": "block_apply_differential",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1",
    "watch_seconds": 120
  },
  "scenario": "ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `block_apply_outcome_matches`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.block_apply_primitives`
- Class: `BlockApplyOutcomeMatches`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/block_apply_outcome_matches.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `expected_outcome` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |
| `source_primitive` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_outcome": "accept_invalid",
    "primitive": "block_apply_outcome_matches"
  },
  "scenario": "ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `blockfetch_continuity_failure_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BlockfetchContinuityFailureRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/blockfetch_continuity_failure_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "blockfetch_continuity_failure_rejected"
  },
  "scenario": "runtime-substrate-blockfetch-continuity-failure-example-smoke"
}
```

## `blockfetch_invalid_block_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BlockfetchInvalidBlockRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/blockfetch_invalid_block_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "blockfetch_invalid_block_rejected"
  },
  "scenario": "runtime-substrate-blockfetch-invalid-block-cbor-example-smoke"
}
```

## `blockfetch_invalid_range_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BlockfetchInvalidRangeRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/blockfetch_invalid_range_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "blockfetch_invalid_range_rejected"
  },
  "scenario": "runtime-substrate-blockfetch-invalid-range-example-smoke"
}
```

## `blockfetch_range_pressure_bounded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BlockfetchRangePressureBounded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/blockfetch_range_pressure_bounded.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_block_range_requests_observed` | `integer` | `no` | Minimum number of BlockFetch RequestRange messages that must be observed. |
| `min_blocks_fetched` | `integer` | `no` | Minimum number of BlockFetch block messages that must be observed. |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "blockfetch_range_pressure_bounded"
  },
  "scenario": "runtime-substrate-blockfetch-range-pressure-example-smoke"
}
```

## `blockfetch_response_range_strict`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BlockfetchResponseRangeStrict`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/blockfetch_response_range_strict.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "blockfetch_response_range_strict"
  },
  "scenario": "runtime-substrate-blockfetch-range-mismatch-example-smoke"
}
```

## `bootstrap_assumptions_safe`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BootstrapAssumptionsSafe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/bootstrap_assumptions_safe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "bootstrap_assumptions_safe"
  },
  "scenario": "runtime-substrate-bootstrap-assumption-probe-example-smoke"
}
```

## `build_block_segments`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.block_apply_primitives`
- Class: `BuildBlockSegments`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/build_block_segments.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `label` | `string` | `no` | - |
| `out_name` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |
| `tx_file` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "label": "cert-phantom",
    "output_dir": "outputs/build-segments",
    "primitive": "build_block_segments",
    "tx_file": "fixture/cert-phantom/cert-phantom.tx"
  },
  "scenario": "ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `bundle_attestation_signature_valid`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BundleAttestationSignatureValid`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/bundle_attestation_signature_valid.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "bundle_attestation_signature_valid"
  },
  "scenario": "runtime-bundle-attestation-example-smoke"
}
```

## `bundle_chain_verify_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BundleChainVerifyClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/bundle_chain_verify_clean.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "bundle_chain_verify_clean"
  },
  "scenario": "runtime-bundle-chain-verify-example-smoke"
}
```

## `bundle_diff_completed_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BundleDiffCompletedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `single-node`, `devnet`
- Schema: `primitives/assertion/bundle_diff_completed_clean.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "bundle_diff_completed_clean"
  },
  "scenario": "runtime-bundle-diff-example-divergence"
}
```

## `bundle_sarif_export_valid`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BundleSarifExportValid`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `single-node`, `devnet`
- Schema: `primitives/assertion/bundle_sarif_export_valid.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "bundle_sarif_export_valid"
  },
  "scenario": "runtime-bundle-export-sarif-example-smoke"
}
```

## `bundle_summary_compose_completed_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BundleSummaryComposeCompletedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/bundle_summary_compose_completed_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "bundle_summary_compose_completed_clean"
  },
  "scenario": "runtime-bundle-summary-compose-example-smoke"
}
```

## `bundle_timeline_emitted_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `BundleTimelineEmittedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/bundle_timeline_emitted_clean.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "bundle_timeline_emitted_clean"
  },
  "scenario": "runtime-bundle-timeline-example-smoke"
}
```

## `byzantine_cardano_node_recorded_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ByzantineCardanoNodeRecordedClean`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/assertion/byzantine_cardano_node_recorded_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "min_completed": 1,
    "primitive": "byzantine_cardano_node_recorded_clean"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke"
}
```

## `byzantine_isolation_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ByzantineIsolationObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/byzantine_isolation_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `byzantine_node_ids` | `array` | `yes` | - |
| `honest_node_ids` | `array` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "byzantine_node_ids": [
      "node3"
    ],
    "honest_node_ids": [
      "node1",
      "node2"
    ],
    "primitive": "byzantine_isolation_observed"
  },
  "scenario": "runtime-substrate-byzantine-blockfetch-example-smoke"
}
```

## `canonical_chain_progress_complete`

- Family: `assertion`
- Version: `0.2.0`
- Module: `profile_manager.primitives`
- Class: `CanonicalChainProgressComplete`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/canonical_chain_progress_complete.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `max_oscillation_episodes` | `integer` | `no` | - |
| `max_oscillation_transitions` | `integer` | `no` | - |
| `minimum_adopted_blocks` | `integer` | `no` | - |
| `minimum_convergence_blocks` | `integer` | `no` | - |
| `minimum_correlations` | `integer` | `no` | - |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "max_oscillation_episodes": 3,
    "max_oscillation_transitions": 8,
    "minimum_adopted_blocks": 30,
    "minimum_convergence_blocks": 3,
    "minimum_correlations": 30,
    "primitive": "canonical_chain_progress_complete"
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `cardano_cbor_dataset_differential_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CardanoCborDatasetDifferentialClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/cardano_cbor_dataset_differential_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |
| `min_inputs_processed` | `integer` | `no` | - |
| `min_samples_per_category` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "min_completed": 1,
    "min_inputs_processed": 100,
    "min_samples_per_category": 25,
    "primitive": "cardano_cbor_dataset_differential_clean"
  },
  "scenario": "cardano-amaru-cbor-dataset-plutus-data-differential"
}
```

## `cbor_conformance_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CborConformanceClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/assertion/cbor_conformance_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |
| `min_inputs_processed` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "min_inputs_processed": 100,
    "primitive": "cbor_conformance_clean"
  },
  "scenario": "client-example-cbor-decoding-amaru-d3a6dafc-regression"
}
```

## `cbor_edge_cases`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CborEdgeCases`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/cbor_edge_cases.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `edge_cases` | `array` | `yes` | - |
| `manifests_dir` | `string` | `yes` | - |
| `per_input_timeout_seconds` | `number` | `no` | - |
| `target_id` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "edge_cases": [
      {
        "hex": "",
        "name": "empty_input"
      },
      {
        "hex": "00",
        "name": "single_zero_byte"
      },
      {
        "hex": "00",
        "name": "uint_zero"
      },
      {
        "hex": "17",
        "name": "uint_max_inline"
      },
      {
        "hex": "1818",
        "name": "uint_min_1byte"
      },
      {
        "hex": "18ff",
        "name": "uint_255"
      },
      {
        "hex": "190100",
        "name": "uint_256"
      },
      {
        "hex": "19ffff",
        "name": "uint_65535"
      },
      {
        "hex": "1a00010000",
        "name": "uint_65536"
      },
      {
        "hex": "1affffffff",
        "name": "uint_max_u32"
      },
      {
        "hex": "1bffffffffffffffff",
        "name": "uint_max_u64"
      },
      {
        "hex": "20",
        "name": "negative_minus_one"
      },
      {
        "hex": "37",
        "name": "negative_min_inline"
      },
      {
        "hex": "40",
        "name": "empty_bytes_definite"
      },
      {
        "hex": "5fff",
        "name": "empty_bytes_indefinite"
      },
      {
        "hex": "60",
        "name": "empty_text_definite"
      },
      {
        "hex": "7fff",
        "name": "empty_text_indefinite"
      },
      {
        "hex": "80",
        "name": "empty_array_definite"
      },
      {
        "hex": "9fff",
        "name": "empty_array_indefinite"
      },
      {
        "hex": "a0",
        "name": "empty_map_definite"
      },
      {
        "hex": "bfff",
        "name": "empty_map_indefinite"
      },
      {
        "hex": "f4",
        "name": "false"
      },
      {
        "hex": "f5",
        "name": "true"
      },
      {
        "hex": "f6",
        "name": "null"
      },
      {
        "hex": "f7",
        "name": "undefined"
      },
      {
        "hex": "f90000",
        "name": "float16_zero"
      },
      {
        "hex": "f97c00",
        "name": "float16_pos_inf"
      },
      {
        "hex": "f9fc00",
        "name": "float16_neg_inf"
      },
      {
        "hex": "f97e00",
        "name": "float16_nan"
      },
      {
        "hex": "fb7ff0000000000000",
        "name": "float64_pos_inf"
      },
      {
        "hex": "c060",
        "name": "tag0_empty_text"
      },
      {
        "hex": "c240",
        "name": "tag2_empty_bytes_bigint_pos"
      },
      {
        "hex": "c340",
        "name": "tag3_empty_bytes_bigint_neg"
      },
      {
        "hex": "d818410100",
        "name": "tag24_cbor_in_cbor_zero"
      },
      {
        "hex": "d90103a0",
        "name": "tag259_empty_map"
      },
      {
        "hex": "81818181818181818181 00",
        "name": "deeply_nested_10_arrays"
      },
      {
        "hex": "81",
        "name": "array_claim_1_no_elem"
      },
      {
        "hex": "a1",
        "name": "map_claim_1_no_kv"
      },
      {
        "hex": "a100",
        "name": "map_with_uint_key_no_value"
      },
      {
        "hex": "a20000 01",
        "name": "map_with_two_keys_one_value"
      },
      {
        "hex": "7f6048656c6c6fff",
        "name": "indef_text_chunk_followed_by_break"
      },
      {
        "hex": "9affffffff",
        "name": "huge_array_length_4gb_no_data"
      }
    ],
    "manifests_dir": "dwarf/targets/manifests",
    "per_input_timeout_seconds": 2,
    "primitive": "cbor_edge_cases",
    "target_id": "amaru-cbor-decode-tx-body"
  },
  "scenario": "edge-cases-cbor-tx-body-amaru"
}
```

## `cbor_fuzz_structured`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CborFuzzStructured`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/cbor_fuzz_structured.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `iterations` | `integer` | `no` | - |
| `manifests_dir` | `string` | `yes` | - |
| `mutation_rate` | `number` | `no` | - |
| `per_input_timeout_seconds` | `number` | `no` | - |
| `shape` | `unspecified` | `yes` | - |
| `target_id` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "iterations": 5000,
    "manifests_dir": "dwarf/targets/manifests",
    "mutation_rate": 0.05,
    "per_input_timeout_seconds": 2,
    "primitive": "cbor_fuzz_structured",
    "shape": {
      "inner": {
        "entries": [
          [
            0,
            {
              "entries": [
                [
                  1,
                  {
                    "length": {
                      "max": 32,
                      "min": 1
                    },
                    "type": "text"
                  }
                ]
              ],
              "type": "map"
            }
          ],
          [
            1,
            {
              "elements": [],
              "type": "array"
            }
          ],
          [
            2,
            {
              "elements": [],
              "type": "array"
            }
          ],
          [
            3,
            {
              "elements": [],
              "type": "array"
            }
          ]
        ],
        "type": "map"
      },
      "tag": 259,
      "type": "tag"
    },
    "target_id": "amaru-cbor-decode-auxiliary-data"
  },
  "scenario": "amaru-cbor-auxiliary-data-fuzz-structured"
}
```

## `cbor_fuzz_target`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CborFuzzTarget`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/cbor_fuzz_target.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `iterations` | `integer` | `no` | - |
| `manifests_dir` | `string` | `yes` | - |
| `max_bytes` | `integer` | `no` | - |
| `min_bytes` | `integer` | `no` | - |
| `per_input_timeout_seconds` | `number` | `no` | - |
| `target_id` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "iterations": 10000,
    "manifests_dir": "dwarf/targets/manifests",
    "max_bytes": 4096,
    "min_bytes": 1,
    "per_input_timeout_seconds": 2,
    "primitive": "cbor_fuzz_target",
    "target_id": "amaru-cbor-decode-auxiliary-data"
  },
  "scenario": "amaru-cbor-auxiliary-data-fuzz"
}
```

## `cbor_roundtrip_consistent`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CborRoundtripConsistent`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/assertion/cbor_roundtrip_consistent.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |
| `min_inputs_processed` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "min_inputs_processed": 100,
    "primitive": "cbor_roundtrip_consistent"
  },
  "scenario": "client-example-cbor-decoding-amaru-d3a6dafc-regression"
}
```

## `chain_select_consistent`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainSelectConsistent`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chain_select_consistent.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "chain_select_consistent"
  },
  "scenario": "consensus-threshold-delay-probe"
}
```

## `chain_select_differential`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainSelectDifferential`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chain_select_differential.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `reference_node_ids` | `array` | `yes` | Node ids of the reference implementation group (e.g. the Haskell cardano-node relays). |
| `require_real_progress` | `boolean` | `no` | Require both groups' tips to show real non-zero chain progress, so the assertion cannot pass on nodes stalled at genesis. |
| `target_node_ids` | `array` | `yes` | Node ids of the target implementation group (e.g. the Amaru relays). |
| `tolerance_slots` | `integer` | `no` | Allowed slot gap between the two groups' converged tips. 0 requires hash-identical tips; >0 permits agreement modulo lag (reported as within_slot_tolerance). |

### Example Invocation

```json
{
  "reference": {
    "primitive": "chain_select_differential",
    "reference_node_ids": [
      "relay1",
      "relay2"
    ],
    "require_real_progress": true,
    "target_node_ids": [
      "amaru-relay-1",
      "amaru-relay-2"
    ],
    "tolerance_slots": 5
  },
  "scenario": "consensus-chainhold-differential-stage1-tiebreak"
}
```

## `chain_switch_consistent`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainSwitchConsistent`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chain_switch_consistent.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `honest_node_ids` | `array` | `no` | - |
| `primitive` | `unspecified` | `yes` | Asserts that a chain-switch converged to the injected target with real peer connectivity and observed slot transitions. |

### Example Invocation

```json
{
  "reference": {
    "honest_node_ids": [
      "node1",
      "node2",
      "node3"
    ],
    "primitive": "chain_switch_consistent"
  },
  "scenario": "runtime-substrate-compound-local-query-recovery-example-smoke"
}
```

## `chainsync_height_monotonicity_enforced`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainsyncHeightMonotonicityEnforced`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chainsync_height_monotonicity_enforced.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "chainsync_height_monotonicity_enforced"
  },
  "scenario": "runtime-substrate-chainsync-nonincrementing-height-example-smoke"
}
```

## `chainsync_parent_discontinuity_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainsyncParentDiscontinuityRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chainsync_parent_discontinuity_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "chainsync_parent_discontinuity_rejected"
  },
  "scenario": "runtime-substrate-chainsync-parent-discontinuity-example-smoke"
}
```

## `chainsync_responder_rollback_then_forward_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainsyncResponderRollbackThenForwardClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chainsync_responder_rollback_then_forward_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_chainsync_messages_observed` | `integer` | `no` | - |
| `min_rollback_then_forward_count` | `integer` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "chainsync_responder_rollback_then_forward_clean"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-chainsync-fork-switch-example-smoke"
}
```

## `chainsync_slot_monotonicity_enforced`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ChainsyncSlotMonotonicityEnforced`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/chainsync_slot_monotonicity_enforced.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "chainsync_slot_monotonicity_enforced"
  },
  "scenario": "runtime-substrate-chainsync-nonmonotonic-slot-example-smoke"
}
```

## `container_runtime_hardening_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ContainerRuntimeHardeningObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/container_runtime_hardening_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_node_ids` | `array` | `no` | - |
| `minimum_container_count` | `integer` | `no` | - |
| `source_primitive` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_node_ids": [
      "node1",
      "node2",
      "node3",
      "node4",
      "node5"
    ],
    "minimum_container_count": 5,
    "primitive": "container_runtime_hardening_observed"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke"
}
```

## `controlled_sync_range_complete`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ControlledSyncRangeComplete`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/controlled_sync_range_complete.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_blocks` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_blocks": 5,
    "primitive": "controlled_sync_range_complete"
  },
  "scenario": "client-example-restart-recovery-sync-amaru-20260918-mixed-1112"
}
```

## `credential_ceremony_recorded_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `CredentialCeremonyRecordedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `devnet`
- Schema: `primitives/assertion/credential_ceremony_recorded_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |
| `min_keys_generated` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "credential_ceremony_recorded_clean"
  },
  "scenario": "runtime-credential-ceremony-example-smoke"
}
```

## `duplex_promotion_slot_limit_enforced`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `DuplexPromotionSlotLimitEnforced`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/duplex_promotion_slot_limit_enforced.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "duplex_promotion_slot_limit_enforced"
  },
  "scenario": "runtime-substrate-duplex-promotion-pressure-example-smoke"
}
```

## `epoch_boundary_timing_within_bounds`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `EpochBoundaryTimingWithinBounds`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/epoch_boundary_timing_within_bounds.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "epoch_boundary_timing_within_bounds"
  },
  "scenario": "consensus-state-lifecycle-bootstrap-differential"
}
```

## `fault_local_port_delay`

- Family: `fault`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `FaultLocalPortDelay`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/fault/fault_local_port_delay.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `delay_ms` | `integer` | `yes` | - |
| `host` | `string` | `no` | - |
| `jitter_ms` | `integer` | `no` | - |
| `protocol` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_port` | `integer` | `no` | - |
| `target_port_file` | `string` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `fault_local_port_drop`

- Family: `fault`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `FaultLocalPortDrop`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/fault/fault_local_port_drop.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `drop_input` | `boolean` | `no` | - |
| `drop_output` | `boolean` | `no` | - |
| `host` | `string` | `no` | - |
| `protocol` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_port` | `integer` | `no` | - |
| `target_port_file` | `string` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `fault_node_freeze`

- Family: `fault`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `FaultNodeFreeze`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/fault/fault_node_freeze.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `freeze_timeout_seconds` | `number` | `no` | - |
| `resume_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_node` | `string` | `yes` | - |

### Example Invocation

_No authored scenario example found._

## `forensic_snapshot_emitted_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ForensicSnapshotEmittedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/forensic_snapshot_emitted_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "forensic_snapshot_emitted_clean"
  },
  "scenario": "runtime-forensic-snapshot-example-smoke"
}
```

## `forge_block`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.block_apply_primitives`
- Class: `ForgeBlock`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/forge_block.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `block_height` | `integer` | `no` | - |
| `body_segments_path` | `string` | `no` | - |
| `forced_nonce` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `pool_keys_dir` | `string` | `no` | - |
| `prev_hash` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |
| `slot` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "block_height": 234,
    "body_segments_path": "outputs/build-segments/body-segments.json",
    "forced_nonce": "3a5e36011eec01657c4478a9b6f021d8d0eced3232b2796b79f40d0cd0620ebe",
    "output_dir": "outputs/forge-block",
    "pool_keys_dir": "block_apply/keys-97b0",
    "prev_hash": "f4d474b8498a97a884f84b32ceede03bf392abae870a2b30b566f722192ff7bd",
    "primitive": "forge_block",
    "slot": 1211
  },
  "scenario": "ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `handshake_cases_match_expected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `HandshakeCasesMatchExpected`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/assertion/handshake_cases_match_expected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `implementation` | `string` | `yes` | - |
| `min_attempts_per_case` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "implementation": "amaru",
    "min_attempts_per_case": 20,
    "primitive": "handshake_cases_match_expected"
  },
  "scenario": "amaru-n2n-handshake-version-table-forward-compat-20260918-mixed-1112"
}
```

## `hf_boundary_rule_consistent`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `HfBoundaryRuleConsistent`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/hf_boundary_rule_consistent.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "hf_boundary_rule_consistent"
  },
  "scenario": "runtime-substrate-compound-hf-txsubmission-example-smoke"
}
```

## `honest_peer_set_uncompromised`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `HonestPeerSetUncompromised`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/honest_peer_set_uncompromised.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `honest_node_ids` | `array` | `yes` | Honest node ids that must remain connected to enough honest peers and emit real non-zero tip observations. |
| `minimum_honest_peers` | `integer` | `yes` | Minimum number of honest peers each configured honest node must retain. |
| `primitive` | `unspecified` | `yes` | Asserts that each configured honest node retains the minimum honest-peer count without peer-set capture and with real non-zero tip evidence. |

### Example Invocation

```json
{
  "reference": {
    "honest_node_ids": [
      "p1",
      "relay1",
      "relay2",
      "amaru-consumer"
    ],
    "minimum_honest_peers": 2,
    "primitive": "honest_peer_set_uncompromised"
  },
  "scenario": "consensus-sync-peer-concentration-eclipse-differential"
}
```

## `honest_quorum_preserved`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `HonestQuorumPreserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/honest_quorum_preserved.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `honest_node_ids` | `array` | `yes` | - |
| `minimum_fraction` | `number` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "honest_node_ids": [
      "node1",
      "node2"
    ],
    "minimum_fraction": 1.0,
    "primitive": "honest_quorum_preserved"
  },
  "scenario": "runtime-substrate-byzantine-blockfetch-example-smoke"
}
```

## `hot_warm_churn_within_bounds`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `HotWarmChurnWithinBounds`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/hot_warm_churn_within_bounds.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `maximum_events_per_hour` | `number` | `yes` | - |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "maximum_events_per_hour": 8,
    "primitive": "hot_warm_churn_within_bounds"
  },
  "scenario": "runtime-substrate-compound-eclipse-txsubmission-example-smoke"
}
```

## `invalid_protocol_cases_contained`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `InvalidProtocolCasesContained`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/invalid_protocol_cases_contained.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "invalid_protocol_cases_contained"
  },
  "scenario": "client-example-cbor-decoding-amaru-d3a6dafc-regression"
}
```

## `k_bound_rollback_recovered`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `KBoundRollbackRecovered`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/k_bound_rollback_recovered.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | Asserts that a within-k rollback recovered with real peer connectivity and observed slot transitions. |

### Example Invocation

```json
{
  "reference": {
    "primitive": "k_bound_rollback_recovered"
  },
  "scenario": "runtime-substrate-compound-local-query-recovery-example-smoke"
}
```

## `keepalive_failure_budget_bounded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `KeepaliveFailureBudgetBounded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/keepalive_failure_budget_bounded.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "keepalive_failure_budget_bounded"
  },
  "scenario": "runtime-substrate-compound-keepalive-topology-capture-example-smoke"
}
```

## `leadership_schedule_recomputes_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `LeadershipScheduleRecomputesClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/leadership_schedule_recomputes_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "leadership_schedule_recomputes_clean"
  },
  "scenario": "runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke"
}
```

## `ledger_peer_stake_weight_preserved`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `LedgerPeerStakeWeightPreserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/ledger_peer_stake_weight_preserved.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `maximum_absolute_delta` | `number` | `yes` | - |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "maximum_absolute_delta": 0.05,
    "primitive": "ledger_peer_stake_weight_preserved"
  },
  "scenario": "runtime-substrate-eclipse-topology-example-smoke"
}
```

## `load_events_are_ok`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `LoadEventsAreOk`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `single-node`, `devnet`
- Schema: `primitives/assertion/load_events_are_ok.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_completed` | `integer` | `no` | - |
| `min_event_count` | `integer` | `no` | Minimum number of outcome-bearing load completed events required before the assertion can pass. |

### Example Invocation

```json
{
  "reference": {
    "min_completed": 1,
    "min_event_count": 1,
    "primitive": "load_events_are_ok"
  },
  "scenario": "amaru-measurement-e2e-stock"
}
```

## `load_shell_command`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `LoadShellCommand`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `single-node`, `devnet`
- Schema: `primitives/load/load_shell_command.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `command` | `string` | `yes` | - |
| `expect_exit` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "command": "python3 /home/nigel/dwarf-v4/tools/cardano_amaru_kes_security_probe.py /home/nigel/dwarf-v4/antithesis/cardano_amaru_kes_security --timeout-seconds 3600 --sample-seconds 10",
    "expect_exit": 0,
    "primitive": "load_shell_command",
    "timeout_seconds": 3700
  },
  "scenario": "cardano-amaru-kes-security-local"
}
```

## `local_query_amplification_bounded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `LocalQueryAmplificationBounded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/local_query_amplification_bounded.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "local_query_amplification_bounded"
  },
  "scenario": "runtime-substrate-compound-local-query-recovery-example-smoke"
}
```

## `local_submit_availability_preserved`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `LocalSubmitAvailabilityPreserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/local_submit_availability_preserved.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "local_submit_availability_preserved"
  },
  "scenario": "runtime-substrate-local-submit-stress-example-smoke"
}
```

## `malformed_input_parity_preserved`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `MalformedInputParityPreserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/malformed_input_parity_preserved.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "malformed_input_parity_preserved"
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `mempool_failure_contained`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `MempoolFailureContained`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/mempool_failure_contained.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "mempool_failure_contained"
  },
  "scenario": "runtime-substrate-mempool-failure-containment-example-smoke"
}
```

## `mempool_relay_pressure_bounded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `MempoolRelayPressureBounded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/mempool_relay_pressure_bounded.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "mempool_relay_pressure_bounded"
  },
  "scenario": "runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke"
}
```

## `minimum_adopted_block_range_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `MinimumAdoptedBlockRangeObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/minimum_adopted_block_range_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_adopted_blocks` | `integer` | `no` | - |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_adopted_blocks": 30,
    "primitive": "minimum_adopted_block_range_observed"
  },
  "scenario": "client-example-block-application-amaru"
}
```

## `mode_switch_genesis_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ModeSwitchGenesisObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/mode_switch_genesis_observed.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "mode_switch_genesis_observed"
  },
  "scenario": "runtime-substrate-era-transition-example-smoke"
}
```

## `mux_ingress_overrun_scoped`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `MuxIngressOverrunScoped`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/mux_ingress_overrun_scoped.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "mux_ingress_overrun_scoped"
  },
  "scenario": "runtime-substrate-mux-ingress-overrun-example-smoke"
}
```

## `no_target_fatal_signal`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `NoTargetFatalSignal`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/no_target_fatal_signal.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "no_target_fatal_signal"
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `opcert_case_verdicts_match_expected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `OpcertCaseVerdictsMatchExpected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/opcert_case_verdicts_match_expected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "opcert_case_verdicts_match_expected"
  },
  "scenario": "opcert-header-validation-boundary-cardano-1112"
}
```

## `opcert_soak_invariant_holds`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `OpcertSoakInvariantHolds`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/opcert_soak_invariant_holds.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "opcert_soak_invariant_holds",
    "report_path": "outputs/opcert-soak/result.json"
  },
  "scenario": "opcert-soak-accept-boundary-amaru-20260918"
}
```

## `opcert_soak_reasons_agree`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `OpcertSoakReasonsAgree`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/opcert_soak_reasons_agree.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "opcert_soak_reasons_agree",
    "report_path": "outputs/opcert-soak/result.json"
  },
  "scenario": "opcert-soak-counter-edge-mixed-1112-amaru-20260918"
}
```

## `opcert_soak_verdicts_agree`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `OpcertSoakVerdictsAgree`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/opcert_soak_verdicts_agree.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "opcert_soak_verdicts_agree",
    "report_path": "outputs/opcert-soak/result.json"
  },
  "scenario": "opcert-soak-counter-edge-mixed-1112-amaru-20260918"
}
```

## `opcert_verdicts_agree`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `OpcertVerdictsAgree`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/opcert_verdicts_agree.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `amaru_report_path` | `string` | `no` | - |
| `cardano_report_path` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "amaru_report_path": "outputs/opcert-header-cases-amaru/result.json",
    "cardano_report_path": "outputs/opcert-header-cases-cardano/result.json",
    "primitive": "opcert_verdicts_agree"
  },
  "scenario": "opcert-header-validation-cases-mixed-1112-amaru-20260918"
}
```

## `overlay_slot_forging_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `OverlaySlotForgingRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/overlay_slot_forging_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "overlay_slot_forging_rejected"
  },
  "scenario": "runtime-substrate-compound-parser-overlay-forging-example-smoke"
}
```

## `panic_path_contained`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PanicPathContained`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/panic_path_contained.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "panic_path_contained"
  },
  "scenario": "consensus-restart-rollback-in-future-differential"
}
```

## `parse_succeeds_or_clean_error`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ParseSucceedsOrCleanError`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/parse_succeeds_or_clean_error.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_outcomes_count` | `integer` | `no` | Minimum number of parse outcomes that must be recorded before the assertion can pass. |

### Example Invocation

```json
{
  "reference": {
    "primitive": "parse_succeeds_or_clean_error"
  },
  "scenario": "amaru-cbor-auxiliary-data-fuzz-structured"
}
```

## `parser_bounds_enforced`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ParserBoundsEnforced`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/parser_bounds_enforced.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "parser_bounds_enforced"
  },
  "scenario": "runtime-substrate-compound-parser-overlay-forging-example-smoke"
}
```

## `parser_exit_status`

- Family: `probe`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ParserExitStatus`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/probe/parser_exit_status.schema.json`

### Parameters

_No parameters._

### Example Invocation

_No authored scenario example found._

## `peer_connectivity_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PeerConnectivityObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/peer_connectivity_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_edges` | `array` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_edges": [
      [
        "node1",
        "node2"
      ],
      [
        "node2",
        "node1"
      ]
    ],
    "primitive": "peer_connectivity_observed"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-blockfetch-range-pressure-example-smoke"
}
```

## `peer_eviction_within_seconds`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PeerEvictionWithinSeconds`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/peer_eviction_within_seconds.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `byzantine_node_ids` | `array` | `yes` | - |
| `honest_node_ids` | `array` | `yes` | - |
| `timeout_seconds` | `number` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "byzantine_node_ids": [
      "node3"
    ],
    "honest_node_ids": [
      "node1",
      "node2"
    ],
    "primitive": "peer_eviction_within_seconds",
    "timeout_seconds": 10
  },
  "scenario": "runtime-substrate-byzantine-blockfetch-example-smoke"
}
```

## `plutus_live_outcomes_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PlutusLiveOutcomesObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/assertion/plutus_live_outcomes_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_per_outcome` | `unspecified` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_per_outcome": 30,
    "primitive": "plutus_live_outcomes_observed"
  },
  "scenario": "client-example-plutus-vm-amaru-onchain-v2-20260918"
}
```

## `plutus_phase2_differential_equivalent`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PlutusPhase2DifferentialEquivalent`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/plutus_phase2_differential_equivalent.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "plutus_phase2_differential_equivalent"
  },
  "scenario": "conway-scriptcontext-txinfo-fidelity-differential-amaru-cardano-node"
}
```

## `plutus_phase2_donotintervene_retry_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PlutusPhase2DoNotInterveneRetryClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/plutus_phase2_donotintervene_retry_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "plutus_phase2_donotintervene_retry_clean"
  },
  "scenario": "runtime-substrate-plutus-phase2-donointervene-retry-clean-example-smoke"
}
```

## `plutus_phase2_exunits_overrun_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PlutusPhase2ExUnitsOverrunRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/plutus_phase2_exunits_overrun_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "plutus_phase2_exunits_overrun_rejected"
  },
  "scenario": "runtime-substrate-plutus-phase2-exunits-cost-model-string-undercharge-mixed-amaru"
}
```

## `plutus_phase2_isvalid_mismatch_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PlutusPhase2IsValidMismatchRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/plutus_phase2_isvalid_mismatch_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "plutus_phase2_isvalid_mismatch_rejected"
  },
  "scenario": "runtime-substrate-plutus-phase2-isvalid-mismatch-rejected-example-smoke"
}
```

## `plutus_result_and_budget_match`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PlutusResultAndBudgetMatch`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/assertion/plutus_result_and_budget_match.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `executions_per_outcome` | `unspecified` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "executions_per_outcome": 30,
    "primitive": "plutus_result_and_budget_match"
  },
  "scenario": "client-example-plutus-vm-amaru-onchain-v2-20260918"
}
```

## `praos_header_assertion_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `PraosHeaderAssertionRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/praos_header_assertion_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "praos_header_assertion_rejected"
  },
  "scenario": "runtime-substrate-praos-header-assertion-example-smoke"
}
```

## `quorum_holds_despite_byzantine`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `QuorumHoldsDespiteByzantine`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/quorum_holds_despite_byzantine.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `byzantine_node_ids` | `array` | `yes` | - |
| `honest_node_ids` | `array` | `yes` | - |
| `min_completed` | `integer` | `no` | - |
| `minimum_honest_consensus_count` | `integer` | `no` | - |
| `minimum_quorum_fraction` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "byzantine_node_ids": [
      "node2"
    ],
    "honest_node_ids": [
      "node1",
      "node3",
      "node4",
      "node5"
    ],
    "minimum_honest_consensus_count": 3,
    "minimum_quorum_fraction": 0.6,
    "primitive": "quorum_holds_despite_byzantine"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke"
}
```

## `reconnection_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ReconnectionClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/reconnection_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `honest_node_ids` | `array` | `yes` | - |
| `primitive` | `unspecified` | `yes` | Asserts that the reconnected node regained real peer connectivity, emitted successful reconnect telemetry, and matched the honest quorum tip. |
| `reconnected_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "honest_node_ids": [
      "node1",
      "node2"
    ],
    "primitive": "reconnection_clean",
    "reconnected_node_id": "node3"
  },
  "scenario": "runtime-substrate-compound-eclipse-recovery-example-smoke"
}
```

## `restart_readiness_complete`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RestartReadinessComplete`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/restart_readiness_complete.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "restart_readiness_complete"
  },
  "scenario": "client-example-restart-recovery-sync-amaru-20260918-mixed-1112"
}
```

## `reward_calculation_boundary_invariant`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RewardCalculationBoundaryInvariant`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/reward_calculation_boundary_invariant.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "reward_calculation_boundary_invariant"
  },
  "scenario": "runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke"
}
```

## `roundtrip_equals_original`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RoundtripEqualsOriginal`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/assertion/roundtrip_equals_original.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_inputs_parsed` | `integer` | `no` | Minimum number of successfully parsed inputs required before roundtrip equality can pass. |

### Example Invocation

```json
{
  "reference": {
    "primitive": "roundtrip_equals_original"
  },
  "scenario": "amaru-cbor-auxiliary-data-fuzz-structured"
}
```

## `runtime_aflpp_campaign`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeAflppCampaign`
- Supports: `cardano-node`
- Runtimes: `library`
- Schema: `primitives/load/runtime_aflpp_campaign.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `afl_mode` | `string` | `no` | - |
| `bin` | `string` | `yes` | - |
| `dict_path` | `string` | `no` | - |
| `env` | `object` | `no` | Extra environment variables for the target (e.g. DWARF_DECODER for the cardano-node coverage harness surface select). |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `replay_harness` | `string` | `yes` | - |
| `replay_target_id` | `string` | `yes` | - |
| `replay_targets` | `array` | `yes` | - |
| `rustup_toolchain` | `string` | `no` | - |
| `sanitizer` | `string` | `no` | - |
| `seconds` | `integer` | `yes` | - |
| `seed_dirs` | `array` | `yes` | - |
| `target_binary_path` | `string` | `no` | - |
| `target_implementation` | `string` | `no` | - |
| `target_triple` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `working_dir` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "bin": "dwarf-decode-any",
    "env": {
      "DWARF_AFL_FUZZ": "/opt/dwarf/afl-harness/afl-fuzz",
      "DWARF_DECODER": "applyblock",
      "DWARF_GENESIS_DIR": "/home/dwarf/dwarf-v4/antithesis/components/dwarf-adversary/ledger-genesis"
    },
    "output_dir": "/tmp/dwarf-cov-applyblock/out",
    "primitive": "runtime_aflpp_campaign",
    "replay_harness": "dwarf-decode-any",
    "replay_target_id": "cardano-node-cov-applyblock",
    "replay_targets": [
      "cardano-node"
    ],
    "seconds": 45,
    "seed_dirs": [
      "/opt/dwarf/afl-harness/corpora/conwaytx"
    ],
    "target_binary_path": "/opt/dwarf/afl-harness/dwarf-decode-any",
    "target_implementation": "cardano-node",
    "timeout_seconds": 300,
    "working_dir": "/tmp/dwarf-cov-applyblock"
  },
  "scenario": "cardano-node-cov-applyblock-aflpp-smoke"
}
```

## `runtime_amaru_measurement_calibration`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeAmaruMeasurementCalibration`
- Supports: `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_amaru_measurement_calibration.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `attempts` | `integer` | `no` | - |
| `case_set` | `string` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `progress_timeout_seconds` | `number` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "attempts": 120,
    "case_set": "accepted-and-rejected-v1",
    "expected_helper_exit": 0,
    "primitive": "runtime_amaru_measurement_calibration",
    "profile_id": "profile-r-amaru-measurement-stock-control",
    "progress_timeout_seconds": 120,
    "response_timeout_seconds": 2,
    "timeout_seconds": 300
  },
  "scenario": "amaru-measurement-boundary-control-stock"
}
```

## `runtime_attach_topology`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeAttachTopology`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/runtime_attach_topology.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `network_magic` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `require_all` | `boolean` | `no` | Fail if any core cardano-node container is missing. |
| `timeout_seconds` | `number` | `no` | - |
| `topology` | `string` | `no` | Upstream topology to bind to. |

### Example Invocation

```json
{
  "reference": {
    "network_magic": 42,
    "output_dir": "outputs/attach",
    "primitive": "runtime_attach_topology",
    "topology": "cardano_amaru"
  },
  "scenario": "consensus-chainhold-differential-stage1-tiebreak"
}
```

## `runtime_bandwidth_throttle`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBandwidthThrottle`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_resource_abuse_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_per_second` | `integer` | `no` | - |
| `duration_seconds` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `fill_target_free_bytes` | `integer` | `no` | - |
| `from_node` | `string` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `kilobits_per_second` | `integer` | `no` | - |
| `max_fill_bytes` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_usage_percent` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `to_node` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "from_node": "node1",
    "kilobits_per_second": 128,
    "output_dir": "outputs/bandwidth-throttle",
    "primitive": "runtime_bandwidth_throttle",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "to_node": "node2"
  },
  "scenario": "runtime-substrate-resource-sync-bandwidth-throttle-example-smoke"
}
```

## `runtime_blockfetch_continuity_failure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchContinuityFailure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/blockfetch-continuity",
    "primitive": "runtime_blockfetch_continuity_failure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-blockfetch-continuity-failure-example-smoke"
}
```

## `runtime_blockfetch_delay_success`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchDelaySuccess`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_blockfetch_delay_success.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_blockfetch_delay_success",
    "timeout_seconds": 120
  },
  "scenario": "m3-runtime-blockfetch-port-delay-bounded-success"
}
```

## `runtime_blockfetch_delay_timeout`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchDelayTimeout`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_blockfetch_delay_timeout.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_blockfetch_delay_timeout",
    "timeout_seconds": 120
  },
  "scenario": "m3-runtime-blockfetch-port-delay-timeout"
}
```

## `runtime_blockfetch_drop_isolated_peer`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchDropIsolatedPeer`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_blockfetch_drop_isolated_peer.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_blockfetch_drop_isolated_peer",
    "timeout_seconds": 60
  },
  "scenario": "m3-runtime-blockfetch-port-drop-isolated-peer"
}
```

## `runtime_blockfetch_drop_timeout`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchDropTimeout`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_blockfetch_drop_timeout.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_blockfetch_drop_timeout",
    "timeout_seconds": 60
  },
  "scenario": "m3-runtime-blockfetch-port-drop-timeout"
}
```

## `runtime_blockfetch_invalid_block_cbor`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchInvalidBlockCbor`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/blockfetch-invalid-block",
    "primitive": "runtime_blockfetch_invalid_block_cbor",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-blockfetch-invalid-block-cbor-example-smoke"
}
```

## `runtime_blockfetch_invalid_range`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchInvalidRange`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/blockfetch-invalid-range",
    "primitive": "runtime_blockfetch_invalid_range",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-blockfetch-invalid-range-example-smoke"
}
```

## `runtime_blockfetch_range_mismatch`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchRangeMismatch`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/blockfetch-range-mismatch",
    "primitive": "runtime_blockfetch_range_mismatch",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-blockfetch-range-mismatch-example-smoke"
}
```

## `runtime_blockfetch_range_pressure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockfetchRangePressure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/blockfetch-range-pressure",
    "primitive": "runtime_blockfetch_range_pressure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-blockfetch-range-pressure-example-smoke"
}
```

## `runtime_blocking_work_starvation`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBlockingWorkStarvation`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/runtime-starvation",
    "primitive": "runtime_blocking_work_starvation",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-recovery-starvation-example-smoke"
}
```

## `runtime_bootstrap_assumption_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBootstrapAssumptionProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/bootstrap-assumptions",
    "primitive": "runtime_bootstrap_assumption_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-bootstrap-assumption-probe-example-smoke"
}
```

## `runtime_bootstrap_topology_concentration`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBootstrapTopologyConcentration`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/bootstrap-topology",
    "primitive": "runtime_bootstrap_topology_concentration",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-bootstrap-topology-concentration-example-smoke"
}
```

## `runtime_bundle_attestation`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleAttestation`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/load/runtime_bundle_attestation.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `python_bin` | `string` | `no` | - |
| `signing_actor` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "output_dir": "outputs/attestation",
    "primitive": "runtime_bundle_attestation",
    "signing_actor": "dwarf",
    "timeout_seconds": 120
  },
  "scenario": "runtime-bundle-attestation-example-smoke"
}
```

## `runtime_bundle_chain`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleChain`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_bundle_chain.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `actor` | `string` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `key_path` | `string` | `no` | - |
| `operator_notes` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `reason_code` | `string` | `yes` | - |
| `reason_text` | `string` | `yes` | - |
| `runs_dir` | `string` | `no` | - |
| `signature_primitive` | `string` | `no` | - |
| `signing_actor` | `string` | `no` | - |
| `source_surface` | `string` | `no` | - |
| `target_run_id` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "actor": "dwarf",
    "expected_helper_exit": 0,
    "operator_notes": "primitive exercise path",
    "primitive": "runtime_bundle_chain",
    "reason_code": "divergence",
    "reason_text": "promote runtime bundle for operator review",
    "signature_primitive": "runtime_bundle_promote",
    "signing_actor": "dwarf",
    "timeout_seconds": 120
  },
  "scenario": "phase3-runtime-profile-a-bundle-chain-primitive"
}
```

## `runtime_bundle_chain_verify`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleChainVerify`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/runtime_bundle_chain_verify.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `python_bin` | `string` | `no` | - |
| `runs_dir` | `string` | `yes` | - |
| `target_run_id` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "output_dir": "outputs/chain-verify",
    "primitive": "runtime_bundle_chain_verify",
    "runs_dir": "~/dwarf-fw/runs",
    "target_run_id": "20260427T092200Z-audittraildemo",
    "timeout_seconds": 120
  },
  "scenario": "runtime-bundle-chain-verify-example-smoke"
}
```

## `runtime_bundle_dedupe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleDedupe`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_bundle_dedupe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runs_dir` | `string` | `no` | - |
| `signature_primitive` | `string` | `no` | - |
| `target_run_id` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_bundle_diff`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleDiff`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `single-node`, `devnet`
- Schema: `primitives/load/runtime_bundle_diff.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `compare_relpaths` | `array` | `yes` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `left_run_id` | `string` | `yes` | - |
| `python_bin` | `string` | `no` | - |
| `right_run_id` | `string` | `yes` | - |
| `runs_dir` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "compare_relpaths": [
      "outputs/runtime-cardano-lsq-extract/result.json"
    ],
    "expected_helper_exit": 0,
    "left_run_id": "20260427T064030Z-33297ed4",
    "primitive": "runtime_bundle_diff",
    "right_run_id": "20260427T075729Z-9262386a",
    "runs_dir": "~/dwarf-fw/runs",
    "timeout_seconds": 120
  },
  "scenario": "runtime-bundle-diff-example-divergence"
}
```

## `runtime_bundle_export`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleExport`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_bundle_export.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `key_path` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `signing_actor` | `string` | `no` | - |
| `target_run_id` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_bundle_export_sarif`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleExportSarif`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `single-node`, `devnet`
- Schema: `primitives/load/runtime_bundle_export_sarif.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runs_dir` | `string` | `no` | - |
| `schema_path` | `string` | `no` | - |
| `target_run_id` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_bundle_export_sarif",
    "runs_dir": "~/dwarf-fw/runs",
    "target_run_id": "20260427T082605Z-98e8aae2",
    "timeout_seconds": 120
  },
  "scenario": "runtime-bundle-export-sarif-example-smoke"
}
```

## `runtime_bundle_promote`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundlePromote`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_bundle_promote.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `actor` | `string` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `operator_notes` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `reason_code` | `string` | `yes` | - |
| `reason_text` | `string` | `yes` | - |
| `source_surface` | `string` | `no` | - |
| `target_run_id` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "actor": "dwarf",
    "expected_helper_exit": 0,
    "operator_notes": "primitive exercise path",
    "primitive": "runtime_bundle_promote",
    "reason_code": "divergence",
    "reason_text": "promote runtime bundle for operator review",
    "timeout_seconds": 120
  },
  "scenario": "phase3-runtime-profile-a-bundle-promote-primitive"
}
```

## `runtime_bundle_sign`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleSign`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_bundle_sign.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `key_path` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `signing_actor` | `string` | `no` | - |
| `target_run_id` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_bundle_sign",
    "signing_actor": "dwarf",
    "timeout_seconds": 120
  },
  "scenario": "phase3-runtime-profile-a-bundle-sign-primitive"
}
```

## `runtime_bundle_summary_compose`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleSummaryCompose`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/runtime_bundle_summary_compose.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `bundle_ids` | `array` | `yes` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `python_bin` | `string` | `no` | - |
| `runs_dir` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "bundle_ids": [
      "20260427T074053Z-ba7c46cc",
      "20260427T085937Z-8e388072",
      "20260427T105341Z-0b24e9a8",
      "20260427T095705Z-e51bf913",
      "20260427T101446Z-289d4fbb",
      "20260427T101446Z-977cfbf5",
      "20260427T091106Z-3bce7cb4",
      "20260427T110547Z-419db366",
      "20260427T120147Z-8f9884ec"
    ],
    "expected_helper_exit": 0,
    "output_dir": "outputs/bundle-summary",
    "primitive": "runtime_bundle_summary_compose",
    "runs_dir": "~/dwarf-fw/runs",
    "timeout_seconds": 180
  },
  "scenario": "runtime-bundle-summary-compose-example-smoke"
}
```

## `runtime_bundle_timeline`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleTimeline`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/runtime_bundle_timeline.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `bundle_ids` | `array` | `yes` | - |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runs_dir` | `string` | `no` | - |
| `scenario_id_filters` | `array` | `no` | - |
| `signature_token_filters` | `array` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "bundle_ids": [
      "20260427T074053Z-ba7c46cc",
      "20260427T075930Z-8a754454",
      "20260427T082605Z-98e8aae2",
      "20260427T083724Z-176c5cf7",
      "20260427T091106Z-3bce7cb4",
      "20260427T094439Z-5b4d3858",
      "20260427T105341Z-0b24e9a8"
    ],
    "output_dir": "outputs/bundle-timeline",
    "primitive": "runtime_bundle_timeline",
    "runs_dir": "~/dwarf-fw/runs",
    "timeout_seconds": 120
  },
  "scenario": "runtime-bundle-timeline-example-smoke"
}
```

## `runtime_bundle_triage`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeBundleTriage`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_bundle_triage.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `actor` | `string` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `operator_notes` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `reason_code` | `string` | `no` | - |
| `reason_text` | `string` | `no` | - |
| `runs_dir` | `string` | `no` | - |
| `signature_primitive` | `string` | `no` | - |
| `source_surface` | `string` | `no` | - |
| `target_run_id` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "actor": "dwarf",
    "expected_helper_exit": 0,
    "operator_notes": "primitive exercise path",
    "primitive": "runtime_bundle_triage",
    "reason_code": "divergence",
    "reason_text": "promote runtime bundle for operator review",
    "signature_primitive": "runtime_bundle_promote",
    "timeout_seconds": 120
  },
  "scenario": "phase3-runtime-profile-a-bundle-triage-primitive"
}
```

## `runtime_byzantine_cardano_node`

- Family: `fault`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeByzantineCardanoNode`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/fault/runtime_byzantine_cardano_node.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `behavior` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `mutate_after_segments` | `integer` | `no` | - |
| `mutation_direction` | `string` | `no` | - |
| `mutation_mode` | `string` | `no` | - |
| `mutation_protocol` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `target_node_id` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_address` | `string` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_cardano_cbor_dataset_differential`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeCardanoCborDatasetDifferential`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/runtime_cardano_cbor_dataset_differential.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `candidate_target_id` | `string` | `yes` | - |
| `dataset_dir` | `string` | `yes` | - |
| `dataset_repo_dir` | `string` | `yes` | - |
| `docker_binary` | `string` | `no` | - |
| `era` | `string` | `yes` | - |
| `expect_exit` | `integer` | `no` | - |
| `expected_source_revision` | `string` | `yes` | - |
| `manifests_dir` | `string` | `yes` | - |
| `output_dir` | `string` | `yes` | - |
| `per_input_timeout_seconds` | `number` | `no` | - |
| `reference_target_id` | `string` | `yes` | - |
| `reference_verifier_binary` | `string` | `no` | - |
| `reference_verifier_image` | `string` | `no` | - |
| `reference_verifier_platform` | `string` | `no` | - |
| `reference_verifier_timeout_seconds` | `number` | `no` | - |
| `rule` | `string` | `yes` | - |
| `samples_per_category` | `integer` | `no` | - |
| `source_repository` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "candidate_target_id": "amaru-cbor-decode-plutus-data",
    "dataset_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO/dataset/conway-123-100",
    "dataset_repo_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO",
    "era": "conway",
    "expected_source_revision": "a7561cd063550c2218898571520f14c3674efe91",
    "manifests_dir": "targets/manifests",
    "output_dir": "outputs/cardano-cbor-dataset-plutus-data-differential",
    "per_input_timeout_seconds": 5,
    "primitive": "runtime_cardano_cbor_dataset_differential",
    "reference_target_id": "cardano-node-cbor-decode-plutus-data",
    "reference_verifier_image": "cardano-cbor-dataset:a7561cd",
    "reference_verifier_platform": "linux/amd64",
    "reference_verifier_timeout_seconds": 600,
    "rule": "plutus_data",
    "samples_per_category": 25,
    "source_repository": "https://github.com/r2rationality/cardano-cbor-dataset.git",
    "timeout_seconds": 1200
  },
  "scenario": "cardano-amaru-cbor-dataset-plutus-data-differential"
}
```

## `runtime_cardano_lsq_extract`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeCardanoLsqExtract`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/load/runtime_cardano_lsq_extract.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `debug_raw_response_path` | `string` | `no` | - |
| `era` | `unspecified` | `yes` | - |
| `expect_exit` | `integer` | `no` | - |
| `network_magic` | `integer` | `yes` | - |
| `output_path` | `string` | `yes` | - |
| `result_json_path` | `string` | `no` | - |
| `socket_path` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "debug_raw_response_path": "outputs/runtime-cardano-lsq-extract/raw-response.bin",
    "era": 6,
    "expect_exit": 0,
    "network_magic": 42,
    "output_path": "outputs/runtime-cardano-lsq-extract/debug-epoch-state.cbor",
    "primitive": "runtime_cardano_lsq_extract",
    "result_json_path": "outputs/runtime-cardano-lsq-extract/result.json",
    "socket_path": "/tmp/ada2-testnet42-socket/node1.sock",
    "timeout_seconds": 180
  },
  "scenario": "cardano-lsq-extract-debug-epoch-state"
}
```

## `runtime_cardano_measurement_calibration`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeCardanoMeasurementCalibration`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_cardano_measurement_calibration.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `attempts` | `integer` | `no` | - |
| `case_set` | `string` | `no` | - |
| `epoch_observation_seconds` | `number` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `observation_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `plutus_transactions` | `integer` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `trace_timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "attempts": 100,
    "expected_helper_exit": 0,
    "observation_seconds": 2,
    "primitive": "runtime_cardano_measurement_calibration",
    "profile_id": "profile-s-cardano-measurement-stock-control",
    "response_timeout_seconds": 2,
    "timeout_seconds": 180,
    "trace_timeout_seconds": 20
  },
  "scenario": "cardano-measurement-e2e-stock"
}
```

## `runtime_chain_switch_inject`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeChainSwitchInject`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_recovery_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `requested_rollback_slots` | `integer` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `security_parameter_k` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/chain-switch",
    "primitive": "runtime_chain_switch_inject",
    "target_node": "node3"
  },
  "scenario": "runtime-substrate-compound-local-query-recovery-example-smoke"
}
```

## `runtime_chainsync_nonincrementing_height`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeChainsyncNonincrementingHeight`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/chainsync-height",
    "primitive": "runtime_chainsync_nonincrementing_height",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-chainsync-nonincrementing-height-example-smoke"
}
```

## `runtime_chainsync_nonmonotonic_slot`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeChainsyncNonmonotonicSlot`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/chainsync-slot",
    "primitive": "runtime_chainsync_nonmonotonic_slot",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-chainsync-nonmonotonic-slot-example-smoke"
}
```

## `runtime_chainsync_parent_discontinuity`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeChainsyncParentDiscontinuity`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/chainsync-parent-discontinuity",
    "primitive": "runtime_chainsync_parent_discontinuity",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-chainsync-parent-discontinuity-example-smoke"
}
```

## `runtime_chainsync_responder_fork_switch`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeChainsyncResponderForkSwitch`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_chainsync_blockfetch_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `activity_timeout_seconds` | `number` | `no` | - |
| `configured_limit` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |
| `upstream_node_id` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/private-fork",
    "primitive": "runtime_chainsync_responder_fork_switch",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "consensus-threshold-delay-probe"
}
```

## `runtime_client_blockfetch_burst`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeClientBlockfetchBurst`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_client_blockfetch_burst.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_client_blockfetch_burst",
    "timeout_seconds": 180
  },
  "scenario": "m3-runtime-blockfetch-historical-range-burst"
}
```

## `runtime_client_blockfetch_multi_peer`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeClientBlockfetchMultiPeer`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_client_blockfetch_multi_peer.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_client_blockfetch_multi_peer",
    "timeout_seconds": 180
  },
  "scenario": "m3-runtime-blockfetch-multi-peer-historical-range"
}
```

## `runtime_client_chainsync_burst`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeClientChainsyncBurst`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_client_chainsync_burst.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_client_chainsync_burst",
    "timeout_seconds": 180
  },
  "scenario": "m3-runtime-chainsync-historical-point-burst"
}
```

## `runtime_client_chainsync_multi_peer`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeClientChainsyncMultiPeer`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_client_chainsync_multi_peer.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_client_chainsync_multi_peer",
    "timeout_seconds": 180
  },
  "scenario": "m3-runtime-chainsync-multi-peer-historical-point"
}
```

## `runtime_compose_substrate`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeComposeSubstrate`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_compose_substrate.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `compose_project` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_root` | `string` | `no` | - |
| `timeout_seconds` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/substrate-compose",
    "primitive": "runtime_compose_substrate"
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `runtime_connection_state`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeConnectionState`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_connection_state.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `connect_attempts` | `integer` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `snapshot_name` | `string` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_container_runtime_inspect`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeContainerRuntimeInspect`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_container_runtime_inspect.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/container-runtime-inspect",
    "primitive": "runtime_container_runtime_inspect",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "timeout_seconds": 120
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke"
}
```

## `runtime_controlled_chain_progress_window`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeControlledChainProgressWindow`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_controlled_chain_progress_window.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |
| `max_oscillation_episodes` | `integer` | `no` | - |
| `max_oscillation_transitions` | `integer` | `no` | - |
| `minimum_adopted_blocks` | `integer` | `no` | - |
| `minimum_blocks` | `integer` | `no` | - |
| `minimum_convergence_blocks` | `integer` | `no` | - |
| `peer_policy` | `string` | `no` | - |
| `poll_interval_seconds` | `number` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `progress_contract` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `warm_up_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "duration_seconds": 180,
    "max_oscillation_episodes": 3,
    "max_oscillation_transitions": 8,
    "minimum_adopted_blocks": 30,
    "minimum_convergence_blocks": 3,
    "primitive": "runtime_controlled_chain_progress_window",
    "profile_id": "profile-u-amaru-measurement-nanoseconds-v2",
    "progress_contract": "canonical-progress-v2",
    "warm_up_seconds": 30
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `runtime_controlled_plutus_transactions`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeControlledPlutusTransactions`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_controlled_plutus_transactions.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `measurement_implementation` | `enum` | `yes` | - |
| `output_dir` | `string` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expect_exit": 0,
    "measurement_implementation": "amaru",
    "output_dir": "outputs/controlled-plutus-transactions",
    "primitive": "runtime_controlled_plutus_transactions",
    "profile_id": "profile-za-amaru-20260918-plutus-v2",
    "timeout_seconds": 3600
  },
  "scenario": "client-example-plutus-vm-amaru-onchain-v2-20260918"
}
```

## `runtime_controlled_simple_transfers`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeControlledSimpleTransfers`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_controlled_simple_transfers.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `attempt_count` | `integer` | `yes` | - |
| `expect_exit` | `integer` | `no` | - |
| `measurement_implementation` | `enum` | `yes` | - |
| `outcome_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "attempt_count": 35,
    "expect_exit": 0,
    "measurement_implementation": "amaru",
    "outcome_timeout_seconds": 90,
    "output_dir": "outputs/controlled-simple-transfers",
    "primitive": "runtime_controlled_simple_transfers",
    "profile_id": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
    "timeout_seconds": 3600
  },
  "scenario": "client-example-simple-transfer-amaru-20260918-mixed-1112"
}
```

## `runtime_controlled_sync_range`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeControlledSyncRange`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_controlled_sync_range.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |
| `minimum_adopted_blocks` | `integer` | `no` | - |
| `minimum_blocks` | `integer` | `no` | - |
| `peer_policy` | `string` | `no` | - |
| `poll_interval_seconds` | `number` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `warm_up_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_blocks": 5,
    "peer_policy": "single-controlled-producer",
    "poll_interval_seconds": 1,
    "primitive": "runtime_controlled_sync_range",
    "profile_id": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
    "timeout_seconds": 180
  },
  "scenario": "client-example-restart-recovery-sync-amaru-20260918-mixed-1112"
}
```

## `runtime_credential_ceremony`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeCredentialCeremony`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`, `devnet`
- Schema: `primitives/load/runtime_credential_ceremony.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cardano_testnet_bin` | `string` | `no` | - |
| `deterministic_seed` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `kes_period_window` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `pool_count` | `integer` | `no` | - |
| `testnet_magic` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "deterministic_seed": "0xCREDC0DE",
    "kes_period_window": 12,
    "output_dir": "outputs/credential-ceremony",
    "pool_count": 1,
    "primitive": "runtime_credential_ceremony",
    "testnet_magic": 42,
    "timeout_seconds": 300
  },
  "scenario": "runtime-credential-ceremony-example-smoke"
}
```

## `runtime_disk_full_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeDiskFullProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_resource_abuse_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_per_second` | `integer` | `no` | - |
| `duration_seconds` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `fill_target_free_bytes` | `integer` | `no` | - |
| `from_node` | `string` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `kilobits_per_second` | `integer` | `no` | - |
| `max_fill_bytes` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_usage_percent` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `to_node` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/disk-full",
    "primitive": "runtime_disk_full_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1",
    "target_usage_percent": 98
  },
  "scenario": "runtime-substrate-resource-disk-full-during-sync-example-smoke"
}
```

## `runtime_duplex_promotion_pressure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeDuplexPromotionPressure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/duplex-promotion-pressure",
    "primitive": "runtime_duplex_promotion_pressure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-duplex-promotion-pressure-example-smoke"
}
```

## `runtime_force_epoch_boundary`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeForceEpochBoundary`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_epoch_boundary_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_slot` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expect_exit": 0,
    "output_dir": "outputs/epoch-boundary",
    "primitive": "runtime_force_epoch_boundary",
    "runtime_metadata_path": "outputs/attach/runtime.json",
    "target_node": "amaru-relay-2",
    "target_slot": 375,
    "timeout_seconds": 240
  },
  "scenario": "consensus-state-lifecycle-bootstrap-differential"
}
```

## `runtime_force_hf_boundary`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeForceHfBoundary`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_force_hf_boundary.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_slot` | `integer` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/force-hf-boundary",
    "primitive": "runtime_force_hf_boundary",
    "target_slot": 500
  },
  "scenario": "runtime-substrate-compound-hf-txsubmission-example-smoke"
}
```

## `runtime_force_rollback`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeForceRollback`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_recovery_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `requested_rollback_slots` | `integer` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `security_parameter_k` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/force-rollback",
    "primitive": "runtime_force_rollback",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node2"
  },
  "scenario": "runtime-substrate-compound-eclipse-recovery-example-smoke"
}
```

## `runtime_forensic_snapshot`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeForensicSnapshot`
- Supports: `cardano-node`, `amaru`
- Runtimes: `library`
- Schema: `primitives/load/runtime_forensic_snapshot.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `output_format` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `run_ids` | `array` | `yes` | - |
| `runs_dir` | `string` | `yes` | - |
| `tag_filters` | `array` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "output_dir": "outputs/forensic-snapshot",
    "output_format": "tar.gz",
    "primitive": "runtime_forensic_snapshot",
    "run_ids": [
      "20260427T092200Z-audittraildemo",
      "20260427T074053Z-ba7c46cc"
    ],
    "runs_dir": "~/dwarf-fw/runs",
    "timeout_seconds": 180
  },
  "scenario": "runtime-forensic-snapshot-example-smoke"
}
```

## `runtime_generated_node_freeze_check`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeGeneratedNodeFreezeCheck`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_generated_node_freeze_check.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `blocked_node` | `string` | `yes` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `healthy_nodes` | `array` | `yes` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_root` | `string` | `yes` | - |
| `sample_seconds` | `number` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "blocked_node": "node2",
    "expected_helper_exit": 0,
    "healthy_nodes": [
      "node1",
      "node3"
    ],
    "primitive": "runtime_generated_node_freeze_check",
    "runtime_root": "~/cardano-profiles/profile-i-generated-haskell3",
    "sample_seconds": 2,
    "timeout_seconds": 60
  },
  "scenario": "phase3-runtime-generated-haskell3-node2-freeze-primitive"
}
```

## `runtime_generated_node_port_drop_check`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeGeneratedNodePortDropCheck`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_generated_node_port_drop_check.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `blocked_node` | `string` | `yes` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `healthy_nodes` | `array` | `yes` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_root` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "blocked_node": "node2",
    "expected_helper_exit": 0,
    "healthy_nodes": [
      "node1",
      "node3"
    ],
    "primitive": "runtime_generated_node_port_drop_check",
    "runtime_root": "~/cardano-profiles/profile-i-generated-haskell3",
    "timeout_seconds": 60
  },
  "scenario": "phase3-runtime-generated-haskell3-node2-port-drop-primitive"
}
```

## `runtime_generated_node_recovery_check`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeGeneratedNodeRecoveryCheck`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_generated_node_recovery_check.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `healthy_nodes` | `array` | `yes` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `recovered_node` | `string` | `yes` | - |
| `required_phase_id` | `string` | `yes` | - |
| `runtime_root` | `string` | `yes` | - |
| `sample_seconds` | `number` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_genesis_mode_simulate`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeGenesisModeSimulate`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_genesis_mode_simulate.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/genesis-mode",
    "primitive": "runtime_genesis_mode_simulate",
    "target_node": "node2"
  },
  "scenario": "runtime-substrate-era-transition-example-smoke"
}
```

## `runtime_handshake_version_negotiation_pressure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeHandshakeVersionNegotiationPressure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/handshake-pressure",
    "primitive": "runtime_handshake_version_negotiation_pressure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-real-adapter-handshake-pressure-example-smoke"
}
```

## `runtime_haskell_gc_capture`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeHaskellGcCapture`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_haskell_gc_capture.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `connect_attempts` | `integer` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `restore_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `sample_seconds` | `number` | `no` | - |
| `startup_timeout_seconds` | `number` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "connect_attempts": 2,
    "expected_helper_exit": 0,
    "primitive": "runtime_haskell_gc_capture",
    "restore_timeout_seconds": 30,
    "runtime_metadata_path": "~/cardano-profiles/profile-i-generated-haskell3/runtime.json",
    "sample_seconds": 2.0,
    "startup_timeout_seconds": 30,
    "target_host": "127.0.0.1",
    "target_node": "node2",
    "timeout_seconds": 180
  },
  "scenario": "phase3-runtime-generated-haskell3-gc-capture-primitive"
}
```

## `runtime_inject_hot_warm_churn`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeInjectHotWarmChurn`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_topology_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `adversary_node` | `string` | `no` | - |
| `events_per_hour` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `sybil_node_ids` | `array` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "events_per_hour": 8,
    "output_dir": "outputs/hot-warm-churn",
    "primitive": "runtime_inject_hot_warm_churn",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node2"
  },
  "scenario": "runtime-substrate-compound-eclipse-txsubmission-example-smoke"
}
```

## `runtime_install_version`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeInstallVersion`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_install_version.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `timeout_seconds` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/substrate-install",
    "primitive": "runtime_install_version"
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `runtime_keepalive_failure_cascade`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeKeepaliveFailureCascade`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/keepalive-failure",
    "primitive": "runtime_keepalive_failure_cascade",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-keepalive-topology-capture-example-smoke"
}
```

## `runtime_kill_node`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeKillNode`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_recovery_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `requested_rollback_slots` | `integer` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `security_parameter_k` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/kill-node",
    "primitive": "runtime_kill_node",
    "target_node": "amaru-relay-1"
  },
  "scenario": "consensus-restart-rollback-in-future-differential"
}
```

## `runtime_live_implementation_baseline`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeLiveImplementationBaseline`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_live_implementation_baseline.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_root` | `string` | `yes` | - |
| `scenario_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_live_implementation_baseline",
    "runtime_root": "~/cardano-profiles/profile-c-mixed-haskell-amaru-minimal",
    "timeout_seconds": 60
  },
  "scenario": "phase1-runtime-live-amaru-parity-baseline"
}
```

## `runtime_local_query_stress`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeLocalQueryStress`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/local-query-stress",
    "primitive": "runtime_local_query_stress",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-local-query-recovery-example-smoke"
}
```

## `runtime_local_submit_stress`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeLocalSubmitStress`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/local-submit-stress",
    "primitive": "runtime_local_submit_stress",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-local-submit-stress-example-smoke"
}
```

## `runtime_localtxmonitor_fault`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeLocalTxMonitorFault`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_protocol_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `localtxmonitor_decoder_path` | `string` | `no` | - |
| `localtxmonitor_state_corpus` | `string` | `no` | - |
| `network_magic` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `peersharing_decoder_path` | `string` | `no` | - |
| `peersharing_state_corpus` | `string` | `no` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/localtxmonitor-fault",
    "primitive": "runtime_localtxmonitor_fault",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-localtxmonitor-fault-example-smoke"
}
```

## `runtime_malformed_input_differential`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMalformedInputDifferential`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/malformed-input-differential",
    "primitive": "runtime_malformed_input_differential",
    "reference_node": "node2",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `runtime_mark_baseline_window`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMarkBaselineWindow`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/runtime_mark_baseline_window.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "duration_seconds": 35,
    "primitive": "runtime_mark_baseline_window"
  },
  "scenario": "client-example-invalid-mini-protocol-amaru-20260918-mixed-1112"
}
```

## `runtime_mark_hostile_window`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMarkHostileWindow`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_mark_hostile_window.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "runtime_mark_hostile_window"
  },
  "scenario": "client-example-invalid-mini-protocol-amaru-20260918-mixed-1112"
}
```

## `runtime_mark_recovery_window`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMarkRecoveryWindow`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_mark_recovery_window.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "duration_seconds": 35,
    "primitive": "runtime_mark_recovery_window"
  },
  "scenario": "client-example-invalid-mini-protocol-amaru-20260918-mixed-1112"
}
```

## `runtime_mempool_failure_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMempoolFailureProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_txsubmission_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/mempool-failure",
    "primitive": "runtime_mempool_failure_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-mempool-failure-containment-example-smoke"
}
```

## `runtime_mempool_relay_pressure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMempoolRelayPressure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/mempool-relay-pressure",
    "primitive": "runtime_mempool_relay_pressure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke"
}
```

## `runtime_multi_node_observation`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMultiNodeObservation`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/load/runtime_multi_node_observation.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cardano_cli` | `string` | `no` | - |
| `connect_attempts` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `network_magic` | `integer` | `no` | - |
| `node_ids` | `array` | `yes` | - |
| `observation_primitives` | `array` | `yes` | - |
| `observation_window_seconds` | `number` | `yes` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `sample_interval_seconds` | `number` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "network_magic": 42,
    "node_ids": [
      "node1",
      "node2"
    ],
    "observation_primitives": [
      "tip_state",
      "connection_state"
    ],
    "observation_window_seconds": 8,
    "output_dir": "outputs/multi-node-observation",
    "primitive": "runtime_multi_node_observation",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "sample_interval_seconds": 1
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `runtime_mux_ingress_overrun`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeMuxIngressOverrun`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_exposure_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_ceiling_pct` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `hard_limit` | `integer` | `no` | - |
| `max_keepalive_failures` | `integer` | `no` | - |
| `minimum_required_trustable_peers` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `submit_queue_depth_limit` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/mux-ingress-overrun",
    "primitive": "runtime_mux_ingress_overrun",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-mux-ingress-overrun-example-smoke"
}
```

## `runtime_network_impairment`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeNetworkImpairment`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_network_impairment.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `from_node` | `string` | `yes` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `jitter_ms` | `integer` | `no` | - |
| `latency_ms` | `integer` | `no` | - |
| `loss_percent` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `partition` | `boolean` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `to_node` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "from_node": "adv1",
    "jitter_ms": 0,
    "latency_ms": 100,
    "loss_percent": 0,
    "output_dir": "outputs/net-impair",
    "partition": false,
    "primitive": "runtime_network_impairment",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "to_node": "node1"
  },
  "scenario": "consensus-threshold-delay-probe"
}
```

## `runtime_network_partition`

- Family: `fault`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeNetworkPartition`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/fault/runtime_network_partition.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `container_name` | `string` | `no` | Override: partition this container directly (skips runtime.json lookup). |
| `network_name` | `string` | `no` | Docker network to disconnect from / reconnect to. |
| `output_dir` | `string` | `no` | - |
| `partition_seconds` | `number` | `no` | How long to hold the partition (drives fork depth). |
| `runtime_metadata_path` | `string` | `no` | runtime.json to resolve target_node -> container_name. |
| `settle_seconds` | `number` | `no` | Post-heal settle time before the load phase observes. |
| `target_node` | `string` | `no` | Node id/name to partition (resolved to its container). |

### Example Invocation

_No authored scenario example found._

## `runtime_observability_log_baseline`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeObservabilityLogBaseline`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_observability_log_baseline.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_observability_log_baseline",
    "timeout_seconds": 120
  },
  "scenario": "m3-observability-log-baseline"
}
```

## `runtime_observability_trace_settings_baseline`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeObservabilityTraceSettingsBaseline`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_observability_trace_settings_baseline.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_observability_trace_settings_baseline",
    "timeout_seconds": 120
  },
  "scenario": "m3-observability-trace-settings-baseline"
}
```

## `runtime_opcert_header_cases`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeOpcertHeaderCases`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_opcert_header_cases.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `case_ids` | `array` | `no` | - |
| `consumer_port` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `listen_port` | `integer` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `peer_bin` | `string` | `no` | - |
| `per_case_timeout` | `number` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "case_ids": [
      "valid-control",
      "counter-behind",
      "kes-after-window"
    ],
    "expect_exit": 0,
    "output_dir": "outputs/opcert-header-cases",
    "primitive": "runtime_opcert_header_cases",
    "profile_id": "profile-opcert-aged-kes-cardano-1112",
    "target_node": "node1",
    "timeout_seconds": 3600
  },
  "scenario": "opcert-header-validation-boundary-cardano-1112"
}
```

## `runtime_opcert_header_soak`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeOpcertHeaderSoak`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_opcert_header_soak.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `family` | `string` | `yes` | - |
| `kes_evolution_aged` | `boolean` | `no` | kes-evolution only: when true, draw both signs (under+over evolution) -- needs an aged-KES devnet. Default false draws over-evolution only (always reachable on a fresh devnet). |
| `output_dir` | `string` | `no` | - |
| `peer_bin` | `string` | `no` | - |
| `per_iteration_timeout` | `number` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `restart_k` | `integer` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `seed` | `integer` | `yes` | - |
| `target_node` | `string` | `no` | - |
| `target_nodes` | `array` | `no` | - |
| `time_budget_seconds` | `number` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expect_exit": 0,
    "family": "accept-boundary",
    "output_dir": "outputs/opcert-soak",
    "primitive": "runtime_opcert_header_soak",
    "profile_id": "profile-z-amaru-20260918-nanoseconds-v3",
    "seed": 525252525,
    "target_node": "amaru-relay-1",
    "time_budget_seconds": 5400,
    "timeout_seconds": 6300
  },
  "scenario": "opcert-soak-accept-boundary-amaru-20260918"
}
```

## `runtime_overlay_slot_forging`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeOverlaySlotForging`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/overlay-slot-forging",
    "primitive": "runtime_overlay_slot_forging",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-parser-overlay-forging-example-smoke"
}
```

## `runtime_panic_path_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePanicPathProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/panic-path",
    "primitive": "runtime_panic_path_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-panic-bootstrap-example-smoke"
}
```

## `runtime_parser_bounds_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeParserBoundsProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/parser-bounds",
    "primitive": "runtime_parser_bounds_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-parser-overlay-forging-example-smoke"
}
```

## `runtime_partition_rejoin`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePartitionRejoin`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_partition_rejoin.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_partition_rejoin",
    "timeout_seconds": 240
  },
  "scenario": "m3-runtime-node2-node3-partition-rejoin"
}
```

## `runtime_pcap_capture`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePcapCapture`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_pcap_capture.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `connect_attempts` | `integer` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `interface` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `settle_seconds` | `number` | `no` | - |
| `startup_seconds` | `number` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_ports` | `array` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `workload_mode` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "connect_attempts": 3,
    "expected_helper_exit": 0,
    "interface": "lo",
    "primitive": "runtime_pcap_capture",
    "target_host": "127.0.0.1",
    "target_ports": [
      33001,
      33002,
      33003
    ],
    "timeout_seconds": 120,
    "workload_mode": "tcp-connect-burst"
  },
  "scenario": "phase3-runtime-profile-a-pcap-capture-primitive"
}
```

## `runtime_peer_session_health`

- Family: `probe`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePeerSessionHealth`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/probe/runtime_peer_session_health.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `report_path` | `string` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_peersharing_fault`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePeerSharingFault`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_protocol_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `localtxmonitor_decoder_path` | `string` | `no` | - |
| `localtxmonitor_state_corpus` | `string` | `no` | - |
| `network_magic` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `peersharing_decoder_path` | `string` | `no` | - |
| `peersharing_state_corpus` | `string` | `no` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/peersharing-fault",
    "primitive": "runtime_peersharing_fault",
    "reference_node": "node2",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-peersharing-fault-example-smoke"
}
```

## `runtime_perturb_ledger_peer_weights`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePerturbLedgerPeerWeights`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_topology_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `adversary_node` | `string` | `no` | - |
| `events_per_hour` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `sybil_node_ids` | `array` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/ledger-peer-weights",
    "primitive": "runtime_perturb_ledger_peer_weights",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node3"
  },
  "scenario": "runtime-substrate-eclipse-topology-example-smoke"
}
```

## `runtime_plutus_phase2_differential_observation`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePlutusPhase2DifferentialObservation`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_plutus_phase2_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `ex_units_override` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `is_valid_flag_override` | `string` | `no` | - |
| `observer_node` | `string` | `yes` | - |
| `output_dir` | `string` | `yes` | - |
| `probe_case` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "observer_node": "node2",
    "output_dir": "outputs/plutus-phase2-differential",
    "primitive": "runtime_plutus_phase2_differential_observation",
    "probe_case": "conway-scriptcontext-txinfo",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "conway-scriptcontext-txinfo-fidelity-differential-amaru-cardano-node"
}
```

## `runtime_plutus_phase2_submit_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePlutusPhase2SubmitProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_plutus_phase2_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `ex_units_override` | `integer` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `is_valid_flag_override` | `string` | `no` | - |
| `observer_node` | `string` | `yes` | - |
| `output_dir` | `string` | `yes` | - |
| `probe_case` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "observer_node": "node2",
    "output_dir": "outputs/plutus-phase2-submit",
    "primitive": "runtime_plutus_phase2_submit_probe",
    "probe_case": "donotintervene-retry-clean",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-plutus-phase2-donointervene-retry-clean-example-smoke"
}
```

## `runtime_praos_header_assertion_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePraosHeaderAssertionProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/praos-header-assertion",
    "primitive": "runtime_praos_header_assertion_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-praos-header-assertion-example-smoke"
}
```

## `runtime_preview_parity_baseline`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePreviewParityBaseline`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_preview_parity_baseline.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `preview_amaru_root` | `string` | `no` | - |
| `preview_cardano_node_root` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `sample_seconds` | `integer` | `no` | - |
| `scenario_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_preview_parity_baseline",
    "sample_seconds": 20,
    "timeout_seconds": 60
  },
  "scenario": "phase3-runtime-preview-parity-baseline-haskell-primitive"
}
```

## `runtime_preview_upstream_delay`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePreviewUpstreamDelay`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_preview_upstream_delay.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `delay_ms` | `integer` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `fault_seconds` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `jitter_ms` | `integer` | `no` | - |
| `preview_amaru_root` | `string` | `no` | - |
| `preview_cardano_node_root` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `recovery_seconds` | `integer` | `no` | - |
| `scenario_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "delay_ms": 400,
    "expected_helper_exit": 0,
    "fault_seconds": 15,
    "jitter_ms": 100,
    "primitive": "runtime_preview_upstream_delay",
    "recovery_seconds": 20,
    "timeout_seconds": 90
  },
  "scenario": "phase3-runtime-preview-upstream-delay-haskell-primitive"
}
```

## `runtime_preview_upstream_drop`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePreviewUpstreamDrop`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_preview_upstream_drop.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `fault_seconds` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `preview_amaru_root` | `string` | `no` | - |
| `preview_cardano_node_root` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `recovery_seconds` | `integer` | `no` | - |
| `scenario_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "fault_seconds": 15,
    "primitive": "runtime_preview_upstream_drop",
    "recovery_seconds": 20,
    "timeout_seconds": 90
  },
  "scenario": "phase3-runtime-preview-upstream-drop-haskell-primitive"
}
```

## `runtime_preview_upstream_loss`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePreviewUpstreamLoss`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_preview_upstream_loss.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `fault_seconds` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `loss_pct` | `integer` | `no` | - |
| `preview_amaru_root` | `string` | `no` | - |
| `preview_cardano_node_root` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `recovery_seconds` | `integer` | `no` | - |
| `scenario_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "fault_seconds": 15,
    "loss_pct": 30,
    "primitive": "runtime_preview_upstream_loss",
    "recovery_seconds": 20,
    "timeout_seconds": 90
  },
  "scenario": "phase3-runtime-preview-upstream-loss-haskell-primitive"
}
```

## `runtime_preview_upstream_reset`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimePreviewUpstreamReset`
- Supports: `amaru`, `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_preview_upstream_reset.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `fault_seconds` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `preview_amaru_root` | `string` | `no` | - |
| `preview_cardano_node_root` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `recovery_seconds` | `integer` | `no` | - |
| `scenario_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "fault_seconds": 15,
    "primitive": "runtime_preview_upstream_reset",
    "recovery_seconds": 20,
    "timeout_seconds": 90
  },
  "scenario": "phase3-runtime-preview-upstream-reset-haskell-primitive"
}
```

## `runtime_profile_copied_state_chainsync_divergence`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProfileCopiedStateChainsyncDivergence`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_profile_copied_state_chainsync_divergence.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_profile_copied_state_chainsync_divergence",
    "timeout_seconds": 420
  },
  "scenario": "phase3-runtime-profile-a-copied-state-chainsync-divergence-primitive"
}
```

## `runtime_profile_copied_state_divergence`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProfileCopiedStateDivergence`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_profile_copied_state_divergence.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_profile_copied_state_divergence",
    "timeout_seconds": 420
  },
  "scenario": "phase3-runtime-profile-a-copied-state-divergence-primitive"
}
```

## `runtime_profile_copied_state_postremediation_blockfetch`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProfileCopiedStatePostremediationBlockfetch`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_profile_copied_state_postremediation_blockfetch.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_profile_copied_state_postremediation_blockfetch",
    "timeout_seconds": 540
  },
  "scenario": "phase3-runtime-profile-a-copied-state-postremediation-blockfetch-primitive"
}
```

## `runtime_profile_copied_state_recovery`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProfileCopiedStateRecovery`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_profile_copied_state_recovery.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_profile_copied_state_recovery",
    "timeout_seconds": 420
  },
  "scenario": "m3-runtime-node2-copied-state-recovery"
}
```

## `runtime_profile_restart_postrecovery_blockfetch`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProfileRestartPostrecoveryBlockfetch`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_profile_restart_postrecovery_blockfetch.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_profile_restart_postrecovery_blockfetch",
    "timeout_seconds": 420
  },
  "scenario": "phase3-runtime-profile-a-restart-postrecovery-blockfetch-primitive"
}
```

## `runtime_profile_restart_recovery`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProfileRestartRecovery`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_profile_restart_recovery.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_profile_restart_recovery",
    "timeout_seconds": 300
  },
  "scenario": "m3-runtime-profile-restart-tip-recovery"
}
```

## `runtime_protocol_decode_cases`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeProtocolDecodeCases`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_protocol_decode_cases.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `attempts_per_case` | `integer` | `no` | - |
| `duration_seconds` | `number` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `observation_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `progress_timeout_seconds` | `number` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_root` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `trace_timeout_seconds` | `number` | `no` | - |
| `workload_digest` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "attempts_per_case": 100,
    "duration_seconds": 240,
    "expected_helper_exit": 0,
    "primitive": "runtime_protocol_decode_cases",
    "profile_id": "profile-x-amaru-cbor-fix-regression-nanoseconds-v2",
    "progress_timeout_seconds": 120,
    "response_timeout_seconds": 2,
    "timeout_seconds": 360,
    "workload_digest": "sha256:1ab6db08d45f22f42b1333c255ed07ac9dc57ecacb69ee7c645ecfd451c4225e"
  },
  "scenario": "client-example-cbor-decoding-amaru-d3a6dafc-regression"
}
```

## `runtime_real_target_restart_and_readiness`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeRealTargetRestartAndReadiness`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_real_target_restart_and_readiness.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |
| `minimum_adopted_blocks` | `integer` | `no` | - |
| `minimum_blocks` | `integer` | `no` | - |
| `peer_policy` | `string` | `no` | - |
| `poll_interval_seconds` | `number` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `warm_up_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "poll_interval_seconds": 1,
    "primitive": "runtime_real_target_restart_and_readiness",
    "profile_id": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
    "timeout_seconds": 240
  },
  "scenario": "client-example-restart-recovery-sync-amaru-20260918-mixed-1112"
}
```

## `runtime_recompute_leadership_schedule`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeRecomputeLeadershipSchedule`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_epoch_boundary_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_slot` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/leadership-schedule",
    "primitive": "runtime_recompute_leadership_schedule",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node2"
  },
  "scenario": "runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke"
}
```

## `runtime_resource_profile`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeResourceProfile`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_resource_profile.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `sample_count` | `integer` | `no` | - |
| `sample_interval_seconds` | `number` | `no` | - |
| `target_node` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "runtime_resource_profile",
    "runtime_metadata_path": "~/cardano-profiles/profile-a-haskell-peersharing-disabled/runtime.json",
    "sample_count": 5,
    "sample_interval_seconds": 0.5,
    "target_node": "node1",
    "timeout_seconds": 120
  },
  "scenario": "phase3-runtime-profile-a-resource-profile-primitive"
}
```

## `runtime_restart_node`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeRestartNode`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_recovery_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `requested_rollback_slots` | `integer` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `security_parameter_k` | `integer` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/restart-node",
    "primitive": "runtime_restart_node",
    "target_node": "amaru-relay-1"
  },
  "scenario": "consensus-restart-rollback-in-future-differential"
}
```

## `runtime_runtime_starvation_probe`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeRuntimeStarvationProbe`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "credential_report_path": "outputs/credential-ceremony/result.json",
    "output_dir": "outputs/runtime-starvation-probe",
    "primitive": "runtime_runtime_starvation_probe",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-real-adapter-runtime-starvation-example-smoke"
}
```

## `runtime_simulate_era_transition`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSimulateEraTransition`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_simulate_era_transition.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `window_end_slot` | `integer` | `yes` | - |
| `window_start_slot` | `integer` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/era-transition",
    "primitive": "runtime_simulate_era_transition",
    "window_end_slot": 502,
    "window_start_slot": 498
  },
  "scenario": "runtime-substrate-compound-hf-txsubmission-example-smoke"
}
```

## `runtime_simulate_peer_set_capture`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSimulatePeerSetCapture`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_topology_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `adversary_node` | `string` | `no` | - |
| `events_per_hour` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `sybil_node_ids` | `array` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/peer-set-capture",
    "primitive": "runtime_simulate_peer_set_capture",
    "runtime_metadata_path": "outputs/attach/runtime.json",
    "sybil_node_ids": [
      "relay2"
    ],
    "target_node": "amaru-relay-1"
  },
  "scenario": "consensus-sync-peer-concentration-eclipse-differential"
}
```

## `runtime_simulate_stake_snapshot_update`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSimulateStakeSnapshotUpdate`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_epoch_boundary_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_slot` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/stake-snapshot",
    "primitive": "runtime_simulate_stake_snapshot_update",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke"
}
```

## `runtime_slow_loris_chainsync`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSlowLorisChainsync`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_resource_abuse_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `bytes_per_second` | `integer` | `no` | - |
| `duration_seconds` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `fill_target_free_bytes` | `integer` | `no` | - |
| `from_node` | `string` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `kilobits_per_second` | `integer` | `no` | - |
| `max_fill_bytes` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_usage_percent` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `to_node` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "bytes_per_second": 1,
    "output_dir": "outputs/slow-loris-chainsync",
    "primitive": "runtime_slow_loris_chainsync",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-resource-slow-loris-chainsync-example-smoke"
}
```

## `runtime_snapshot_capture`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSnapshotCapture`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_snapshot_substrate.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `byte_count` | `integer` | `no` | - |
| `byte_offset` | `integer` | `no` | - |
| `corruption_mode` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `snapshot_path` | `string` | `no` | - |
| `stop_node_during_capture` | `boolean` | `no` | - |
| `target_node` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `truncate_bytes` | `integer` | `no` | - |
| `xor_mask` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "healthy_timeout_seconds": 120,
    "output_dir": "outputs/snapshot-capture",
    "primitive": "runtime_snapshot_capture",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-snapshot-corrupt-chainsync-rollback-example-smoke"
}
```

## `runtime_snapshot_corrupt`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSnapshotCorrupt`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_snapshot_substrate.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `byte_count` | `integer` | `no` | - |
| `byte_offset` | `integer` | `no` | - |
| `corruption_mode` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `snapshot_path` | `string` | `no` | - |
| `stop_node_during_capture` | `boolean` | `no` | - |
| `target_node` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `truncate_bytes` | `integer` | `no` | - |
| `xor_mask` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "byte_offset": 1024,
    "corruption_mode": "flip_bits",
    "output_dir": "outputs/snapshot-corrupt",
    "primitive": "runtime_snapshot_corrupt",
    "snapshot_path": "outputs/snapshot-capture/node1-snapshot.tar",
    "xor_mask": 255
  },
  "scenario": "runtime-substrate-compound-snapshot-corrupt-chainsync-rollback-example-smoke"
}
```

## `runtime_snapshot_restore`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSnapshotRestore`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_snapshot_substrate.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `byte_count` | `integer` | `no` | - |
| `byte_offset` | `integer` | `no` | - |
| `corruption_mode` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `snapshot_path` | `string` | `no` | - |
| `stop_node_during_capture` | `boolean` | `no` | - |
| `target_node` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `truncate_bytes` | `integer` | `no` | - |
| `xor_mask` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "healthy_timeout_seconds": 120,
    "output_dir": "outputs/snapshot-restore",
    "primitive": "runtime_snapshot_restore",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "snapshot_path": "outputs/snapshot-capture/node1-snapshot.tar",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-snapshot-corrupt-chainsync-rollback-example-smoke"
}
```

## `runtime_starvation_bounded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeStarvationBounded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/runtime_starvation_bounded.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "runtime_starvation_bounded"
  },
  "scenario": "runtime-substrate-compound-recovery-starvation-example-smoke"
}
```

## `runtime_substitute_big_ledger_peers`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSubstituteBigLedgerPeers`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_topology_fault.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `adversary_node` | `string` | `no` | - |
| `events_per_hour` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `sybil_node_ids` | `array` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/big-ledger-subset",
    "primitive": "runtime_substitute_big_ledger_peers",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-eclipse-topology-example-smoke"
}
```

## `runtime_substrate_checkpoint`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSubstrateCheckpoint`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_substrate_checkpoint.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `checkpoint_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `stop_nodes_during_capture` | `boolean` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "healthy_timeout_seconds": 120,
    "output_dir": "outputs/substrate-checkpoint",
    "primitive": "runtime_substrate_checkpoint",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json"
  },
  "scenario": "runtime-substrate-checkpoint-capture-clean-example-smoke"
}
```

## `runtime_substrate_resume`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSubstrateResume`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_substrate_checkpoint.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `checkpoint_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `stop_nodes_during_capture` | `boolean` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "checkpoint_path": "outputs/substrate-checkpoint/substrate-checkpoint.tar",
    "healthy_timeout_seconds": 120,
    "output_dir": "outputs/substrate-resume",
    "primitive": "runtime_substrate_resume",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json"
  },
  "scenario": "runtime-substrate-checkpoint-resume-recovers-example-smoke"
}
```

## `runtime_substrate_tip_warmup`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSubstrateTipWarmup`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/runtime_substrate_tip_warmup.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `cardano_cli` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `minimum_ready_nodes` | `integer` | `no` | - |
| `minimum_slot` | `integer` | `no` | - |
| `network_magic` | `integer` | `no` | - |
| `node_ids` | `array` | `yes` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `sample_interval_seconds` | `number` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_ready_nodes": 3,
    "minimum_slot": 1,
    "node_ids": [
      "node1",
      "node2",
      "node3"
    ],
    "output_dir": "outputs/substrate-tip-warmup",
    "primitive": "runtime_substrate_tip_warmup",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "sample_interval_seconds": 2,
    "timeout_seconds": 240
  },
  "scenario": "consensus-threshold-delay-probe"
}
```

## `runtime_syscall_trace`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeSyscallTrace`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_syscall_trace.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `connect_attempts` | `integer` | `no` | - |
| `expected_helper_exit` | `integer` | `no` | - |
| `helper_script` | `string` | `no` | - |
| `python_bin` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `settle_seconds` | `number` | `no` | - |
| `startup_seconds` | `number` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "connect_attempts": 3,
    "expected_helper_exit": 0,
    "primitive": "runtime_syscall_trace",
    "runtime_metadata_path": "~/cardano-profiles/profile-a-haskell-peersharing-disabled/runtime.json",
    "target_node": "node1",
    "timeout_seconds": 120
  },
  "scenario": "phase3-runtime-profile-a-syscall-trace-primitive"
}
```

## `runtime_target_health_and_progress`

- Family: `probe`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTargetHealthAndProgress`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/probe/runtime_target_health_and_progress.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `progress_reference` | `string` | `no` | - |
| `report_path` | `string` | `no` | - |

### Example Invocation

_No authored scenario example found._

## `runtime_teardown_substrate`

- Family: `teardown`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTeardownSubstrate`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/teardown/runtime_teardown_substrate.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/substrate-teardown",
    "primitive": "runtime_teardown_substrate"
  },
  "scenario": "cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node"
}
```

## `runtime_time_skew`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTimeSkew`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_time_skew.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `healthy_timeout_seconds` | `number` | `no` | - |
| `libfaketime_path` | `string` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `skew_seconds` | `integer` | `yes` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "duration_seconds": 20,
    "output_dir": "outputs/time-skew",
    "primitive": "runtime_time_skew",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "skew_seconds": 600,
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-time-skew-epoch-boundary-example-smoke"
}
```

## `runtime_tracer_capture`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTracerCapture`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_tracer_capture.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `output_dir` | `string` | `yes` | Run-relative directory for the captured tracer artifacts. |
| `prometheus_port` | `integer` | `no` | Tracer Prometheus port to snapshot. |
| `timeout_seconds` | `number` | `no` | Capture subprocess timeout. |
| `tracer_container` | `string` | `no` | Name of the cardano-tracer container to capture from. |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/tracer",
    "primitive": "runtime_tracer_capture",
    "prometheus_port": 4000,
    "tracer_container": "tracer"
  },
  "scenario": "consensus-chainhold-differential-stage1-tiebreak"
}
```

## `runtime_trigger_rupd_pulse`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTriggerRupdPulse`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_epoch_boundary_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_slot` | `integer` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/rupd-pulse",
    "primitive": "runtime_trigger_rupd_pulse",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node3"
  },
  "scenario": "runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke"
}
```

## `runtime_tx_submit_differential`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.submit_primitives`
- Class: `RuntimeTxSubmitDifferential`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_tx_submit_differential.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `expected_reference_outcome` | `string` | `no` | - |
| `expected_target_outcome` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |
| `reference_node` | `string` | `no` | - |
| `reference_submit_api` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `no` | - |
| `target_submit_api` | `string` | `no` | - |
| `tx_file` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_reference_outcome": "reject",
    "expected_target_outcome": "accept",
    "output_dir": "outputs/tx-submit-differential",
    "primitive": "runtime_tx_submit_differential",
    "reference_node": "node2",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1",
    "tx_file": "antithesis/cardano_amaru_adversarial/fixture/deleg_cert_class/stakevotedeleg-preflight.cbor"
  },
  "scenario": "ledger-submit-stakevotedeleg-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `runtime_txsubmission_batch_pressure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTxsubmissionBatchPressure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_txsubmission_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/txsubmission-batch",
    "primitive": "runtime_txsubmission_batch_pressure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-compound-eclipse-txsubmission-example-smoke"
}
```

## `runtime_txsubmission_unexpected_body`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTxsubmissionUnexpectedBody`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_txsubmission_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/serdes-txsubmission-unexpected-body",
    "primitive": "runtime_txsubmission_unexpected_body",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-serdes-txsubmission-unexpected-body-example-smoke"
}
```

## `runtime_txsubmission_window_pressure`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeTxsubmissionWindowPressure`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_txsubmission_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/txsubmission-window",
    "primitive": "runtime_txsubmission_window_pressure",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-txsubmission-window-pressure-example-smoke"
}
```

## `runtime_validation_path_differential`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeValidationPathDifferential`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/runtime_hardening_probe.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `credential_report_path` | `string` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `memory_ceiling_mb` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `reference_node` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "output_dir": "outputs/validation-path-differential",
    "primitive": "runtime_validation_path_differential",
    "reference_node": "node2",
    "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
    "target_node": "node1"
  },
  "scenario": "conway-phase1-validation-rules-differential-amaru-cardano-node"
}
```

## `runtime_verify_exact_target`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeVerifyExactTarget`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/runtime_verify_exact_target.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `executable_digest` | `string` | `no` | - |
| `image_digest` | `string` | `yes` | - |
| `implementation` | `string` | `yes` | - |
| `mode` | `string` | `yes` | - |
| `patch_set_sha256` | `string` | `no` | - |
| `source_revision` | `string` | `yes` | - |
| `version` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "executable_digest": "sha256:05233bac96c1914a232a2d9c5a704f08401aff0b20356c015e848f295b919b78",
    "image_digest": "sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862",
    "implementation": "amaru",
    "mode": "patched",
    "patch_set_sha256": "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0",
    "primitive": "runtime_verify_exact_target",
    "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
    "version": "10.11.20260912"
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `runtime_version_pinned_cbor_conformance`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeVersionPinnedCborConformance`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/load/runtime_version_pinned_cbor_conformance.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `adapter_manifest` | `string` | `no` | - |
| `adapter_manifest_sha256` | `string` | `no` | - |
| `adapter_record` | `string` | `no` | - |
| `adapter_registry_root` | `string` | `no` | - |
| `dataset_dir` | `string` | `yes` | - |
| `dataset_repo_dir` | `string` | `yes` | - |
| `dataset_revision` | `unspecified` | `yes` | - |
| `expect_exit` | `integer` | `no` | - |
| `implementation` | `enum` | `yes` | - |
| `output_dir` | `string` | `yes` | - |
| `per_input_timeout_seconds` | `number` | `no` | - |
| `source_repository` | `unspecified` | `yes` | - |
| `source_revision` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "adapter_manifest": "targets/amaru/conformance-adapters/d3a6dafcced78f5809a96619e883cf04911d2bdc/manifest.json",
    "adapter_manifest_sha256": "8877a4ae7f46521ae1a8bf6584e4092b39ef3f0f8acf93bbcc4e0bd551f1f685",
    "dataset_dir": "/home/nigel/cardano-cbor-dataset/dataset/conway-123-100",
    "dataset_repo_dir": "/home/nigel/cardano-cbor-dataset",
    "dataset_revision": "a7561cd063550c2218898571520f14c3674efe91",
    "expect_exit": 0,
    "implementation": "amaru",
    "output_dir": "outputs/cbor-conformance",
    "per_input_timeout_seconds": 5,
    "primitive": "runtime_version_pinned_cbor_conformance",
    "source_repository": "https://github.com/r2rationality/cardano-cbor-dataset.git",
    "source_revision": "d3a6dafcced78f5809a96619e883cf04911d2bdc",
    "timeout_seconds": 1200
  },
  "scenario": "client-example-cbor-decoding-amaru-d3a6dafc-regression"
}
```

## `runtime_version_pinned_plutus_conformance`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeVersionPinnedPlutusConformance`
- Supports: `cardano-node`, `amaru`
- Runtimes: `single-node`, `devnet`
- Schema: `primitives/load/runtime_version_pinned_plutus_conformance.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `adapters` | `object` | `yes` | - |
| `cost_model` | `string` | `yes` | - |
| `executions_per_script` | `unspecified` | `no` | - |
| `expect_exit` | `integer` | `no` | - |
| `output_dir` | `string` | `yes` | - |
| `timeout_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "adapters": {
      "amaru": {
        "record": "/home/nigel/.local/share/dwarf/state/measurement-targets/amaru/plutus-conformance/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/a32bbf07fe4351502a55ef25a424a17991f6e81a625c6bb0892c4a6dd37fe9f2.json",
        "source_revision": "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
      },
      "cardano-node": {
        "record": "/home/nigel/.local/share/dwarf/state/measurement-targets/cardano-node/plutus-conformance/fef83fed01d7926f3de83b3b917be5a4a48768b5/0d73dc998d695bd557be97e24bce10f5b25066a8620e77578be6081f4f38fc6e.json",
        "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5"
      }
    },
    "cost_model": "/home/nigel/dwarf-pragma/dwarf/corpora/cardano-measurement/plutus-v2-cost-model-protocol-v10.json",
    "executions_per_script": 30,
    "expect_exit": 0,
    "output_dir": "outputs/plutus-conformance",
    "primitive": "runtime_version_pinned_plutus_conformance",
    "timeout_seconds": 1200
  },
  "scenario": "client-example-plutus-vm-amaru-onchain-v2-20260918"
}
```

## `runtime_wait_for_chain_progress`

- Family: `setup`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `RuntimeWaitForChainProgress`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/setup/runtime_wait_for_chain_progress.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `duration_seconds` | `number` | `no` | - |
| `minimum_adopted_blocks` | `integer` | `no` | - |
| `minimum_blocks` | `integer` | `no` | - |
| `peer_policy` | `string` | `no` | - |
| `poll_interval_seconds` | `number` | `no` | - |
| `profile_id` | `string` | `no` | - |
| `runtime_metadata_path` | `string` | `no` | - |
| `timeout_seconds` | `number` | `no` | - |
| `warm_up_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_blocks": 5,
    "poll_interval_seconds": 1,
    "primitive": "runtime_wait_for_chain_progress",
    "profile_id": "profile-u-amaru-measurement-nanoseconds-v2",
    "timeout_seconds": 120
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `serve_crafted_block`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.block_apply_primitives`
- Class: `ServeCraftedBlock`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/serve_crafted_block.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `bind_address` | `string` | `no` | - |
| `bind_port` | `integer` | `no` | - |
| `block_json_path` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "bind_port": 3001,
    "block_json_path": "outputs/assemble/block.json",
    "output_dir": "outputs/serve",
    "primitive": "serve_crafted_block"
  },
  "scenario": "ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `shim_peer_invalid_cbor`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ShimPeerInvalidCbor`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/shim_peer_invalid_cbor.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `invalid_cbor_id` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "invalid_cbor_id": "truncated-handshake-propose-v10",
    "primitive": "shim_peer_invalid_cbor",
    "receive_bytes": 64,
    "response_timeout_seconds": 2,
    "runtime_metadata_path": "~/cardano-profiles/profile-i-generated-haskell3/runtime.json",
    "target_node": "node1"
  },
  "scenario": "phase3-runtime-generated-haskell3-node1-invalid-cbor"
}
```

## `shim_peer_malformed_blockfetch`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ShimPeerMalformedBlockfetch`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/shim_peer_malformed_blockfetch.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `malformation_id` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "malformation_id": "invalid-point-hash-short",
    "primitive": "shim_peer_malformed_blockfetch",
    "receive_bytes": 64,
    "response_timeout_seconds": 2,
    "runtime_metadata_path": "~/cardano-profiles/profile-i-generated-haskell3/runtime.json",
    "target_node": "node1"
  },
  "scenario": "phase3-runtime-generated-haskell3-node1-malformed-blockfetch"
}
```

## `shim_peer_malformed_handshake`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ShimPeerMalformedHandshake`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/shim_peer_malformed_handshake.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `malformation_id` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "malformation_id": "bad-v11-short-version-data",
    "primitive": "shim_peer_malformed_handshake",
    "receive_bytes": 64,
    "response_timeout_seconds": 2,
    "runtime_metadata_path": "~/cardano-profiles/profile-i-generated-haskell3/runtime.json",
    "target_node": "node1"
  },
  "scenario": "phase3-runtime-generated-haskell3-node1-malformed-handshake"
}
```

## `shim_peer_malformed_txsubmission`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ShimPeerMalformedTxsubmission`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/shim_peer_malformed_txsubmission.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `malformation_id` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "malformation_id": "bad-requesttxids-shape",
    "primitive": "shim_peer_malformed_txsubmission",
    "receive_bytes": 64,
    "response_timeout_seconds": 2,
    "runtime_metadata_path": "~/cardano-profiles/profile-i-generated-haskell3/runtime.json",
    "target_node": "node1"
  },
  "scenario": "phase3-runtime-generated-haskell3-node1-malformed-txsubmission"
}
```

## `shim_peer_raw_bytes`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ShimPeerRawBytes`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/load/shim_peer_raw_bytes.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `payload_hex` | `string` | `yes` | - |
| `receive_bytes` | `integer` | `no` | - |
| `response_timeout_seconds` | `number` | `no` | - |
| `runtime_metadata_path` | `string` | `yes` | - |
| `target_host` | `string` | `no` | - |
| `target_node` | `string` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "payload_hex": "00000000",
    "primitive": "shim_peer_raw_bytes",
    "receive_bytes": 64,
    "response_timeout_seconds": 2,
    "runtime_metadata_path": "~/cardano-profiles/profile-i-generated-haskell3/runtime.json",
    "target_node": "node2"
  },
  "scenario": "phase3-runtime-generated-haskell3-node2-null-bytes"
}
```

## `shim_responder_stale_blockfetch`

- Family: `load`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ShimResponderStaleBlockfetch`
- Supports: `cardano-node`
- Runtimes: `devnet`
- Schema: `primitives/load/shim_responder_stale_blockfetch.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `expected_helper_exit` | `integer` | `no` | Expected helper process exit status. Defaults to 0. |
| `helper_script` | `string` | `no` | Optional path to the stale BlockFetch runtime helper. Defaults to the canonical dwarf-fw helper path. |
| `python_bin` | `string` | `no` | Optional Python interpreter to launch the helper. Defaults to python3. |
| `timeout_seconds` | `number` | `no` | Outer timeout for the hostile responder helper invocation. |

### Example Invocation

```json
{
  "reference": {
    "expected_helper_exit": 0,
    "primitive": "shim_responder_stale_blockfetch",
    "timeout_seconds": 30
  },
  "scenario": "m3-runtime-blockfetch-stale-single-point-rejection"
}
```

## `simple_transfers_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SimpleTransfersObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/simple_transfers_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_attempts` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "minimum_attempts": 30,
    "primitive": "simple_transfers_observed"
  },
  "scenario": "client-example-simple-transfer-amaru-20260918-mixed-1112"
}
```

## `snapshot_captured_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SnapshotCapturedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/snapshot_captured_clean.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "snapshot_captured_clean"
  },
  "scenario": "runtime-substrate-compound-snapshot-corrupt-chainsync-rollback-example-smoke"
}
```

## `snapshot_corruption_detected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SnapshotCorruptionDetected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/snapshot_corruption_detected.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "snapshot_corruption_detected"
  },
  "scenario": "runtime-substrate-compound-snapshot-corrupt-chainsync-rollback-example-smoke"
}
```

## `snapshot_restore_succeeded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SnapshotRestoreSucceeded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/snapshot_restore_succeeded.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "snapshot_restore_succeeded"
  },
  "scenario": "runtime-substrate-compound-snapshot-corrupt-chainsync-rollback-example-smoke"
}
```

## `stake_snapshot_freeze_consistent`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `StakeSnapshotFreezeConsistent`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/stake_snapshot_freeze_consistent.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "stake_snapshot_freeze_consistent"
  },
  "scenario": "runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke"
}
```

## `submit_outcome_matches`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.submit_primitives`
- Class: `SubmitOutcomeMatches`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/submit_outcome_matches.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `args` | `array` | `no` | - |
| `expected_reference_outcome` | `string` | `no` | - |
| `expected_target_outcome` | `string` | `no` | - |
| `output_dir` | `string` | `no` | - |
| `primitive` | `unspecified` | `yes` | - |
| `primitive_version` | `string` | `no` | - |
| `source_primitive` | `string` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "expected_reference_outcome": "reject",
    "expected_target_outcome": "accept",
    "primitive": "submit_outcome_matches"
  },
  "scenario": "ledger-submit-stakevotedeleg-deleg-unregistered-differential-amaru-cardano-node"
}
```

## `substrate_checkpoint_recorded_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SubstrateCheckpointRecordedClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/substrate_checkpoint_recorded_clean.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "substrate_checkpoint_recorded_clean"
  },
  "scenario": "runtime-substrate-checkpoint-capture-clean-example-smoke"
}
```

## `substrate_quorum_observed`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SubstrateQuorumObserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/substrate_quorum_observed.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `minimum_fraction` | `number` | `no` | Minimum fraction of observed nodes that must agree on a real non-zero tip with non-empty hashes. |
| `primitive` | `unspecified` | `yes` | Asserts that a quorum of observed nodes agrees on one real non-zero tip group. |

### Example Invocation

```json
{
  "reference": {
    "primitive": "substrate_quorum_observed"
  },
  "scenario": "runtime-substrate-compound-eclipse-recovery-example-smoke"
}
```

## `substrate_resume_succeeded`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `SubstrateResumeSucceeded`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/substrate_resume_succeeded.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "substrate_resume_succeeded"
  },
  "scenario": "runtime-substrate-checkpoint-resume-recovers-example-smoke"
}
```

## `target_progress_continues`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TargetProgressContinues`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/target_progress_continues.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "target_progress_continues"
  },
  "scenario": "client-example-block-application-amaru-canonical-v2"
}
```

## `tip_convergence_clean`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TipConvergenceClean`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/tip_convergence_clean.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `deadline_seconds` | `number` | `no` | - |
| `tolerance_slots` | `integer` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "deadline_seconds": 10,
    "primitive": "tip_convergence_clean",
    "tolerance_slots": 0
  },
  "scenario": "runtime-substrate-honest-baseline-example-smoke"
}
```

## `topology_bootstrap_diversity_preserved`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TopologyBootstrapDiversityPreserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/topology_bootstrap_diversity_preserved.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "topology_bootstrap_diversity_preserved"
  },
  "scenario": "runtime-substrate-bootstrap-topology-concentration-example-smoke"
}
```

## `transition_window_validated`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TransitionWindowValidated`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/transition_window_validated.schema.json`

### Parameters

_No parameters._

### Example Invocation

```json
{
  "reference": {
    "primitive": "transition_window_validated"
  },
  "scenario": "runtime-substrate-compound-hf-txsubmission-example-smoke"
}
```

## `txsubmission_batch_enforced`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TxsubmissionBatchEnforced`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/txsubmission_batch_enforced.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "txsubmission_batch_enforced"
  },
  "scenario": "runtime-substrate-compound-eclipse-txsubmission-example-smoke"
}
```

## `txsubmission_unexpected_body_rejected`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TxsubmissionUnexpectedBodyRejected`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/txsubmission_unexpected_body_rejected.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "txsubmission_unexpected_body_rejected"
  },
  "scenario": "runtime-substrate-serdes-txsubmission-unexpected-body-example-smoke"
}
```

## `txsubmission_window_enforced`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `TxsubmissionWindowEnforced`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/txsubmission_window_enforced.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `min_txids_processed` | `integer` | `no` | Minimum number of txids or tx bodies that must be observed in a truthful counted session before the overflow check. |
| `min_txsubmission_messages_observed` | `integer` | `no` | Minimum number of TxSubmission messages that must be observed across the valid prelude and overflow attempt. |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "txsubmission_window_enforced"
  },
  "scenario": "runtime-substrate-cardano-node-mixed-minor-txsubmission-window-pressure-example-smoke"
}
```

## `unrelated_peer_session_usable`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `UnrelatedPeerSessionUsable`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`
- Schema: `primitives/assertion/unrelated_peer_session_usable.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `max_recovery_seconds` | `number` | `no` | - |

### Example Invocation

```json
{
  "reference": {
    "max_recovery_seconds": 35,
    "primitive": "unrelated_peer_session_usable"
  },
  "scenario": "client-example-invalid-mini-protocol-amaru-20260918-mixed-1112"
}
```

## `validation_path_parity_preserved`

- Family: `assertion`
- Version: `0.1.0`
- Module: `profile_manager.primitives`
- Class: `ValidationPathParityPreserved`
- Supports: `cardano-node`, `amaru`
- Runtimes: `devnet`, `library`
- Schema: `primitives/assertion/validation_path_parity_preserved.schema.json`

### Parameters

| Param | Type | Required | Description |
| --- | --- | --- | --- |
| `primitive` | `unspecified` | `yes` | - |

### Example Invocation

```json
{
  "reference": {
    "primitive": "validation_path_parity_preserved"
  },
  "scenario": "conway-phase1-validation-rules-differential-amaru-cardano-node"
}
```
