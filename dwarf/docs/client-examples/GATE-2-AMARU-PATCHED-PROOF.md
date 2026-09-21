# Gate 2 — Amaru patched measurement proof

Status: accepted calibration evidence; not a final client example.

## Exact target and framework

- Framework commit: `d1d409022a1c23af3d14ad9e6f4a1a61a7f113dc`.
- Amaru version: `10.11.20260912`.
- Source revision: `b159172f25a9c389f82f20bca4f15e3032791638`.
- Patch-set digest: `f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af`.
- Patch SHA-256: `639a49b5e7702d3b805e719287b18f8ca0c60a03bc5ca6e367548fbf373bb06d`.
- Manifest SHA-256: `586d21a0ed7c6b4cb4d62e5a3b109c1f51f3d6d1ebb96cec7db1247cecf928b1`.
- Patched executable digest: `sha256:b0cebe917e092c3d4e47ac4841de7a49338dca1e586616803fe17e92d0d93425`.
- Patched image digest: `sha256:dacb2351e69ab1d0bbfbc569b222d1bde40d9a158e79c556b30df554375addcc`.
- Stock image digest: `sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e`.

## Matched executions

| Leg | Run | Result | Target mode | Outcomes |
|---|---|---|---|---|
| Patched | `20260919T135417Z-cca4cc8d` | pass | patched | 40 accepted, 40 refused, 40 malformed, 0 unexpected |
| Stock | `20260919T142302Z-f856f303` | pass | stock | 40 accepted, 40 refused, 40 malformed, 0 unexpected |

Both legs used workload digest
`sha256:cb649d0b4f89d338615371178cff9676684c9b41a5c8a9fdce34bdc04846c5a9`, runner digest
`sha256:134fff6fa87ecefb4553517d5a1046f0e8d19e266b0b8d7e308fd44fca3c3daf`, the same timing policy,
the same hardware identity, and the same terminal outcomes.

The patched run retained 80 framed samples, 40 mux-malformed samples, 80 decoded samples, 80 state-admitted samples,
40 accepted negotiation samples, and 40 refused negotiation samples. All 14 patched-run collectors finalized without
an error. The stock run selected 11 stock/external collectors. All 11 finalized without an error. No patched-only
collector was selected for the stock target.

## Observer-overhead envelope

The operational acceptance envelope is:

- common externally observed metric only;
- at least 30 samples per leg;
- exact target-source, workload, runner, timing, hardware, and terminal-outcome parity;
- maximum 5% absolute delta across mean, median, p95, and p99;
- informational and non-gating for the security scenario.

The pair used 120 samples per leg. Its maximum observed absolute delta was `0.059686888%`. The result is within the 5%
envelope.

Pair artifact:

`state/evidence/measurement-pairs/20260919T142302Z-amaru-stock-patched-handshake/result.json`

SHA-256: `a1096645186f25b5c6cca2f8a53b0efdcf98296b2f5b98527192b5a738ba5d69`.

## Claims and non-claims

This evidence proves that the exact patched target emitted the intended Handshake boundary events, retained correlated
accepted and rejected timing, preserved the external terminal behavior of the matched stock workload, stayed healthy,
and continued honest chain progress.

This evidence does not complete the final invalid-mini-protocol example. It does not measure all mini-protocols, flood
behavior, unrelated-peer recovery under the frozen contract-03 load, throughput, saturation, or a Cardano-versus-Amaru
comparison. The 5% envelope calibrates the common externally observed Handshake round trip. It does not bound the cost
of each internal hook.

Child explanation: both real nodes got the same messages and gave the same kinds of answers. The node with measuring
marks stayed healthy. The outside time was almost the same, but this does not tell us the cost of each mark inside.
