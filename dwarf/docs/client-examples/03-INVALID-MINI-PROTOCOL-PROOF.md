# Card 03 invalid mini-protocol proof

Status: Both implementation legs have accepted evidence on the preserved `whole-microseconds-v1` measurement revision.

Child explanation: DWARF sent two kinds of bad hello messages to each node. Both nodes said no, stayed awake, kept an honest friend connected, and kept adding blocks.

## Exact runs

- Amaru: `20260920T072858Z-2cc3bb0c`
- Cardano-node: `20260920T073447Z-ab81bfb7`
- Framework commit: `719d3188a8ab0ab120f483960bb369eaae9a4126`
- Workload: 100 unsupported-version attempts and 100 malformed-CBOR attempts per implementation
- Measurement revision: `whole-microseconds-v1`

Each run passed all four security assertions. Every hostile attempt was classified. Each target stayed running without a restart, out-of-memory condition, or fatal signal. Each unrelated honest peer was usable before, during, and after the hostile window, with zero recovery delay.

The Amaru run retained 200 Handshake ingress samples, 100 decode samples, 100 negotiation samples, 311 CPU samples, and 312 RSS samples. The Cardano-node run retained 1,691 receive/decode samples, including 100 decode-failure samples, plus 312 CPU samples and 313 RSS samples.

## Retained digests

| Evidence | Amaru | Cardano-node |
|---|---|---|
| Manifest | `aa97dd7b3c6fc57f42ed6596622d2d423fdb086f0ef26caf16eb43c48d28d05e` | `c09460f77f6b36f3fdd272ac88bf2d605e65e60262ae0bf2e510ad9521261d45` |
| Assertions | `7876db626a654d2f3ac17bfa4af28cdccd99d4c1c4be7943e2f0dd1caf4acc36` | `cc1aa458e45975ed5721f7dee8974208835780430df4673e91b839ea41eb6882` |
| Measurement report | `21aea4e253d7b8b42ec7335ef314555f0c46f402254232021b4c995ec982485d` | `cd7a99cbb63eceec0dea0cf42fe4d3e707520aaa4a6782e4fa2ece1c6ee9930d` |
| Live protocol result | `f37444555b67c53820dcbb1c3878945b69113a2e5a1aac2ea8f0cf71b22e2b31` | `afec8f52388bb878202cbfcbc593f6f34df1f2b59303e743d51af0c936db2d78` |
| Exported bundle | `01313324cf1a22ac0cd39960afe369c9ce22dc303cdb44ad0ac400d236451b81` | `e2d8bb5b817a330ef71e1a8a12dcf9f03b8206f3393f8d308e2ad247b48e4a61` |

## Claim limit

These runs prove containment, liveness, peer usability, and collection for the two frozen Handshake cases. They do not cover every mini-protocol, illegal state transition, flood rate, or peer-governor behavior. They are separate implementation runs, not a mixed-node benchmark.
