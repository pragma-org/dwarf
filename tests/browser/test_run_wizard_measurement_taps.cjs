const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const script = path.join(root, 'dwarf/dashboard/static/js/run-wizard.js');
const styles = [
  'dwarf/dashboard/static/css/plex.css',
  'dwarf/dashboard/static/css/tokens.css',
  'dwarf/dashboard/static/css/themes.css',
  'dwarf/dashboard/static/css/base.css',
  'dwarf/dashboard/static/css/bento-noir.css',
  'dwarf/dashboard/static/css/basic.css',
  'dwarf/dashboard/static/css/deck.css',
  'dwarf/dashboard/static/css/deck-op-a.css',
  'dwarf/dashboard/static/css/deck-op-b.css',
  'dwarf/dashboard/static/css/deck-op-c.css',
  'dwarf/dashboard/static/css/deck-op-d.css',
  'dwarf/dashboard/static/css/deck-op-e.css',
].map((file) => path.join(root, file));

const html = `<!doctype html><html><body data-ui="basic"><main class="shell-main"><section id="run-wizard"
  data-resolve-url="/api/run/resolve" data-topology-url="/api/topology/configurations"
  data-prepare-url="/api/run/prepare" data-start-url="/api/run/start" data-dashboard-token="test">
  <figure data-run-logo-state="unconfigured"><figcaption data-run-readiness-text></figcaption></figure>
  <form data-run-wizard-form>
    <input type="radio" name="topology_mode" value="existing" checked><input type="radio" name="topology_mode" value="new">
    <div data-topology-mode-panel="existing"><select id="run-profile" name="profile_id"><option value="profile-r" selected>Profile R</option><option value="profile-next">Profile next</option></select></div>
    <div data-topology-mode-panel="new" hidden></div>
    <input type="radio" name="topology_kind" value="amaru" checked><input type="radio" name="topology_kind" value="mixed">
    <input id="run-topology-id" name="topology_id" value="topology-a">
    <input id="run-cardano-count" name="haskell_count" value="0"><input id="run-amaru-count" name="amaru_count" value="1">
    <input id="run-network-magic" name="network_magic" value="42">
    <select id="run-version-policy" name="version_policy"><option value="">Default</option><option value="policy-next">Policy next</option></select>
    <select id="run-cardano-version" name="cardano_version"><option value="">Default</option><option value="11.1.2">11.1.2</option></select>
    <select id="run-amaru-version" name="amaru_version"><option value="">Default</option><option value="local-next">Local next</option></select>
    <select id="run-operator-registration" name="operator_registration_id"><option value="">Catalog</option><option value="registration-next">Registration next</option></select>
    <label data-version-acknowledgement><input name="acknowledge_unknown_versions" type="checkbox">Acknowledge local version</label>
    <select id="run-scenario" name="scenario_id"><option value="scenario-a" selected>Scenario A</option><option value="scenario-b">Scenario B</option></select>
    <button type="button" data-scenario-id="scenario-a" data-scenario-group="amaru" data-recommended="false" data-search="scenario a">Scenario A</button>
    <button type="button" data-scenario-id="scenario-b" data-scenario-group="amaru" data-recommended="false" data-search="scenario b">Scenario B</button>
    <div data-scenario-picker></div><p data-scenario-picker-count></p>
    <article data-scenario-summary><h3 data-scenario-summary-title></h3><dl data-scenario-summary-facts></dl><p data-scenario-summary-purpose></p><p data-scenario-summary-limit></p><p data-scenario-summary-evidence></p><code data-scenario-raw-id></code></article>
    <select id="run-measurements" name="measurement_profile"><option value="profile-taps" selected>Profile taps</option></select>
    <fieldset class="run-measurement-taps" data-run-measurement-taps><legend>Individual compatible taps</legend></fieldset>
    <div data-run-errors hidden></div><div data-run-summary="review"></div><dl data-run-summary="target"></dl>
    <div data-run-summary="profile"></div><div data-run-summary="versions"></div><div data-run-summary="measurements"></div>
    <div data-run-summary="primitives"></div><dl data-run-summary="settings"></dl><div data-run-summary="readiness"></div><pre data-run-summary="plan"></pre>
    <span data-basic-plan="scenario"></span><span data-basic-plan="profile"></span><span data-basic-plan="target"></span><span data-basic-plan="measurements"></span><span data-basic-plan="digest"></span>
    <div data-basic-run-stage="prepare"></div><div data-basic-run-stage="run"></div><span data-basic-run-gate></span><span data-basic-run-status></span>
    <label data-destructive-confirmation hidden><input type="checkbox" name="confirm_topology_change"></label>
    <button type="button" data-run-action="prepare">Prepare</button><button type="submit" data-run-action="start">Run</button>
    <p data-run-prepare-status></p><p data-run-launch-status></p>
  </form>
  <script id="run-wizard-bootstrap" type="application/json">${JSON.stringify({
    scenarios: [
      {id: 'scenario-a', short_title: 'Scenario A', implementation: 'Amaru', implementation_key: 'amaru', purpose: 'Exercise taps.', limitation: 'None'},
      {id: 'scenario-b', short_title: 'Scenario B', implementation: 'Amaru', implementation_key: 'amaru', purpose: 'Exercise taps.', limitation: 'None'},
    ],
    measurement_profiles: [{id: 'profile-taps', title: 'Tap profile', description: 'Authoritative compatible taps.', taps: [
      {id: 'tap-alpha', enabled: false, title: 'Alpha', description: 'Alpha detail'},
      {id: 'tap-beta', enabled: true, title: 'Beta', description: 'Beta detail'},
      {id: 'tap-gamma', enabled: true, title: 'Gamma', description: 'Gamma detail'},
    ]}],
  })}</script>
</section></main></body></html>`;

