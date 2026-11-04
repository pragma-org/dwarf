# Cross-implementation measurement compare — design

Date: 2026-11-04. Repo: V10-FRESH (`pragma-org/dwarf` public main, internal main).
Status: design for approval. Follows `docs/plans/2026-11-03-operator-build-workload-measurements-design.md`,
whose four workload runs are the first data this feature is for.

## 1. Intent

The GitHub Pages viewer (`docs/index.html`, mirrored at `/index.html`) carries one Amaru
workload pair and one cardano-node workload pair as default data. Its Compare page shows
exactly one workload at a time and keys metric rows on the raw metric id plus the scenario,
measurement profile and collector digests. So Amaru and cardano-node never share a card, and
even stock and patched builds of one node land on separate rows for the same metric.

PRAGMA's question is "how do the two implementations behave on the same job". That needs:

1. a way to pick individual runs from any workload and compare them on one page;
2. rows keyed on the **measurement concept** ("Block application time") instead of each
   node's raw metric id (`block_application`, `keepalive_rtt`, `handler`, …), so the two nodes
   land on one card with one bar per build;
3. a complete concept vocabulary: every raw id the four workload runs emit bound to a concept,
   exact pairs bound on both sides, and near pairs bound with an explicit "approximate" mark;
4. on hover only, the raw id each bar was taken from.

DWARF already has the vocabulary (`dwarf/measurements/presentation-v1.json`, 59 concepts with
per-implementation bindings) and a concept-keyed row path (the Antithesis import hook in the
viewer). This design wires them together and fills the vocabulary. Nothing new is invented.

Framing: authorized defensive conformance testing of Amaru and cardano-node for PRAGMA.
Every number shown stays traceable to the run archive and raw metric id it came from.

## 2. Goals, non-goals, anti-goals

Goals
- Pick any set of loaded runs, from any workloads, and see them compared on concept rows.
- Exact and approximate pairs share one card and one axis. Stock and patched builds of one
  node are separate bars on that card.
- Hover a bar: see implementation, raw metric id(s), raw unit and the binding's boundary note.
  Not shown otherwise. The existing "Data provenance" toggle still shows the full fingerprint.
- No "different clock" caveat anywhere. Wall-clock is wall-clock (user decision).
- Every raw id in the four 2026-11-04 workload runs has a concept. New pairs and
  equivalences from §5 are in the catalog.
- The embedded catalog in both viewer copies is generated from `presentation-v1.json`, not
  hand-edited, so the vocabulary has one source.
- Default behaviour with no picks is unchanged.

Non-goals
- No change to the live dashboard. There is no live measurement-compare page; the Python model
  `measurement_compare.py` has no callers (verified; only `SCENARIO_TARGET_IDENTITY_FIELDS` is
  imported elsewhere). It gets the catalog-index and verdict-sum changes for parity only.
- No new collectors, no changes to what runs record.
- No URL persistence of picks in this iteration (picks are page state; see §4.6).
- No new Pages export variant.

Anti-goals
- Silently aggregating numbers from different measurement boundaries into one value. Bars are
  per build; a cell never mixes implementations. Approximate pairs are marked in the data and
  in the hover, never hidden.
- Pretending a one-sided metric has a counterpart. One-sided concepts show one bar and
  "not measured" on the other side.
- Losing traceability: every bar still carries its contributor run ids, raw metric id and
  fingerprint.

## 3. Constraints (verified in the repo)

- The offline viewer's JavaScript lives only inside `docs/index.html` as
  `<script data-inlined="js/<name>.js">` blocks. There are no source files and no build step.
  `/index.html` is the same file with `./docs/` script paths (diff after path normalisation: 0
  lines). Both must be edited together; `tests/` must guard that.
- The viewer's catalog is a reduced index embedded in the `data-portable-viewer-payload` JSON
  (line ~9830): `presentation_catalog = {by_metric: {id: {title, category, comparison_direction}},
  categories, implementation_labels, unit_short, unit_full}`. Nothing in the repo generates it.
  `comparison_direction` exists only there, not in `presentation-v1.json`.
- `presentation-v1.json` validation (`measurement_presentation.py`) checks schema version,
  category ids, metric types, duplicate concept ids and binding `source`
  (`stock|external|patched|reserved`). Unknown fields pass silently. `_binding_for` picks the
  first binding whose implementation matches (exact before `*`) and whose `modes` include the
  target mode: one binding per concept per implementation+mode.
