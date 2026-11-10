// Offline viewer: default runs show the manifest's short title, sit in collapsible
// groups, and every row in the list has the same width.
// Run: NODE_PATH=<dir with playwright> [DWARF_BROWSER_CHANNEL=chrome] node tests/browser/test_portable_run_groups_browser.cjs
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
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
const longTitle = 'Compose a substrate scenario that combines peer-set capture pressure with rollback and fork-switch recovery';
function archive(runId, scenario, ended) {
  const identity = {implementation: 'cardano-node', version: '11.1.2', source_revision: 'a'.repeat(40), image_digest: 'sha256:' + 'd'.repeat(64)};
  const manifest = {run_id: runId, runtime: 'devnet', scenario: {id: scenario, title: longTitle}, target: identity, ended_at: ended, exit_status: 'pass'};
  return gzipSync(tar({[runId + '/manifest.json']: manifest}));
}

const serveDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dwarf-viewer-groups-'));
for (const name of ['index.html', 'antithesis-import.js', 'portable-defaults.js']) fs.copyFileSync(path.join(root, 'docs', name), path.join(serveDir, name));
fs.mkdirSync(path.join(serveDir, 'evidence/runs'), {recursive: true});
const runs = [
  {run_id: 'r1', scenario: 'runtime-substrate-chainsync-nonmonotonic-slot-example-smoke', title: 'ChainSync — slot goes back', group: 'Protocol faults', ended: '2026-11-10T01:00:00Z'},
  {run_id: 'r2', scenario: 'runtime-substrate-compound-eclipse-recovery-example-smoke', title: 'Compound — eclipse and recovery', group: 'Compound faults', ended: '2026-11-10T09:00:00Z'},
  {run_id: 'r3', scenario: 'runtime-substrate-blockfetch-invalid-range-example-smoke', title: 'BlockFetch — invalid range', group: 'Protocol faults', ended: '2026-11-10T02:00:00Z'},
];
const archives = runs.map((run) => {
  const bytes = archive(run.run_id, run.scenario, run.ended), url = 'runs/' + run.run_id + '-bundle-export.tar.gz';
  fs.writeFileSync(path.join(serveDir, 'evidence', url), bytes);
  return {url, size_bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), run_id: run.run_id, title: run.title, group: run.group};
});
fs.writeFileSync(path.join(serveDir, 'evidence/default-runs.json'), JSON.stringify({schema_version: 'dwarf.default-run-archives.v1', archives}));

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
    await page.waitForFunction(() => document.querySelectorAll('[data-offline-recent] [data-dk-item]').length === 3);

    // Short manifest titles replace the long scenario title.
    const titles = await page.locator('[data-offline-recent] .dk-item__title').allTextContents();
    assert.deepEqual(titles.slice().sort(), runs.map(r => r.title).sort(), 'manifest titles shown: ' + titles.join(' | '));
    assert.equal((await page.locator('[data-offline-recent]').textContent()).includes(longTitle), false, 'long scenario title is not the row name');

    // Runs sit in collapsible groups, in manifest order, with counts; the first group is open.
    const groups = page.locator('[data-offline-recent] details[data-run-group]');
    assert.equal(await groups.count(), 2);
    const summaries = await groups.locator('> summary').allTextContents();
    assert.match(summaries[0], /Protocol faults/); assert.match(summaries[0], /2 runs/);
    assert.match(summaries[1], /Compound faults/); assert.match(summaries[1], /1 run\b/);
    assert.equal(await groups.nth(0).getAttribute('open'), '');
    assert.equal(await groups.nth(1).getAttribute('open'), null, 'later groups start closed');

    // Every row in an open group has the same width.
    await groups.nth(1).locator('> summary').click();
    const widths = await page.locator('[data-offline-recent] [data-dk-item]').evaluateAll(nodes => nodes.map(n => Math.round(n.getBoundingClientRect().width)));
    assert.equal(new Set(widths).size, 1, 'rows share one width: ' + widths.join(','));

    // Search hides groups with no match and opens groups that match.
    await groups.nth(1).locator('> summary').click();
    await page.locator('.offline-run-filters > summary').click();
    await page.locator('[data-dk-grid-search]').fill('eclipse');
    await page.waitForFunction(() => document.querySelectorAll('[data-offline-recent] details[data-run-group]:not([hidden])').length === 1);
    assert.equal(await groups.nth(0).isHidden(), true, 'group without a match is hidden');
    assert.equal(await groups.nth(1).getAttribute('open'), '', 'matching group opens');
    assert.deepEqual(errors, []);
    await page.screenshot({path: path.join(os.tmpdir(), 'dwarf-run-groups.png'), fullPage: true});
    console.log('PASS: short titles, collapsible groups, one row width');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; server.kill(); });