const plan = {
  scenario: {id: 'scenario-a', title: 'Scenario A', target: {implementation: 'amaru', version: '10.11.20260912'}, runtime: 'devnet'},
  profile: {id: 'profile-r', label: 'Profile R'},
  topology_shape: {physical_cardano_node_count: 0, physical_amaru_node_count: 1, infrastructure_services: []},
  versions: {policy: 'default', status: 'unconfirmed', requires_acknowledgement: true, resolved: {}},
  measurements: {
    profile: {id: 'profile-taps'},
    resolved: [
      {id: 'tap-beta', definition: {title: 'Beta', description: 'Beta detail'}},
      {id: 'tap-gamma', definition: {title: 'Gamma', description: 'Gamma detail'}},
    ],
    available: [
      {id: 'tap-alpha', definition: {title: 'Alpha', description: 'Alpha detail'}},
      {id: 'tap-beta', definition: {title: 'Beta', description: 'Beta detail'}},
      {id: 'tap-gamma', definition: {title: 'Gamma', description: 'Gamma detail'}},
      {id: 'tap-delta', definition: {title: 'Delta', description: 'Delta detail'}},
    ],
    incompatible: [{id: 'tap-delta', reason: 'requires an authoritative patched target capability'}],
  },
  primitive_cells: [], primitive_names: [], readiness: {required_checks: []}, availability: {state: 'ready', reason: 'ready'},
};

