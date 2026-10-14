const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const controller = fs.readFileSync(
  path.join(root, 'dwarf/dashboard/static/js/scenario-discovery.js'),
  'utf8',
);
const basicReducer = fs.readFileSync(path.join(root, 'dwarf/dashboard/static/js/basic-reduce.js'), 'utf8');
const styleNames = [
  'plex.css', 'tokens.css', 'themes.css', 'base.css', 'bento-noir.css', 'basic.css',
  'deck.css', 'deck-op-a.css', 'deck-op-b.css', 'deck-op-c.css', 'deck-op-d.css', 'deck-op-e.css',
];
const styleSheets = Object.fromEntries(styleNames.map((name) => [
  `/${name}`,
  fs.readFileSync(path.join(root, `dwarf/dashboard/static/css/${name}`), 'utf8'),
]));
const tokens = styleSheets['/tokens.css'];
const base = styleSheets['/base.css'];
const deck = styleSheets['/deck.css'];
const deckOpE = styleSheets['/deck-op-e.css'];
const fullStyleLinks = styleNames.map((name) => `<link rel="stylesheet" href="/${name}">`).join('');

const facets = [
  ['implementation', 'amaru'], ['implementation', 'cardano-node'],
  ['goal', 'recovery'], ['execution', 'live'],
];
const buttons = facets.map(([group, value]) =>
  `<button type="button" data-facet-group="${group}" data-facet-value="${value}" aria-pressed="false">${value}</button>`,
).join('');

const catalog = `<!doctype html><body>
  <section data-scenario-discovery data-discovery-scope="browser-contract">
    <input data-discovery-query><input type="checkbox" data-discovery-show-maintenance>
    ${buttons}<button type="button" data-discovery-clear>Clear</button><p data-discovery-count></p>
    <details data-scenario-family-group="family-one"><summary>Family one</summary>
      <article data-scenario-card data-scenario-family="family-one" data-maintenance="shown"
        data-search="first exact-one" data-facet-implementation="amaru" data-facet-execution="live"
        data-facet-goal="recovery"><a href="/run?scenario=exact-one">Exact one</a></article>
    </details>
    <details data-scenario-family-group="family-two"><summary>Family two</summary>
      <article data-scenario-card data-scenario-family="family-two" data-maintenance="shown"
        data-search="second exact-two" data-facet-implementation="cardano-node" data-facet-execution="live"
        data-facet-goal="recovery"><a href="/run?scenario=exact-two">Exact two</a></article>
    </details>
  </section>
  <script type="module" src="/scenario-discovery.js"></script>
</body>`;

const selectedRun = `<!doctype html><body>
  <section data-scenario-discovery data-discovery-scope="run">
    <input data-discovery-query><input type="checkbox" data-discovery-show-maintenance>
    ${buttons}<button type="button" data-discovery-clear>Clear</button><p data-discovery-count></p>
    <select id="run-scenario"><option value="maintenance-exact" selected>Maintenance exact</option><option value="other-exact">Other exact</option></select>
    <span data-basic-plan="digest">frozen-plan-digest</span>
    <details data-scenario-family-group="selected-family">
      <summary>Selected family</summary>
      <button type="button" data-scenario-card data-scenario-id="maintenance-exact"
        data-scenario-family="selected-family" data-maintenance="hidden-by-default"
        data-search="maintenance exact selected" data-facet-implementation="amaru"
        data-facet-execution="live" data-facet-goal="recovery"
        aria-selected="true">Maintenance exact</button>
    </details>
    <details data-scenario-family-group="other-family"><summary>Other family</summary>
      <button type="button" data-scenario-card data-scenario-id="other-exact"
        data-scenario-family="other-family" data-maintenance="hidden-by-default"
        data-search="other exact" data-facet-implementation="cardano-node"
        data-facet-execution="live" data-facet-goal="recovery"
        aria-selected="false">Other exact</button>
    </details>
  </section>
  <script type="module" src="/scenario-discovery.js"></script>
</body>`;

