# Advisory draft

This file is ready for manual entry. No advisory or CVE was created by DWARF.

## Fields

| Field | Value |
| --- | --- |
| Title | Amaru drops valid handshake offers that include an unsupported version — protocol forward-compatibility failure |
| Severity | Low (recommended) |
| Package | Amaru; component `amaru-protocols`. Use Other if the form requires an ecosystem. Do not invent a registry package. |
| Affected versions | `v10.11.20260925` confirmed on the official stock artifact. Earlier evidence: `v10.11.20260918`, `v10.11.20260912`, `v10.11.20260903`. |
| Patched versions | No verified patched release. |
| Weakness | CWE-440: Expected Behavior Violation |
| CVSS | Not assigned. The test does not establish node-wide availability loss. |
| Credit | J-GainSec / DWARF |

Do not use a blanket `v10.11.x` range. The evidence does not establish every
release in that range. Do not describe the current main branch as affected
without a new check.

## Description

### Summary

Amaru closes an incoming node-to-node connection when a valid handshake offer
contains supported versions and an unsupported version with a different
parameter format. It sends no reply.

Amaru accepted V14/V15-only offers. It closed the same offers when they also
contained V16 or an unknown version 99. Amaru does not need to support these
versions. It must ignore their parameters and select the supported V15 entry.

### Details

At stock release source `eaf8ac3fa309f2a174c045454c276194c9312b8d`:

- `version_table.rs:103-110` decodes every offered parameter record. A decoding
  error aborts the whole table.
- `version_number.rs:55-57` applies the same format condition to all versions
  at or above 11, including unsupported versions.
- `version_data.rs:179-188` requires four elements. V16's five-element record
  fails this check. The test's two-element version-99 record also fails it.
- The error prevents version selection, which would otherwise select V15.

These files are under `crates/amaru-protocols/src/protocol_messages/`.
The handshake specification requires unsupported parameters to be ignored.

### Proof of concept

Use a permitted devnet node with network magic 42. Send each complete request
as one initiator MUX segment on handshake protocol 0:

| Offer | Complete request hex | Stock Amaru 0925 |
| --- | --- | --- |
| V14/V15 | `00000000000000118200a20e84182af401f40f84182af401f4` | 20/20 accepted; V15 |
| V14/V15/V16 | `00000000000000198200a30e84182af401f40f84182af401f41085182af401f4f4` | 20/20 EOF; no reply |
| V14/V15/version 99 | `000000000000001d8200a30e84182af401f40f84182af401f4186382182a66667574757265` | 20/20 EOF; no reply |

All 40 failed requests had zero response bytes. The node trace has 40
Handshake child-termination events. The process stayed running, with no new
restart, OOM, or fatal signal. The honest chain advanced.

DWARF run: `20261014T212526Z-b401ed89`. The test completed and retained a
failed assertion. The browser reported verified bundle integrity.

### Impact

The confirmed effect is incoming-connection failure for otherwise compatible
peers that advertise differently shaped unsupported-version parameters.
Newer peers can fail to connect even when both nodes support V15.

No node crash, loss of other peers, resource exhaustion, or consensus failure
was demonstrated. A future default protocol-version change could increase the
effect, but a network-wide outage was not demonstrated. This is a compatibility
defect with a limited confirmed availability effect, not proof of network-wide
denial of service.

### References

- [Full report and exact source links](https://github.com/pragma-org/dwarf/blob/main/dwarf/docs/finding-amaru-n2n-handshake-unknown-version-data.md)
- [Stock request/response evidence](https://github.com/pragma-org/dwarf/tree/main/reports/amaru-n2n-handshake-unknown-version-data-evidence/verification-20261014)
- [Ouroboros Network Specification, section 3.6.7](https://ouroboros-network.cardano.intersectmbo.org/pdfs/network-spec/network-spec.pdf#page=20)
