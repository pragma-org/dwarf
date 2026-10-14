# Stock Amaru verification: 2026-10-14

Run: `20261014T212526Z-b401ed89`.
Target: official stock Amaru `v10.11.20260925`.
Source: `eaf8ac3fa309f2a174c045454c276194c9312b8d`.
Image manifest: `sha256:ad95eabd49d29b04978763b94e3036ad727c5e53c06946eeea98bd706cbaaee7`.
DWARF source: `63d829332f386c46255366117b1d0e3f4870022a`.

## Result

| Offer | Attempts | Result |
| --- | ---: | --- |
| V14 and V15 | 20 | Accepted; V15 selected |
| V14, V15 and V16 | 20 | EOF; zero reply bytes |
| V14, V15 and version 99 | 20 | EOF; zero reply bytes |

The process stayed running. The chain advanced. No restart-count increase,
OOM, or fatal signal was recorded. The test completed with a failed security
assertion. The result page reported verified bundle integrity.

## Files

- `attempts.ndjson`: all 60 retained attempts, with complete request and
  response bytes, their hashes, timestamps and terminal classifications.
  Only the local `target_endpoint` field was omitted.
- `result.json`: the retained workload result. Local `hardware` metadata
  and the `target_health.observer` container metadata were omitted.
- `assertions.json`: the retained failed assertion. JSON values were not changed.
- `scenario.yaml`: the frozen executed scenario. JSON values were not changed. The
  wizard supplied the exact release and resolved default-tap selection.
- `node-handshake-events.ndjson`: a projection of the stock node trace.
  It retains timestamp, level, target, event name, child and connection ID
  for handshake completion and Handshake child-termination events. Local
  peer addresses and unrelated trace events were omitted.

Text files have a final newline for publication. Compare JSON values, not
whole-file hashes, with the original files.

These files are a publication subset, not an independently verifiable full
DWARF bundle. No fresh integrity claim is made for this subset. The complete
browser-exported bundle was retained separately. Its SHA-256 is:

`d29eabac6dc65d144f63ca6b525720e7eb07d81d3be3cb512a3e6294994a770b`.

The source trace in the finding uses the exact target revision. Stock trace
events show the connection failures but do not expose the internal CBOR
decoder error. The source path establishes that cause. Internal decoder
measurements from the older instrumented release remain separate.