async function checkedIds(page) {
  return page.locator('[data-measurement-tap-id]:checked').evaluateAll((nodes) => nodes.map((node) => node.dataset.measurementTapId));
}

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      await page.setContent(html);
      for (const style of styles) await page.addStyleTag({path: style});
      await page.evaluate(() => {
        window.__resolveBodies = [];
        window.__requests = [];
        window.fetch = (url, options) => {
          window.__requests.push({url: String(url), method: options?.method || 'GET'});
          if (options?.body) window.__resolveBodies.push(JSON.parse(options.body));
          return new Promise(() => {});
        };
      });
      await page.addScriptTag({path: script});
      await page.evaluate((value) => {
        renderPlan(value, 'plan-digest-1');
        document.querySelector('[data-run-logo-state]').dataset.runLogoState = 'ready';
        document.querySelector('[data-run-action="prepare"]').disabled = false;
        document.querySelector('[data-run-action="start"]').disabled = false;
        window.__resolveBodies.length = 0;
        window.__requests.length = 0;
      }, plan);

      const disclosure = page.locator('[data-measurement-taps-disclosure]');
      assert.equal(await disclosure.count(), 1, 'tap presentation uses one disclosure');
      assert.equal(await disclosure.getAttribute('open'), null, 'tap disclosure starts collapsed');
      assert.equal(await disclosure.locator(':scope > summary').textContent(), 'Individual taps · 2 selected');
      assert.deepEqual(await checkedIds(page), ['tap-beta', 'tap-gamma']);
      assert.deepEqual(
        await page.locator('[data-measurement-tap-id]').evaluateAll((nodes) => nodes.map((node) => node.dataset.measurementTapId)),
        ['tap-alpha', 'tap-beta', 'tap-gamma'],
        'authoritative payload-checkbox order remains stable in the DOM',
      );

      await disclosure.locator(':scope > summary').click();
      const search = page.locator('[data-measurement-tap-search]');
      await search.fill('beta');
      await search.blur();
      assert.equal(await page.evaluate(() => window.__resolveBodies.length), 0, 'search then blur never resolves a plan');
      await search.focus();
      await search.press('Enter');
      assert.equal(await page.evaluate(() => window.__requests.length), 0, 'Enter in tap search cannot implicitly submit, prepare, start, or resolve');
      assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'plan-digest-1');
      assert.equal(await page.locator('[data-run-action="start"]').isEnabled(), true, 'display-only controls retain Ready');
      assert.equal(await page.locator('[data-measurement-tap-id]').count(), 3, 'filtered payload checkboxes remain in the DOM');
      assert.deepEqual(await checkedIds(page), ['tap-beta', 'tap-gamma']);

      await search.fill('');
      const visualOrder = await page.locator('[data-measurement-tap-row]').evaluateAll((rows) => rows
        .filter((row) => !row.hidden)
        .map((row) => ({id: row.dataset.measurementTapRow, top: row.getBoundingClientRect().top}))
        .sort((left, right) => left.top - right.top)
        .map((row) => row.id));
      assert.deepEqual(visualOrder.slice(0, 2), ['tap-beta', 'tap-gamma'], 'selected taps render first without moving DOM nodes');

      const unavailable = page.locator('[data-unavailable-measurement-tap-id="tap-delta"]');
      assert.equal(await unavailable.isDisabled(), true);
      assert.match(await unavailable.locator('xpath=../..').textContent(), /requires an authoritative patched target capability/);
      const descriptions = page.locator('[data-measurement-tap-description]');
      assert.ok(await descriptions.count() >= 3);
      assert.equal(await descriptions.first().getAttribute('open'), null, 'descriptions are on demand');
      const beforeDescription = await checkedIds(page);
      await descriptions.first().locator('summary').click();
      assert.deepEqual(await checkedIds(page), beforeDescription, 'description disclosure is a sibling and cannot toggle its checkbox');
      assert.equal(await page.evaluate(() => window.__resolveBodies.length), 0, 'description disclosure never resolves a plan');
      assert.equal(await page.locator('[data-run-action="start"]').isEnabled(), true, 'description disclosure retains Ready');

      await page.evaluate((value) => renderPlan(value, 'plan-digest-1', {invalidate: false}), plan);
      assert.equal(await page.locator('[data-measurement-taps-disclosure]').getAttribute('open'), '', 'manual disclosure state survives rerender');
      assert.equal(await page.locator('[data-measurement-tap-search]').inputValue(), '', 'search state survives rerender');

      const panelMetrics = await page.locator('[data-run-measurement-taps]').evaluate((node) => {
        const parseColor = (value) => {
          const parts = value.match(/rgba?\(([^)]+)\)/)[1].split(/[, ]+/).filter(Boolean).map(Number);
          return {r: parts[0], g: parts[1], b: parts[2]};
        };
        const luminance = ({r, g, b}) => {
          const channel = (value) => {
            const normalized = value / 255;
            return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
          };
          return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
        };
        const contrast = (foreground, background) => {
          const foregroundLuminance = luminance(parseColor(foreground));
          const backgroundLuminance = luminance(parseColor(background));
          return (Math.max(foregroundLuminance, backgroundLuminance) + 0.05)
            / (Math.min(foregroundLuminance, backgroundLuminance) + 0.05);
        };
        const style = getComputedStyle(node);
        const search = node.querySelector('[data-measurement-tap-search]');
        const searchStyle = getComputedStyle(search);
        const searchLabelStyle = getComputedStyle(node.querySelector('.run-measurement-taps__search'));
        const summaryStyle = getComputedStyle(node.querySelector('[data-measurement-taps-disclosure] > summary'));
        const tapLabelStyle = getComputedStyle(node.querySelector('[data-measurement-tap-id]').closest('label'));
        const checkboxStyle = getComputedStyle(node.querySelector('[data-measurement-tap-id]'));
        const descriptionStyle = getComputedStyle(node.querySelector('[data-measurement-tap-description] > summary'));
        const placeholderStyle = getComputedStyle(search, '::placeholder');
        const expected = document.createElement('i');
        expected.style.cssText = 'position:absolute;color:var(--dk-gold);border-color:var(--dk-line);background:var(--dk-table2)';
        document.body.append(expected);
        const expectedStyle = getComputedStyle(expected);
        const result = {
          overflow: node.scrollWidth <= node.clientWidth,
          border: style.borderColor,
          expectedBorder: expectedStyle.borderColor,
          summary: summaryStyle.color,
          expectedGold: expectedStyle.color,
          summaryFont: summaryStyle.fontFamily,
          summarySize: parseFloat(summaryStyle.fontSize),
          summaryHeight: node.querySelector('[data-measurement-taps-disclosure] > summary').getBoundingClientRect().height,
          searchBackground: searchStyle.backgroundColor,
          expectedBackground: expectedStyle.backgroundColor,
          searchFont: searchStyle.fontFamily,
          searchSize: parseFloat(searchStyle.fontSize),
          searchLabelFont: searchLabelStyle.fontFamily,
          searchLabelSize: parseFloat(searchLabelStyle.fontSize),
          tapLabelFont: tapLabelStyle.fontFamily,
          tapLabelSize: parseFloat(tapLabelStyle.fontSize),
          checkboxAccent: checkboxStyle.accentColor,
          checkboxWidth: parseFloat(checkboxStyle.width),
          checkboxHeight: parseFloat(checkboxStyle.height),
          descriptionFont: descriptionStyle.fontFamily,
          descriptionSize: parseFloat(descriptionStyle.fontSize),
          descriptionHeight: node.querySelector('[data-measurement-tap-description] > summary').getBoundingClientRect().height,
          placeholderContrast: contrast(placeholderStyle.color, searchStyle.backgroundColor),
        };
        expected.remove();
        return result;
      });
      assert.equal(panelMetrics.overflow, true, 'tap disclosure does not overflow');
      assert.equal(panelMetrics.border, panelMetrics.expectedBorder, 'tap panel uses the Basic line token');
      assert.equal(panelMetrics.summary, panelMetrics.expectedGold, 'tap summary uses the Basic gold token');
      assert.equal(panelMetrics.searchBackground, panelMetrics.expectedBackground, 'tap search uses the Basic table token');
      for (const [role, family] of Object.entries({summary: panelMetrics.summaryFont, search: panelMetrics.searchFont, searchLabel: panelMetrics.searchLabelFont, tapLabel: panelMetrics.tapLabelFont, description: panelMetrics.descriptionFont})) {
        assert.match(family, /IBM Plex Sans/, `${role} uses the single interface font`);
      }
      assert.equal(panelMetrics.summarySize, 14, 'tap disclosure uses the 14px secondary role');
      assert.equal(panelMetrics.searchSize, 16, 'tap search uses the 16px form role');
      assert.equal(panelMetrics.searchLabelSize, 14, 'tap search label uses the 14px secondary role');
      assert.equal(panelMetrics.tapLabelSize, 16, 'tap names use the 16px interface role');
      assert.equal(panelMetrics.descriptionSize, 14, 'tap description disclosure uses the 14px secondary role');
      assert.ok(panelMetrics.descriptionHeight >= 44, 'tap description disclosure retains a 44px target');
      assert.ok(panelMetrics.summaryHeight >= 44, 'tap disclosure retains a 44px target');
      assert.equal(panelMetrics.checkboxAccent, panelMetrics.expectedGold, 'tap checkboxes use the Basic gold accent');
      assert.ok(panelMetrics.checkboxWidth >= 18 && panelMetrics.checkboxHeight >= 18, 'tap checkboxes expose a legible control');
      assert.ok(panelMetrics.placeholderContrast >= 4.5, `tap placeholder remains readable (${panelMetrics.placeholderContrast})`);

      await page.locator('[name="acknowledge_unknown_versions"]').evaluate((input) => { input.checked = true; });
      await page.locator('[data-measurement-tap-id="tap-alpha"]').check();
      await page.waitForFunction(() => window.__resolveBodies.length === 1);
      const body = await page.evaluate(() => window.__resolveBodies[0]);
      assert.deepEqual(body.measurement_overrides, [
        {id: 'tap-alpha', enabled: true}, {id: 'tap-beta', enabled: true},
        {id: 'tap-gamma', enabled: true},
      ], 'explicit checkbox change alone uses the existing full override payload');
      assert.equal(
        await page.locator('[name="acknowledge_unknown_versions"]').isChecked(),
        true,
        'tap-only changes preserve acknowledgement for the unchanged exact version identity',
      );
      assert.equal(await page.locator('[data-run-action="start"]').isEnabled(), false, 'explicit checkbox change invalidates Ready');

      const consent = {
        tap_ids: ['tap-alpha'],
        context_digest: `sha256:${'6'.repeat(64)}`,
      };
      await page.evaluate(({value, expected}) => {
        window.__resolveBodies.length = 0;
        window.__requests.length = 0;
        syncVersionAcknowledgement(true);
        document.querySelector('[name="acknowledge_unknown_versions"]').checked = true;
        renderError({
          field: 'measurements',
          message: 'measurement tap-alpha requires experimental consent',
          details: {
            profile: {id: 'profile-taps'},
            requested: [{id: 'tap-alpha', enabled: true, source: 'launch-override'}],
            resolved: [], incompatible: [], unavailable: [], disabled: [],
            available: [{
              id: 'tap-alpha',
              definition: {title: 'Alpha', description: 'Alpha detail'},
              compatibility: {state: 'unknown', reason: 'source is unverified'},
              capability: {state: 'available', reason: 'external collector'},
            }],
            experimental: [{
              id: 'tap-alpha', reason: 'unknown tap compatibility requires experimental consent',
              compatibility: {state: 'unknown', reason: 'source is unverified'},
              capability: {state: 'available', reason: 'external collector'},
            }],
            experimental_consent_required: expected,
          },
        }, false);
      }, {value: plan, expected: consent});
      assert.equal(
        await page.locator('[name="acknowledge_unknown_versions"]').isChecked(),
        true,
        'measurement consent errors preserve acknowledgement of the unchanged exact local version',
      );
      assert.equal(
        await page.locator('[data-version-acknowledgement]').isVisible(),
        true,
        'measurement consent errors keep the required local-version acknowledgement visible',
      );
      assert.equal(await page.locator('[data-measurement-tap-id="tap-alpha"]').isChecked(), true, 'unknown candidate remains visibly selected before consent');
      assert.match(await page.locator('[data-measurement-tap-row="tap-alpha"]').textContent(), /Unknown compatibility · available capability/);
      assert.equal(await page.locator('[data-run-action="prepare"]').isDisabled(), true, 'unknown candidate cannot Prepare before consent');
      await page.locator('[data-experimental-measurement-consent]').check();
      await page.waitForFunction(() => window.__resolveBodies.length === 1);
      const consentBody = await page.evaluate(() => window.__resolveBodies[0]);
      assert.deepEqual(consentBody.experimental_measurement_consent, consent, 'server-issued exact context is echoed with selected tap IDs');
      assert.deepEqual(consentBody.measurement_overrides, [{id: 'tap-alpha', enabled: true}], 'consent does not change the selected tap payload');

      const identityChanges = [
        {name: 'profile_id', value: 'profile-next'},
        {name: 'scenario_id', value: 'scenario-b'},
        {name: 'version_policy', value: 'policy-next'},
        {name: 'cardano_version', value: '11.1.2'},
        {name: 'amaru_version', value: 'local-next'},
        {name: 'operator_registration_id', value: 'registration-next'},
        {name: 'topology_mode', value: 'new', radio: true},
        {name: 'topology_kind', value: 'mixed', radio: true},
        {name: 'topology_id', value: 'topology-b'},
        {name: 'haskell_count', value: '1'},
        {name: 'amaru_count', value: '2'},
        {name: 'network_magic', value: '43'},
      ];
      for (const change of identityChanges) {
        await page.evaluate((expectedConsent) => {
          window.__resolveBodies.length = 0;
          document.querySelector('[name="acknowledge_unknown_versions"]').checked = true;
          experimentalMeasurementConsent = expectedConsent;
        }, consent);
        const control = page.locator(`[name="${change.name}"]${change.radio ? `[value="${change.value}"]` : ''}`);
        if (change.radio) {
          await control.check();
        } else if (await control.evaluate((node) => node.tagName === 'SELECT')) {
          await control.selectOption(change.value);
        } else {
          await control.evaluate((input, nextValue) => { input.value = nextValue; }, change.value);
          await control.dispatchEvent('change');
        }
        await page.waitForFunction(() => window.__resolveBodies.length >= 1);
        assert.equal(
          await page.locator('[name="acknowledge_unknown_versions"]').isChecked(),
          false,
          `${change.name} identity changes clear the prior exact-version acknowledgement`,
        );
        const changedIdentityBody = await page.evaluate(() => window.__resolveBodies.at(-1));
        assert.equal(
          changedIdentityBody.acknowledge_unknown_versions,
          false,
          `${change.name} changes cannot replay acknowledgement against a new exact context`,
        );
        assert.ok(
          changedIdentityBody.experimental_measurement_consent == null,
          `${change.name} changes clear the prior tap consent binding`,
        );
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
