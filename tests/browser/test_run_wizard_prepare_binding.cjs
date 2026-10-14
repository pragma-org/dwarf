const assert = require('node:assert/strict');
const path = require('node:path');
let browserLibrary = 'playwright';
let chromium;
try {
  ({chromium} = require('playwright'));
} catch (_error) {
  browserLibrary = 'playwright-core';
  ({chromium} = require('playwright-core'));
}

const script = path.resolve(__dirname, '../../dwarf/dashboard/static/js/run-wizard.js');
const scenarioId = 'amaru-boundary-stock-20260925-1200-local';
const profileId = 'profile-local-amaru-20260925-stock-experiment';
const registrationId = 'registration-dc21b4a1f5584c6b942ad626';
const consent = {
  context_digest: 'sha256:e659a2d83fc4dbce201f8c0e2ea75838da7d4e8bd24731a6fff488f30d806898',
  tap_ids: ['amaru-stock-network', 'amaru-external-workload-accounting'],
  tap_definition_digests: {
    'amaru-stock-network': 'sha256:tap-network',
    'amaru-external-workload-accounting': 'sha256:tap-workload',
  },
};
const plan = {
  request: {
    scenario_id: scenarioId,
    profile_id: profileId,
    operator_registration_id: registrationId,
    measurement_profile: 'amaru-security-default',
    acknowledge_unknown_versions: true,
    experimental_measurement_consent: consent,
  },
  scenario: {
    id: scenarioId,
    title: 'Latest Amaru stock boundary control — 1,200 attempts',
    target: {implementation: 'amaru', version: '10.11.20260925-official-binary'},
    runtime: 'devnet',
    seed: '0xA11CE502',
  },
  profile: {id: profileId, label: 'Experimental Amaru stock measurement control'},
  topology_shape: {physical_cardano_node_count: 6, physical_amaru_node_count: 2, infrastructure_services: []},
  versions: {
    policy: 'exact', status: 'unknown', requires_acknowledgement: true,
    resolved: {amaru: {implementation: 'amaru', version: '10.11.20260925-official-binary', executable_digest: 'sha256:d9c857'}},
  },
  measurements: {
    profile: {id: 'amaru-security-default'},
    resolved: [
      {id: 'amaru-stock-network', enabled: true, definition: {title: 'Network', description: 'Network evidence'}},
      {id: 'amaru-external-workload-accounting', enabled: true, definition: {title: 'Workload accounting', description: 'Workload evidence'}},
    ],
    requested: [
      {id: 'amaru-stock-network', enabled: true},
      {id: 'amaru-external-workload-accounting', enabled: true},
    ],
    available: [
      {id: 'amaru-stock-network', definition: {title: 'Network', description: 'Network evidence'}},
      {id: 'amaru-external-workload-accounting', definition: {title: 'Workload accounting', description: 'Workload evidence'}},
    ],
    incompatible: [],
    experimental: [
      {id: 'amaru-stock-network', capability: {state: 'unknown'}},
      {id: 'amaru-external-workload-accounting', capability: {state: 'available'}},
    ],
    experimental_consent_required: consent,
  },
  primitive_cells: [], primitive_names: [],
  readiness: {required_checks: []},
  availability: {state: 'ready', reason: 'Exact local artifact is available.'},
};

const scenario = {
  id: scenarioId, short_title: plan.scenario.title, implementation: 'Amaru',
  implementation_key: 'amaru', measurement_state: 'measurement-enabled',
  measurement_profile: 'amaru-security-default', deployment_profile: profileId,
  target_version: '10.11.20260925', proof_state: 'experimental',
  purpose: 'Exercise the stock boundary workload.', limitation: 'Compatibility is unknown until run.',
};