- Live run page cards (`build_measurement_presentation`, used by `operate_run.py`) use the
  catalog. Adding a binding moves ids from fallback cards to concept cards. A reserved binding
  produces a "not implemented" card even without data. `tests/test_measurement_presentation.py`
  asserts the reserved raw ids for `handler-queue-residence` and `txsubmission-residence`
  exactly, and asserts a fixed id inventory produces no fallback cards.
- Viewer compare model (`portable-store.js` block): `buildComparison(records, filters,
  presentationCatalog)`; rows keyed `rowKey = mapped ? mapped.key : fp.key` where `mapped`
  comes from `root.DwarfAntithesisImport.comparisonMetric(record, id, metric)` (Antithesis runs
  only, `unit === 'us'`, key `['antithesis-concept-v1', scenario_id, mode, concept, unit]`).
  Groups (bars) keyed by `implementation|version|source_revision|image_digest` ("exact") or
  per run. `focus_required = !filters.scenario && scenarioLinks.length > 1`; when true the
  overview renders no rows and `buildResult` picks the first workload.
- Runs enter the comparison through the session inventory "Include run" checkboxes
  (`store.setIncluded`); `store.snapshot().records` is the comparison input.
- A metric cell's primary value is `value`, else rate/delta, else `median`, read from
  `metric.all` when present. `header_validation` on both nodes is a count whose `value` is a
  verdict dict `{accepted, rejected}` → primary null → "unavailable" today.
- Collector-emitted ids collide inside one report: a second collector emitting an existing name
  gets `<collector-id>.<name>` (e.g. `amaru-patched-txsubmission-residence.pending_depth`,
  `cardano-stock-ledger-block-epoch.adopted_blocks`). Which one is prefixed depends on
  collector order.
- JS tests run under Node `vm` by extracting inlined blocks from `docs/index.html`
  (`tests/browser/test_antithesis_import.cjs`); render-level tests use Playwright
  (`tests/browser/*.cjs`, `require('playwright')`), which is not installed on this Mac.
  Python tests: `PYTHONPATH=dwarf python3 -m pytest …`.
- Pages publish (`.github/workflows/pages.yml`) copies `index.html`, `docs/index.html`,
  `docs/portable-defaults.js`, `docs/antithesis-import.js`, `docs/evidence/**`. No build.

## 4. The how (decided)

### 4.1 Catalog as the single source

`dwarf/measurements/presentation-v1.json` gains three optional fields, all validated:

- concept `comparison_direction`: `lower | higher | neutral` (default `neutral`). Seeded from
  the current embedded snapshot so nothing changes for existing concepts.
- concept `unit`: canonical display unit for concept-keyed rows (e.g. `us`, `transactions`,
  `requests`, `count`). When set, a binding only matches a raw metric whose unit shortens to
  this unit, or whose binding declares `unit_equivalent: true` (used by approximate count pairs
  whose raw units differ, e.g. `events` vs `blocks`).
- binding `equivalence`: `exact | approximate` (default `exact`), and binding `boundary`: one
  short sentence naming what that node measures, shown in the hover and provenance.

`measurement_presentation.load_measurement_presentation_catalog` validates the enums and types.
`measurement_compare._catalog_index(catalog)` grows to emit the viewer index:

```
{
  by_metric: {raw_id: {title, category, comparison_direction}},          # unchanged shape
  categories, order,                                                        # unchanged
  concepts: {concept_id: {title, category, comparison_direction, unit, metric_type}},
  bindings: {implementation_or_star: {raw_id: {concept, modes, equivalence, boundary,
                                               unit_equivalent, primary: bool}}}
}
```
`primary` is true for the binding's first id not ending in `_by_outcome`; companions are
never concept rows.

### 4.2 Generator for the embedded catalog

New `dwarf/scripts/render_viewer_presentation_catalog.py` (in-repo tooling next to
`ci_stage_pages_campaigns.py`): reads the catalog through `_catalog_index`, locates the
`data-portable-viewer-payload` JSON in `docs/index.html` and `/index.html`, replaces
`presentation_catalog.by_metric`, `.categories`, `.concepts`, `.bindings`, preserves
`implementation_labels`, `unit_short`, `unit_full` and every other payload key, and writes
both files. `--check` exits non-zero when either file differs from the generated output. A test
runs it on a temp copy and asserts idempotence and preserved keys. The viewer-sync test
(§6) asserts the two html files are identical after `./docs/` → `./` normalisation.

