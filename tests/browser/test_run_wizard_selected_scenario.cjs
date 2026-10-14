const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const script = path.join(root, 'dwarf/dashboard/static/js/run-wizard.js');
const basicReducer = path.join(root, 'dwarf/dashboard/static/js/basic-reduce.js');
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

const scenario = {
  id: 'cardano-measurement-e2e-stock',
  short_title: 'Stock measurements — real-node end-to-end proof',
  implementation: 'Cardano-node', implementation_key: 'cardano-node',
  measurement_state: 'measurement-enabled', measurement_profile: 'cardano-security-default',
  deployment_profile: 'profile-cardano-stock', target_version: '11.1.2',
  proof_state: 'retained-proof', retained_run_id: 'retained-run', evidence_url: '/operate/runs/retained-run',
  purpose: 'Prove the stock real-node measurement path.', limitation: 'Exact compatible stock target required.',
};

const plan = {
  scenario: {id: scenario.id, title: scenario.short_title, target: {implementation: 'cardano-node', version: '11.1.2'}, runtime: 'devnet'},
  profile: {id: 'profile-cardano-stock', label: 'Cardano stock'},
  topology_shape: {physical_cardano_node_count: 1, physical_amaru_node_count: 0, infrastructure_services: []},
  versions: {policy: 'default', status: 'confirmed', resolved: {}},
  measurements: {
    profile: {id: 'cardano-security-default'},
    resolved: [
      {id: 'tap-one', definition: {title: 'Tap one', description: 'First tap'}},
      {id: 'tap-two', definition: {title: 'Tap two', description: 'Second tap'}},
    ],
    available: [
      {id: 'tap-one', definition: {title: 'Tap one', description: 'First tap'}},
      {id: 'tap-two', definition: {title: 'Tap two', description: 'Second tap'}},
    ],
    incompatible: [],
  },
  primitive_cells: [], primitive_names: [], readiness: {required_checks: []},
  availability: {state: 'ready', reason: 'ready'},
};

const extraSubjects = Array.from({length: 7}, (_, subjectIndex) => {
  const cards = Array.from({length: 50}, (_, cardIndex) => {
    const id = `fixture-scenario-${subjectIndex}-${cardIndex}`;
    return `<button type="button" data-scenario-card data-scenario-id="${id}" data-scenario-group="cardano-node" data-recommended="false" data-search="${id}" aria-selected="false">${id}</button>`;
  }).join('');
  return `<details data-scenario-disclosure="subject:fixture-${subjectIndex}"><summary>Fixture subject ${subjectIndex}</summary><details data-scenario-disclosure="family:fixture-${subjectIndex}"><summary>Fixture family ${subjectIndex}</summary>${cards}</details></details>`;
}).join('');

