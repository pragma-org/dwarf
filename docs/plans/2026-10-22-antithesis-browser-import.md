# Antithesis browser import implementation plan

**Goal:** Import retained Antithesis tap archives in the existing Pages viewer.

**Architecture:** A separate `docs/antithesis-import.js` adapter recognizes the
snapshot/report format, converts each service into a derived viewer model, and
retains source hashes and metric mappings. The existing archive reader keeps its
path, size and entry checks. Small opt-in hooks support this adapter and multiple
models per source file; ordinary DWARF archives follow their existing path.

**Tech stack:** Plain browser JavaScript, existing tar/WebCrypto utilities, Node
tests and one local Playwright import check.

1. Add `tests/browser/test_antithesis_import.cjs` with synthetic archives and
   value/identity checks. Observe failure before implementation.
2. Implement the separate adapter. Keep different measurement boundaries distinct;
   normalize `microseconds` to `us` without changing values. Keep unavailable
   signals, source names, collector warnings, and unknown campaign metadata.
3. Add narrow integration hooks in `docs/index.html`. Handle repeated imports,
   malformed inputs, multiple targets and existing batch bounds. No changes to
   Local execution, GitHub workflows, embedded run data or page layout.
4. Check the supplied archive and an existing hosted archive in a local preview.
   Compare normal-import output against the original page. Review before release.

Derived view hashes identify the conversion and source bytes; original manifest
chains remain absent. Record this explicitly; never infer a security verdict or run times
from collection duration, file timestamps or successful parsing. Leave absent
scenario/profile hashes absent so incompatible evidence cannot be rolled up.

Publication is deferred until the local result is reviewed.

## Local review result

- The supplied archive imports two target records: Amaru (37 metric rows) and
  cardano-node (26). Original numeric values, sample counts and unavailable
  states are preserved. Native metric IDs stay unchanged, including names
  qualified by collector on collision; no new aliases are invented.
- Snapshot/report identity disagreement remains detectable. Original source
  chain and signature checks stay absent. Source window limits and collector
  warnings are retained in provenance. Repeated imports are deduplicated by
  derived content identity without presenting it as original attestation.
- A failed target does not prevent other targets in the same archive from
  importing. Existing archive size, path, checksum and entry checks still apply.
- Focused Node checks pass, including exact normal-archive output comparison
  against the original viewer. A real Chromium import, Comparison navigation
  and run-detail check pass with both targets and no browser errors.
- Existing detail/comparison rendering and embedded run data are unchanged.
  Some patched metrics use the viewer's existing generic label fallback.
- The optional JavaScript adapter must accompany `index.html` for this new
  format. Ordinary imports still work when the adapter is absent. This phase
  does not change standalone HTML generation, runtime flows or deployments.

## Approved follow-up: concept grouping in the local preview

User approved grouping matching measurement concepts visually while preserving
source IDs, sample counts and boundaries. Implement in the isolated adapter:

1. Add focused assertions for a single header-validation card with both native
   metrics, a shared protocol/handler concept with distinct boundary labels,
   retained unavailable KeepAlive data and separate queue bounds. Verify failure.
2. Adapter supplies optional display descriptors only for derived Antithesis
   records. Existing comparison retains original metric IDs and compatibility
   fingerprints in contributors. Group display rows by concept/scenario/mode/unit.
3. Keep every imported Antithesis target/run separate even at the same version;
   overlapping snapshots are never pooled. Disable rankings, baseline deltas and
   disagreement claims for concept-grouped rows. Unknown metrics retain existing
   handling. Existing Local/Actions comparison remains unchanged.
4. Add compact source/boundary details to grouped cards. Check real browser
   rendering and filters plus the retained normal-import regression. Repackage
   the local preview; do not publish or change execution workflows.


Follow-up verified: supplied two-target archive shows shared header validation,
protocol receive/decode and BlockFetch handler concepts; KeepAlive preserves
Cardano's missing samples. Native IDs, timing boundaries and sample counts are
visible in per-card disclosures. Queue bounds remain distinct. Each snapshot
has its own group; concept rows have no rankings or deltas. Browser tests cover
concept filtering, disclosures, run navigation and mobile overflow. Ordinary
archive import and comparison results match the original viewer exactly.
The local v2 preview tarball is packaged; no publication was performed.

Known scope: collision-qualified patched header IDs in the supplied snapshot
are mapped. Bare header_validation remains unmapped because the native stock
metric with that name contains counts; extending recognition requires timing
units and collector provenance. Unknown metrics remain unchanged.


## Publication approval

User approved the reviewed v2 viewer and original Antithesis tap archive for
public GitHub Pages, including automatic default loading. The Pages assembly
copies the separate converter; default staging retains the committed snapshot
when rolling campaign defaults exist. Existing archive count/size/hash limits
still apply. Local automatic-load browser verification passed, as did seven
focused Pages staging/workflow checks. No execution workflows were changed.