### 4.3 Catalog concept hook in the viewer

In the `portable-store.js` block, next to the Antithesis hook:

```
function catalogConcept(record, id, metric, catalog)  -> null | {
  id, title, category, comparison_direction, boundary, equivalence, raw_unit,
  key: JSON.stringify(['catalog-concept-v1', conceptId, unit])
}
```
Resolution: `catalog.bindings[record.implementation][id]` else `catalog.bindings['*'][id]`;
require `primary`, require `record.mode` in `modes` when `modes` is non-empty; `unit` =
concept.unit if set, else `unit_short[metric.unit] || metric.unit`; when concept.unit is set
and the raw unit does not shorten to it, require `unit_equivalent`. Returns null otherwise.

`buildComparison` precedence per metric: Antithesis hook (unchanged, Antithesis records only)
→ catalog hook **when `filters.runs` is set** → raw fingerprint. Rows from the catalog hook get
`concept_grouped: true`, `comparison_direction` from the concept (not forced neutral), and
`approximate: true` when any contributor binding is approximate. Baseline deltas and the
"preferred value" highlight work on these rows. Disagreement detection (same-group spread)
stays per group, so it still works; cross-group it never applied.

A concept cell belongs to one build group only, so a cell never mixes implementations. Inside a
cell, contributors keep their own `fingerprint`, `metric_id`, `raw_unit`, `boundary`; if
contributors of one cell carry different fingerprints the cell gets `mixed_provenance: true`
(shown in provenance; the value is still the median of run primaries, as today for one build).

### 4.4 Verdict-dict counts

`metricCell` (viewer) and `_metric_cell` (Python): when `value` is an object whose values are
all finite numbers, primary = their sum, and `count` = that sum (positive evidence). This makes
`header_validation` (`{accepted: 205, rejected: 0}`) a bar of 205. `tapreport.py` on the box
already sums the same way.

### 4.5 "Pick runs" control

A new filter key `runs` (array of run ids; empty = off). In the Compare toolbar
(`[data-compare-controls]`), after the Workload select, a `<details class="mc-disclosure">`
titled **Pick runs** lists every included, eligible run from `store.snapshot()`, grouped under
its workload label (`scenarioLabel`), one checkbox each, labelled
`<implementation label> <version> <mode> · <run id>`, plus "Clear picks".

When `filters.runs` is non-empty:
- `recordMatches` additionally requires `filters.runs` to contain `record.run_id`;
  the `scenario` predicate is ignored and `focus_required` is false.
- The workload pill strip is replaced by a pick strip: one pill per picked run, grouped by
  workload, each with a remove "×".
- The Workload select is disabled with its placeholder "Picked runs".
- Rows key by concept (4.3). Rows whose concept is bound on one side only still render,
  with "not measured" cells for the other builds.
- Summary line under the heading: "N runs picked across M workloads · concept rows".

Clearing picks restores the previous behaviour exactly (first workload auto-selected).

### 4.6 Hover source

