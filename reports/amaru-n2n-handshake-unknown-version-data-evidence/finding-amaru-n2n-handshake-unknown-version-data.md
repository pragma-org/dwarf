# Amaru closes a valid handshake offer that includes an unsupported version

Component: Amaru node-to-node handshake decoder (`amaru-protocols`).
Finding ID: `amaru-n2n-handshake-unknown-version-data`.
Status: Confirmed in stock `v10.11.20260925`.
First report: 2026-09-23. Stock release verification: 2026-10-14.
Recommended severity: Low, for the confirmed connection-level effect.
No patched release was verified in this work.

## Summary

Amaru closes an incoming connection when a valid handshake offer includes
supported versions and an unsupported version with a different parameter format.
It sends no handshake reply.

The peer does not ask Amaru to use the unsupported version. The peer offers
several choices. Amaru must select the highest shared version and ignore the
parameters of unsupported versions.

In the test, Amaru accepted an offer for V14 and V15. It closed the connection
when the same offer also included either V16 or an unknown version 99. The
supported V14 and V15 entries did not change.

This is a protocol compatibility defect. It is not evidence that Amaru must
implement V16. It is not a node crash or a consensus failure.

## Environment and affected versions

| Item | Verified value |
| --- | --- |
| Latest stock target tested | Amaru `v10.11.20260925` |
| Source revision | `eaf8ac3fa309f2a174c045454c276194c9312b8d` |
| Official image | `ghcr.io/pragma-org/amaru:v10.11.20260925@sha256:ad95eabd49d29b04978763b94e3036ad727c5e53c06946eeea98bd706cbaaee7` |
| Executable version observed | `Amaru 10.11.20260925 (eaf8ac3)` |
| DWARF revision | `63d829332f386c46255366117b1d0e3f4870022a` |
| DWARF run | `20261014T212526Z-b401ed89` |
| Scenario | `amaru-20260925-stock-version-table-qualification` |
| Target mode | Stock. No Amaru instrumentation patch. |
| Network | Isolated devnet; network magic 42 |
| Scope | Amaru as the incoming handshake responder |

The Amaru target used the existing bootstrap wrapper and Cardano 11.1.2 support
services. The test does not establish standalone Amaru block production or
mixed-topology qualification.

The earlier evidence in this directory covers `v10.11.20260918` with
instrumentation, `v10.11.20260912` in topology qualification, and
`v10.11.20260903` in a manual connection check. Source review also covered the
historical main revision `5a2a08bc`. Do not describe all `v10.11.x` releases or
the current main branch as affected without further checks.

## Details

The source links below use the exact stock release revision, not a moving branch.

