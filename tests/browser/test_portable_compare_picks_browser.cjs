// Offline viewer: pick runs across workloads, mode pills on bars, hover source tooltip.
// Run: NODE_PATH=<dir with playwright> [DWARF_BROWSER_CHANNEL=chrome] node tests/browser/test_portable_compare_picks_browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {gzipSync} = require('node:zlib');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');

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
function archive(runId, impl, mode, scenario, metrics) {
  const identity = {implementation: impl, version: '1.0.0', source_revision: 'a'.repeat(40), image_digest: 'sha256:' + (mode === 'patched' ? 'c' : 'd').repeat(64)};
  const manifest = {run_id: runId, runtime: 'devnet', scenario: {id: scenario}, target: identity, ended_at: '2026-11-04T01:00:00Z', exit_status: 'pass'};
  const report = {schema_version: 'v1', scenario, target_identity: {...identity, mode}, measurements: metrics};
  const selection = {resolution: {target_identity: {...identity, mode}, profile: {id: impl + '-' + mode, definition_digest: 'p'.repeat(64)}, resolved: []}};
  const file = path.join(os.tmpdir(), runId + '.tar.gz');
  fs.writeFileSync(file, gzipSync(tar({[runId + '/manifest.json']: manifest, [runId + '/measurements/report.json']: report, [runId + '/measurements/selection.json']: selection})));
  return file;
}
const us = median => ({status: 'available', unit: 'us', median, sample_count: 10, mean: median, p95: median, p99: median});
const amaruFile = archive('a1', 'amaru', 'stock', 'amaru-measurement-workload-stock-portable',
  {keepalive_round_trip: us(585), header_validation: {status: 'available', unit: 'count', value: {accepted: 205, rejected: 0}},
   mempool_evicted: {status: 'unavailable', unit: 'events', reason: 'no evictions observed'}});
const cardanoFile = archive('c1', 'cardano-node', 'stock', 'cardano-measurement-workload-stock-portable',
  {keepalive_rtt: us(569.5), header_validation: {status: 'available', unit: 'count', value: {accepted: 127, rejected: 0}}});

// Serve only the viewer files: without docs/evidence the default archives never load, so the
// two synthetic runs are the whole session and the test does not race a 30 MB download.
const serveDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dwarf-viewer-'));
for (const name of ['index.html', 'antithesis-import.js', 'portable-defaults.js']) fs.copyFileSync(path.join(root, 'docs', name), path.join(serveDir, name));
const server = spawn('python3', ['-u', '-c', `
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=${JSON.stringify(serveDir)}))
print(server.server_port, flush=True)
server.serve_forever()
`], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});

