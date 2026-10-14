const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const script = path.resolve(__dirname, '../../dwarf/dashboard/static/js/run-wizard.js');

const html = `<!doctype html><body><section id="run-wizard"
  data-resolve-url="/api/run/resolve" data-topology-url="/api/topology/configurations"
  data-prepare-url="/api/run/prepare" data-start-url="/api/run/start" data-dashboard-token="test">
  <figure data-run-logo-state="unconfigured"><figcaption data-run-readiness-text></figcaption></figure>
  <form data-run-wizard-form>
    <input type="radio" name="topology_mode" value="existing" checked>
    <div data-topology-mode-panel="existing"><select id="run-profile" name="profile_id">
      <option value="prior" data-haskell-count="1" data-amaru-count="0" selected>Prior</option>
      <option value="changed" data-haskell-count="0" data-amaru-count="1">Changed</option>
    </select></div>
    <div data-topology-mode-panel="new" hidden></div>
    <input type="radio" name="topology_kind" value="cardano-node" checked>
    <input id="run-cardano-count" name="haskell_count" value="1">
    <input id="run-amaru-count" name="amaru_count" value="0">
    <select id="run-version-policy" name="version_policy"><option value="">Default</option></select>
    <select id="run-cardano-version" name="cardano_version"><option value="">Default</option></select>
    <select id="run-amaru-version" name="amaru_version"><option value="">Default</option></select>
    <label data-version-acknowledgement hidden><input name="acknowledge_unknown_versions" type="checkbox"> Acknowledge this exact unconfirmed version</label>
    <input id="run-scenario-search">
    <button type="button" data-scenario-filter="all" aria-pressed="true">All</button>
    <select id="run-scenario" name="scenario_id"><option value="" selected>Choose</option><option value="changed-scenario">Changed</option></select>
    <button type="button" data-scenario-id="changed-scenario" data-scenario-group="cardano-node"
      data-recommended="false" data-search="changed scenario">Changed scenario</button>
    <div data-scenario-picker></div><p data-scenario-picker-count></p>
    <article data-scenario-summary><h3 data-scenario-summary-title></h3><dl data-scenario-summary-facts></dl>
      <p data-scenario-summary-purpose></p><p data-scenario-summary-limit></p>
      <p data-scenario-summary-evidence></p><code data-scenario-raw-id></code></article>
    <select id="run-measurements" name="measurement_profile"><option value="">Default</option></select>
    <fieldset data-run-measurement-taps></fieldset>
    <div data-run-errors hidden></div>
    <div data-run-summary="review"></div><dl data-run-summary="target"></dl>
    <div data-run-summary="profile"></div><div data-run-summary="versions"></div>
    <div data-run-summary="measurements"></div><div data-run-summary="primitives"></div>
    <details data-run-reproducibility><summary>Reproducibility</summary><label>Seed override<input name="seed" data-run-seed></label></details>
    <dl data-run-summary="settings"></dl><div data-run-summary="readiness"></div>
    <pre data-run-summary="plan"></pre>
    <span data-basic-plan="measurements"></span>
    <div data-basic-run-stage="prepare"></div><div data-basic-run-stage="run"></div>
    <span data-basic-run-gate></span><span data-basic-run-status></span>
    <label data-destructive-confirmation hidden><input type="checkbox" name="confirm_topology_change"></label>
    <button type="button" data-run-action="prepare">Prepare</button>
    <button type="submit" data-run-action="start">Run</button>
    <p data-run-prepare-status></p><p data-run-launch-status></p>
  </form>
  <script id="run-wizard-bootstrap" type="application/json">${JSON.stringify({
    scenarios: [{
      id: 'changed-scenario', short_title: 'Changed scenario', implementation: 'Cardano-node',
      implementation_key: 'cardano-node', measurement_state: 'measurement-enabled',
      measurement_profile: 'cardano-security-default', deployment_profile: 'changed',
      target_version: '11.1.2', proof_state: 'supported-without-retained-proof',
      purpose: 'Exercise pending resolution.', limitation: 'None',
    }],
    measurement_profiles: [{
      id: 'amaru-security-patched', title: 'Amaru patched', description: 'Default and optional compatible taps.',
      taps: [{id: 'amaru-stock-network', enabled: true, title: 'Network', description: 'Network evidence'}],
    }],
  })}</script>
</section></body>`;