1. [VersionNumber decoder and supported versions](https://github.com/pragma-org/amaru/blob/eaf8ac3fa309f2a174c045454c276194c9312b8d/crates/amaru-protocols/src/protocol_messages/version_number.rs#L34-L72):
   the supported list ends at V15. The decoder can read other unsigned version
   numbers. The parameter-format condition is `self.0 >= 11`; it has no upper
   bound at V15.
2. [VersionTable decoder](https://github.com/pragma-org/amaru/blob/eaf8ac3fa309f2a174c045454c276194c9312b8d/crates/amaru-protocols/src/protocol_messages/version_table.rs#L98-L112):
   the loop decodes the parameters for every map entry. It does not skip an
   unsupported entry. The statement `let value = d.decode_with(&mut ctx)?;`
   returns an error for the whole table if one parameter record fails.
3. [VersionData decoder](https://github.com/pragma-org/amaru/blob/eaf8ac3fa309f2a174c045454c276194c9312b8d/crates/amaru-protocols/src/protocol_messages/version_data.rs#L179-L196):
   every version at or above 11 reaches a check for a four-element array.
   V16's five-element record fails this check. The test's version-99 record has
   two elements and also fails it.
4. [Array-length check](https://github.com/pragma-org/amaru/blob/eaf8ac3fa309f2a174c045454c276194c9312b8d/crates/amaru-minicbor-extra/src/decode.rs#L172-L184):
   the check requires the exact declared length.
5. [Handshake message decoder](https://github.com/pragma-org/amaru/blob/eaf8ac3fa309f2a174c045454c276194c9312b8d/crates/amaru-protocols/src/handshake/messages.rs#L75-L87):
   the table error prevents construction of a proposal.
   [Version selection](https://github.com/pragma-org/amaru/blob/eaf8ac3fa309f2a174c045454c276194c9312b8d/crates/amaru-protocols/src/handshake/mod.rs#L44-L61)
   therefore cannot select the supported V15 entry.

The defect is the early decoding of unsupported parameters. It is not the
four-field check for a supported V14 or V15 record.

An unknown version does not necessarily cause a failure. If its parameters
happen to match the assumed format, this particular length failure need not
occur. The confirmed cases use valid CBOR with a different parameter format.

The [Ouroboros Network Specification, section 3.6.7](https://ouroboros-network.cardano.intersectmbo.org/pdfs/network-spec/network-spec.pdf#page=20)
requires the responder to find the shared versions first. It then selects the
highest shared version and decodes its parameters. Parameters of unsupported
versions must be ignored.

## Proof of concept

Use only a node that you own or have permission to test. The payloads below use
network magic 42. Use a matching devnet.

Each payload is one CBOR `MsgProposeVersions` message. Send it in one initiator
MUX segment on protocol 0. The first eight bytes of each complete request form
the MUX header.

| Case | Complete request, in hexadecimal |
| --- | --- |
| V14 and V15 | `00000000000000118200a20e84182af401f40f84182af401f4` |
| V14, V15 and V16 | `00000000000000198200a30e84182af401f40f84182af401f41085182af401f4f4` |
| V14, V15 and version 99 | `000000000000001d8200a30e84182af401f40f84182af401f4186382182a66667574757265` |

V16 has the extra `perasSupport` field. Version 99 has the valid CBOR value
`[42, "future"]`. Amaru does not need to understand either record to use V15.

For a minimal manual check, send the V16 request to a permitted devnet node:

```bash
printf '%s' '00000000000000198200a30e84182af401f40f84182af401f41085182af401f4f4' |
  xxd -r -p |
  nc -w 3 <devnet-host> <n2n-port> |
  xxd -p
```

Amaru in this test closed the connection with no reply. Use the V14/V15 control
request to distinguish this result from an unreachable node or wrong network.
The retained attempts record listener reachability and the terminal result.

### Observed result

DWARF sent 20 copies of each request to the same stock target.

| Offer | Expected result | Observed result |
| --- | --- | --- |
| V14 and V15 | Accept V15 | 20 accepted; V15 reply |
| V14, V15 and V16 | Ignore V16; accept V15 | 20 closed; zero reply bytes |
| V14, V15 and version 99 | Ignore version 99; accept V15 | 20 closed; zero reply bytes |

The 40 failed requests ended with EOF, not a `MsgRefuse` response.
The node log has 40 matching `connection.child_died` events for the
`Handshake` child. The Amaru process remained running. The restart count did
not increase. No OOM or fatal signal was recorded. The honest chain advanced.

DWARF completed the run and retained a failed assertion. The browser showed
bundle integrity as verified. A failed security assertion does not mean that
the test runner failed.

The earlier reference run against Cardano-node 11.1.2 accepted all three
offers. It selected V16 for the V16 offer and V15 for the version-99 offer.
That reference run is historical; it was not repeated on 2026-10-14.

## Impact and severity

Recommended severity: **Low** for the confirmed effect.

A peer that offers a supported version together with differently shaped data
for an unsupported version cannot complete this incoming handshake. This can
prevent connections from otherwise compatible newer peers. The V16 case is
relevant when a peer advertises experimental protocol versions.

The confirmed effect is limited to the affected connection. The test did not
show a node crash, loss of other peers, resource exhaustion, ledger corruption,
or consensus failure. It did not show that a remote peer can disconnect other
peers. It did not establish a public-network outage.

A future change to the default versions offered by other nodes could increase
the effect. That is a conditional risk, not an observed network-wide failure.

The earlier report assigned Medium severity based on the projected rollout
risk. This update recommends Low for the demonstrated scope and separates the
projected risk from measured impact.

## Suggested correction

Read each parameter value as an opaque CBOR item before version selection.
Ignore unsupported entries. Select the highest shared version. Decode only
the selected version's parameters with that version's format.

If no shared version exists, send `MsgRefuse(VersionMismatch)`. If the selected
version's parameters are invalid, use the protocol-defined decode refusal.
Do not require Amaru to support V16 to correct unknown-version handling.

Regression tests must include the three offers above, an offer with only
unsupported versions, and an offer with invalid parameters for the selected
supported version. No Amaru correction was implemented or tested in this work.

## Evidence and history

The [2026-10-14 stock verification](https://github.com/pragma-org/dwarf/tree/main/reports/amaru-n2n-handshake-unknown-version-data-evidence/verification-20261014)
contains all 60 request/response records, the result, assertions, frozen
scenario, and selected node log events. Its README states which local
environment fields were omitted for publication.

The surrounding evidence directory retains the original 2026-09-23 Amaru and
Cardano reference runs, instrumented decoder results, qualification records,
and historical source copies. Those files were not replaced.

The new stock run confirms the behavior on the official September 25 artifact.
The exact-release source trace explains the failure. It does not provide new
patched internal telemetry or a new Cardano reference run.

## Advisory fields

- Title: Amaru drops valid handshake offers that include an unsupported version — protocol forward-compatibility failure
- Package ecosystem: Other
- Package name: Amaru (`amaru-protocols`; no separate registry package identified)
- Affected version confirmed by this stock run: `v10.11.20260925`
- Earlier affected releases: `v10.11.20260918`, `v10.11.20260912`, `v10.11.20260903`; see the evidence limits above.
- Patched versions: No verified patched release.
- Severity: Low (recommended; no CVSS score assigned).
- Weakness: [CWE-440: Expected Behavior Violation](https://cwe.mitre.org/data/definitions/440.html).
- Description: Use Summary, Details, Proof of concept, and Impact and severity above.
- Credit: J-GainSec / DWARF.
