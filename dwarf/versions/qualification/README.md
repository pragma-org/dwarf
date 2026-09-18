# Real-node version qualification

`dwarf/scripts/qualify_node_versions.py` derives isolated candidate projects
from the checked-in upstream mixed topology baseline. It never mutates the
retained `cardano_amaru_relay_bootstrap_control` project and never promotes a
catalog record automatically.

Contracts:

- `cardano-only`: real Cardano block producers and relays, exact identity,
  sustained progress, convergence, clean logs, and clean teardown.
- `amaru-only`: Amaru relay/consumer qualification with a fixed known-good
  Cardano producer source. This is not a claim that Amaru independently forges
  a fresh devnet.
- `mixed`: exact Cardano/Amaru pair plus the isolated consumer whose only path
  after seeding is through Amaru.

Each attempt writes its rendered Compose model, identities, health samples,
logs, teardown output, result, and review-only catalog proposal under
`$DWARF_STATE_DIR/version-qualifications/`.
