const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const script = path.resolve(__dirname, '../../dwarf/dashboard/static/js/run-wizard.js');

const priorPlan = {
  scenario: {id: 'prior-scenario', title: 'Prior scenario', target: {implementation: 'cardano-node', version: '11.1.2'}, runtime: 'devnet'},
  profile: {id: 'prior', label: 'Prior profile'},
  topology_shape: {physical_cardano_node_count: 1, physical_amaru_node_count: 0, infrastructure_services: []},
  versions: {policy: 'exact', status: 'confirmed', resolved: {}},
  measurements: {profile: null, resolved: [], incompatible: []},
  primitive_cells: [], primitive_names: [], readiness: {required_checks: []},
  availability: {state: 'ready', reason: 'ready'},
};
const changedPlan = {
  ...priorPlan,
  scenario: {...priorPlan.scenario, id: 'changed-scenario', title: 'Changed scenario'},
};
const scenarios = [
  {
    id: 'prior-scenario', short_title: 'Prior scenario', implementation: 'Cardano-node',
    implementation_key: 'cardano-node', measurement_state: 'measurement-enabled',
    measurement_profile: null, deployment_profile: 'prior', target_version: '11.1.2',
    proof_state: 'supported-without-retained-proof', purpose: 'Prior run.', limitation: 'None',
  },
  {
    id: 'changed-scenario', short_title: 'Changed scenario', implementation: 'Cardano-node',
    implementation_key: 'cardano-node', measurement_state: 'measurement-enabled',
    measurement_profile: null, deployment_profile: 'prior', target_version: '11.1.2',
    proof_state: 'supported-without-retained-proof', purpose: 'Changed run.', limitation: 'None',
  },
];

const html = `<!doctype html><body><section id="run-wizard"
  data-resolve-url="/api/run/resolve" data-topology-url="/api/topology/configurations"
  data-prepare-url="/api/run/prepare" data-start-url="/api/run/start" data-dashboard-token="test">
  <figure data-run-logo-state="unconfigured"><figcaption data-run-readiness-text></figcaption></figure>
  <form data-run-wizard-form>
    <input type="radio" name="topology_mode" value="existing" checked>
    <div data-topology-mode-panel="existing"><select id="run-profile" name="profile_id">
      <option value="prior" data-haskell-count="1" data-amaru-count="0" selected>Prior</option>
    </select></div><div data-topology-mode-panel="new" hidden></div>
    <input type="radio" name="topology_kind" value="cardano-node" checked>
    <input id="run-cardano-count" name="haskell_count" value="1"><input id="run-amaru-count" name="amaru_count" value="0">
    <select id="run-version-policy" name="version_policy"><option value="">Default</option></select>
    <select id="run-cardano-version" name="cardano_version"><option value="">Default</option></select>
    <select id="run-amaru-version" name="amaru_version"><option value="">Default</option></select>
    <input name="acknowledge_unknown_versions" type="checkbox"><input id="run-scenario-search">
    <button type="button" data-scenario-filter="all" aria-pressed="true">All</button>
    <select id="run-scenario" name="scenario_id"><option value="prior-scenario" selected>Prior</option><option value="changed-scenario">Changed</option></select>
    <button type="button" data-scenario-id="prior-scenario" data-scenario-group="cardano-node" data-recommended="true" data-search="prior">Prior</button>
    <button type="button" data-scenario-id="changed-scenario" data-scenario-group="cardano-node" data-recommended="true" data-search="changed">Changed</button>
    <div data-scenario-picker></div><p data-scenario-picker-count></p>
    <article data-scenario-summary><h3 data-scenario-summary-title></h3><dl data-scenario-summary-facts></dl><p data-scenario-summary-purpose></p><p data-scenario-summary-limit></p><p data-scenario-summary-evidence></p><code data-scenario-raw-id></code></article>
    <select id="run-measurements" name="measurement_profile"><option value="">Default</option></select><fieldset data-run-measurement-taps></fieldset>
    <div data-run-errors hidden></div><div data-run-summary="review"></div><dl data-run-summary="target"></dl><div data-run-summary="profile"></div><div data-run-summary="versions"></div><div data-run-summary="measurements"></div><div data-run-summary="primitives"></div><dl data-run-summary="settings"></dl><div data-run-summary="readiness"></div><pre data-run-summary="plan"></pre>
    <span data-basic-plan="scenario"></span><span data-basic-plan="profile"></span><span data-basic-plan="target"></span><span data-basic-plan="measurements"></span><span data-basic-plan="digest"></span>
    <div data-basic-run-stage="prepare"></div><div data-basic-run-stage="run"></div><div data-basic-run-stage="evidence"></div><span data-basic-run-gate></span><span data-basic-run-status></span><span data-run-basic-evidence></span>
    <div data-run-lifecycle="queued"></div><div data-run-lifecycle="running"></div><div data-run-lifecycle="completed"></div>
    <button type="button" data-run-action="prepare">Prepare</button><button type="submit" data-run-action="start">Run</button><button type="button" data-run-action="start-basic">Run basic</button>
    <p data-run-prepare-status></p><p data-run-launch-status></p><pre data-run-prepare-log></pre><pre data-run-launch-log></pre><pre data-run-basic-log></pre>
    <div data-destructive-confirmation hidden><input name="confirm_topology_change" type="checkbox"></div>
  </form>
  <script id="run-wizard-bootstrap" type="application/json">${JSON.stringify({scenarios, measurement_profiles: [], default_scenario_id: 'prior-scenario', default_plan: priorPlan, default_plan_digest: 'prior-digest'})}</script>
</section></body>`;