const html = `<!doctype html><body><section id="run-wizard"
  data-resolve-url="/api/run/resolve" data-topology-url="/api/topology/configurations"
  data-prepare-url="/api/run/prepare" data-start-url="/api/run/start" data-dashboard-token="test">
  <figure data-run-logo-state="unconfigured"><figcaption data-run-readiness-text></figcaption></figure>
  <form data-run-wizard-form>
    <input type="radio" name="topology_mode" value="existing" checked>
    <div data-topology-mode-panel="existing"><select id="run-profile" name="profile_id">
      <option value="profile-r-amaru-measurement-stock-control" data-haskell-count="6" data-amaru-count="2">Curated default</option>
      <option value="${profileId}" data-haskell-count="6" data-amaru-count="2" selected>Experimental profile</option>
    </select></div><div data-topology-mode-panel="new" hidden></div>
    <input type="radio" name="topology_kind" value="mixed" checked>
    <input id="run-cardano-count" name="haskell_count" value="6"><input id="run-amaru-count" name="amaru_count" value="2">
    <select id="run-version-policy" name="version_policy"><option value="">Default</option></select>
    <select id="run-cardano-version" name="cardano_version"><option value="">Default</option></select>
    <select id="run-amaru-version" name="amaru_version"><option value="">Default</option></select>
    <select id="run-operator-registration" name="operator_registration_id"><option value="">Catalog</option><option value="${registrationId}" selected>Imported</option></select>
    <label data-version-acknowledgement><input name="acknowledge_unknown_versions" type="checkbox" checked> Acknowledge</label>
    <input id="run-scenario-search" value="amaru-boundary-stock-20260925-1200-local">
    <button type="button" data-scenario-filter="all" aria-pressed="true">All</button>
    <select id="run-scenario" name="scenario_id"><option value="${scenarioId}" selected>Boundary</option></select>
    <button type="button" data-scenario-id="${scenarioId}" data-scenario-group="amaru" data-recommended="false" data-search="boundary">Boundary</button>
    <div data-scenario-picker></div><p data-scenario-picker-count></p>
    <article data-scenario-summary><h3 data-scenario-summary-title></h3><dl data-scenario-summary-facts></dl><p data-scenario-summary-purpose></p><p data-scenario-summary-limit></p><p data-scenario-summary-evidence></p><code data-scenario-raw-id></code></article>
    <select id="run-measurements" name="measurement_profile"><option value="amaru-security-default" selected>Amaru default</option></select>
    <fieldset data-run-measurement-taps></fieldset><div data-run-errors hidden></div>
    <div data-run-summary="review"></div><dl data-run-summary="target"></dl><div data-run-summary="profile"></div><div data-run-summary="versions"></div><div data-run-summary="measurements"></div><div data-run-summary="primitives"></div><dl data-run-summary="settings"></dl><div data-run-summary="readiness"></div><pre data-run-summary="plan"></pre>
    <span data-basic-plan="scenario"></span><span data-basic-plan="profile"></span><span data-basic-plan="target"></span><span data-basic-plan="measurements"></span><span data-basic-plan="digest"></span>
    <div data-basic-run-stage="prepare"></div><div data-basic-run-stage="run"></div><span data-basic-run-gate></span><span data-basic-run-status></span>
    <label data-destructive-confirmation hidden><input type="checkbox" name="confirm_topology_change"></label>
    <button type="button" data-run-action="prepare">Prepare</button><button type="submit" data-run-action="start">Run</button><button type="button" data-run-action="start-basic">Run basic</button>
    <p data-run-prepare-status></p><p data-run-launch-status></p><pre data-run-prepare-log></pre>
  </form>
  <script id="run-wizard-bootstrap" type="application/json">${JSON.stringify({scenarios: [scenario], measurement_profiles: [], default_scenario_id: scenarioId, default_plan: plan, default_plan_digest: 'sha256:initial'})}</script>
</section></body>`;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(browserLibrary === 'playwright-core'
      ? {channel: process.env.DWARF_BROWSER_CHANNEL || 'chrome'}
      : process.env.DWARF_BROWSER_CHANNEL ? {channel: process.env.DWARF_BROWSER_CHANNEL} : {}),
  });
  try {
    const page = await browser.newPage();
    await page.route('http://dwarf.test/**', (route) => route.fulfill({status: 200, contentType: 'text/html', body: html}));
    await page.goto('http://dwarf.test/run');
    await page.evaluate((refreshedPlan) => {
      window.__prepareBodies = [];
      window.__duringPrepare = null;
      window.fetch = (_url, options = {}) => {
        window.__prepareBodies.push(JSON.parse(options.body));
        const encode = (name, payload) => new TextEncoder().encode(`event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`);
        const body = new ReadableStream({start(controller) {
          window.setTimeout(() => controller.enqueue(encode('plan_refresh', {
            previous_plan_digest: 'sha256:initial',
            plan_digest: 'sha256:refreshed',
            plan: refreshedPlan,
          })), 10);
          window.setTimeout(() => {
            window.__duringPrepare = {
              scenario: document.querySelector('#run-scenario').value,
              profile: document.querySelector('#run-profile').value,
              registration: document.querySelector('#run-operator-registration').value,
              acknowledgement: document.querySelector('[name="acknowledge_unknown_versions"]').checked,
              consent: document.querySelector('[data-experimental-measurement-consent]').checked,
              selectedTaps: [...document.querySelectorAll('[data-measurement-tap-id]:checked')].map((node) => node.dataset.measurementTapId).sort(),
              runDisabled: document.querySelector('[data-run-action="start"]').disabled,
              digest: document.querySelector('[data-basic-plan="digest"]').textContent,
            };
            controller.enqueue(encode('ready', {
              state: 'ready', plan_digest: 'sha256:refreshed', profile_id: refreshedPlan.profile.id,
              fresh: true, semantic_health: true, topology_identity: true,
              measurement_compatible: true, immutable_targets: true,
              availability_state: 'ready', measurement_count: 2,
            }));
            controller.enqueue(encode('done', {state: 'ready', plan_digest: 'sha256:refreshed'}));
            controller.close();
          }, 80);
        }});
        return Promise.resolve(new Response(body, {status: 200, headers: {'Content-Type': 'text/event-stream'}}));
      };
    }, plan);
    await page.addScriptTag({path: script});
    await page.waitForFunction(() => document.querySelector('[data-experimental-measurement-consent]')?.checked === true);
    await page.evaluate(() => prepareRun());
    const during = await page.evaluate(() => window.__duringPrepare);
    assert.deepEqual(during, {
      scenario: scenarioId, profile: profileId, registration: registrationId,
      acknowledgement: true, consent: true,
      selectedTaps: ['amaru-external-workload-accounting', 'amaru-stock-network'],
      runDisabled: true, digest: 'sha256:refreshed',
    });
    const terminal = await page.evaluate(() => ({
      scenario: document.querySelector('#run-scenario').value,
      profile: document.querySelector('#run-profile').value,
      registration: document.querySelector('#run-operator-registration').value,
      acknowledgement: document.querySelector('[name="acknowledge_unknown_versions"]').checked,
      consent: document.querySelector('[data-experimental-measurement-consent]').checked,
      runDisabled: document.querySelector('[data-run-action="start"]').disabled,
      status: document.querySelector('[data-run-prepare-status]').textContent,
      body: window.__prepareBodies[0],
    }));
    assert.equal(terminal.scenario, scenarioId);
    assert.equal(terminal.profile, profileId);
    assert.equal(terminal.registration, registrationId);
    assert.equal(terminal.acknowledgement, true);
    assert.equal(terminal.consent, true);
    assert.equal(terminal.runDisabled, false, terminal.status);
    assert.equal(terminal.body.request.scenario_id, scenarioId);
    assert.equal(terminal.body.request.profile_id, profileId);
    assert.equal(terminal.body.request.operator_registration_id, registrationId);
    assert.deepEqual(terminal.body.request.experimental_measurement_consent, consent);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
