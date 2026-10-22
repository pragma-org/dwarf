const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const server = spawn('python3', ['-u', '-c', `
from http.server import ThreadingHTTPServer
from profile_manager.dashboard import serve_dashboard_handler_factory
server = ThreadingHTTPServer(('127.0.0.1', 0), serve_dashboard_handler_factory(None))
print(server.server_port, flush=True)
server.serve_forever()
`], {cwd: root, env: {...process.env, PYTHONPATH: '.:dwarf'}, stdio: ['ignore', 'pipe', 'pipe']});
let serverErrors = '';
server.stderr.on('data', data => { serverErrors += data; });

(async () => {
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      let output = '';
      const timeout = setTimeout(() => reject(new Error(`Server startup timed out: ${serverErrors}`)), 20000);
      server.once('error', error => { clearTimeout(timeout); reject(error); });
      server.once('exit', code => { clearTimeout(timeout); reject(new Error(`Server exited ${code}: ${serverErrors}`)); });
      server.stdout.on('data', data => {
        output += data;
        if (/^\d+\n/.test(output)) { clearTimeout(timeout); resolve(Number(output.trim())); }
      });
    });
    const origin = `http://127.0.0.1:${port}`;
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const entry of ['/', '/operate']) {
      await page.goto(origin + entry);
      await page.getByRole('link', {name: /Enter the lab/}).filter({visible: true}).click();
      assert.equal(new URL(page.url()).pathname, '/lab');
    }
    for (const width of [1440, 390, 720]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(origin + '/lab');
      const cards = page.locator('[data-lab-destination]');
      assert.equal(await cards.count(), 2);
      const boxes = await cards.evaluateAll(nodes => nodes.map(node => {
        const box = node.getBoundingClientRect();
        return {top: box.top, bottom: box.bottom};
      }));
      assert.ok(width > 800 ? Math.abs(boxes[0].top - boxes[1].top) < 2 : boxes[1].top > boxes[0].bottom);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    const remote = page.locator('[data-lab-destination="antithesis"]');
    await remote.focus();
    await remote.press('Enter');
    await page.waitForURL('**/run/antithesis');
    const mutations = [];
    const localRequests = [];
    page.on('request', request => {
      if (request.method() !== 'GET') mutations.push(request.url());
      if (new URL(request.url()).pathname.startsWith('/api/run/')) localRequests.push(request.url());
    });
    for (const width of [1440, 390, 720]) {
      await page.setViewportSize({width, height: 900});
      assert.equal(await page.locator('section.run-wizard__step[id^="run-stage-"]:visible').count(), 6);
      assert.equal(await page.locator('.shell-main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    }
    assert.equal(await page.locator('#antithesis-topology-choice').innerText(), 'Scenario default');
    assert.equal(await page.locator('#antithesis-profile').isVisible(), false);
    await page.locator('#antithesis-topology-override > summary').click();
    await page.locator('[name="topology-source"][value="new"]').check();
    await page.locator('#antithesis-config-name').fill('Draft <script>not markup</script>');
    assert.equal(await page.locator('#antithesis-profile').isDisabled(), true);
    await page.locator('#antithesis-policy').selectOption('exact');
    assert.equal(await page.locator('#antithesis-version-cardano-node').isEnabled(), true);
    const scenario = await page.locator('#antithesis-scenario option').nth(1).getAttribute('value');
    await page.locator('#antithesis-scenario').selectOption(scenario);
    await page.locator('#antithesis-scenario-search').fill('no-such-scenario-xyz');
    assert.equal(await page.locator('#antithesis-scenario').inputValue(), scenario);
    assert.match(await page.locator('#antithesis-scenario-count').innerText(), /current selection retained/);
    const profileOption = page.locator('#antithesis-measurement-profile option[data-tap-ids]').filter({hasText: /./}).first();
    const profile = await profileOption.getAttribute('value');
    const expectedTaps = JSON.parse(await profileOption.getAttribute('data-tap-ids'));
    await page.locator('#antithesis-measurement-profile').selectOption(profile);
    assert.deepEqual((await page.locator('[name="antithesis-tap"]:checked').evaluateAll(nodes => nodes.map(node => node.value))).sort(), expectedTaps.sort());
    const firstTap = page.locator('[name="antithesis-tap"]').first();
    await firstTap.setChecked(!(await firstTap.isChecked()));
    assert.equal(await page.locator('#antithesis-measurement-profile').inputValue(), '');
    const selectedCount = await page.locator('[name="antithesis-tap"]:checked').count();
    await page.locator('#antithesis-tap-search').fill('no-such-tap-xyz');
    assert.equal(await page.locator('[name="antithesis-tap"]:checked').count(), selectedCount);
    assert.equal(await page.locator('[data-tap-row]:visible').count(), 0);
    assert.match(await page.locator('#antithesis-review').innerText(), /Draft <script>not markup<\/script>/);
    assert.equal(await page.locator('#antithesis-review script').count(), 0);
    // Package source captures intent only: no requests, and switching preserves edits.
    const source = mode => page.locator(`[name="antithesis-package-source"][value="${mode}"]`);
    assert.equal(await page.locator('[name="antithesis-package-source"]').count(), 3);
    assert.equal(await source('build').isChecked(), true);
    assert.equal(await page.locator('#antithesis-package-url-fields').isVisible(), false);
    assert.equal(await page.locator('#antithesis-package-existing-fields').isVisible(), false);
    await source('url').check();
    await page.locator('#antithesis-package-url').fill('https://example.invalid/test-package.tar.gz');
    assert.match(await page.locator('#antithesis-review').innerText(), /https:\/\/example.invalid\/test-package.tar.gz/);
    await source('existing').check();
    assert.equal(await page.locator('#antithesis-package-url').isDisabled(), true);
    await page.locator('#antithesis-compose-path').fill('/experiments/<script>literal</script>');
    await page.locator('#antithesis-image-references').fill('custom/node:experimental\ncustom/workload:test');
    const existingReview = await page.locator('#antithesis-review').innerText();
    assert.match(existingReview, /custom\/node:experimental/);
    assert.match(existingReview, /<script>literal<\/script>/);
    assert.doesNotMatch(existingReview, /example.invalid/);
    assert.equal(await page.locator('#antithesis-review script').count(), 0);
    await source('build').check();
    assert.doesNotMatch(await page.locator('#antithesis-review').innerText(), /custom\/node|example.invalid|literal/);
    await source('url').check();
    assert.equal(await page.locator('#antithesis-package-url').inputValue(), 'https://example.invalid/test-package.tar.gz');
    await source('existing').check();
    assert.equal(await page.locator('#antithesis-compose-path').inputValue(), '/experiments/<script>literal</script>');
    assert.equal(await page.locator('#antithesis-image-references').inputValue(), 'custom/node:experimental\ncustom/workload:test');
    assert.equal(await page.locator('#antithesis-scenario').inputValue(), scenario);
    for (const width of [1440, 390, 720]) {
      await page.setViewportSize({width, height:900});
      assert.equal(await page.locator('.shell-main').evaluate(node => node.scrollWidth > node.clientWidth), false);
    }
    assert.equal(await page.locator('#antithesis-prepare').isDisabled(), true);
    assert.equal(await page.locator('#antithesis-submit').isDisabled(), true);
    assert.deepEqual(mutations, []);
    assert.deepEqual(localRequests, []);
    assert.deepEqual(errors, []);
    await page.getByRole('link', {name: /Choose environment/}).click();
    await page.locator('[data-lab-destination="local"]').click();
    await page.waitForURL('**/run');
    assert.equal(await page.locator('#run-wizard').getAttribute('data-start-url'), '/api/run/start');
    console.log('PASS: entry links, responsive cards, keyboard navigation, independent draft controls, no mutation/local requests, Local route intact');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