const subjectDetails = Array.from({length: 8}, (_, index) => `
    <details class="scenario-discovery__subject" data-scenario-disclosure="subject:${index === 0 ? 'cbor' : `subject-${index}`}">
      <summary><span>${index === 0 ? 'CBOR' : `Subject ${index + 1}`}</span><small>${index === 0 ? 43 : 12} exact variants</small></summary>
      ${index === 0 ? `<section data-scenario-subject-child="cbor:tx-body"><h3>TX Body</h3>
        <details class="scenario-discovery__family" data-scenario-disclosure="family:cbor:tx-body:decoder"><summary><span>Transaction body parser</span><small>1 exact variant</small></summary>
          <article data-scenario-card data-scenario-id="tx-body-exact" data-maintenance="shown"
            data-search="tx body parser cbor" data-facet-implementation="amaru" data-facet-goal="malformed-input"
            data-facet-threat-model="TM-001" data-facet-risk-register="RR-001">TX Body exact</article>
        </details>
      </section>` : ''}
    </details>`).join('');

const subjectCatalog = `<!doctype html><head>
  <link rel="stylesheet" href="/tokens.css"><link rel="stylesheet" href="/base.css">
</head><body>
  <section data-scenario-discovery data-discovery-scope="subject-contract">
    <input data-discovery-query><input type="checkbox" data-discovery-show-maintenance>
    <button data-facet-group="implementation" data-facet-value="amaru">Amaru</button>
    <button data-facet-group="goal" data-facet-value="malformed-input">Malformed input</button>
    <input data-facet-option-search="threat_model">
    <div><button data-facet-group="threat_model" data-facet-value="TM-001" data-facet-label="tm-001 malformed parser input">TM-001</button><button data-facet-group="threat_model" data-facet-value="unmapped" data-facet-label="unmapped">Unmapped</button></div>
    <input data-facet-option-search="risk_register">
    <div><button data-facet-group="risk_register" data-facet-value="RR-001" data-facet-label="rr-001 parser rejection">RR-001</button><button data-facet-group="risk_register" data-facet-value="unmapped" data-facet-label="unmapped">Unmapped</button></div>
    <button type="button" data-discovery-clear>Clear</button><p data-discovery-count></p>
    <div class="scenario-discovery__families">${subjectDetails}
    <details class="scenario-discovery__subject" data-scenario-disclosure="subject:n2n"><summary><span>N2N</span><small>1 exact variant</small></summary>
      <details data-scenario-disclosure="family:n2n:general:protocol"><summary>Protocol</summary>
        <article data-scenario-card data-scenario-id="protocol-exact" data-maintenance="shown"
          data-search="n2n protocol" data-facet-implementation="cardano-node" data-facet-goal="peer-isolation"
          data-facet-threat-model="unmapped" data-facet-risk-register="unmapped">Protocol exact</article>
      </details>
    </details></div>
  </section>
  <script type="module" src="/scenario-discovery.js"></script>
</body>`;

const subjectRun = `<!doctype html><head>
  <link rel="stylesheet" href="/tokens.css"><link rel="stylesheet" href="/base.css">
</head><body><section class="scenario-picker" data-scenario-discovery data-discovery-scope="subject-run">
  ${subjectDetails}
</section><script type="module" src="/scenario-discovery.js"></script></body>`;

