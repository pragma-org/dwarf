const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const state = fs.mkdtempSync(path.join(os.tmpdir(), 'dwarf-topology-test-'));
const server = spawn('python3', ['-u', '-c', `
from http.server import ThreadingHTTPServer
from profile_manager import dashboard
def forbidden(*args, **kwargs):
    raise AssertionError('Topology step must never execute a local command')
dashboard._cli_command = forbidden
server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.serve_dashboard_handler_factory('topology-test-token'))
print(server.server_port, flush=True)
server.serve_forever()
`], {cwd: root, env: {...process.env, PYTHONPATH: '.:dwarf', ADA2_DWARF_STATE_DIR: state,
  ADA2_DWARF_RUNS_DIR: path.join(state, 'runs'), ADA2_DWARF_BUNDLES_DIR: path.join(state, 'bundles')}, stdio: ['ignore', 'pipe', 'pipe']});
let stderr = '';
server.stderr.on('data', data => { stderr += data; });

(async () => {
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(stderr || 'Server startup timeout')), 20000);
      server.once('error', error => { clearTimeout(timeout); reject(error); });
      server.once('exit', code => { clearTimeout(timeout); reject(new Error(`Server exited ${code}: ${stderr}`)); });
      server.stdout.once('data', data => { clearTimeout(timeout); resolve(Number(data.toString().trim())); });
    });
    const origin = `http://127.0.0.1:${port}`;
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    const errors = [], localRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (/\/api\/(run|topology|deploy|remove)[/\?]?/.test(request.url())) localRequests.push(request.url()); });
    const control = name => page.locator(`#antithesis-${name}`);
    const waitState = expected => page.waitForFunction(expected => document.querySelector('#antithesis-topology-state').dataset.state === expected, expected);
    const resolve = async expected => { await control('resolve-topology').click(); await waitState(expected); };
    await page.goto(origin + '/run/antithesis');
    await page.waitForFunction(() => !document.querySelector('#antithesis-resolve-topology').disabled);
    await waitState('pending');
    assert.equal(await control('topology-choice').innerText(), 'Scenario default');
    assert.equal(await control('profile').isVisible(), false);
    assert.equal(await control('reset-topology').isVisible(), false);
    await page.locator('#antithesis-topology-override > summary').click();
    await resolve('pending');
    assert.equal(await control('save-topology').isDisabled(), true);
    await control('profile').selectOption('local:profile-l-amaru-closed-devnet');
    await resolve('blocked');
    assert.match(await control('topology-state').innerText(), /Local profiles/);
    await page.locator('[name="topology-source"][value="new"]').check();
    await control('config-name').fill('Saved remote <control>');
    for (const [template, count] of [['cardano-control-v1', 9], ['amaru-control-v1', 14], ['mixed-control-v1', 14]]) {
      await control('template').selectOption(template);
      await resolve('resolved');
      const details = JSON.parse(await control('topology-details').textContent());
      assert.equal(details.graph.services.length, count);
      assert.equal(await control('save-topology').isEnabled(), true);
      assert.equal(await control('prepare').isDisabled(), true);
      assert.equal(await control('submit').isDisabled(), true);
      assert.match(await control('review').innerText(), /Saved remote <control>/);
    }
    const digest = await page.locator('[data-antithesis-wizard]').getAttribute('data-topology-digest');
    await control('save-topology').click();
    await page.waitForFunction(() => document.querySelector('#antithesis-topology-state').textContent.startsWith('Saved Antithesis configuration:'));
    const saved = await control('profile').inputValue();
    assert.match(saved, /^atop-[0-9a-f]{64}$/);
    assert.equal(fs.readdirSync(path.join(state, 'antithesis-run/topologies')).filter(name => name.endsWith('.json')).length, 1);
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#antithesis-resolve-topology').disabled);
    await page.locator('#antithesis-topology-override > summary').click();
    await control('profile').selectOption(saved);
    await resolve('resolved');
    assert.equal(await page.locator('[data-antithesis-wizard]').getAttribute('data-topology-digest'), digest);
    // Collapsing controls preserves the override and keeps its identity visible.
    await page.locator('#antithesis-topology-override > summary').click();
    assert.match(await control('topology-choice').innerText(), /Override: Saved remote <control>/);
    assert.equal(await control('profile').inputValue(), saved);
    // Returning to the default explicitly clears authority from the resolved override.
    await control('reset-topology').click();
    await waitState('pending');
    assert.equal(await control('topology-choice').innerText(), 'Scenario default');
    assert.equal(await control('profile').inputValue(), '');
    assert.equal(await control('topology-preview').isVisible(), false);
    assert.equal(await control('save-topology').isDisabled(), true);
    assert.equal(await page.locator('[data-antithesis-wizard]').getAttribute('data-topology-digest'), null);
    await page.locator('#antithesis-topology-override > summary').click();
    await control('profile').selectOption(saved);
    await resolve('resolved');
    for (const width of [1440, 390, 720]) {
      await page.setViewportSize({width, height: 1000});
      await control('topology-preview').scrollIntoViewIfNeeded();
      assert.equal(await page.locator('#run-stage-topology').evaluate(node => node.scrollWidth > node.clientWidth), false);
    }
    await page.screenshot({path: '/tmp/dwarf-topology-step1-preview.png'});
    // An older successful reply must not resurrect a changed selection.
    let release, captured;
    const hold = new Promise(resolve => { release = resolve; });
    const seen = new Promise(resolve => { captured = resolve; });
    const resolveURL = '**/api/antithesis-run/topology/resolve';
    await page.route(resolveURL, async route => { const response = await route.fetch(); captured(); await hold; await route.fulfill({response}); });
    await control('resolve-topology').click();
    await seen;
    await page.locator('[name="topology-source"][value="new"]').check();
    await control('config-name').fill('Changed during request');
    release();
    await page.waitForLoadState('networkidle');
    assert.equal(await control('topology-state').getAttribute('data-state'), 'unresolved');
    assert.equal(await page.locator('[data-antithesis-wizard]').getAttribute('data-topology-digest'), null);
    assert.equal(await control('save-topology').isDisabled(), true);
    await page.unroute(resolveURL);
    // A failed request clears old authority and supports retry.
    await page.route(resolveURL, route => route.fulfill({status: 503, json: {ok: false, error: {message: 'Test service outage'}}}));
    await resolve('error');
    assert.match(await control('topology-state').innerText(), /Test service outage/);
    assert.equal(await control('save-topology').isDisabled(), true);
    await page.unroute(resolveURL);
    await resolve('resolved');
    // Reloading the catalog must not overwrite edits made while it was loading.
    await control('template').selectOption('cardano-control-v1');
    let releaseCatalog, catalogSeen;
    const catalogHold = new Promise(resolve => { releaseCatalog = resolve; });
    const catalogCaptured = new Promise(resolve => { catalogSeen = resolve; });
    const catalogURL = '**/api/antithesis-run/topology/catalog';
    await page.route(catalogURL, async route => { const response = await route.fetch(); catalogSeen(); await catalogHold; await route.fulfill({response}); });
    await control('reload-topologies').click();
    await catalogCaptured;
    await control('template').selectOption('amaru-control-v1');
    releaseCatalog();
    await page.waitForFunction(() => !document.querySelector('#antithesis-resolve-topology').disabled);
    assert.equal(await control('template').inputValue(), 'amaru-control-v1');
    await page.unroute(catalogURL);
    // A stale saved configuration can be explicitly reopened for review.
    const staleRequest = {source: 'new', name: 'Saved old revision', template_id: 'mixed-control-v1', cardano_count: 2, amaru_count: 2, network_magic: 42};
    await page.route(resolveURL, route => route.fulfill({status: 200, json: {ok: true, topology: {
      state: 'stale', request: staleRequest, issues: [{message: 'Template inputs changed. Review the new revision.'}]}}}));
    await resolve('stale');
    assert.equal(await control('save-topology').isDisabled(), true);
    await page.locator('#antithesis-topology-override > summary').click();
    await control('review-stale-topology').click();
    assert.equal(await control('config-name').isVisible(), true);
    assert.equal(await control('config-name').inputValue(), 'Saved old revision');
    assert.equal(await control('template').inputValue(), 'mixed-control-v1');
    assert.equal(await page.locator('[data-antithesis-wizard]').getAttribute('data-topology-digest'), null);
    await page.unroute(resolveURL);
    await resolve('resolved');
    const api = page.request;
    assert.equal((await api.get(origin + '/api/antithesis-run/topology/resolve')).status(), 405);
    assert.equal((await api.post(origin + '/api/antithesis-run/topology/configurations', {data: {}})).status(), 403);
    assert.equal((await api.post(origin + '/api/antithesis-run/topology/resolve', {data: 'x'.repeat(8193)})).status(), 413);
    assert.deepEqual(errors, []);
    assert.deepEqual(localRequests, []);
    console.log('PASS: real HTTP topology catalog/resolve/save/reload; three exact service graphs; pending and blocked sources; stale responses and errors; authentication; limits; responsive layout; no Local execution');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    fs.rmSync(state, {recursive: true, force: true});
  }
})().catch(error => { console.error(error, stderr); process.exitCode = 1; });
