const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const {gzipSync} = require('node:zlib');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8');
const context = vm.createContext({crypto: webcrypto, TextEncoder, TextDecoder, Blob,
  DecompressionStream, Uint8Array, ArrayBuffer, setTimeout, clearTimeout, console});
for (const name of ['portable-archive', 'portable-detail', 'portable-store']) {
  const source = html.match(new RegExp('<script data-inlined="js/' + name + '\\.js">([\\s\\S]*?)</script>'))[1];
  vm.runInContext(source, context);
}
const modulePath = path.join(root, 'docs/antithesis-import.js');
assert.ok(fs.existsSync(modulePath), 'separate Antithesis adapter must exist');
vm.runInContext(fs.readFileSync(modulePath, 'utf8'), context);
const api = context.DwarfPortableArchive, storeApi = context.DwarfPortableStore;
function tar(files) {
  const chunks = [];
  for (const [name, value] of Object.entries(files)) {
    const data = Buffer.from(typeof value === 'string' ? value : JSON.stringify(value));
    const h = Buffer.alloc(512); h.write(name); h.write('0000644\0', 100);
    h.write(data.length.toString(8).padStart(11, '0') + '\0', 124);
    h.fill(32, 148, 156); h[156] = 48; h.write('ustar\0', 257);
    const sum = h.reduce((a, b) => a + b, 0);
    h.write(sum.toString(8).padStart(6, '0') + '\0 ', 148);
    chunks.push(h, data, Buffer.alloc((512 - data.length % 512) % 512));
  }
  return Buffer.concat([...chunks, Buffer.alloc(1024)]);
}
function file(bytes, name = 'input.tar.gz') {
  const blob = new Blob([gzipSync(bytes)]); blob.name = name; return blob;
}
function fixture(service, impl, metrics) {
  const identity = {implementation: impl, version: 'test-version', source_revision: 'a'.repeat(40),
    image_digest: 'sha256:' + 'b'.repeat(64), mode: 'patched', engine: 'antithesis', non_authoritative_performance: true};
  const report = {schema_version: 'v1', scenario: 'example', target_identity: identity, measurements: metrics};
  return {[service + '/snapshot.json']: {service, source_available: true,
    performance_authority: 'Antithesis virtualized execution; not hardware benchmarking',
    window: 'bounded recent trace history; overlapping snapshots are not additive',
    source_window: {incomplete: true},
    summary: {resolution: {target_identity: {...identity}}, collector_errors: [], collector_results: {}}},
    [service + '/measurements/report.json']: report};
}
const metric = {status: 'available', unit: 'microseconds', sample_count: 7, median: 2.375, mean: 2.4, p95: 3};
(async () => {
  const input = file(tar({...fixture('sm-amaru-victim', 'amaru', {block_fetch: metric, handler: metric}),
    ...fixture('sm-cardano-victim', 'cardano-node', {block_application: metric})}));
  const outcome = await api.importArchiveFiles({tarball: input});
  assert.equal(outcome.archiveError, null);
  assert.equal(outcome.models.length, 2);
  assert.equal(outcome.verification.verdict, 'unsigned');
  const amaru = outcome.models.find(m => m.manifest.target.implementation === 'amaru');
  assert.equal(amaru.measurementReport.measurements.block_fetch.unit, 'us');
  assert.equal(amaru.measurementReport.measurements.block_fetch.median, metric.median);
  assert.equal(amaru.measurementReport.measurements.block_fetch.sample_count, 7);
  assert.ok(amaru.measurementReport.measurements.handler);
  assert.equal(context.DwarfAntithesisImport.metricName('amaru-patched-header-validation.header_validation'), 'amaru-patched-header-validation.header_validation');
  assert.equal(amaru.manifest.runtime, 'antithesis');
  assert.equal(amaru.manifest.started_at, null);
  assert.equal(amaru.manifest.execution.security_verdict, 'unknown');
  assert.equal(amaru.manifest.provenance.status, 'derived-antithesis-import');
  assert.equal(amaru.chain.manifest_hash, undefined, 'conversion must not invent an original chain');
  assert.equal(amaru.identityConsistent, null);
  assert.equal(amaru.manifestHashMatches, null);
  assert.equal(amaru.manifest.provenance.source_window.incomplete, true);
  assert.match(amaru.manifest.provenance.window, /not additive/);
  assert.equal(amaru.measurementSelection.resolution.target_identity.version, 'test-version');
  assert.equal((await storeApi.recordFromModel(amaru)).evidence_state, 'absent');
  const store = storeApi.createRunStore();
  const first = await storeApi.importBatch({archives: [input], store});
  assert.equal(first.added, 2); assert.equal(first.failed, 0); assert.equal(first.completed, first.total);
  const again = await storeApi.importBatch({archives: [input], store});
  assert.equal(again.duplicate, 2); assert.equal(store.snapshot().records.length, 2);
  const three = file(tar({...fixture('one', 'amaru', {block_fetch: metric}),
    ...fixture('two', 'amaru', {block_fetch: metric}), ...fixture('three', 'cardano-node', {block_application: metric})}));
  const partialStore = storeApi.createRunStore(), add = partialStore.add;
  let calls = 0;
  partialStore.add = value => {if (++calls === 2) throw new Error('simulated target storage failure'); return add(value);};
  const partial = await storeApi.importBatch({archives: [three], store: partialStore});
  assert.equal(partial.total, 3); assert.equal(partial.completed, 3);
  assert.equal(partial.failed, 1); assert.equal(partial.added, 2); assert.equal(partial.results.length, 3);
  const mismatch = fixture('sm-amaru-victim', 'amaru', {block_fetch: metric});
  mismatch['sm-amaru-victim/snapshot.json'].summary.resolution.target_identity.version = 'different';
  const mismatchedStore = storeApi.createRunStore();
  await storeApi.importBatch({archives: [file(tar(mismatch))], store: mismatchedStore});
  assert.equal(mismatchedStore.snapshot().entries[0].record.provenance_state, 'incoherent');
  const original = file(tar({'normal/manifest.json': {run_id: 'normal', runtime: 'devnet'},
    'normal/measurements/report.json': {schema_version: 'v1', measurements: {block_fetch: metric}}}));
  const ordinary = await api.importArchiveFiles({tarball: original});
  assert.equal(ordinary.model.runId, 'normal'); assert.equal(ordinary.models, undefined);
  assert.equal(ordinary.model.measurementReport.measurements.block_fetch.unit, 'microseconds');
  const other = await api.importArchiveFiles({tarball: file(tar({'a/thing.json': {}, 'b/thing.json': {}}))});
  assert.ok(other.archiveError); assert.equal(other.model, null);
  const unsafe = await api.importArchiveFiles({tarball: file(tar({'../snapshot.json': {}}))});
  assert.ok(unsafe.archiveError);
  const invalid = fixture('sm-amaru-victim', 'amaru', {block_fetch: metric});
  invalid['sm-amaru-victim/measurements/report.json'].target_identity.engine = 'devnet';
  const bad = await api.importArchiveFiles({tarball: file(tar(invalid))});
  assert.ok(bad.archiveError); assert.equal(bad.model, null);
  if (process.argv[2]) {
    const real = new Blob([fs.readFileSync(process.argv[2])]); real.name = 'dwarf-taps.tar.gz';
    const result = await api.importArchiveFiles({tarball: real});
    assert.equal(result.archiveError, null); assert.equal(result.models.length, 2);
    const expanded = await api.gunzipWithLimit(real);
    for (const model of result.models) {
      const originalEntry = model.entries.find(e => e.path.endsWith('/measurements/report.json'));
      const source = JSON.parse(api.entryText(expanded, originalEntry));
      const identity = model.manifest.target;
      assert.equal(JSON.stringify(identity), JSON.stringify(source.target_identity));
      for (const [name, value] of Object.entries(source.measurements)) {
        const id = context.DwarfAntithesisImport.metricName(name, identity.implementation);
        const converted = model.measurementReport.measurements[id];
        const expected = JSON.parse(JSON.stringify(value).replace(/"unit":"microseconds"/g, '"unit":"us"'));
        assert.equal(JSON.stringify(converted), JSON.stringify(expected), name);
      }
      assert.equal(Object.keys(model.measurementReport.measurements).length, Object.keys(source.measurements).length);
      console.log('Retained archive:', identity.implementation, identity.version, Object.keys(source.measurements).length, 'metrics verified');
    }
    const imported = await storeApi.importBatch({archives: [real], store: storeApi.createRunStore()});
    assert.equal(imported.added, 2); assert.equal(imported.failed, 0);
    const comparisonStore = storeApi.createRunStore();
    await storeApi.importBatch({archives: [real], store: comparisonStore});
    const records = comparisonStore.snapshot().records;
    const catalog = JSON.parse(html.match(/<script type="application\/json" data-portable-viewer-payload>([\s\S]*?)<\/script>/)[1]).presentation_catalog;
    const compared = storeApi.buildComparison(records, {show_unmeasured: '1', baseline: 'run|' + records[0].run_id}, catalog);
    const header = compared.rows.filter(r => r.metric_id === 'header_validation_time');
    assert.equal(header.length, 1, 'both native header timings must share one concept card');
    assert.deepEqual(Object.values(header[0].cells).map(c => c.value).sort((a,b) => a-b), [24.26, 34.4515]);
    assert.equal(header[0].comparison_direction, 'neutral');
    assert.ok(Object.values(header[0].cells).every(c => c.delta_percent === null));
    const ids = Object.values(header[0].cells).flatMap(c => c.contributors.map(x => x.metric_id));
    assert.ok(ids.includes('amaru-patched-header-validation.header_validation'));
    assert.ok(ids.includes('cardano-patched-header-validation.header_validation'));
    const handlers = compared.rows.find(r => r.metric_id === 'blockfetch_handler_time');
    assert.equal(Object.values(handlers.cells).filter(c => c.value !== null).length, 2);
    assert.equal(new Set(Object.values(handlers.cells).flatMap(c => c.contributors.map(x => x.boundary))).size, 2);
    const keepalive = compared.rows.find(r => r.metric_id === 'keepalive_round_trip' && r.concept_grouped);
    assert.deepEqual(Object.values(keepalive.cells).map(c => c.state).sort(), ['available', 'unavailable']);
    assert.ok(compared.rows.some(r => r.metric_id === 'queue_residence_lower_bound'));
    assert.ok(compared.rows.some(r => r.metric_id === 'queue_residence_upper_bound'));
    assert.ok(compared.rows.some(r => r.metric_id === 'queue_residence'));
    assert.equal(compared.disagreements.filter(r => ['header_validation_time','blockfetch_handler_time','protocol_receive_decode'].includes(r.metric_id)).length, 0);
    const filtered = storeApi.buildComparison(records, {metric: 'header_validation_time'}, catalog);
    assert.equal(filtered.rows.length, 1); assert.equal(Object.values(filtered.rows[0].cells).filter(c => c.value !== null).length, 2);
    const repeat = {...records[0], run_id: records[0].run_id + '-snapshot2'};
    const repeated = storeApi.buildComparison([...records, repeat], {}, catalog);
    assert.equal(repeated.groups.length, 3, 'snapshots of the same build must never pool samples');
    assert.ok(repeated.rows.flatMap(r => Object.values(r.cells)).every(c => c.run_count <= 1));
    console.log('PASS: shared concepts, native IDs, boundary labels, missing samples, no ranking or snapshot pooling');

  }
  if (process.argv[3]) {
    const baseline = vm.createContext({crypto: webcrypto, TextEncoder, TextDecoder, Blob, DecompressionStream, Uint8Array, ArrayBuffer, setTimeout, clearTimeout});
    const oldHtml = execFileSync('git', ['show', 'fcbaafcf:docs/index.html'], {cwd: root, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024});
    const source = oldHtml.match(/<script data-inlined="js\/portable-archive.js">([\s\S]*?)<\/script>/)[1];
    vm.runInContext(source, baseline);
    const normal = new Blob([fs.readFileSync(process.argv[3])]); normal.name = path.basename(process.argv[3]);
    const before = await baseline.DwarfPortableArchive.importArchiveFiles({tarball: normal});
    const after = await api.importArchiveFiles({tarball: normal});
    assert.equal(JSON.stringify(after), JSON.stringify(before), 'ordinary hosted archive output must be identical');
    for (const name of ['portable-detail', 'portable-store']) {
      vm.runInContext(oldHtml.match(new RegExp('<script data-inlined="js/' + name + '\\.js">([\\s\\S]*?)</script>'))[1], baseline);
    }
    const catalog = JSON.parse(html.match(/<script type="application\/json" data-portable-viewer-payload>([\s\S]*?)<\/script>/)[1]).presentation_catalog;
    const normalRecord = await storeApi.recordFromModel(after.model);
    const oldComparison = baseline.DwarfPortableStore.buildComparison([normalRecord], {}, catalog);
    const newComparison = storeApi.buildComparison([normalRecord], {}, catalog);
    assert.equal(JSON.stringify(newComparison), JSON.stringify(oldComparison), 'ordinary comparison remains identical');
    console.log('Retained hosted archive: import and comparison outputs identical to unchanged viewer');
  }
  console.log('PASS: Antithesis multi-target conversion, metric semantics, provenance, deduplication, ordinary imports and rejected formats');
})().catch(error => {console.error(error); process.exitCode = 1;});