(async () => {
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error('Server startup timeout')), 15000);
      server.on('error', reject); server.once('exit', code => reject(new Error('Server exited: ' + code)));
      server.stdout.on('data', chunk => { output += chunk; if (/^\d+\n/.test(output)) { clearTimeout(timer); resolve(Number(output.trim())); } });
    });
    browser = await chromium.launch({headless: true, ...(process.env.DWARF_BROWSER_CHANNEL ? {channel: process.env.DWARF_BROWSER_CHANNEL} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    page.setDefaultTimeout(10000);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:' + port + '/');
    await page.waitForFunction(() => window.DwarfPortableSession && window.DwarfPortableStore);
    await page.locator('.portable-import__disclosure > summary').click();
    await page.locator('[data-import-files]').setInputFiles([amaruFile, cardanoFile]);
    await page.locator('[data-import-open]').click();
    await page.waitForFunction(() => window.DwarfPortableSession.store.snapshot().records.filter(r => ['a1', 'c1'].includes(r.run_id)).length === 2);
    await page.evaluate(() => { location.hash = '#compare'; });
    await page.waitForFunction(() => document.body.getAttribute('data-offline-page') === 'comparison');

    // Default: first workload auto-selected, the other hidden.
    await page.locator('.mc-metric[data-metric="keepalive_round_trip"]').waitFor();
    assert.equal(await page.locator('.mc-metric[data-metric="keepalive_rtt"]').count(), 0, 'one workload at a time by default');
    assert.equal(await page.locator('[data-compare-filter="scenario"]').isDisabled(), false);

    // Pick runs across both workloads.
    const picker = page.locator('[data-compare-pick-runs]');
    await picker.locator('> summary').click();
    assert.equal(await picker.locator('fieldset').count(), 2, 'one fieldset per workload');
    const legends = await picker.locator('legend').allTextContents();
    assert.notEqual(legends[0], legends[1], 'workload legends must be told apart: ' + legends.join(' | '));
    await picker.locator('[data-compare-pick="a1"]').check();
    await picker.locator('[data-compare-pick="c1"]').check();
    await page.waitForFunction(() => /2 runs picked across 2 workloads/.test((document.querySelector('[data-compare-pick-summary]') || {}).textContent || ''));
    assert.match(await page.locator('[data-compare-pick-summary]').textContent(), /2 concept rows with values · 1 without a value hidden/, 'summary counts hidden valueless rows');
    await page.locator('.mc-workload-picker > summary').click();   // the strip lives in the (lazy) Picked runs disclosure
    await page.locator('.mc-picks').waitFor();                     // the details toggle event is asynchronous
    assert.equal(await page.locator('.mc-picks .mc-pick').count(), 2, 'pick strip replaces workload pills');
    assert.equal(await page.locator('.mc-workload').count(), 0, 'no workload pills while picks are active');
    assert.equal(await page.locator('[data-compare-filter="scenario"]').isDisabled(), true);

    const card = page.locator('.mc-metric[data-metric="keepalive-round-trip-time"]');
    await card.waitFor();
    assert.equal(await page.locator('.mc-note', {hasText: 'Antithesis measurements are grouped'}).count(), 0, 'no Antithesis note without Antithesis runs');
    assert.equal(await card.locator('.mc-bar').count(), 2, 'Amaru and cardano on one card');
    assert.equal(await card.locator('.mc-bar__label .mc-pill--mode').first().textContent(), 'stock');
    const verdicts = page.locator('.mc-metric[data-metric="header-validation-verdicts"]');
    assert.equal(await verdicts.locator('.mc-value').count(), 2, 'verdict dicts read as sums on both bars');

    // Hover source: hidden by default, visible on hover, names the raw id.
    const firstBar = card.locator('.mc-bar').first(), tip = firstBar.locator('.mc-bar__source');
    assert.equal(await tip.isVisible(), false, 'tooltip hidden until hover');
    await firstBar.hover();
    await tip.waitFor({state: 'visible'});
    assert.match(await tip.textContent(), /Amaru · keepalive_round_trip · us/);
    await card.locator('.mc-bar').nth(1).hover();
    assert.match(await card.locator('.mc-bar').nth(1).locator('.mc-bar__source').textContent(), /Cardano · keepalive_rtt · us/);
    assert.equal(await card.locator('.mc-best').count(), 1, 'exact rows still highlight the preferred value');

    // Under picks the Metric filter offers concept ids, so one card can be isolated.
    await page.locator('[data-compare-filter-disclosure] > summary').click();
    const metricOptions = await page.locator('[data-compare-filter="metric"] option').allTextContents();
    assert.ok(metricOptions.includes('keepalive-round-trip-time'), 'metric filter lists concept ids under picks: ' + metricOptions.join(','));
    await page.locator('[data-compare-filter="metric"]').selectOption('keepalive-round-trip-time');
    await page.waitForFunction(() => document.querySelectorAll('.mc-metric').length === 1);
    await page.locator('[data-compare-filter="metric"]').selectOption('');
    await page.locator('[data-compare-filter-disclosure] > summary').click();

    // Clear picks restores the one-workload view.
    await page.locator('[data-compare-pick-clear]').click();
    await page.locator('.mc-metric[data-metric="keepalive_round_trip"]').waitFor();
    assert.equal(await page.locator('.mc-picks').count(), 0);
    assert.equal(await page.locator('[data-compare-filter="scenario"]').isDisabled(), false);
    assert.deepEqual(errors, []);
    await page.screenshot({path: path.join(os.tmpdir(), 'dwarf-compare-picks.png')});
    console.log('PASS: pick runs across workloads, mode pills and hover source');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; server.kill(); });