const html = `<!doctype html><html><body data-ui="basic"><main class="shell-main"><h1 class="visually-hidden">Run</h1>
<section id="run-wizard" class="run-wizard run-wizard--basic"
  data-basic-reducer="skip"
  data-resolve-url="/api/run/resolve" data-topology-url="/api/topology/configurations"
  data-prepare-url="/api/run/prepare" data-start-url="/api/run/start" data-dashboard-token="test">
  <figure class="run-readiness-logo run-readiness-logo--centered" data-run-logo-state="unconfigured" aria-labelledby="run-readiness-text">
    <span class="run-readiness-logo__image" aria-hidden="true"><img><span class="run-readiness-logo__fill"><img></span></span>
    <figcaption id="run-readiness-text" data-run-readiness-text aria-live="polite">Choose a supported topology to begin.</figcaption>
  </figure>
  <nav class="run-wizard__progress" aria-label="Basic run stages"><ol><li><a data-run-progress="topology"><span>1</span>Topology</a></li><li><a data-run-progress="measurements"><span>4</span>Measurement taps</a></li></ol></nav>
  <form data-run-wizard-form>
    <fieldset class="run-choice-grid"><legend>Topology source</legend><label><input type="radio" name="topology_mode" value="existing" checked><strong>Use existing</strong><span>Reuse a managed topology.</span></label></fieldset>
    <div data-topology-mode-panel="existing"><label for="run-profile">Topology profile <span class="field-required">Required</span></label><select id="run-profile" name="profile_id"><option value="profile-cardano-stock" selected>Cardano stock</option></select></div>
    <div data-topology-mode-panel="new" hidden></div>
    <input type="radio" name="topology_kind" value="cardano-node" checked>
    <input id="run-cardano-count" name="haskell_count" value="1"><input id="run-amaru-count" name="amaru_count" value="0">
    <label for="run-version-policy">Version policy <span class="field-optional">Exact selection</span></label><select id="run-version-policy" name="version_policy"><option value="">Default</option></select>
    <p class="run-wizard__selection">Catalog <code data-version-catalog-revision>2026-09-23-test-catalog</code>. Unsupported and unavailable pins remain visible.</p>
    <select id="run-cardano-version" name="cardano_version"><option value="">Default</option></select>
    <select id="run-amaru-version" name="amaru_version"><option value="">Default</option></select>
    <input name="acknowledge_unknown_versions" type="checkbox">
    <select id="run-scenario" name="scenario_id"><option value="${scenario.id}" selected>${scenario.short_title}</option></select>
    <div data-scenario-discovery><div data-scenario-picker>
      <details data-scenario-disclosure="subject:measurements"><summary>Measurement validation <span data-selected-ancestor-badge hidden>✓ Selected</span></summary>
        <details data-scenario-disclosure="family:measurements:stock"><summary>Stock measurements <span data-selected-ancestor-badge hidden>✓ Selected</span></summary>
          <button type="button" data-scenario-card data-scenario-id="${scenario.id}" data-scenario-group="cardano-node" data-recommended="false" data-search="stock measurements" aria-selected="true">${scenario.short_title}</button>
          <button type="button" class="scenario-picker__option scenario-picker__option--amaru" aria-disabled="true" data-unavailable-reason="Requires a compatible local artifact"><strong class="scenario-picker__title">Unavailable Amaru check</strong><span class="scenario-picker__purpose">Explains why this exact choice cannot run.</span></button>
        </details>
      </details>
      ${extraSubjects}
    </div></div><p data-scenario-picker-count></p>
    <details class="scenario-selection" data-scenario-summary aria-live="polite">
      <summary><span>Selected scenario:</span> <strong data-scenario-summary-title>Selected scenario</strong> <span class="scenario-selection__badge" data-scenario-selected-badge>✓ Selected</span></summary>
      <div class="scenario-selection__body"><dl data-scenario-summary-facts></dl><p data-scenario-summary-purpose></p><p data-scenario-summary-limit></p><p data-scenario-summary-evidence></p><div data-scenario-summary-context></div><details data-scenario-technical><summary>Technical scenario ID</summary><code data-scenario-raw-id></code></details></div>
    </details>
    <h2 id="run-step-measurements">Measurement taps</h2>
    <select id="run-measurements" name="measurement_profile"><option value="cardano-security-default" selected>Cardano security default</option></select>
    <fieldset data-run-measurement-taps></fieldset>
    <div data-run-errors hidden></div><div class="run-wizard__review" data-run-summary="review">Resolved configuration is ready for review.</div><dl data-run-summary="target"></dl>
    <div data-run-summary="profile"></div><div data-run-summary="versions"></div><div data-run-summary="measurements"></div><div data-run-summary="primitives"></div><dl data-run-summary="settings"></dl><div data-run-summary="readiness"></div><pre data-run-summary="plan"></pre>
    <span data-basic-plan="scenario"></span><span data-basic-plan="profile"></span><span data-basic-plan="target"></span><span data-basic-plan="measurements"></span><span data-basic-plan="digest"></span>
    <div data-basic-run-stage="prepare"></div><div data-basic-run-stage="run"></div><span data-basic-run-gate></span><span data-basic-run-status></span>
    <div class="run-basic__lifecycle"><span>Queued</span><span>Running</span><span>Terminal</span></div>
    <label data-destructive-confirmation hidden><input type="checkbox" name="confirm_topology_change"></label>
    <button type="button" data-run-action="prepare">Prepare</button><button class="cta" type="submit" data-run-action="start">Run</button>
    <p data-run-prepare-status></p><p data-run-launch-status></p>
  </form>
  <script id="run-wizard-bootstrap" type="application/json">${JSON.stringify({scenarios: [scenario], measurement_profiles: [{id: 'cardano-security-default', title: 'Cardano security default', description: 'Stock taps.', taps: []}]})}</script>
</section></main></body></html>`;