const themeDiscovery = `<!doctype html><head>${fullStyleLinks}</head><body data-ui="basic"><main class="shell-main"><section data-scenario-discovery data-basic-reducer="skip">
  <div class="scenario-discovery__controls" data-discovery-controls>
    <label for="theme-search">Find a scenario</label><input id="theme-search" type="search" data-discovery-query placeholder="Search scenarios">
    <label class="scenario-discovery__maintenance"><input type="checkbox" data-discovery-show-maintenance> Show health and smoke checks</label>
    <fieldset class="scenario-discovery__facet"><legend>Implementation</legend>
      <button type="button" data-facet-clear="implementation" aria-pressed="true">All</button>
      <button type="button" data-facet-group="implementation" data-facet-value="amaru" aria-pressed="false">Amaru <span>114</span></button>
    </fieldset>
    <fieldset class="scenario-discovery__facet scenario-discovery__facet--searchable"><legend>Threat Model</legend>
      <details class="scenario-discovery__facet-options"><summary>Choose Threat Model entries</summary><div><button type="button">TM-001</button></div></details>
    </fieldset>
    <details class="scenario-discovery__more"><summary>More filters</summary></details>
    <p class="scenario-discovery__count" data-discovery-count>187 families · 351 exact variants</p>
  </div>
  <div class="scenario-discovery__families">
    <details class="scenario-discovery__subject" open><summary><span>CBOR</span><small>43 exact variants</small></summary>
      <details class="scenario-discovery__family" open><summary><span>Structured CBOR fuzz</span><small>8 exact variants</small></summary>
        <article class="scenario-picker__option scenario-picker__option--amaru" data-scenario-id="cbor-one" aria-selected="false">
          <strong class="scenario-picker__title">Structured CBOR parser validation</strong>
          <span class="scenario-picker__purpose">Exercises malformed and well-formed parser input.</span>
          <span class="scenario-picker__check">Checks clean acceptance or rejection.</span>
          <span class="scenario-picker__badges"><span class="scenario-picker__badge scenario-picker__badge--implementation">Standalone library</span><span class="scenario-picker__badge scenario-picker__badge--recommended">Recommended</span></span>
          <code>cbor-one</code>
        </article>
      </details>
    </details>
  </div>
</section></main><script src="/basic-reduce.js"></script></body>`;

