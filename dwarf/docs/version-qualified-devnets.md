# Version-qualified real-node devnets

DWARF keeps release discovery, operator defaults, and runtime proof separate.
An upstream stable release is selectable, but it becomes `confirmed` only after
the exact artifact passes the complete real-node contract for a named scope:
`cardano-only`, `amaru-only`, or `mixed`.

## Status and policy

- `confirmed`: the exact release or pair passed every scope-specific gate.
- `unknown`: discovered or explicitly selected, but not yet qualified.
- `incompatible`: an exercised pair violated a required compatibility gate.
- `blocked`: a known prerequisite prevents a valid qualification.
- `default`: an independent operator choice; it does not imply confirmation.

Profiles choose one policy:

```json
{"version_policy": "latest-confirmed"}
```

```json
{"version_policy": "latest-stable"}
```

```json
{
  "version_policy": "exact",
  "compatibility_pair": "cardano-10.7.1__amaru-10.11.0"
}
```

A single-implementation exact profile uses `cardano_version` or
`amaru_version`. An unlisted mixed pair remains `unknown`; independently valid
releases are not assumed compatible.

## Refresh releases

Release discovery writes a separate candidate file for review and never
promotes runtime status:

```bash
PYTHONPATH=dwarf python3 dwarf/scripts/refresh_version_catalog.py \
  --catalog dwarf/versions/catalog.json \
  --output /tmp/dwarf-version-catalog-candidate.json
```

The command reads official GitHub release feeds, resolves all release tags with
one Git transport query per repository, and performs a bounded number of OCI
manifest lookups. Existing evidence, issue links, defaults, and compatibility
records are preserved.

## Runtime proof

Each qualification uses a unique project and fresh volumes and retains:

- DWARF, topology, and node source revisions;
- requested policy and exact resolved releases;
- image reference, image ID, and immutable digest;
- node-reported version;
- configuration, genesis, era-history, and topology hashes;
- startup, peer, chain-progress, convergence, observation, and teardown logs.

Cardano-only, Amaru-only, and mixed are independent claims. If an Amaru release
requires an external honest source, DWARF labels the result an Amaru
relay/consumer qualification. It does not prove standalone Amaru block
production. Mixed confirmation additionally requires the isolated Amaru-fed
consumer to advance through Amaru's path and converge, so another Cardano peer
cannot make the check vacuous.

An image build, Compose render, container start, open port, or validation-only
result does not prove the runtime contract. A local confirmation also does not
prove public-network compatibility, full security coverage, or Antithesis
readiness. Paid/live Antithesis submission remains a separate approval gate.

## Unknown and rejected selections

An `unknown` stable selection requires explicit one-run acknowledgement:

```bash
cardano-profile deploy PROFILE --approve --acknowledge-unknown-version
```

That acknowledgement is retained with deployment evidence and does not alter
the catalog. `incompatible` and `blocked` selections cannot deploy. Legacy
profiles without a version policy keep their previous behavior and are labelled
as runtime-resolved rather than receiving a fabricated version claim.