async function state(page) {
  return page.evaluate(() => ({
    scenario: document.querySelector('#run-scenario').value,
    profile: document.querySelector('#run-profile').value,
    checked: [...document.querySelectorAll('[data-measurement-tap-id]:checked')].map((node) => node.dataset.measurementTapId),
    digest: document.querySelector('[data-basic-plan="digest"]').textContent,
    runDisabled: document.querySelector('[data-run-action="start"]').disabled,
    logo: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
  }));
}

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      await page.setContent(html);
      for (const style of styles) await page.addStyleTag({path: style});
      await page.evaluate(() => {
        window.__requests = [];
        window.fetch = (url) => { window.__requests.push(new URL(url, location.href).pathname); return new Promise(() => {}); };
      });
      await page.addScriptTag({path: script});
      await page.addScriptTag({path: basicReducer});
      await page.evaluate((value) => {
        renderPlan(value, 'stock-plan-digest');
        document.querySelector('[data-run-logo-state]').dataset.runLogoState = 'ready';
        document.querySelector('[data-run-action="start"]').disabled = false;
        document.querySelector('[data-run-action="prepare"]').disabled = false;
        window.__requests.length = 0;
      }, plan);

      const selection = page.locator('[data-scenario-summary]');
      const subjectSummaries = page.locator('[data-scenario-disclosure^="subject:"] > summary');
      assert.equal(await subjectSummaries.count(), 8, 'all eight subject summaries remain in the Run picker');
      assert.equal((await subjectSummaries.first().innerText()).toLowerCase().includes('measurement validation'), true, 'measurement subject uses the validation label');
      assert.equal((await page.locator('[data-run-progress="measurements"]').innerText()).toLowerCase().replace(/\s+/g, ' ').includes('measurement taps'), true);
      assert.equal((await page.locator('#run-step-measurements').innerText()).toLowerCase(), 'measurement taps');
      for (let index = 0; index < 8; index += 1) assert.equal(await subjectSummaries.nth(index).isVisible(), true, `subject ${index + 1} summary stays visible`);
      assert.equal(await page.locator('[data-scenario-card]').count(), 351, 'the reducer retains all 351 canonical scenario choices');
      assert.equal(await selection.isVisible(), true, 'Basic reducer keeps the selected scenario visible');
      assert.equal(await selection.evaluate((node) => node.classList.contains('is-basic-capped')), false, 'selected scenario is not capped behind Show more');
      assert.equal(await selection.getAttribute('open'), null, 'selected scenario starts closed');
      assert.equal((await selection.locator(':scope > summary').innerText()).replace(/\s+/g, ' ').trim(), `Selected scenario: ${scenario.short_title} ✓ Selected`);
      assert.equal(await selection.locator('[data-scenario-selected-badge]').isVisible(), true);
      const ancestors = page.locator('[data-scenario-disclosure][data-contains-selected="true"]');
      assert.equal(await ancestors.count(), 2);
      for (let index = 0; index < 2; index += 1) {
        const ancestor = ancestors.nth(index);
        assert.equal(await ancestor.getAttribute('open'), null, 'selected ancestor remains closed');
        assert.equal(await ancestor.getAttribute('data-contains-selected'), 'true');
        assert.equal(await ancestor.locator(':scope > summary [data-selected-ancestor-badge]').getAttribute('hidden'), null);
      }
      assert.equal(await ancestors.first().locator(':scope > summary [data-selected-ancestor-badge]').isVisible(), true, 'top-level closed ancestor visibly names the selection');
      const measurementDisclosure = page.locator('[data-measurement-taps-disclosure]');
      assert.equal(await measurementDisclosure.isVisible(), true, 'measurement disclosure remains visible');
      assert.equal(await measurementDisclosure.getAttribute('open'), null, 'measurement disclosure starts closed');

      const before = await state(page);
      await selection.locator(':scope > summary').click();
      await selection.locator('[data-scenario-technical] > summary').click();
      await selection.locator(':scope > summary').focus();
      await page.keyboard.press('Enter');
      const after = await state(page);
      assert.deepEqual(after, before, 'display disclosures preserve exact selection, taps, digest, Ready, and logo state');
      assert.deepEqual(await page.evaluate(() => window.__requests), [], 'display disclosures emit no resolve, prepare, or start request');

      await page.locator('[data-run-action="start"]').evaluate(async (node) => {
        node.disabled = true;
        await Promise.all(node.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
      });
      const layout = await page.evaluate(() => {
        const parseColor = (value) => {
          const match = value.match(/rgba?\(([^)]+)\)/);
          const parts = match[1].split(/[, ]+/).filter(Boolean).map(Number);
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
        const logo = document.querySelector('[data-run-logo-state]').getBoundingClientRect();
        const wizard = document.querySelector('#run-wizard').getBoundingClientRect();
        const panel = document.querySelector('[data-scenario-summary]');
        const panelSummary = panel.querySelector(':scope > summary');
        const panelBox = panel.getBoundingClientRect();
        const summaryBox = panelSummary.getBoundingClientRect();
        const panelStyle = getComputedStyle(panel);
        const bodyStyle = getComputedStyle(document.body);
        const probe = document.createElement('i');
        probe.style.color = 'var(--dk-gold)'; document.body.append(probe);
        const gold = getComputedStyle(probe).color; probe.remove();
        const surfaceProbe = document.createElement('i');
        surfaceProbe.style.backgroundColor = 'var(--dk-back)'; document.body.append(surfaceProbe);
        const expectedDisabledSurface = getComputedStyle(surfaceProbe).backgroundColor; surfaceProbe.remove();
        const disabledActionProbe = document.createElement('i');
        disabledActionProbe.style.backgroundColor = 'var(--dk-table2)'; document.body.append(disabledActionProbe);
        const expectedDisabledActionSurface = getComputedStyle(disabledActionProbe).backgroundColor; disabledActionProbe.remove();
        const progressNode = document.querySelector('[data-run-progress="measurements"]');
        const progress = getComputedStyle(progressNode);
        const heading = getComputedStyle(document.querySelector('#run-step-measurements'));
        const selection = getComputedStyle(panelSummary);
        const badge = getComputedStyle(document.querySelector('[data-scenario-selected-badge]'));
        const readiness = getComputedStyle(document.querySelector('[data-run-logo-state]'));
        const technical = getComputedStyle(document.querySelector('[data-scenario-raw-id]'));
        const catalogCode = getComputedStyle(document.querySelector('[data-version-catalog-revision]'));
        const formLabel = getComputedStyle(document.querySelector('label[for="run-profile"]'));
        const profileNode = document.querySelector('#run-profile');
        const profileControl = getComputedStyle(profileNode);
        const versionControl = getComputedStyle(document.querySelector('#run-version-policy'));
        const measurementControl = getComputedStyle(document.querySelector('#run-measurements'));
        const radioLabel = document.querySelector('.run-choice-grid > label');
        const radioLabelStyle = getComputedStyle(radioLabel);
        const choiceLegendStyle = getComputedStyle(document.querySelector('.run-choice-grid legend'));
        const choiceTitleStyle = getComputedStyle(document.querySelector('.run-choice-grid strong'));
        const reviewStyle = getComputedStyle(document.querySelector('.run-wizard__review'));
        const lifecycleStyle = getComputedStyle(document.querySelector('.run-basic__lifecycle span'));
        const requiredStyle = getComputedStyle(document.querySelector('.field-required'));
        const unavailableCard = document.querySelector('.scenario-picker__option[aria-disabled="true"]');
        const unavailableStyle = getComputedStyle(unavailableCard);
        const unavailablePurposeStyle = getComputedStyle(unavailableCard.querySelector('.scenario-picker__purpose'));
        const unavailableReasonStyle = getComputedStyle(unavailableCard, '::after');
        const run = document.querySelector('[data-run-action="start"]');
        const disabled = getComputedStyle(run);
        return {
          centered: Math.abs((logo.left + logo.width / 2) - (wizard.left + wizard.width / 2)) <= 1,
          noOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
          panelBorder: getComputedStyle(panel).borderColor,
          panelPaddingTop: panelStyle.paddingTop,
          panelPaddingBottom: panelStyle.paddingBottom,
          closedPanelExtraHeight: panelBox.height - summaryBox.height,
          gold,
          logoState: document.querySelector('[data-run-logo-state]').dataset.runLogoState,
          hiddenHeading: getComputedStyle(document.querySelector('h1')).position === 'absolute',
          basicInk: bodyStyle.getPropertyValue('--dk-ink').trim().length > 0,
          progressFont: progress.fontFamily,
          progressSize: parseFloat(progress.fontSize),
          progressHeight: progressNode.getBoundingClientRect().height,
          headingFont: heading.fontFamily,
          headingSize: parseFloat(heading.fontSize),
          selectionFont: selection.fontFamily,
          selectionSize: parseFloat(selection.fontSize),
          badgeFont: badge.fontFamily,
          badgeSize: parseFloat(badge.fontSize),
          readinessFont: readiness.fontFamily,
          readinessSize: parseFloat(readiness.fontSize),
          technicalFont: technical.fontFamily,
          catalogCodeColor: catalogCode.color,
          formLabelFont: formLabel.fontFamily,
          formLabelSize: parseFloat(formLabel.fontSize),
          profileFont: profileControl.fontFamily,
          profileSize: parseFloat(profileControl.fontSize),
          profileHeight: profileNode.getBoundingClientRect().height,
          versionFont: versionControl.fontFamily,
          versionSize: parseFloat(versionControl.fontSize),
          measurementFont: measurementControl.fontFamily,
          measurementSize: parseFloat(measurementControl.fontSize),
          radioFont: radioLabelStyle.fontFamily,
          radioSize: parseFloat(radioLabelStyle.fontSize),
          radioHeight: radioLabel.getBoundingClientRect().height,
          choiceLegendFont: choiceLegendStyle.fontFamily,
          choiceLegendSize: parseFloat(choiceLegendStyle.fontSize),
          choiceTitleFont: choiceTitleStyle.fontFamily,
          choiceTitleSize: parseFloat(choiceTitleStyle.fontSize),
          reviewFont: reviewStyle.fontFamily,
          reviewSize: parseFloat(reviewStyle.fontSize),
          lifecycleFont: lifecycleStyle.fontFamily,
          lifecycleSize: parseFloat(lifecycleStyle.fontSize),
          requiredFont: requiredStyle.fontFamily,
          requiredSize: parseFloat(requiredStyle.fontSize),
          unavailableOpacity: unavailableStyle.opacity,
          unavailableSurface: unavailableStyle.backgroundColor,
          expectedDisabledSurface,
          unavailablePurposeContrast: contrast(unavailablePurposeStyle.color, unavailableStyle.backgroundColor),
          unavailableReasonContrast: contrast(unavailableReasonStyle.color, unavailableStyle.backgroundColor),
          unavailableReasonFont: unavailableReasonStyle.fontFamily,
          unavailableReasonSize: parseFloat(unavailableReasonStyle.fontSize),
          disabledBackground: disabled.backgroundColor,
          expectedDisabledActionSurface,
          disabledFont: disabled.fontFamily,
          disabledSize: parseFloat(disabled.fontSize),
          disabledOpacity: disabled.opacity,
          disabledContrast: contrast(disabled.color, disabled.backgroundColor),
        };
      });
      assert.equal(layout.centered, true, 'existing readiness logo is centered');
      assert.equal(layout.noOverflow, true, 'compact layout has no horizontal overflow');
      assert.equal(layout.panelBorder, layout.gold, 'selected scenario has Basic gold outline');
      assert.equal(layout.panelPaddingTop, '0px', 'closed selected scenario has no blank outer top padding');
      assert.equal(layout.panelPaddingBottom, '0px', 'closed selected scenario has no blank outer bottom padding');
      assert.ok(layout.closedPanelExtraHeight <= 2, `closed panel stays tight around its summary (${layout.closedPanelExtraHeight}px extra)`);
      assert.equal(layout.logoState, 'ready', 'existing readiness state is unchanged');
      assert.equal(layout.hiddenHeading, true, 'Run heading remains accessible but not visible');
      assert.equal(layout.basicInk, true);
      for (const [role, family] of Object.entries({progress: layout.progressFont, heading: layout.headingFont, selection: layout.selectionFont, badge: layout.badgeFont, readiness: layout.readinessFont})) {
        assert.match(family, /IBM Plex Sans/, `${role} uses the single interface font`);
      }
      assert.match(layout.technicalFont, /Mono|monospace/, 'technical IDs retain a monospace role');
      assert.equal(layout.catalogCodeColor, layout.gold, 'Step 2 catalog revision uses the Basic technical gold role');
      for (const [role, family] of Object.entries({formLabel: layout.formLabelFont, profile: layout.profileFont, version: layout.versionFont, measurement: layout.measurementFont, radio: layout.radioFont, choiceLegend: layout.choiceLegendFont, choiceTitle: layout.choiceTitleFont, review: layout.reviewFont, lifecycle: layout.lifecycleFont, required: layout.requiredFont, unavailableReason: layout.unavailableReasonFont})) {
        assert.match(family, /IBM Plex Sans/, `${role} uses the single interface font`);
      }
      assert.equal(layout.headingSize, 20, 'section heading uses the 20px role');
      assert.equal(layout.selectionSize, 16, 'selected scenario uses the 16px interface role');
      assert.equal(layout.progressSize, 12, 'progress navigation uses the 12px compact role');
      assert.equal(layout.badgeSize, 12, 'selected badge uses the 12px compact role');
      assert.equal(layout.readinessSize, 14, 'readiness helper uses the 14px secondary role');
      assert.equal(layout.formLabelSize, 16, 'Run form labels use the 16px interface role');
      assert.equal(layout.profileSize, 16, 'profile control uses the 16px form role');
      assert.equal(layout.versionSize, 16, 'version control uses the 16px form role');
      assert.equal(layout.measurementSize, 16, 'measurement control uses the 16px form role');
      assert.equal(layout.radioSize, 16, 'radio labels use the 16px interface role');
      assert.equal(layout.choiceLegendSize, 14, 'topology choice legend uses the 14px secondary role');
      assert.equal(layout.choiceTitleSize, 16, 'topology choice title uses the 16px interface role');
      assert.equal(layout.reviewSize, 14, 'review text uses the 14px secondary role');
      assert.equal(layout.lifecycleSize, 12, 'lifecycle states use the 12px compact role');
      assert.equal(layout.requiredSize, 14, 'required hints remain readable secondary text');
      assert.ok(layout.profileHeight >= 44 && layout.radioHeight >= 44, 'Run form controls retain 44px targets');
      assert.equal(layout.unavailableOpacity, '1', 'disabled scenario distinction does not dim the whole card');
      assert.equal(layout.unavailableSurface, layout.expectedDisabledSurface, 'disabled scenario uses the intended Basic back surface');
      assert.equal(layout.unavailableReasonSize, 14, 'disabled scenario reason uses the 14px helper role');
      assert.ok(layout.unavailablePurposeContrast >= 4.5, `disabled scenario purpose remains readable (${layout.unavailablePurposeContrast})`);
      assert.ok(layout.unavailableReasonContrast >= 4.5, `disabled scenario reason remains readable (${layout.unavailableReasonContrast})`);
      assert.ok(layout.progressHeight >= 44, 'progress navigation retains a 44px target');
      assert.notEqual(layout.disabledBackground, layout.gold, 'disabled Run is not presented as an enabled gold action');
      assert.equal(layout.disabledBackground, layout.expectedDisabledActionSurface, 'disabled Run uses the intended Basic table surface after its CSS transition');
      assert.match(layout.disabledFont, /IBM Plex Sans/, 'Run action uses the single interface font');
      assert.equal(layout.disabledSize, 16, 'Run action uses the 16px control role');
      assert.equal(layout.disabledOpacity, '1', 'disabled distinction does not depend on low opacity');
      assert.ok(layout.disabledContrast >= 4.5, `disabled Run remains readable (${layout.disabledContrast})`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
