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
- No new live dashboard page. There is no live measurement-compare page; the Python model
  `measurement_compare.py` has no callers (verified; only `SCENARIO_TARGET_IDENTITY_FIELDS` is
  imported elsewhere). It gets the catalog-index, `runs` filter and verdict-sum changes for
  parity only. Live run-page cards keep their titles: equivalences are expressed with a
  compare-only link (§4.1 `compare_as`), never by merging or renaming live concepts. The one
  live-visible change is the verdict-sum rule (§4.4), which turns a stringified dict into a
  number on the live header-validation card.
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

`dwarf/measurements/presentation-v1.json` gains optional fields, all validated:

- concept `comparison_direction`: `lower | higher | neutral` (default `neutral`). Seeded from
  the current embedded snapshot so nothing changes for existing concepts.
- concept `unit`: canonical display unit for concept-keyed rows (e.g. `us`, `transactions`,
  `requests`, `count`). When set, a binding only matches a raw metric whose unit shortens to
  this unit, or whose binding declares `unit_equivalent: true` (used by approximate count pairs
  whose raw units differ, e.g. `events` vs `blocks`).
- concept `compare_as`: id of another concept. In concept-keyed compare the concept's metrics
  row under that target concept instead of their own. Live run-page cards are untouched. The
  target must exist and must not itself set `compare_as`. A target may have no bindings of
  its own (then it never makes a live card; it exists only as a compare row).
- concept `equivalence`: `exact | approximate` (default `exact`), meaningful with
  `compare_as`; `approximate` marks every bar the concept contributes to the target row.
- binding `boundary`: one short sentence naming what that node measures, shown in the hover
  and provenance.

Companion ids: any id matching `_by_[a-z_]*outcome$` (`block_application_by_outcome`,
`protocol_decode_by_decode_outcome`, `handshake_state_by_outcome`, `protocol_total_by_state_outcome`)
is a companion, never a concept row. A binding's primary id is its first non-companion id.

`measurement_presentation.load_measurement_presentation_catalog` validates the enums, types
and `compare_as` targets. `measurement_compare._catalog_index(catalog)` grows to emit the
viewer index:

