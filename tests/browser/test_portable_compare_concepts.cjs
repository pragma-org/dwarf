// Offline viewer compare model: picked runs from different workloads land on
// concept-keyed rows (Amaru and cardano-node on one row, one cell per build).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8');
const context = vm.createContext({crypto: webcrypto, TextEncoder, TextDecoder, Blob, DecompressionStream, Uint8Array, ArrayBuffer, setTimeout, clearTimeout, console});
for (const name of ['portable-archive', 'portable-detail', 'portable-store']) {
  const source = html.match(new RegExp('<script data-inlined="js/' + name + '\\.js">([\\s\\S]*?)</script>'))[1];
  vm.runInContext(source, context);
}
vm.runInContext(fs.readFileSync(path.join(root, 'docs/antithesis-import.js'), 'utf8'), context);
const storeApi = context.DwarfPortableStore;
// Values cross the vm realm boundary, so compare by serialization rather than prototype-strict deepEqual.
const same = (actual, expected, message) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
const catalog = JSON.parse(html.match(/<script type="application\/json" data-portable-viewer-payload>([\s\S]*?)<\/script>/)[1]).presentation_catalog;

function record(runId, impl, mode, scenario, metrics, extra) {
  const cells = {};
  for (const [id, raw] of Object.entries(metrics)) cells[id] = storeApi.metricCell(raw);
  return Object.assign({
    run_id: runId, implementation: impl, version: 'v1', source_revision: 'a'.repeat(40),
    image_digest: 'sha256:' + (mode === 'patched' ? 'c' : 'd').repeat(64), mode,
    scenario_id: scenario, scenario_behavior_sha256: 's'.repeat(64), runtime: 'devnet',
    measurement_profile_id: impl + '-' + mode, measurement_profile_digest: 'p'.repeat(64),
    collector_digests: ['k'.repeat(64)], collector_missing: [], profile_id: 'profile-x', profile_sha256: 'q'.repeat(64),
    report_schema_version: 'v1', provenance_state: 'complete', identity_missing: [], identity_source: 'report',
    exit_status: 'pass', verdict: 'pass', ended_at: '2026-11-04T01:00:00Z', evidence_state: 'bound',
    sources: [], session_order: 0, metrics: cells,
  }, extra || {});
}
const us = median => ({status: 'available', unit: 'us', median, sample_count: 10});
const count = (unit, value) => ({status: 'available', unit, value});

const amaru = record('a1', 'amaru', 'stock', 'amaru-measurement-workload-stock-portable', {
  keepalive_round_trip: us(585), header_validation: count('count', {accepted: 205, rejected: 0}), chain_tip_updates: count('events', 160)});
const cardano = record('c1', 'cardano-node', 'stock', 'cardano-measurement-workload-stock-portable', {
  keepalive_rtt: us(569.5), header_validation: count('count', {accepted: 127, rejected: 0}), adopted_blocks: count('blocks', 173)}, {session_order: 1});
const amaruPatched = record('a2', 'amaru', 'patched', 'amaru-measurement-workload-stock-portable', {
  pending_depth: {status: 'available', unit: 'requests', median: 0, sample_count: 9},
  'amaru-patched-txsubmission-residence.pending_depth': {status: 'available', unit: 'transactions', median: 0, sample_count: 4},
  'amaru-patched-header-validation.header_validation': us(40)}, {session_order: 2});

// Gate: two workloads, nothing picked → focus required, no concept rows.
let r = storeApi.buildComparison([amaru, cardano], {}, catalog);
assert.equal(r.focus_required, true);
assert.ok(!r.rows.some(row => row.concept_grouped), 'no concept rows without picks');

