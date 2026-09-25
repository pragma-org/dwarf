# Evidence bundle — Amaru drops the node-to-node handshake when an offer contains a version it does not know

Supporting runs, measurements, SARIF, qualification results and source for the finding
`finding-amaru-n2n-handshake-unknown-version-data.md` (included). Generated 2026-09-23 on
DWARF devnets (testnet_42) against Amaru `v10.11.20260918` and Cardano-node `11.1.2`.

## The finding in one line

Amaru decodes the parameters of **every** version in `MsgProposeVersions` with its V11–V15
4-field layout (`version_table.rs` + `version_data.rs`), so the five-field `NodeToNodeV_16`
record that Cardano-node 11.1.x offers with experimental protocols — or any unknown version —
aborts the decode and Amaru closes the connection; Cardano-node skips unknown versions and
accepts the greatest common version.

## Contents

- **`finding-amaru-n2n-handshake-unknown-version-data.md`** — the write-up (summary, root cause, severity, remediation).
- **`environment.txt`** — images, digests, revisions, profiles, run and qualification IDs.
- **`differential/per-case-outcomes.txt`** — per-case replies decoded from the retained attempt rows:

  | Offer | Amaru 10.11.20260918 | Cardano-node 11.1.2 |
  |---|---|---|
  | V14 + V15 | 20 × `MsgAcceptVersion` v15 | 20 × `MsgAcceptVersion` v15 |
  | V14 + V15 + V16 (perasSupport) | 20 × **no reply, connection closed** | 20 × `MsgAcceptVersion` v16 |
  | V14 + V15 + unknown v99 | 20 × **no reply, connection closed** | 20 × `MsgAcceptVersion` v15 |

- **`runs/amaru-20260923T145318Z-508a12bd/`** — the Amaru DWARF run
  (`amaru-n2n-handshake-version-table-forward-compat-20260918-mixed-1112`), classified
  `completed_with_security_finding` / `amaru-n2n-handshake-unknown-version-data`:
  - `manifest.json` (exact patched target identity, 14/14 Amaru collectors finalized, verdict), `assertions.json`, `scenario.yaml`, `resolved-profile.json`.
  - `outputs/sarif-export/dwarf-export.sarif` — one `error` result, rule `dwarf.assertion.handshake_cases_match_expected`.
  - `outputs/amaru-measurement-calibration/attempts.ndjson` — all 60 attempts with request/response bytes, SHA-256s and timing; `result.json` — leg report incl. target health (no restart, no fatal signal, chain progressed).
  - `measurements/collectors/amaru-patched-protocol-decode/` — Amaru's own instrumentation: 60 frames **framed**, 20 **decoded**, **40 `malformed` at the handshake decoder**, negotiation **`not_attempted`** for those 40 (`result.json`, `normalized.ndjson`).
  - `measurements/selection.json`, `metrics/summary.json`.
- **`runs/cardano-node-20260923T150024Z-5ac32f61/`** — the reference run
  (`cardano-n2n-handshake-version-table-forward-compat-1112`, pass): same case set, 60/60
  accepted, 12/12 Cardano collectors finalized, SARIF with no results.
- **`qualification/`**
  - `20260918T092619Z…89b35bbd/` — Cardano-node 11.1.2 + Amaru 10.11.20260912, experimental protocols **on**: classified *incompatible*; `amaru-handshake-lines.txt` (Amaru `connection.child_died child="Handshake"` for the consumer's connections), `consumer-bearerclosed-lines.txt` (Cardano consumer `ColdToWarm … BearerClosed`).
  - `20260923T140419Z…867a6f18/` — Cardano-node 11.1.2 + Amaru 10.11.20260918 with the flag **pinned off**: passed every mixed gate.
- **`source/amaru/`** — `version_table.rs`, `version_data.rs`, `version_number.rs`, `handshake/mod.rs` at `aedfe797`; `AMARU-COMMIT.txt` (release and `main` revisions; the decoder hunk hashes identically on both).
- **`source/ouroboros-network/`** — `Handshake-Codec.hs` (`decodeVersions` skips unrecognised versions) and `NodeToNode-Version.hs` (V16 adds `perasSupport`), with the upstream commit.

The complete run directories and qualification directories (logs, compose files, raw traces)
are in `amaru-n2n-handshake-unknown-version-data-evidence-full.tar.gz`.

## One-line reproduction

```bash
printf '\x00\x00\x00\x00\x00\x00\x00\x19\x82\x00\xa3\x0e\x84\x18\x2a\xf4\x01\xf4\x0f\x84\x18\x2a\xf4\x01\xf4\x10\x85\x18\x2a\xf4\x01\xf4\xf4' | nc -q2 <node> <port> | xxd
# Amaru (port 3000): no bytes, connection closed.
# Cardano-node 11.1.2 (port 3001): 8301 10 85 … = MsgAcceptVersion 16.
```

## What is proven vs. open

- **Proven (DWARF run):** Amaru 10.11.20260918 drops the connection for the exact
  Cardano-node 11.1.x experimental offer and for an offer containing an unknown version, 20/20
  each, while accepting V14+V15; Cardano-node 11.1.2 accepts all three.
- **Proven (measurement):** the failure is at Amaru's handshake message decoder (`malformed`),
  before negotiation; the mux framed every frame; the node stayed healthy.
- **Proven (qualification):** the defect is what made Cardano-node 11.1.2 + Amaru
  *incompatible* in DWARF's mixed topology, and pinning `ExperimentalProtocolsEnabled=false`
  makes the pair pass every gate.
- **Proven (source):** root cause in `version_table.rs`/`version_data.rs`; decoder identical on
  `main` `5a2a08bc`; the Haskell codec skips unknown versions.
- **Not tested:** node-to-client handshakes (same `VersionTable` type); stock (unpatched) 0918
  image through DWARF — the decode path is unmodified stock code, and stock 0903/0912 builds
  showed the same behaviour.
- **Severity:** Medium — interoperability/availability and a latent network-wide break at the
  next default protocol version; not consensus-affecting, no crash.
