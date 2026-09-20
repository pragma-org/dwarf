# Card 02 frozen-topology finding: no Plutus V2 cost model

Status: The Amaru live-transaction leg cannot be accepted with the frozen support topology.

Child explanation: Both program engines gave the same answers. The Amaru test chain does not have the rule table that lets a real Plutus V2 transaction run.

## Exact boundary

The retained diagnostic runs used these fixed identities:

- Amaru target revision: `b159172f25a9c389f82f20bca4f15e3032791638`
- Amaru measurement revision: `nanoseconds-v2`
- Amaru patch set SHA-256: `4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0`
- Amaru image digest: `sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862`
- Qualified support node: Cardano-node `10.7.1`
- Cost-model SHA-256: `675a27a3c1f2f9b32954c67c1f0ad21479713eef5513386638d78e05f5e277cc`
- Success script SHA-256: `8ec6e6882130b15c84f6062125fc6d7665e1f72f8aa4e559b93d963b47c83a0a`
- Failure script SHA-256: `99f9d7bf49fdd9c257321942bedf074d562c5e6d50eb33bd134b53afc877ae34`

The exact evaluator adapters ran each script 30 times for each implementation. All 120 executions matched on outcome, CPU budget, and memory budget. Raw nanoseconds and fractional microseconds were retained.

## Live-transaction result

The qualified Amaru topology uses Cardano-node 10.7.1 producers to make blocks for the Amaru target. The live protocol parameters in that chain have no Plutus V2 cost model. The first controlled Plutus transaction stopped during `cardano-cli transaction build` with:

`No cost model was found for language PlutusV2`

No Plutus transaction was submitted. The failure occurred before the frozen `plutus_live_outcomes_observed` assertion could collect a sample. Adding an off-chain cost model would not make the on-chain protocol parameters accept Plutus V2, so it would not satisfy the real-node contract.

## Retained diagnostic evidence

- Run `20260920T113104Z-1b3d7b41`: exact target and 120 evaluator executions passed; the first containerized support-node probe exposed a file-owner defect before submission.
- Run `20260920T113249Z-b7d21c2e`: exact target and 120 evaluator executions passed; the first real transaction build proved the missing Plutus V2 cost model.
- Cardano-node final candidate run `20260920T110302Z-9df1686f`: 120 evaluator executions passed, 30 valid transactions were included, 30 invalid transactions were included, all three assertions passed, and target progress continued.

The two Amaru runs are diagnostic evidence. They are not accepted Gate 5 evidence.

## Required decision

Card 02 remains incomplete until an authorized frozen-topology change supplies a qualified Amaru test chain with active Plutus V2 protocol parameters, or the controlling card changes its real-node requirement. This finding does not change the Card 02 security assertions, the Card 03 accepted evidence, or the Card 03 whole-microsecond measurement revision.

## Approved resolution

Add a separate measurement-specific Amaru topology. Preserve all existing topologies. The new topology must use the proven Cardano-producer and Amaru-consumer lifecycle and must activate the pinned Plutus V2 cost model in the generated on-chain genesis before producers start. It must retain generated genesis digests, live protocol parameters, the exact cost-model digest, revisions, images, and real transaction identities.

An off-chain cost-model file is not sufficient. The live queried protocol parameters must contain the same Plutus V2 model.

Child explanation: Make a new test chain whose real rule book contains the missing Plutus V2 rules. Do not pretend that a separate file changes the chain.