// Picks lift the gate and key rows by concept.
r = storeApi.buildComparison([amaru, cardano, amaruPatched], {runs: 'a1,c1,a2', show_unmeasured: '1'}, catalog);
assert.equal(r.focus_required, false);
assert.equal(r.groups.length, 3);
same(r.groups.map(g => g.label_short).sort(), ['Amaru v1 · patched', 'Amaru v1 · stock', 'Cardano v1 · stock']);
same(r.groups.map(g => g.mode).sort(), ['patched', 'stock', 'stock']);
assert.equal(r.picked_run_count, 3); assert.equal(r.picked_scenario_count, 2);
const ka = r.rows.find(row => row.metric_id === 'keepalive-round-trip-time');
assert.ok(ka, 'keepalive concept row'); assert.equal(ka.concept_grouped, true);
assert.equal(ka.comparison_direction, 'lower'); assert.equal(ka.approximate, false);
same(Object.values(ka.cells).map(c => c.value).filter(v => v !== null).sort((a, b) => a - b), [569.5, 585]);
same([...new Set(Object.values(ka.cells).flatMap(c => c.contributors.map(x => x.metric_id)))].sort(), ['keepalive_round_trip', 'keepalive_rtt']);
const contributor = Object.values(ka.cells).flatMap(c => c.contributors).find(c => c.metric_id === 'keepalive_rtt');
assert.equal(contributor.raw_unit, 'us'); assert.ok(contributor.boundary.length > 0); assert.equal(contributor.approximate, false);
const verdicts = r.rows.find(row => row.metric_id === 'header-validation-verdicts');
assert.ok(verdicts, 'verdict count row');
same(Object.values(verdicts.cells).map(c => c.value).filter(v => v !== null).sort((a, b) => a - b), [127, 205]);
assert.equal(r.rows.filter(row => /^header-validation/.test(row.metric_id)).length, 2, 'count and us header rows stay apart');
const adv = r.rows.find(row => row.metric_id === 'chain-advance-count');
assert.ok(adv, 'approximate target row'); assert.equal(adv.approximate, true); assert.equal(adv.comparison_direction, 'neutral'); assert.equal(adv.unit, 'events');
same(Object.values(adv.cells).map(c => c.value).filter(v => v !== null).sort((a, b) => a - b), [160, 173]);
const depth = r.rows.filter(row => row.metric_id === 'txsubmission-pending-depth');
assert.equal(depth.length, 1); assert.equal(depth[0].unit, 'transactions');
assert.ok(r.rows.some(row => row.metric_id === 'blockfetch-pending-depth' && row.unit === 'requests'));
assert.ok(!r.disagreements.some(d => d.metric_id === 'chain-advance-count'), 'approximate rows never disagree');

// Default view titles by unit even without picks.
const plain = storeApi.buildComparison([amaruPatched], {scenario: amaruPatched.scenario_id, show_unmeasured: '1'}, catalog);
assert.ok(plain.rows.some(row => row.metric_id === 'pending_depth' && row.title === 'BlockFetch pending depth'));
assert.ok(plain.rows.some(row => row.metric_id === 'amaru-patched-txsubmission-residence.pending_depth' && row.title === 'TxSubmission pending depth'));

// Unknown pick → nothing, no throw.
const none = storeApi.buildComparison([amaru, cardano], {runs: 'nope'}, catalog);
assert.equal(none.rows.length, 0); assert.equal(none.picked_run_count, 0); assert.equal(none.focus_required, false);

// Antithesis records keep their own hook (scenario-keyed), never merging with devnet rows.
const anti = record('x1', 'amaru', 'patched', 'anti', {keepalive_round_trip: us(5)}, {runtime: 'antithesis', derived_import_identity: 'f'.repeat(64)});
const mixed = storeApi.buildComparison([amaru, anti], {runs: 'a1,x1', show_unmeasured: '1'}, catalog);
assert.equal(mixed.rows.filter(row => /keepalive/.test(row.metric_id)).length, 2);

// Direct hook and helpers.
assert.equal(storeApi.canonicalUnit('microseconds', catalog), 'us');
assert.equal(storeApi.catalogConcept(amaru, 'keepalive_round_trip', amaru.metrics.keepalive_round_trip, catalog).id, 'keepalive-round-trip-time');
assert.equal(storeApi.catalogConcept(amaru, 'keepalive_round_trip_by_outcome', {unit: 'us'}, catalog), null, 'companions are never rows');
console.log('PASS: concept-keyed cross-implementation compare model');
