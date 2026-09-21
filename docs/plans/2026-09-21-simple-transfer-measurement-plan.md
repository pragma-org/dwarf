# Dedicated simple-transfer measurement implementation plan

1. Freeze the dataset, exact target identities, scenario IDs, seed, and card contract fields in failing tests.
2. Add failing tests for per-attempt accounting, timeout preservation, exact byte totals, latency distributions, real-target correlation, and unavailable-stage reasons.
3. Add the shared runtime script, primitive, schema, registry entry, assertion, and two version-pinned scenarios.
4. Extend the existing external accounting collector only for fields required by the frozen workload and keep legacy input compatibility.
5. Add the card contract, documentation, measurement-coverage evidence mapping, and concise existing-page links.
6. Run focused tests, then the full relevant suite, validators, and offline profile renders. Review the exact diff.
7. Execute the Amaru scenario through DWARF, inspect non-vacuous measurements and correlations, verify the report, and export the retained bundle.
8. Execute the Cardano-node scenario through DWARF and perform the same checks.
9. Update only the new card evidence fields with exact run IDs and digests; rerun validation and browser checks.
10. Commit a reviewed checkpoint, confirm the internal origin, push normally to internal main, build/deploy that exact commit, and verify live routes and downloads.
11. Only if the primary goal is complete and capacity remains, apply the same workload-accounting event semantics to the two accepted Plutus scenarios and prove them in fresh runs.