const priorPlan = {
  scenario: {id: 'prior-scenario', title: 'Prior scenario', target: {implementation: 'cardano-node', version: '11.1.2'}, runtime: 'devnet'},
  profile: {id: 'prior', label: 'Prior profile'},
  topology_shape: {physical_cardano_node_count: 1, physical_amaru_node_count: 0, infrastructure_services: []},
  versions: {policy: 'exact', status: 'confirmed', resolved: {}},
  measurements: {profile: null, resolved: [], incompatible: []},
  primitive_cells: [], primitive_names: [], readiness: {required_checks: []},
  availability: {state: 'ready', reason: 'ready'},
};

async function exercise(page, trigger) {
  await page.setContent(html);
  await page.evaluate(() => {
    window.__pendingResolve = null;
    window.__pendingReject = null;
    window.fetch = () => new Promise((resolve, reject) => {
      window.__pendingResolve = resolve;
      window.__pendingReject = reject;
    });
  });
  await page.addScriptTag({path: script});
  await page.evaluate((plan) => {
    renderPlan(plan, 'prior-digest');
    const logo = document.querySelector('[data-run-logo-state]');
    logo.dataset.runLogoState = 'ready';
    document.querySelector('[data-run-action="prepare"]').disabled = false;
    document.querySelector('[data-run-action="start"]').disabled = false;
  }, priorPlan);
  if (trigger === 'scenario') {
    await page.locator('[data-scenario-id="changed-scenario"]').click();
  } else {
    await page.locator('#run-profile').selectOption('changed');
  }
  await page.waitForFunction(() => window.__pendingResolve !== null);
  return page.evaluate(() => ({
    prepareDisabled: document.querySelector('[data-run-action="prepare"]').disabled,
    runDisabled: document.querySelector('[data-run-action="start"]').disabled,
    logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
    prepareStage: document.querySelector('[data-basic-run-stage="prepare"]').dataset.state,
  }));
}

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.DWARF_BROWSER_CHANNEL ? {channel: process.env.DWARF_BROWSER_CHANNEL} : {})});
  try {
    const contractPage = await browser.newPage();
    await contractPage.setContent(html);
    await contractPage.evaluate(() => {
      window.__resolveBodies = [];
      window.fetch = (_url, options) => {
        window.__resolveBodies.push(JSON.parse(options.body));
        return new Promise(() => {});
      };
    });
    await contractPage.addScriptTag({path: script});
    await contractPage.evaluate((basePlan) => renderPlan({
      ...basePlan,
      versions: {...basePlan.versions, requires_acknowledgement: false},
    }, 'confirmed-digest'), priorPlan);
    const acknowledgement = contractPage.locator('[data-version-acknowledgement]');
    assert.equal(await acknowledgement.isHidden(), true, 'confirmed selection hides one-run acknowledgement');
    await contractPage.evaluate((basePlan) => renderPlan({
      ...basePlan,
      versions: {...basePlan.versions, status: 'unknown', requires_acknowledgement: true},
    }, 'experimental-digest'), priorPlan);
    assert.equal(await acknowledgement.isVisible(), true, 'server-required experimental acknowledgement is visible');
    await acknowledgement.locator('input').check();
    await contractPage.evaluate((basePlan) => renderPlan({
      ...basePlan,
      versions: {...basePlan.versions, status: 'confirmed', requires_acknowledgement: false},
    }, 'confirmed-again'), priorPlan);
    assert.equal(await acknowledgement.isHidden(), true);
    assert.equal(await acknowledgement.locator('input').isChecked(), false, 'hidden acknowledgement cannot remain stale');

    await contractPage.locator('[data-run-reproducibility] > summary').click();
    await contractPage.locator('[data-run-action="start"]').evaluate((node) => { node.disabled = false; });
    const initialResolveCount = await contractPage.evaluate(() => window.__resolveBodies.length);
    await contractPage.locator('[data-run-seed]').fill('0x2a');
    assert.equal(await contractPage.locator('[data-run-action="start"]').isEnabled(), false, 'typing a seed revokes Ready before blur');
    assert.equal(await contractPage.locator('[data-run-action="prepare"]').isEnabled(), false, 'typing a seed blocks stale Prepare before debounce');
    await contractPage.waitForFunction((count) => window.__resolveBodies.length > count, initialResolveCount);
    assert.equal((await contractPage.evaluate(() => window.__resolveBodies.at(-1))).seed, '0x2a');
    const beforeBlurCount = await contractPage.evaluate(() => window.__resolveBodies.length);
    await contractPage.locator('[data-run-seed]').blur();
    await contractPage.waitForTimeout(250);
    assert.equal(await contractPage.evaluate(() => window.__resolveBodies.length), beforeBlurCount, 'blur does not duplicate an already submitted seed resolve');
    await contractPage.locator('[data-run-seed]').fill('0');
    await contractPage.waitForFunction(() => window.__resolveBodies.some((body) => body.seed === '0'));
    assert.equal((await contractPage.evaluate(() => window.__resolveBodies.at(-1))).seed, '0', 'zero is an explicit reproducible seed');
    await contractPage.locator('[data-run-seed]').fill('');
    await contractPage.waitForFunction((count) => window.__resolveBodies.length > count + 2, initialResolveCount);
    assert.equal(Object.hasOwn(await contractPage.evaluate(() => window.__resolveBodies.at(-1)), 'seed'), false, 'blank seed delegates to scenario default');
    await contractPage.close();

    const optionalPage = await browser.newPage();
    await optionalPage.setContent(html);
    await optionalPage.evaluate(() => {
      window.__resolveBody = null;
      window.fetch = (_url, options) => new Promise(() => {
        window.__resolveBody = JSON.parse(options.body);
      });
    });
    await optionalPage.addScriptTag({path: script});
    await optionalPage.evaluate((basePlan) => renderPlan({
      ...basePlan,
      measurements: {
        profile: {id: 'amaru-security-patched'},
        resolved: [{id: 'amaru-stock-network', definition: {title: 'Network', description: 'Network evidence'}}],
        available: [
          {id: 'amaru-stock-network', definition: {title: 'Network', description: 'Network evidence'}},
          {id: 'amaru-stock-header-validation', definition: {title: 'Header validation', description: 'Header verdict evidence'}},
        ],
        incompatible: [],
      },
    }, 'optional-plan'), priorPlan);
    const optional = optionalPage.locator('[data-measurement-tap-id="amaru-stock-header-validation"]');
    assert.equal(await optional.count(), 1, 'compatible optional tap is visible in Basic');
    assert.equal(await optional.isChecked(), false, 'optional tap starts disabled');
    await optionalPage.locator('[data-measurement-taps-disclosure] > summary').click();
    await optional.check();
    await optionalPage.waitForFunction(() => window.__resolveBody !== null);
    const overrides = await optionalPage.evaluate(() => window.__resolveBody.measurement_overrides);
    assert.equal(overrides.find((item) => item.id === 'amaru-stock-header-validation').enabled, true);
    assert.equal(overrides.find((item) => item.id === 'amaru-stock-network').enabled, true);
    await optionalPage.evaluate((basePlan) => renderPlan({
      ...basePlan,
      measurements: {
        profile: {id: 'amaru-security-patched'}, resolved: [], available: [], incompatible: [],
      },
    }, 'empty-compatible-plan'), priorPlan);
    assert.equal(
      await optionalPage.locator('[data-measurement-tap-id]').count(),
      0,
      'an authoritative empty compatible set never exposes unfiltered profile taps',
    );
    await optionalPage.evaluate((basePlan) => renderPlan({
      ...basePlan,
      measurements: {
        profile: {id: 'amaru-security-patched'}, resolved: [], incompatible: [],
      },
    }, 'legacy-plan'), priorPlan);
    assert.equal(
      await optionalPage.locator('[data-measurement-tap-id="amaru-stock-network"]').count(),
      1,
      'a legacy plan without availability retains its profile-tap fallback',
    );
    await optionalPage.close();

    const refreshedPlan = {
      ...priorPlan,
      scenario: {...priorPlan.scenario, id: 'changed-scenario', title: 'Changed scenario'},
      profile: {id: 'prior', label: 'Prior profile'},
      request: {scenario_id: 'changed-scenario', profile_id: 'prior', acknowledge_unknown_versions: false},
    };
    const preparationFrames = [
      `event: plan_refresh\ndata: ${JSON.stringify({
        stage: 'plan_refresh', state: 'resolved', plan: refreshedPlan,
        previous_plan_digest: 'initial-plan-digest', plan_digest: 'current-plan-digest',
        detail: 'Trusted discovery metadata was added.',
      })}\n\n`,
      `event: ready\ndata: ${JSON.stringify({
        stage: 'ready', state: 'ready', plan_digest: 'current-plan-digest',
        profile_id: 'prior', fresh: true, semantic_health: true,
        topology_identity: true, measurement_compatible: true,
        immutable_targets: true, availability_state: 'ready', measurement_count: 0,
      })}\n\n`,
      'event: done\ndata: {"state":"ready","plan_digest":"current-plan-digest"}\n\n',
    ].join('');
    const preparedPage = await browser.newPage();
    await preparedPage.setContent(html);
    await preparedPage.locator('#run-scenario').selectOption('changed-scenario');
    await preparedPage.evaluate((frames) => {
      window.__startBody = null;
      window.fetch = async (url, options) => {
        if (url.startsWith('/api/run/prepare')) {
          return new Response(frames, {status: 200, headers: {'Content-Type': 'text/event-stream'}});
        }
        if (url.startsWith('/api/run/start')) {
          window.__startBody = JSON.parse(options.body);
          return new Response('event: done\ndata: {"exit_code":0}\n\n', {
            status: 200, headers: {'Content-Type': 'text/event-stream'},
          });
        }
        throw new Error(`unexpected URL ${url}`);
      };
    }, preparationFrames);
    await preparedPage.addScriptTag({path: script});
    await preparedPage.evaluate((plan) => renderPlan(plan, 'initial-plan-digest'), refreshedPlan);
    await preparedPage.evaluate(() => prepareRun());
    assert.equal(await preparedPage.locator('[data-run-action="start"]').isEnabled(), true);
    assert.match(
      await preparedPage.locator('[data-run-prepare-status]').textContent(),
      /^Ready:/,
    );
    await preparedPage.evaluate(() => startRun());
    await preparedPage.waitForFunction(() => window.__startBody !== null);
    assert.equal(
      await preparedPage.evaluate(() => window.__startBody.plan_digest),
      'current-plan-digest',
      'Run submits the authoritative refreshed digest, never the stale prepared digest',
    );
    await preparedPage.close();

    const changedDuringPreparePage = await browser.newPage();
    await changedDuringPreparePage.setContent(html);
    await changedDuringPreparePage.locator('#run-scenario').selectOption('changed-scenario');
    await changedDuringPreparePage.evaluate((frames) => {
      window.fetch = async (url) => {
        if (!url.startsWith('/api/run/prepare')) return new Promise(() => {});
        const encoder = new TextEncoder();
        return new Response(new ReadableStream({
          start(controller) {
            setTimeout(() => { controller.enqueue(encoder.encode(frames)); controller.close(); }, 40);
          },
        }), {status: 200, headers: {'Content-Type': 'text/event-stream'}});
      };
    }, preparationFrames);
    await changedDuringPreparePage.addScriptTag({path: script});
    await changedDuringPreparePage.evaluate((plan) => renderPlan(plan, 'initial-plan-digest'), refreshedPlan);
    const pendingPrepare = changedDuringPreparePage.evaluate(() => prepareRun());
    await changedDuringPreparePage.evaluate(() => {
      document.querySelector('#run-profile').value = 'changed';
    });
    await pendingPrepare;
    assert.equal(await changedDuringPreparePage.locator('[data-run-action="start"]').isEnabled(), false);
    assert.match(
      await changedDuringPreparePage.locator('[data-run-prepare-status]').textContent(),
      /user selection changed while Prepare was running/,
    );
    await changedDuringPreparePage.close();

    const cases = [
      {
        name: 'measurement rejection',
        trigger: 'scenario',
        field: 'measurements',
        message: 'measurement amaru-patched-protocol-decode is incompatible: target mode stock is unsupported; expected patched',
      },
      {
        name: 'version rejection',
        trigger: 'form',
        field: 'versions',
        message: 'Amaru 10.11.20260918 is not qualified for the selected profile',
      },
      {
        name: 'fetch failure',
        trigger: 'scenario',
        fetchFailure: 'offline',
        message: 'Resolution failed: Error: offline',
      },
    ];
    for (const rejectedCase of cases) {
      const page = await browser.newPage();
      const state = await exercise(page, rejectedCase.trigger);
      assert.equal(state.prepareDisabled, true, `${rejectedCase.name}: Prepare locks before response`);
      assert.equal(state.runDisabled, true, `${rejectedCase.name}: Run locks before response`);
      assert.notEqual(state.logo, 'ready', `${rejectedCase.name}: ready logo is revoked before response`);
      assert.equal(state.prepareStage, 'resolving', `${rejectedCase.name}: Prepare stage is visibly resolving`);
      if (rejectedCase.fetchFailure) {
        await page.evaluate((message) => window.__pendingReject(new Error(message)), rejectedCase.fetchFailure);
      } else {
        await page.evaluate(({field, message}) => window.__pendingResolve({
          ok: false,
          status: 400,
          json: async () => ({ok: false, error: {field, message}}),
        }), rejectedCase);
      }
      await page.waitForFunction(() => !document.querySelector('[data-run-errors]').hidden);
      await page.locator('[data-measurement-taps-disclosure] > summary').click();
      const rejected = await page.evaluate(() => ({
        error: document.querySelector('[data-run-errors]').textContent,
        taps: document.querySelector('[data-run-measurement-taps]').innerText,
        basicMeasurements: document.querySelector('[data-basic-plan="measurements"]').textContent,
        prepareStatus: document.querySelector('[data-run-prepare-status]').textContent,
        prepareDisabled: document.querySelector('[data-run-action="prepare"]').disabled,
        runDisabled: document.querySelector('[data-run-action="start"]').disabled,
        logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
        prepareStage: document.querySelector('[data-basic-run-stage="prepare"]').dataset.state,
      }));
      assert.equal(rejected.error, rejectedCase.message);
      assert.match(rejected.taps, new RegExp(rejectedCase.message.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      assert.doesNotMatch(rejected.taps, /Resolving compatible taps/);
      assert.match(rejected.basicMeasurements, new RegExp(rejectedCase.message.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      assert.doesNotMatch(rejected.basicMeasurements, /compatible taps/);
      assert.equal(rejected.prepareStatus, rejected.error);
      assert.equal(rejected.prepareDisabled, true);
      assert.equal(rejected.runDisabled, true);
      assert.equal(rejected.logo, 'blocked');
      assert.equal(rejected.prepareStage, 'blocked');
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