function sse(events) {
  return events.map(([name, payload]) => `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`).join('');
}

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.DWARF_BROWSER_CHANNEL ? {channel: process.env.DWARF_BROWSER_CHANNEL} : {})});
  try {
    const page = await browser.newPage();
    await page.route('http://dwarf.test/**', (route) => route.fulfill({status: 200, contentType: 'text/html', body: html}));
    await page.goto('http://dwarf.test/run');
    await page.evaluate(({changedPlan}) => {
      let pendingStart;
      window.__startRequested = false;
      window.__finishStart = () => pendingStart(new Response([
        ['launch', {state: 'running', run_id: 'run-old'}],
        ['result', {state: 'completed', security_verdict: 'pass', run_id: 'run-old'}],
        ['done', {exit_code: 0, run_id: 'run-old'}],
      ].map(([name, payload]) => `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`).join(''), {status: 200, headers: {'Content-Type': 'text/event-stream'}}));
      window.fetch = (url) => {
        const rawUrl = String(url);
        const pathname = rawUrl.startsWith('/') ? rawUrl.split('?')[0] : new URL(rawUrl, location.href).pathname;
        if (pathname === '/api/run/prepare') return Promise.resolve(new Response([
          ['ready', {state: 'ready', plan_digest: 'prior-digest', profile_id: 'prior', fresh: true, semantic_health: true, topology_identity: true, measurement_compatible: true, immutable_targets: true, availability_state: 'ready', measurement_count: 0}],
          ['done', {state: 'ready', plan_digest: 'prior-digest'}],
        ].map(([name, payload]) => `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`).join(''), {status: 200, headers: {'Content-Type': 'text/event-stream'}}));
        if (pathname === '/api/run/start') {
          window.__startRequested = true;
          return new Promise((resolve) => { pendingStart = resolve; });
        }
        if (pathname === '/api/run/resolve') return Promise.resolve(new Response(JSON.stringify({ok: true, plan: changedPlan, plan_digest: 'changed-digest'}), {status: 200, headers: {'Content-Type': 'application/json'}}));
        throw new Error(`unexpected fetch ${pathname}`);
      };
    }, {changedPlan});
    await page.addScriptTag({path: script});
    await page.evaluate(() => prepareRun());
    const prepared = await page.evaluate(() => ({
      disabled: document.querySelector('[data-run-action="start"]').disabled,
      status: document.querySelector('[data-run-prepare-status]').textContent,
      log: document.querySelector('[data-run-prepare-log]').textContent,
    }));
    assert.equal(prepared.disabled, false, `${prepared.status}\n${prepared.log}`);
    await page.locator('[data-run-action="start"]').click();
    await page.waitForFunction(() => window.__startRequested === true);
    await page.locator('[data-scenario-id="changed-scenario"]').click();
    await page.waitForFunction(() => document.querySelector('[data-basic-plan="scenario"]').textContent === 'Changed scenario');
    const afterChange = await page.evaluate(() => ({
      logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
      runDisabled: document.querySelector('[data-run-action="start"]').disabled,
      digest: document.querySelector('[data-basic-plan="digest"]').textContent,
    }));
    assert.notEqual(afterChange.logo, 'completed');
    assert.equal(afterChange.runDisabled, true);
    assert.equal(afterChange.digest, 'changed-digest');
    await page.evaluate(() => window.__finishStart());
    await page.waitForTimeout(800);
    const afterOldTerminal = await page.evaluate(() => ({
      logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
      runDisabled: document.querySelector('[data-run-action="start"]').disabled,
      digest: document.querySelector('[data-basic-plan="digest"]').textContent,
      status: document.querySelector('[data-basic-run-status]').textContent,
      url: location.pathname,
    }));
    assert.notEqual(afterOldTerminal.logo, 'completed', 'old terminal event must not repaint the changed plan as completed');
    assert.equal(afterOldTerminal.runDisabled, true, 'old terminal event must not unlock the changed plan');
    assert.equal(afterOldTerminal.digest, 'changed-digest');
    assert.doesNotMatch(afterOldTerminal.status, /Completed/);
    assert.equal(afterOldTerminal.url, '/run');
    await page.close();

    const blockedPage = await browser.newPage();
    await blockedPage.route('http://dwarf.test/**', (route) => route.fulfill({status: 200, contentType: 'text/html', body: html}));
    await blockedPage.goto('http://dwarf.test/run');
    await blockedPage.evaluate(() => {
      const stream = (events) => events.map(([name, payload]) => `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`).join('');
      window.fetch = (url) => {
        const pathname = String(url).split('?')[0];
        if (pathname === '/api/run/prepare') return Promise.resolve(new Response(stream([
          ['ready', {state: 'ready', plan_digest: 'prior-digest', profile_id: 'prior', fresh: true, semantic_health: true, topology_identity: true, measurement_compatible: true, immutable_targets: true, availability_state: 'ready', measurement_count: 0}],
          ['done', {state: 'ready', plan_digest: 'prior-digest'}],
        ]), {status: 200}));
        if (pathname === '/api/run/start') return Promise.resolve(new Response(stream([
          ['plan', {plan_digest: 'prior-digest'}],
          ['preflight', {state: 'blocked', detail: 'producer spread is outside the readiness gate'}],
          ['error', {error: 'preflight_failed'}],
          ['done', {exit_code: 78}],
        ]), {status: 200}));
        throw new Error(`unexpected fetch ${pathname}`);
      };
    });
    await blockedPage.addScriptTag({path: script});
    await blockedPage.evaluate(() => prepareRun());
    await blockedPage.locator('[data-run-action="start"]').click();
    await blockedPage.waitForTimeout(100);
    const blocked = await blockedPage.evaluate(() => ({
      logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
      runDisabled: document.querySelector('[data-run-action="start"]').disabled,
      runStage: document.querySelector('[data-basic-run-stage="run"]').dataset.state,
      status: document.querySelector('[data-basic-run-status]').textContent,
    }));
    assert.equal(blocked.logo, 'blocked');
    assert.equal(blocked.runDisabled, true);
    assert.equal(blocked.runStage, 'blocked');
    assert.match(blocked.status, /producer spread is outside the readiness gate/);
    await blockedPage.close();

    const staleHttpPage = await browser.newPage();
    await staleHttpPage.route('http://dwarf.test/**', (route) => route.fulfill({status: 200, contentType: 'text/html', body: html}));
    await staleHttpPage.goto('http://dwarf.test/run');
    await staleHttpPage.evaluate(({changedPlan}) => {
      let rejectOldStart;
      window.__oldStartRequested = false;
      window.__rejectOldStart = () => rejectOldStart(new Response(JSON.stringify({
        error: {field: 'plan_digest', message: 'old plan digest is stale'},
      }), {status: 409, headers: {'Content-Type': 'application/json'}}));
      const stream = (events) => events.map(([name, payload]) => `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`).join('');
      window.fetch = (url, options = {}) => {
        const pathname = String(url).split('?')[0];
        if (pathname === '/api/run/resolve') return Promise.resolve(new Response(JSON.stringify({ok: true, plan: changedPlan, plan_digest: 'changed-digest'}), {status: 200, headers: {'Content-Type': 'application/json'}}));
        if (pathname === '/api/run/prepare') {
          const digest = JSON.parse(options.body).plan_digest;
          return Promise.resolve(new Response(stream([
            ['ready', {state: 'ready', plan_digest: digest, profile_id: 'prior', fresh: true, semantic_health: true, topology_identity: true, measurement_compatible: true, immutable_targets: true, availability_state: 'ready', measurement_count: 0}],
            ['done', {state: 'ready', plan_digest: digest}],
          ]), {status: 200}));
        }
        if (pathname === '/api/run/start') {
          window.__oldStartRequested = true;
          return new Promise((resolve) => { rejectOldStart = resolve; });
        }
        throw new Error(`unexpected fetch ${pathname}`);
      };
    }, {changedPlan});
    await staleHttpPage.addScriptTag({path: script});
    await staleHttpPage.evaluate(() => prepareRun());
    await staleHttpPage.locator('[data-run-action="start"]').click();
    await staleHttpPage.waitForFunction(() => window.__oldStartRequested === true);
    await staleHttpPage.locator('[data-scenario-id="changed-scenario"]').click();
    await staleHttpPage.waitForFunction(() => document.querySelector('[data-basic-plan="digest"]').textContent === 'changed-digest');
    await staleHttpPage.evaluate(() => prepareRun());
    const replacementReady = await staleHttpPage.evaluate(() => ({
      logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
      runDisabled: document.querySelector('[data-run-action="start"]').disabled,
    }));
    assert.equal(replacementReady.logo, 'ready');
    assert.equal(replacementReady.runDisabled, false);
    await staleHttpPage.evaluate(() => window.__rejectOldStart());
    await staleHttpPage.waitForTimeout(100);
    const afterOld409 = await staleHttpPage.evaluate(() => ({
      logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
      runDisabled: document.querySelector('[data-run-action="start"]').disabled,
      digest: document.querySelector('[data-basic-plan="digest"]').textContent,
      status: document.querySelector('[data-basic-run-status]').textContent,
    }));
    assert.equal(afterOld409.logo, 'ready');
    assert.equal(afterOld409.runDisabled, false, 'old 409 must not revoke the prepared replacement plan');
    assert.equal(afterOld409.digest, 'changed-digest');
    assert.equal(afterOld409.status, 'Exact preparation is complete. Run is unlocked.');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
