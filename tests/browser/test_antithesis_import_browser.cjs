const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const server = spawn('python3', ['-u', '-c', `
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=${process.env.DWARF_PREVIEW_ROOT === '1' ? "'.'" : "'docs'"}))
print(server.server_port, flush=True)
server.serve_forever()
`], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
(async () => {
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error('Server startup timeout')), 15000);
      server.on('error', reject); server.once('exit', code => reject(new Error('Server exited: ' + code)));
      server.stdout.on('data', chunk => { output += chunk; if (/^\d+\n/.test(output)) {clearTimeout(timer); resolve(Number(output.trim()));} });
    });
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.DWARF_PREVIEW_URL || 'http://127.0.0.1:' + port + '/');
    await page.waitForFunction(() => window.DwarfPortableSession && window.DwarfAntithesisImport);
    if (!process.argv.includes('--defaults')) {
      await page.locator('.portable-import__disclosure > summary').click();
      await page.locator('[data-import-files]').setInputFiles(process.argv[2]);
      await page.locator('[data-import-open]').click();
    }
    await page.waitForFunction(() => window.DwarfPortableSession.store.snapshot().records.filter(r => r.runtime === 'antithesis').length === 2);
    const records = await page.evaluate(() => window.DwarfPortableSession.store.snapshot().records.filter(r => r.runtime === 'antithesis'));
    if (process.argv.includes('--defaults')) {
      assert.ok(records.every(r => r.sources.includes('antithesis-taps-20261022.tar.gz')));
      const ordinary = await page.evaluate(() => DwarfPortableSession.store.snapshot().records.filter(r => r.runtime !== 'antithesis'));
      assert.ok(ordinary.some(r => r.run_id === '20261017T195819Z-6721da4e'));
      console.log('Default evidence loaded automatically, including both Antithesis targets and retained stock runs');
    }
    assert.deepEqual(records.map(r => r.implementation).sort(), ['amaru', 'cardano-node']);
    assert.equal(records.reduce((sum, r) => sum + Object.keys(r.metrics).length, 0), 63);
    assert.ok(records.every(r => r.evidence_state === 'absent'));
    assert.ok(records.every(r => r.provenance_state === 'complete'));
    const retained = await page.evaluate(() => DwarfPortableSession.store.snapshot().entries
      .filter(e => e.record.runtime === 'antithesis').map(e => ({chain: e.model.chain,
        identity: e.model.identityConsistent, hash: e.model.manifestHashMatches,
        window: e.model.manifest.provenance.window})));
    assert.ok(retained.every(e => !e.chain.manifest_hash && e.identity === null && e.hash === null && /not additive/.test(e.window)));
    await page.evaluate(() => { location.hash = '#compare'; });
    await page.locator('[data-compare-filter-disclosure] > summary').click();
    await page.locator('[data-compare-filter="scenario"]').selectOption('cardano-amaru-miniprotocol-security-local');
    await page.locator('[data-compare-filter="rollup"]').selectOption('run');
    await page.locator('[data-compare-filter-disclosure] > summary').click();
    const comparison = await page.evaluate(() => {
      const payload = JSON.parse(document.querySelector('[data-portable-viewer-payload]').textContent);
      return DwarfPortableStore.buildComparison(DwarfPortableSession.store.snapshot().records,
        {scenario: 'cardano-amaru-miniprotocol-security-local', rollup: 'run'}, payload.presentation_catalog);
    });
    assert.equal(comparison.excluded_runs.length, 0);
    assert.equal(comparison.groups.length, 2);
    const headerCard = page.locator('article[data-metric="header_validation_time"]');
    assert.equal(await headerCard.count(), 1);
    assert.equal(await headerCard.locator('.mc-value').count(), 2);
    assert.match(await headerCard.innerText(), /34\.452/);
    assert.match(await headerCard.innerText(), /24\.26/);
    assert.equal(await headerCard.locator('.mc-best').count(), 0);
    await headerCard.locator('summary').click();
    await headerCard.getByText(/amaru-patched-header-validation.header_validation/).waitFor({state: 'visible'});
    assert.match(await headerCard.innerText(), /amaru-patched-header-validation.header_validation/);
    assert.match(await headerCard.innerText(), /cardano-patched-header-validation.header_validation/);
    assert.match(await headerCard.innerText(), /40 samples/);
    assert.match(await headerCard.innerText(), /47 samples/);
    await headerCard.locator('summary').click();
    await page.locator('[data-compare-filter-disclosure] > summary').click();
    await page.locator('[data-compare-filter="metric"]').selectOption('header_validation_time');
    assert.equal(await page.locator('article.mc-metric').count(), 1);
    assert.equal(await page.locator('article.mc-metric .mc-value').count(), 2);
    await page.locator('[data-compare-filter="metric"]').selectOption('');
    await page.locator('[data-compare-filter-disclosure] > summary').click();
    await page.screenshot({path: '/private/tmp/dwarf-antithesis-import-comparison.png', fullPage: false});
    await page.setViewportSize({width: 390, height: 844});
    await headerCard.locator('summary').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile page must not overflow');
    await page.screenshot({path: '/private/tmp/dwarf-antithesis-import-mobile.png', fullPage: false});
    await page.evaluate(id => {location.hash = '#run/' + id;}, records[0].run_id);
    await page.waitForFunction(() => document.body.innerText.includes('antithesis'));
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('PASS: real archive via Import UI, 63 metrics across two targets, Comparison and run detail; no browser errors');
  } finally {if (browser) await browser.close(); server.kill();}
})().catch(error => {console.error(error); process.exitCode = 1; server.kill();});