```
{
  by_metric: {raw_id: {title, category, comparison_direction}},   # unchanged shape; first
                                                                   # concept wins, as today
  categories, order,                                               # unchanged
  concepts: {concept_id: {title, category, comparison_direction, unit, metric_type,
                          compare_as, equivalence}},
  bindings: {implementation_or_star: {raw_id: [ {concept, modes, boundary, unit_equivalent,
                                                 primary: bool}, ... ]}}
}
```
`bindings[impl][raw_id]` is a list because one raw id can belong to two concepts told apart by
unit (`pending_depth` in requests vs transactions). The viewer's `describe(record, id, metric)`
becomes unit-aware everywhere, not only under picks: it walks the record's implementation
bindings then `*`, keeps the entries whose concept `unit` (if any) matches the raw unit and
whose `modes` include the record mode, and falls back to `by_metric[id]`, then to the humanised
id. So the default view titles `pending_depth` correctly whichever collector came first.

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
  id, title, category, comparison_direction, boundary, approximate, raw_unit, source_concept,
  key: JSON.stringify(['catalog-concept-v1', rowConceptId, unit])
}
```
Resolution: the unit-aware `describe` lookup above, restricted to `primary` entries; the row
concept is `concept.compare_as || concept.id`; `approximate` is true when the source concept's
`equivalence` is `approximate`; `unit` = row concept's `unit` if set, else
`unit_short[metric.unit] || metric.unit`; when the row concept declares a `unit` and the raw unit
does not shorten to it, require `unit_equivalent` on the binding. Returns null otherwise.

`buildComparison` precedence per metric: Antithesis hook (unchanged, Antithesis records only)
→ catalog hook **when `filters.runs` is set** → raw fingerprint. Rows from the catalog hook get
`concept_grouped: true`, `comparison_direction` from the row concept, and `approximate: true`
when any contributor is approximate. Baseline deltas work on these rows. The "preferred value"
highlight works on exact rows and is suppressed on approximate rows (an approximate pair must
not crown a winner). Disagreement detection (same-group spread) stays per group.

Bars are build groups. The group key gains `mode`
(`implementation|version|mode|source_revision|image_digest`) and the bar label shows it
(`Amaru 10.11.20261029 · patched`), in the viewer and the Python mirror, so stock and patched
builds of one release are told apart by name, not by run id. Finding from the builder check:
the two stock workload runs retain no `image_digest` in any identity source, so they are
"incomplete identity" groups isolated per run (as today). That is a runtime identity gap for
operator-registered stock images, recorded as a follow-up; this design does not paper over it.

A concept cell belongs to one build group only, so a cell never mixes implementations. Inside a
cell, contributors keep their own `fingerprint`, `metric_id`, `raw_unit`, `boundary`; if
contributors of one cell carry different fingerprints the cell gets `mixed_provenance: true`
(shown in provenance; the value is still the median of run primaries, as today for one build).

### 4.4 Verdict-dict counts

`metricCell` (viewer), `_metric_cell` (Python compare) and `measurement_presentation._primary`
(live and offline run cards): when `value` is an object whose values are all finite numbers,
primary = their sum, and `count` = that sum (positive evidence). This makes `header_validation`
(`{accepted: 205, rejected: 0}`) a bar of 205 and a live card reading 205 instead of a
stringified dict. `tapreport.py` on the box already sums the same way.

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
| `handler-queue-residence` (existing; type → distribution/median) | **add** patched `queue_residence`, `queue_residence_by_outcome` | existing reserved `handler_queue_residence`; its `modes` widen to `stock, patched` (the stock collector emits the id as unavailable) | us | lower |
| `txsubmission-residence` (existing; type → distribution/median) | **add** patched `advertised_to_terminal`, `…_by_outcome` | existing reserved `txsubmission_residence`; `modes` widen to `stock, patched` | us | lower |
| `txsubmission-request-to-body-time` (new) | patched `advertised_to_body` | patched `request_to_body_lower_bound` (`cardano_txsubmission_residence.py`) | us | lower |
| `txsubmission-body-to-terminal-time` (new) | patched `body_to_terminal` | patched `body_to_terminal_lower_bound` | us | lower |
| `txsubmission-pending-depth` (new, unit transactions) | patched `pending_depth`, `amaru-patched-txsubmission-residence.pending_depth` | patched `pending_body_depth` | transactions | neutral |
| `txsubmission-inflight-depth` (new, unit transactions) | patched `inflight_depth`, `amaru-patched-txsubmission-residence.inflight_depth` | patched `inflight_body_depth` | transactions | neutral |
| `blockfetch-pending-depth` (new, unit requests) | patched `pending_depth` | patched `pending_range_depth` | requests | neutral |
| `submit-to-block-inclusion-time` (new) | `*` external `submit_to_block_inclusion` | same | us | lower |
| `submit-to-chain-adoption-time` (new) | `*` external `submit_to_chain_adoption` | same | us | lower |
| `submit-to-protocol-response-time` (new) | `*` external `submit_to_protocol_response` | same | us | lower |
| `submit-to-mempool-visibility-time` (new) | `*` external `submit_to_mempool_visibility` (unavailable in all four runs today) | same | us | lower |
| `offered-byte-count` (new, count) | `*` external `offered_bytes` | same | bytes | higher |
| `timed-out-operation-count` (new, count, workload-outcomes) | `*` external `timed_out_operations` | same | operations | lower |
| `container-memory-usage` (existing) | implementation → `*` | | bytes | lower |
| `container-task-count` (existing) | implementation → `*` | | tasks | lower |

Depth gauges are `neutral` everywhere (a deeper queue is not better or worse by itself).

The `pending_depth`/`inflight_depth` collision is resolved by the concept `unit` guard
(§4.1): the BlockFetch depth concepts declare `requests`, the TxSubmission ones
`transactions`, and each binds both the bare and the prefixed id. Whichever collector was
first, the unit routes the id to the right concept.

Ids in this table that no current run emits (`…patched-header-validation.header_validation`,
`handler_service`, `request_to_body_lower_bound`, `body_to_terminal_lower_bound`,
`pending_body_depth`, `inflight_body_depth`, `pending_range_depth`) are bound for the
collectors that exist in the tree; they appear once those contracts are built.

### 5.2 Approximate pairs (concept `compare_as` + `equivalence: approximate`)

Live cards keep their concepts and titles. Each pair adds a compare-only target concept with no
bindings and points the two existing concepts at it.

| Compare row (new target) | Sources (`compare_as` → target, `approximate`) | unit | direction | boundary notes |
| --- | --- | --- | --- | --- |
| `chain-advance-count` "Selected-chain advances" | `chain-tip-update-count` (Amaru `chain_tip_updates`, events); `adopted-block-count` (cardano `adopted_blocks`, `cardano-stock-ledger-block-epoch.adopted_blocks`, blocks, `unit_equivalent`) | events | neutral | Amaru counts selected-tip changes including rollback re-selection; cardano counts chain adoption events. |
| `mempool-intake-count` "Transactions reaching the mempool" | `mempool-received-count` (Amaru `mempool_received`, events); `transaction-outcome-count` (cardano `transaction_outcomes`, transactions, `unit_equivalent`) | transactions | neutral | Amaru counts receipt events at the mempool boundary; cardano counts accepted plus rejected decisions, one per received transaction. |

Approximate rows never show the "preferred value" highlight (§4.3).

Rejected candidate: Amaru `mempool_terminal_latency` against cardano `submit_to_protocol_response`.
Different vantage (in-node trace vs driver-observed including network), and the same external
id exists on both sides anyway (it has 0 samples on the Amaru leg because that leg submits via
the consumer node). It stays one-sided; making the Amaru leg record the protocol response is
a follow-up on the workload, not the catalog.

### 5.3 One-sided, newly bound (card with one bar)

Amaru patched: `block-decode-time` (`block_decode`, `block_decode_by_outcome`),
`protocol-total-time` (`protocol_total`, `protocol_total_by_state_outcome`),
`handshake-ingress-time` (`handshake_ingress`, `handshake_ingress_by_outcome`),
`handshake-decode-time` (`handshake_decode`, `handshake_decode_by_decode_outcome`),
`handshake-negotiation-time` (`handshake_negotiation`, `handshake_negotiation_by_outcome`,
`handshake_state_by_outcome`), `txsubmission-unacked-depth` (`unacked_depth`, transactions),
`txsubmission-blocking-residence` (`blocking_residence`, `blocking_residence_by_outcome`, us),
`blockfetch-inflight-depth` (`inflight_depth`, requests).
Durations are `lower`; depth gauges are `neutral`.

Already bound one-sided concepts stay as they are (Amaru header lifecycle, block prepare,
fork switch, transfer validation, Plutus sub-stages, evictions, mux failures, peer connects;
cardano rollbacks, served blocks, BlockFetch bytes, ledger samples, chain events, protocol
events, mempool sync).

### 5.4 Removed concepts

None. Existing concept ids, titles and live cards are stable; the two approximate pairs are
compare-only links (§5.2).

### 5.5 Completeness rule

The fixture id sets for the no-fallback test are taken from the four real reports (every key
of `measurements` in `measurements/report.json` of runs 8901e42d, 572908c9, b1a2f8cb,
c6fd2046), not from the tables above, with rows `("amaru","stock")`, `("amaru","patched")`,
`("cardano-node","stock")`, `("cardano-node","patched")`. The existing inventory test keeps
its sets and gains the `("amaru","patched")` row.

## 6. Tests

Python (`PYTHONPATH=dwarf python3 -m pytest`):
- `tests/test_measurement_presentation.py`: new field validation (bad direction, bad
  equivalence, bad unit type, unknown or chained `compare_as` rejected); every raw id from the
  four workload runs (§5.5 fixture sets) binds with no fallback card; unit guard routes
  `pending_depth` by unit; `_primary` sums a verdict dict; live cards for the two approximate
  pairs keep their original titles.
- `tests/test_measurement_compare.py` (new): `_catalog_index` emits concepts/bindings lists and
  `compare_as`; `_metric_cell` sums verdict dicts; group key and label carry `mode`; `runs`
  filter + concept keying merges an Amaru and a cardano record on one row, two groups, correct
  direction, approximate flag from `compare_as`; no picks → unchanged rows and `focus_required`.
- `tests/test_render_viewer_presentation_catalog.py` (new): generator idempotent, preserves
  non-catalog payload keys, `--check` detects drift, both html copies identical after path
  normalisation.

JS (`node tests/browser/test_portable_compare_concepts.cjs`, Node vm, same extraction pattern
as `test_antithesis_import.cjs`): synthetic Amaru and cardano records with different
scenario ids and the real embedded catalog: no `runs` → `focus_required` true; `runs` set →
one `keepalive-round-trip-time` row with two cells, `header-validation-verdicts` value 205
vs 127, approximate flag on `chain-advance-count` and no preferred highlight on it,
`txsubmission-pending-depth` only takes the transactions-unit id and the default view titles
the bare `pending_depth` by unit, bar labels carry `stock`/`patched`, Antithesis hook still
wins for Antithesis records, concept rows carry contributor `metric_id`/`raw_unit`/`boundary`.

Render (`node tests/browser/test_portable_compare_picks_browser.cjs`, Playwright, same
pattern as `test_antithesis_import_browser.cjs`): Pick-runs disclosure lists runs grouped by
workload; picking one run from each workload renders concept cards with two bars; the
`.mc-bar__source` tooltip is hidden by default and visible on hover/focus with the raw id.
Runs where Playwright is available (CI or cardano-box); the plan records if it could not run.

## 7. Acceptance

On https://pragma-org.github.io/dwarf/ Compare: pick amaru 20261029 stock + patched and
cardano-node 11.1.2 stock + patched → one page; "Block application time", "KeepAlive
round-trip time", "Restart recovery time", "Plutus VM execution time", "Header validation
verdicts", "Protocol receive and decode time" each show four bars labelled
`Amaru 10.11.20261029 · stock`, `… · patched`, `Cardano 11.1.2 · stock`, `… · patched` (or
fewer where a build has no tap) on one axis; hovering the Amaru keepalive bar shows
`keepalive_round_trip`, the cardano bar `keepalive_rtt`; "Selected-chain advances" shows the ≈
mark on hover and no preferred highlight; clearing picks returns to the one-workload view. No
metric id from the four runs appears under "Other metrics", in picks or in the default view.

## 8. Left to the builder

Exact CSS for the tooltip and pick strip (follow `mc-*` conventions in the file), the
ordering of picked runs (session order), pagination of the pick list beyond 25 runs
(reuse `navigation`), wording of boundary sentences for the new bindings (one sentence each,
factual, naming the trace or event), and whether the generator rewrites `generated_at_utc`
(recommended: yes, UTC now).