async function assertCompactSubjectLayout(page, url, viewport) {
  await page.setViewportSize(viewport);
  await page.goto(url);
  const root = page.locator('.scenario-picker, .scenario-discovery__families').first();
  const subject = root.locator(':scope > .scenario-discovery__subject').first();
  const summary = subject.locator(':scope > summary');
  const metrics = await summary.evaluate((node) => {
    const title = node.querySelector('span');
    const count = node.querySelector('small');
    const details = node.parentElement;
    const nr = node.getBoundingClientRect();
    const tr = title.getBoundingClientRect();
    const cr = count.getBoundingClientRect();
    const ns = getComputedStyle(node);
    const ds = getComputedStyle(details);
    const ts = getComputedStyle(title);
    const marker = getComputedStyle(node, '::before');
    return {
      summaryHeight: nr.height,
      marginTop: parseFloat(ds.marginTop),
      marginBottom: parseFloat(ds.marginBottom),
      titleSize: parseFloat(ts.fontSize),
      countGap: cr.left - tr.right,
      marker: marker.content,
      justify: ns.justifyContent,
    };
  });
  assert.ok(metrics.summaryHeight >= 44 && metrics.summaryHeight <= 48, `compact accessible summary: ${JSON.stringify(metrics)}`);
  assert.ok(metrics.marginTop <= 6 && metrics.marginBottom <= 6, `normal closed-row spacing: ${JSON.stringify(metrics)}`);
  assert.ok(metrics.titleSize <= 14, `modest title size: ${JSON.stringify(metrics)}`);
  assert.ok(metrics.countGap >= 0 && metrics.countGap <= 24, `count remains adjacent: ${JSON.stringify(metrics)}`);
  assert.notEqual(metrics.marker, 'none', `disclosure indicator is visible: ${JSON.stringify(metrics)}`);
  assert.equal(metrics.justify, 'flex-start', `summary content does not stretch: ${JSON.stringify(metrics)}`);
  await summary.focus();
  assert.notEqual(await summary.evaluate((node) => getComputedStyle(node).outlineStyle), 'none', 'keyboard focus is visible');
}

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.route('http://dwarf.test/**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/scenario-discovery.js') {
        await route.fulfill({status: 200, contentType: 'text/javascript', body: controller});
      } else if (url.pathname === '/basic-reduce.js') {
        await route.fulfill({status: 200, contentType: 'text/javascript', body: basicReducer});
      } else if (styleSheets[url.pathname]) {
        await route.fulfill({status: 200, contentType: 'text/css', body: styleSheets[url.pathname]});
      } else if (url.pathname === '/operate/scenarios') {
        await route.fulfill({status: 200, contentType: 'text/html', body: catalog});
      } else if (url.pathname === '/run') {
        const body = url.searchParams.get('scenario') === 'maintenance-exact'
          ? selectedRun
          : `<p id="selected">${url.searchParams.get('scenario')}</p>`;
        await route.fulfill({status: 200, contentType: 'text/html', body});
      } else if (url.pathname === '/subject-catalog') {
        await route.fulfill({status: 200, contentType: 'text/html', body: subjectCatalog});
      } else if (url.pathname === '/subject-run') {
        await route.fulfill({status: 200, contentType: 'text/html', body: subjectRun});
      } else if (url.pathname === '/theme-discovery') {
        await route.fulfill({status: 200, contentType: 'text/html', body: themeDiscovery});
      } else {
        await route.fulfill({status: 404, body: 'not found'});
      }
    });
    await page.goto('http://dwarf.test/operate/scenarios');
    await page.waitForFunction(() => document.querySelector('[data-scenario-card]')?.dataset.discoveryMatch);
    assert.equal(await page.locator('details[open]').count(), 0, 'families start compact');
    assert.equal(await page.locator('[data-scenario-card]:not([hidden])').count(), 0, 'collapsed variants leave keyboard order');

    await page.locator('[data-facet-group="implementation"][data-facet-value="amaru"]').click();
    await page.locator('[data-facet-group="goal"][data-facet-value="recovery"]').click();
    assert.equal(await page.locator('[data-scenario-family-group="family-one"]').isHidden(), false);
    assert.equal(await page.locator('[data-scenario-family-group="family-two"]').isHidden(), true);
    assert.equal(
      await page.locator('[data-discovery-count]').textContent(),
      '1 families · 1 of 2 exact variants',
      'implementation and security-goal facets intersect',
    );
    await page.locator('[data-discovery-clear]').click();

    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      await assertCompactSubjectLayout(page, 'http://dwarf.test/subject-run', viewport);
      await assertCompactSubjectLayout(page, 'http://dwarf.test/subject-catalog', viewport);
      await page.goto('http://dwarf.test/theme-discovery');
      const themed = await page.locator('[data-discovery-controls]').evaluate((node) => {
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
        const input = node.querySelector('[data-discovery-query]');
        const selected = node.querySelector('[data-facet-clear]');
        const unselected = node.querySelector('[data-facet-group]');
        const count = node.querySelector('[data-discovery-count]');
        const more = node.querySelector('.scenario-discovery__more > summary');
        const subject = document.querySelector('.scenario-discovery__subject > summary');
        const family = document.querySelector('.scenario-discovery__family > summary');
        const title = document.querySelector('.scenario-picker__title');
        const purpose = document.querySelector('.scenario-picker__purpose');
        const implementation = document.querySelector('.scenario-picker__badge--implementation');
        const recommended = document.querySelector('.scenario-picker__badge--recommended');
        const card = document.querySelector('.scenario-picker__option--amaru');
        const code = document.querySelector('.scenario-picker__option code');
        const checkbox = node.querySelector('[data-discovery-show-maintenance]');
        const inputStyle = getComputedStyle(input);
        const placeholderStyle = getComputedStyle(input, '::placeholder');
        const expected = document.createElement('i');
        expected.style.cssText = 'position:absolute;color:var(--dk-gold);background:var(--dk-face);border-color:var(--dk-line2);outline-color:var(--dk-impl-amaru)';
        document.body.append(expected);
        const expectedStyle = getComputedStyle(expected);
        const result = {
          noOverflow: node.scrollWidth <= node.clientWidth,
          inputBackground: inputStyle.backgroundColor,
          expectedInputBackground: getComputedStyle(document.body).getPropertyValue('--dk-table').trim(),
          inputBorder: inputStyle.borderColor,
          expectedBorder: expectedStyle.borderColor,
          selectedBackground: getComputedStyle(selected).backgroundColor,
          expectedGold: expectedStyle.color,
          unselectedBackground: getComputedStyle(unselected).backgroundColor,
          expectedFace: expectedStyle.backgroundColor,
          countColor: getComputedStyle(count).color,
          expectedMuted: getComputedStyle(document.body).getPropertyValue('--dk-muted').trim(),
          moreColor: getComputedStyle(more).color,
          inputFont: inputStyle.fontFamily,
          inputSize: parseFloat(inputStyle.fontSize),
          placeholderContrast: contrast(placeholderStyle.color, inputStyle.backgroundColor),
          subjectFont: getComputedStyle(subject).fontFamily,
          subjectSize: parseFloat(getComputedStyle(subject).fontSize),
          subjectHeight: subject.getBoundingClientRect().height,
          familyFont: getComputedStyle(family).fontFamily,
          familySize: parseFloat(getComputedStyle(family).fontSize),
          familyHeight: family.getBoundingClientRect().height,
          titleFont: getComputedStyle(title).fontFamily,
          titleSize: parseFloat(getComputedStyle(title).fontSize),
          purposeFont: getComputedStyle(purpose).fontFamily,
          purposeSize: parseFloat(getComputedStyle(purpose).fontSize),
          badgeFont: getComputedStyle(implementation).fontFamily,
          badgeSize: parseFloat(getComputedStyle(implementation).fontSize),
          codeFont: getComputedStyle(code).fontFamily,
          codeSize: parseFloat(getComputedStyle(code).fontSize),
          codeColor: getComputedStyle(code).color,
          checkboxAccent: getComputedStyle(checkbox).accentColor,
          checkboxWidth: parseFloat(getComputedStyle(checkbox).width),
          checkboxHeight: parseFloat(getComputedStyle(checkbox).height),
          implementationColor: getComputedStyle(implementation).color,
          implementationBorder: getComputedStyle(card).borderLeftColor,
          expectedImplementation: expectedStyle.outlineColor,
          recommendedColor: getComputedStyle(recommended).color,
        };
        expected.remove();
        return result;
      });
      assert.equal(themed.noOverflow, true, 'Basic discovery controls do not overflow');
      assert.equal(themed.inputBorder, themed.expectedBorder, 'search uses the Basic line token');
      assert.equal(themed.selectedBackground, themed.expectedGold, 'selected facet uses Basic gold');
      assert.equal(themed.unselectedBackground, themed.expectedFace, 'unselected facet uses a Basic face');
      assert.equal(themed.moreColor, themed.expectedGold, 'More filters uses Basic gold');
      for (const [role, family] of Object.entries({input: themed.inputFont, subject: themed.subjectFont, family: themed.familyFont, title: themed.titleFont, purpose: themed.purposeFont, badge: themed.badgeFont})) {
        assert.match(family, /IBM Plex Sans/, `${role} uses the single interface font`);
      }
      assert.match(themed.codeFont, /Mono|monospace/, 'technical IDs retain monospace');
      assert.equal(themed.inputSize, 16, 'search uses the 16px form role');
      assert.equal(themed.subjectSize, 16, 'subject summary uses the 16px interface role');
      assert.equal(themed.familySize, 14, 'family summary uses the 14px secondary role');
      assert.equal(themed.titleSize, 16, 'scenario title uses the 16px interface role');
      assert.equal(themed.purposeSize, 14, 'scenario purpose uses the 14px secondary role');
      assert.equal(themed.badgeSize, 12, 'scenario badges use the 12px compact role');
      assert.equal(themed.codeSize, 12, 'technical IDs use the 12px compact role');
      assert.ok(themed.subjectHeight >= 44 && themed.familyHeight >= 44, 'subject and family disclosures retain 44px targets');
      assert.ok(themed.placeholderContrast >= 4.5, `placeholder remains readable (${themed.placeholderContrast})`);
      assert.equal(themed.checkboxAccent, themed.expectedGold, 'discovery checkboxes use Basic gold');
      assert.ok(themed.checkboxWidth >= 18 && themed.checkboxHeight >= 18, 'discovery checkbox remains legible');
      assert.equal(themed.codeColor, themed.expectedGold, 'technical IDs use Basic gold rather than legacy cyan');
      assert.equal(themed.implementationColor, themed.expectedImplementation, 'implementation badge uses a Basic semantic token');
      assert.equal(themed.implementationBorder, themed.expectedImplementation, 'implementation border uses the same semantic token');
      assert.equal(themed.recommendedColor, themed.expectedGold, 'recommended badge uses Basic gold');
    }

    await page.goto('http://dwarf.test/subject-catalog');
    await page.waitForFunction(() => document.querySelector('[data-scenario-card]')?.dataset.discoveryMatch);
    assert.equal(await page.locator('[data-scenario-disclosure][open]').count(), 0, 'all subjects and families start collapsed');
    await page.locator('[data-discovery-query]').fill('tx body');
    assert.equal(await page.locator('[data-scenario-disclosure="subject:cbor"]').getAttribute('open'), '');
    assert.equal(await page.locator('[data-scenario-disclosure^="family:cbor"]').getAttribute('open'), '');
    assert.equal(await page.locator('[data-scenario-id="tx-body-exact"]').isHidden(), false);
    await page.locator('[data-scenario-disclosure="subject:cbor"] > summary').click();
    assert.equal(await page.locator('[data-scenario-disclosure="subject:cbor"]').getAttribute('open'), null, 'a searched subject can be manually collapsed');
    await page.waitForFunction(() => document.querySelector('[data-scenario-discovery]')._scenarioDiscoveryController.state.closedFamilies.includes('subject:cbor'));
    await page.evaluate(() => document.querySelector('[data-scenario-discovery]')._scenarioDiscoveryController.refresh());
    assert.equal(await page.locator('[data-scenario-disclosure="subject:cbor"]').getAttribute('open'), null, 'manual collapse wins over search intent');
    await page.locator('[data-discovery-clear]').click();
    await page.locator('[data-facet-option-search="threat_model"]').fill('TM-001');
    assert.equal(await page.locator('[data-facet-group="threat_model"][data-facet-value="TM-001"]').isHidden(), false);
    assert.equal(await page.locator('[data-facet-group="threat_model"][data-facet-value="unmapped"]').isHidden(), true, 'option search narrows choices without selecting them');
    await page.locator('[data-facet-group="implementation"][data-facet-value="amaru"]').click();
    await page.locator('[data-facet-group="goal"][data-facet-value="malformed-input"]').click();
    await page.locator('[data-facet-group="threat_model"][data-facet-value="TM-001"]').click();
    await page.locator('[data-facet-group="risk_register"][data-facet-value="RR-001"]').click();
    assert.equal(await page.locator('[data-discovery-count]').textContent(), '1 families · 1 of 2 exact variants', 'TM/RR combine with implementation and security goal');
    assert.equal(await page.locator('[data-scenario-disclosure="subject:n2n"]').getAttribute('hidden'), '');
    await page.locator('[data-discovery-clear]').click();
    assert.equal(await page.locator('[data-facet-option-search="threat_model"]').inputValue(), '', 'Clear resets option search');
    assert.equal(await page.locator('[data-facet-group="threat_model"][data-facet-value="unmapped"]').isHidden(), false, 'Clear restores narrowed options');

    await page.goto('http://dwarf.test/operate/scenarios');
    await page.waitForFunction(() => document.querySelector('[data-scenario-card]')?.dataset.discoveryMatch);

    await page.locator('[data-discovery-query]').fill('exact-two');
    assert.equal(await page.locator('[data-scenario-family-group="family-two"]').getAttribute('open'), '');
    assert.equal(await page.locator('[data-scenario-family-group="family-one"]').isHidden(), true);
    assert.equal(await page.locator('[data-scenario-family-group="family-two"] [data-scenario-card]').isHidden(), false);

    await page.locator('[data-discovery-query]').fill('');
    assert.equal(
      await page.locator('[data-scenario-family-group="family-two"]').getAttribute('open'),
      null,
      'removing an auto-open search returns the family to its compact default',
    );
    await page.locator('[data-discovery-query]').fill('exact-two');

    const searchedFamily = page.locator('[data-scenario-family-group="family-two"]');
    await searchedFamily.locator('summary').click();
    assert.equal(await searchedFamily.getAttribute('open'), null, 'a search result can be manually collapsed');
    assert.equal(
      await searchedFamily.locator('[data-scenario-card]').isHidden(),
      true,
      'collapsed search results leave keyboard order',
    );
    await page.waitForFunction(() => (
      document.querySelector('[data-scenario-discovery]')
        ._scenarioDiscoveryController.state.closedFamilies.includes('family-two')
    ));
    await page.evaluate(() => document.querySelector('[data-scenario-discovery]')._scenarioDiscoveryController.refresh());
    assert.equal(await searchedFamily.getAttribute('open'), null, 'filter renders respect manual collapse');
    await searchedFamily.locator('summary').click();
    assert.equal(await searchedFamily.getAttribute('open'), '', 'a collapsed search result can be reopened');

    await page.getByRole('link', {name: 'Exact two'}).click();
    await page.waitForURL('**/run?scenario=exact-two');
    assert.equal(await page.locator('#selected').textContent(), 'exact-two', 'exact variant is explicit in navigation');
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('[data-discovery-query]')?.value === 'exact-two');
    assert.equal(await page.locator('[data-scenario-family-group="family-two"]').getAttribute('open'), '', 'Back restores discovery state');

    await page.locator('[data-discovery-clear]').click();
    assert.equal(await page.locator('[data-discovery-query]').inputValue(), '');
    assert.equal(await page.locator('details[open]').count(), 0, 'Clear collapses unselected families');
    await page.locator('[data-discovery-query]').fill('exact-two');
    assert.equal(
      await page.locator('[data-scenario-family-group="family-two"]').getAttribute('open'),
      '',
      'Clear does not manufacture a manual-close state that blocks later search',
    );
    await page.locator('[data-discovery-clear]').click();

    await page.evaluate(() => {
      sessionStorage.clear();
      sessionStorage.setItem('dwarf:scenario-discovery:run', JSON.stringify({
        query: 'incompatible saved query',
        showMaintenance: false,
        facets: {implementation: ['cardano-node']},
        openFamilies: [],
      }));
    });
    await page.goto('http://dwarf.test/run?scenario=maintenance-exact');
    await page.waitForFunction(() => document.querySelector('[data-scenario-card]')?.dataset.discoveryMatch);
    const selectedFamily = page.locator('[data-scenario-family-group="selected-family"]');
    const selectedCard = page.locator('[data-scenario-id="maintenance-exact"]');
    assert.equal(
      await page.locator('[data-discovery-show-maintenance]').isChecked(),
      false,
      'a selected maintenance variant stays visible without globally revealing maintenance checks',
    );
    assert.equal(await selectedFamily.isHidden(), false, 'saved filters never hide the selected family');
    assert.equal(await selectedFamily.getAttribute('open'), null, 'a fresh mount does not open the selected family');
    assert.equal(await selectedCard.isHidden(), true, 'a collapsed selected variant leaves keyboard order');
    assert.equal(
      await page.locator('[data-discovery-count]').textContent(),
      '0 families · 0 of 2 exact variants',
      'the selected-state exception is not counted as a filter match',
    );
    assert.equal(await page.locator('#run-scenario').inputValue(), 'maintenance-exact');
    assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'frozen-plan-digest');

    await selectedFamily.locator('summary').click();
    assert.equal(await selectedFamily.getAttribute('open'), '', 'the selected family can be explicitly opened');
    await page.waitForFunction(() => !document.querySelector('[data-scenario-id="maintenance-exact"]').hidden);
    assert.equal(await selectedCard.isHidden(), false, 'an explicitly opened selected variant is reachable');
    assert.equal(await page.locator('#run-scenario').inputValue(), 'maintenance-exact');
    assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'frozen-plan-digest');
    await page.waitForFunction(() => (
      document.querySelector('[data-scenario-discovery]')
        ._scenarioDiscoveryController.state.openFamilies.includes('selected-family')
    ));
    await page.evaluate(() => document.querySelector('[data-scenario-discovery]')._scenarioDiscoveryController.refresh());
    assert.equal(await selectedFamily.getAttribute('open'), '', 'selection refresh respects an explicit open');
    await page.goto('http://dwarf.test/operate/scenarios');
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('[data-scenario-card]')?.dataset.discoveryMatch);
    assert.equal(await selectedFamily.getAttribute('open'), '', 'Back restores the explicit open state');
    assert.equal(await page.locator('#run-scenario').inputValue(), 'maintenance-exact');
    assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'frozen-plan-digest');

    await page.locator('[data-discovery-clear]').click();
    assert.equal(await selectedFamily.isHidden(), false, 'Clear preserves the selected family');
    assert.equal(await selectedFamily.getAttribute('open'), null, 'Clear returns the selected family to its compact default');
    assert.equal(await selectedCard.isHidden(), true, 'Clear keeps collapsed variants out of keyboard order');
    assert.equal(await page.locator('#run-scenario').inputValue(), 'maintenance-exact');
    assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'frozen-plan-digest');

    await page.evaluate(() => {
      const root = document.querySelector('[data-scenario-discovery]');
      root.querySelector('[data-scenario-id="maintenance-exact"]').setAttribute('aria-selected', 'false');
      root.querySelector('[data-scenario-id="other-exact"]').setAttribute('aria-selected', 'true');
      document.querySelector('#run-scenario').value = 'other-exact';
      document.querySelector('[data-basic-plan="digest"]').textContent = 'other-frozen-digest';
      root._scenarioDiscoveryController.state.query = 'matches neither variant';
      root._scenarioDiscoveryController.state.facets.implementation = ['amaru'];
      root._scenarioDiscoveryController.state.showMaintenance = false;
      root._scenarioDiscoveryController.refresh();
    });
    const otherFamily = page.locator('[data-scenario-family-group="other-family"]');
    const otherCard = page.locator('[data-scenario-id="other-exact"]');
    assert.equal(await otherFamily.isHidden(), false, 'the current selection replaces the initial exception');
    assert.equal(await otherFamily.getAttribute('open'), null, 'selection alone does not open another family');
    assert.equal(await otherCard.isHidden(), true, 'collapsed selected maintenance remains outside keyboard order');
    assert.equal(await selectedFamily.isHidden(), true, 'the initial selection loses its filter exception');
    assert.equal(await page.locator('#run-scenario').inputValue(), 'other-exact');
    assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'other-frozen-digest');

    await page.locator('[data-discovery-clear]').click();
    assert.equal(await otherFamily.isHidden(), false, 'Clear preserves the current selected family');
    assert.equal(await otherFamily.getAttribute('open'), null, 'Clear leaves the current selected disclosure compact');
    assert.equal(await otherCard.isHidden(), true, 'Clear leaves collapsed variants outside keyboard order');
    assert.equal(await selectedFamily.isHidden(), true, 'Clear does not restore the former selection exception');
    assert.equal(await page.locator('#run-scenario').inputValue(), 'other-exact');
    assert.equal(await page.locator('[data-basic-plan="digest"]').textContent(), 'other-frozen-digest');

  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
