# Antithesis harness work

Before creating, changing, packaging or submitting a scenario in this directory,
read [spec.md](spec.md) and the
[scenario preflight checklist](../dwarf/docs/antithesis-scenario-preflight-checklist.md).

- Record the scenario-specific contract using section 9 of `spec.md`.
- Review the final Compose **and immutable image contents**, including setup,
  Test Composer discovery, names, networks, fault scope and observation wiring.
- Reuse the applicable existing engine and harness; inspect source rather than
  copying a historical “working” label.
- Report evidence at its actual gate. Local validation is not remote execution.
- Preserve the dedicated Antithesis path and Local `/run` behavior.
- Update the contract and scenario record when a newly observed failure reveals
  a missing requirement. Do not mark unimplemented requirements as passing.