Each bar (`li.mc-bar`) gets `tabindex="0"` and a child `span.mc-bar__source` (hidden until
`:hover` or `:focus-within`, positioned over the bar, `role="tooltip"`, `aria-hidden` until
shown) with text:
`<Implementation label> · <raw metric id>[, <second id>] · <raw unit> · <boundary>` and, for
approximate bindings, a leading "≈ approximate pair". Contributors from several runs with the
same raw id collapse to one id. Nothing from evidence is rendered as HTML (textContent only,
as the file's header rule states). The tooltip is also present on the coverage table cells.
Default (non-pick) rows get the same hover with the raw id they already key on.

Picks are page state, not URL state. My call: the route parser matches `#compare` exactly and
metric anchors (`#mc-metric-…`) already drive workload selection; adding a query to the hash
would need the router reworked. Picks survive filter changes and store changes in the session;
they reset on reload.

### 4.7 Python compare model

`build_measurement_comparison` gains the same `runs` filter and catalog-concept keying for
parity, behind the same rule (`filters["runs"]` non-empty), with tests. It has no live caller;
this keeps the documented mirror honest and gives the generator (4.2) a tested index.

## 5. Vocabulary changes (presentation-v1.json)

Every raw id below was verified in the four 2026-11-04 workload reports. Units are the
reports' units. "both" means the row merges Amaru and cardano-node.

### 5.1 Exact pairs, new or completed

| Concept | Amaru binding | cardano-node binding | unit | dir |
| --- | --- | --- | --- | --- |
| `header-validation-verdicts` (new, count) | stock+patched `header_validation` | stock+patched `header_validation` | count (verdict sum) | higher |
| `header-validation-time` (new, distribution) | patched `amaru-patched-header-validation.header_validation`, `…_by_outcome` | patched `cardano-patched-header-validation.header_validation`, `…_by_outcome` | us | lower |
| `protocol-decode-time` (existing) | **add** patched `protocol_decode`, `protocol_decode_by_decode_outcome` | existing `protocol_receive_decode` | us | lower |
| `blockfetch-handler-time` (new) | patched `handler`, `handler_by_outcome` | patched `handler_service` (collector `cardano_blockfetch_queue.py`, contract unbuilt today) | us | lower |
| `handler-queue-residence` (existing; type → distribution/median) | **add** patched `queue_residence`, `queue_residence_by_outcome` | existing reserved `handler_queue_residence` (unchanged) | us | lower |
| `txsubmission-residence` (existing; type → distribution/median) | **add** patched `advertised_to_terminal`, `…_by_outcome` | existing reserved `txsubmission_residence` (unchanged) | us | lower |
| `txsubmission-request-to-body-time` (new) | patched `advertised_to_body` | patched `request_to_body_lower_bound` (`cardano_txsubmission_residence.py`) | us | lower |
| `txsubmission-body-to-terminal-time` (new) | patched `body_to_terminal` | patched `body_to_terminal_lower_bound` | us | lower |
| `txsubmission-pending-depth` (new, unit transactions) | patched `pending_depth`, `amaru-patched-txsubmission-residence.pending_depth` | patched `pending_body_depth` | transactions | lower |
| `txsubmission-inflight-depth` (new, unit transactions) | patched `inflight_depth`, `amaru-patched-txsubmission-residence.inflight_depth` | patched `inflight_body_depth` | transactions | lower |
| `blockfetch-pending-depth` (new, unit requests) | patched `pending_depth` | patched `pending_range_depth` | requests | lower |
| `submit-to-block-inclusion-time` (new) | `*` external `submit_to_block_inclusion` | same | us | lower |
| `submit-to-chain-adoption-time` (new) | `*` external `submit_to_chain_adoption` | same | us | lower |
| `submit-to-protocol-response-time` (new) | `*` external `submit_to_protocol_response` | same | us | lower |
| `offered-byte-count` (new, count) | `*` external `offered_bytes` | same | bytes | higher |
| `container-memory-usage` (existing) | implementation → `*` | | bytes | lower |
| `container-task-count` (existing) | implementation → `*` | | tasks | lower |

The `pending_depth`/`inflight_depth` collision is resolved by the concept `unit` guard
(§4.1): the BlockFetch depth concepts declare `requests`, the TxSubmission ones
`transactions`, and each binds both the bare and the prefixed id. Whichever collector was
first, the unit routes the id to the right concept.

### 5.2 Approximate pairs (binding `equivalence: approximate`, boundary text on each side)

| Concept | Amaru | cardano-node | unit | note |
| --- | --- | --- | --- | --- |
| `chain-advance-count` (replaces `chain-tip-update-count` and `adopted-block-count`) | `chain_tip_updates` (events) | `adopted_blocks`, `cardano-stock-ledger-block-epoch.adopted_blocks` (blocks), `unit_equivalent` | events | Amaru counts selected-tip changes including rollback re-selection; cardano counts adoption events. |
| `mempool-received-count` (existing) | `mempool_received` (events) | **add** `transaction_outcomes` (transactions), `unit_equivalent`; `transaction-outcome-count` concept removed | transactions | cardano outcomes = accepted + rejected decisions, i.e. every received transaction reaching a verdict. |

Rejected candidate: Amaru `mempool_terminal_latency` against cardano `submit_to_protocol_response`.
Different vantage (in-node trace vs driver-observed including network), and the same external
id exists on both sides anyway (it has 0 samples on the Amaru leg because that leg submits via
the consumer node). It stays one-sided; making the Amaru leg record the protocol response is
a follow-up on the workload, not the catalog.

### 5.3 One-sided, newly bound (card with one bar)

Amaru patched: `block-decode-time` (`block_decode`), `protocol-total-time` (`protocol_total`),
`handshake-ingress-time`, `handshake-decode-time`, `handshake-negotiation-time`
(`handshake_ingress`, `handshake_decode`, `handshake_negotiation` + their outcome companions),
`txsubmission-unacked-depth` (`unacked_depth`, transactions), `txsubmission-blocking-residence`
(`blocking_residence`, us), `blockfetch-inflight-depth` (`inflight_depth`, requests).
All directions `lower` except depth counts, which are `neutral`.

Already bound one-sided concepts stay as they are (Amaru header lifecycle, block prepare,
fork switch, transfer validation, Plutus sub-stages, evictions, mux failures, peer connects;
cardano rollbacks, served blocks, BlockFetch bytes, ledger samples, chain events, protocol
events, mempool sync).

### 5.4 Removed concepts

`chain-tip-update-count`, `adopted-block-count`, `transaction-outcome-count` (merged above).
Their ids remain bound, so the no-fallback inventory test keeps passing; its id sets are
extended with the workload-run ids and a third row for `("amaru", "patched")`.

## 6. Tests

Python (`PYTHONPATH=dwarf python3 -m pytest`):
- `tests/test_measurement_presentation.py`: new field validation (bad direction, bad
  equivalence, bad unit type rejected); every raw id from the four workload runs (fixture id
  sets for amaru stock, amaru patched, cardano stock, cardano patched) binds with no fallback
  card; unit guard routes `pending_depth` by unit; approximate pairs carry the flag.
- `tests/test_measurement_compare.py` (new): `_catalog_index` emits concepts/bindings;
  `_metric_cell` sums verdict dicts; `runs` filter + concept keying merges an Amaru and a
  cardano record on one row, two groups, correct direction; no picks → unchanged rows and
  `focus_required`.
- `tests/test_render_viewer_presentation_catalog.py` (new): generator idempotent, preserves
  non-catalog payload keys, `--check` detects drift, both html copies identical after path
  normalisation.

JS (`node tests/browser/test_portable_compare_concepts.cjs`, Node vm, same extraction pattern
as `test_antithesis_import.cjs`): synthetic Amaru and cardano records with different
scenario ids and the real embedded catalog: no `runs` → `focus_required` true; `runs` set →
one `keepalive-round-trip-time` row with two cells, `header-validation-verdicts` value 205
vs 127, approximate flag on `chain-advance-count`, `txsubmission-pending-depth` only takes the
transactions-unit id, Antithesis hook still wins for Antithesis records, concept rows carry
contributor `metric_id`/`raw_unit`/`boundary`.

Render (`node tests/browser/test_portable_compare_picks_browser.cjs`, Playwright, same
pattern as `test_antithesis_import_browser.cjs`): Pick-runs disclosure lists runs grouped by
workload; picking one run from each workload renders concept cards with two bars; the
`.mc-bar__source` tooltip is hidden by default and visible on hover/focus with the raw id.
Runs where Playwright is available (CI or cardano-box); the plan records if it could not run.

## 7. Acceptance

On https://pragma-org.github.io/dwarf/ Compare: pick amaru 20261029 stock + patched and
cardano-node 11.1.2 stock + patched → one page; "Block application time", "KeepAlive
round-trip time", "Restart recovery time", "Plutus VM execution time", "Header validation
verdicts", "Protocol receive and decode time" each show four bars (or fewer where a build has
no tap) on one axis; hovering the Amaru keepalive bar shows `keepalive_round_trip`, the cardano
bar `keepalive_rtt`; "Selected-chain advances" shows the ≈ mark on hover; clearing picks
returns to the one-workload view. No metric id from the four runs appears under
"Other metrics".

## 8. Left to the builder

Exact CSS for the tooltip and pick strip (follow `mc-*` conventions in the file), the
ordering of picked runs (session order), pagination of the pick list beyond 25 runs
(reuse `navigation`), wording of boundary sentences for the new bindings (one sentence each,
factual, naming the trace or event), and whether the generator rewrites `generated_at_utc`
(recommended: yes, UTC now).
