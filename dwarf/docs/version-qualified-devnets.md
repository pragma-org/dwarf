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
- `default`: the operator-selected release or pair; only a `confirmed` record
  may be the out-of-box default.

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

## Current evidence-backed defaults

As of 2026-09-18, the checked-in defaults are:

- Cardano-only: Cardano-node `11.1.2`.
- Amaru target: Amaru `10.11.20260912`, with Cardano-node `10.7.1`
  disclosed as the required honest bootstrap producer. This proves real Amaru
  relay/consumer behavior; it is not a claim of standalone Amaru forging.
- Mixed: Cardano-node `10.7.1` with Amaru `10.11.0`, retained as the confirmed
  fallback after the newest exact pair failed its isolated serve-through gate.

The Amaru default is selected by retained runtime evidence, not merely by its
upstream release channel. The current stable release passed only after DWARF
reused the proven live-producer lifecycle: each Amaru relay bootstraps from a
safe snapshot of the same coherent Cardano producer it subsequently follows.
Earlier failures from a separately relaunched synthetic ChainDB are preserved
as superseded harness evidence and are not compatibility findings.

The bounded mixed check of Cardano-node `11.1.2` with Amaru
`10.11.20260912` kept every exact process alive and both Amaru relays followed
the chain, but the Cardano consumer fed only by those relays never advanced
from its seeded tip during the full 30-minute recovery window. That exact pair
is therefore incompatible with the tested serve-through contract. The result
does not assign an unproven root cause to either node implementation.

## Refresh releases

Opening `/operate/versions` performs a staleness-limited background check of
the official Cardano-node and Amaru sources. The page remains available from
cached data while the check runs. **Check for new versions** starts the same
authenticated, serialized check manually and reports the last attempt, last
success, per-source status, newly discovered releases, and any rate-limit or
registry failure.

Discovery is written to a runtime-state overlay under
`$ADA2_DWARF_STATE_DIR/version-catalog/`; it never rewrites the checked-in
catalog. A new release enters the effective operator view and exact selectors
only after DWARF has its exact source revision and an immutable OCI digest. It
enters as `unknown`; discovery cannot change confirmation, incompatibility,
mixed compatibility, or any default. A one-run unknown acknowledgement embeds
the effective catalog snapshot in deployment evidence.

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

Each qualification uses a unique project and fresh volumes. Cardano producers
remain live with their coherent genesis, configuration, credentials, and
ChainDB while each Amaru bootstrap wrapper works from a safe snapshot and then
starts the exact selected Amaru binary against that same producer. DWARF
retains:

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
