const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let browserLibrary = 'playwright';
let chromium;
try {
  ({chromium} = require('playwright'));
} catch (_error) {
  browserLibrary = 'playwright-core';
  ({chromium} = require('playwright-core'));
}

const root = path.resolve(__dirname, '../..');
const styles = [
  'dwarf/dashboard/static/css/plex.css',
  'dwarf/dashboard/static/css/tokens.css',
  'dwarf/dashboard/static/css/themes.css',
  'dwarf/dashboard/static/css/base.css',
  'dwarf/dashboard/static/css/bento-noir.css',
  'dwarf/dashboard/static/css/basic.css',
  'dwarf/dashboard/static/css/deck.css',
].map((file) => path.join(root, file));

const html = `<!doctype html><html><body data-ui="basic"><main class="shell-main run-results-basic">
  <h1 class="run-id-heading"><code>run-cleanup</code></h1>
  <section class="basic-only dk" aria-label="Run card">
    <p class="visually-hidden" data-run-verdict-summary>Test verdict: pass. Operational outcome: pass. Assertions: 1 of 1 passed. Bundle integrity: verified.</p>
    <div class="dk-runlay"><article class="dk-card" role="button" aria-label="Run card for run-cleanup. Flip for manifest, hash chain and commands"><span>PASS</span></article><div class="run-results__summary">
      <p>Completed against Amaru.</p>
      <p><strong>2 metrics measured</strong></p>
      <div class="dk-row run-results__actions">
        <a class="dk-btn dk-btn--solid" href="/bundle">Export bundle</a>
        <button class="dk-btn" type="button" data-run-details-trigger data-run-details-target="run-manifest">Run details</button>
      </div>
    </div></div>
  </section>
  <details class="run-disclosure" id="run-manifest" data-run-section="manifest"><summary class="run-disclosure__summary">Bundle manifest</summary><div class="run-disclosure__body"><p>seed 7</p></div></details>
  <details class="run-disclosure evidence-disclosure" data-run-section="export"><summary class="run-disclosure__summary"><span><span class="eyebrow">Export</span><span class="run-disclosure__title">SARIF export</span></span><span class="run-disclosure__meta">Available</span></summary><div class="run-disclosure__body"><a href="/sarif">Download SARIF</a></div></details>
  <details class="run-disclosure evidence-disclosure" data-run-section="execution-definition"><summary class="run-disclosure__summary"><span><span class="eyebrow">Scenario snapshot</span><span class="run-disclosure__title">Execution definition</span></span><span class="run-disclosure__meta">FAIL · 1 gap</span></summary><div class="run-disclosure__body">
    <table class="config-table run-execution-table"><thead><tr><th>Phase</th><th>Count</th><th>Primitives</th></tr></thead><tbody><tr><th scope="row">assert</th><td>1</td><td><ul class="run-probe-list"><li><code class="run-evidence-identity" title="amaru_measurement_boundary_proven">amaru_measurement_boundary_proven</code></li></ul></td></tr></tbody></table>
  </div></details>
  <section class="measurement-report" data-measurement-report>
    <h2 class="dk-mrep__title">Measurements</h2>
    <div data-mviz><a href="#measurement-report-heading" data-mviz-cat="workload">Workload card</a><div data-mviz-detail hidden><h4 data-mviz-detail-title></h4><div data-mviz-detail-value></div><div data-mviz-detail-body></div></div><p data-mviz-detail-empty>Choose a metric</p></div>
    <label>Search measurements<input type="search" data-measurement-search></label>
    <div class="measurement-controls__categories"><button class="measurement-filter" data-measurement-category="all" aria-pressed="true">All</button><button class="measurement-filter" data-measurement-category="workload" aria-pressed="false">Workload and outcomes <span>7</span></button><button class="measurement-filter" data-measurement-category="latency" aria-pressed="false">Latency and processing time <span>9</span></button><button class="measurement-filter" data-measurement-category="throughput" aria-pressed="false">Throughput and chain progress <span>2</span></button><button class="measurement-filter" data-measurement-category="resources" aria-pressed="false">CPU, memory, disk, and network <span>5</span></button><button class="measurement-filter" data-measurement-category="protocol" aria-pressed="false">Protocol and ledger activity <span>10</span></button></div>
    <div class="measurement-groups"><details class="measurement-group" data-measurement-group="workload">
      <summary class="measurement-group__summary"><h3>Workload and outcomes</h3></summary>
      <div class="measurement-card-grid"><article class="measurement-card dk-mcard" data-measurement-card data-measurement-category-id="workload" data-measurement-search-text="rate">9.4</article></div>
    </details></div>
  </section>
  <details class="run-disclosure evidence-disclosure run-actions" data-run-section="actions"><summary class="run-disclosure__summary"><span><span class="eyebrow">Actions</span><span class="run-disclosure__title">Operator actions</span></span><span class="run-disclosure__meta">4 bundle workflows</span></summary><div class="run-disclosure__body">${['Replay this run','Diff against another bundle','Export SARIF','Verify chain to genesis'].map((label, index) => `<article class="run-action"><header class="run-action__head"><span class="eyebrow">${label}</span><code class="run-action__primitive">runtime_action_${index}</code></header><p class="run-action__summary">Retained command and input ${index}</p><pre class="cli-example__pre">cardano-profile action --run run-cleanup</pre></article>`).join('')}</div></details>
  <details class="run-disclosure evidence-disclosure floor-preview" data-run-section="floor-preview"><summary class="run-disclosure__summary"><span><span class="eyebrow">Floor preview</span><span class="run-disclosure__title">Recorded vs floor-preview</span></span><span class="run-disclosure__meta">0 gaps · 1 match</span></summary><div class="run-disclosure__body"><table class="config-table floor-preview__table run-evidence-table"><thead><tr><th>Primitive</th><th>Recorded</th><th>Floor preview</th><th>Kind</th></tr></thead><tbody><tr class="floor-preview__row"><td><code class="run-evidence-identity" title="amaru_measurement_boundary_proven">amaru_measurement_boundary_proven</code></td><td>PASS</td><td>PASS</td><td>match</td></tr><tr class="run-evidence-detail-row"><td colspan="4"><details class="run-payload-disclosure" data-evidence-payload="floor-preview"><summary>Why / evaluated value</summary><pre class="run-payload-pre"><code>{"expected_mode":"stock","observed":{"accepted":400,"unsupported":400,"malformed":400},"digest":"${'a'.repeat(128)}"}</code></pre></details></td></tr></tbody></table></div></details>
  <details class="run-disclosure evidence-disclosure" data-run-section="assertions"><summary class="run-disclosure__summary"><span><span class="eyebrow">Assertions</span><span class="run-disclosure__title">Recorded assertions</span></span><span class="run-disclosure__meta">1 assertion</span></summary><div class="run-disclosure__body"><table class="config-table run-assertions-table run-evidence-table"><thead><tr><th>Primitive</th><th>Params</th><th>Result</th></tr></thead><tbody><tr><td><code class="run-evidence-identity" title="amaru_measurement_boundary_proven">amaru_measurement_boundary_proven</code></td><td><code>{"expected_mode":"stock"}</code></td><td>pass</td></tr><tr class="run-evidence-detail-row"><td colspan="3"><details class="run-payload-disclosure" data-evidence-payload="assertion"><summary>Evaluated payload (512 chars)</summary><pre class="run-payload-pre"><code>{"accepted":400,"unsupported_version":400,"malformed":400,"payload":"${'b'.repeat(512)}"}</code></pre></details></td></tr></tbody></table></div></details>
  <details class="run-disclosure evidence-disclosure" data-run-section="log-tail"><summary class="run-disclosure__summary"><span><span class="eyebrow">Log tail</span><span class="run-disclosure__title">Last log event</span></span><span class="run-disclosure__meta">Most recent events</span></summary><div class="run-disclosure__body"><table class="config-table run-log-table run-evidence-table"><thead><tr><th>ts</th><th>phase</th><th>primitive</th><th>event</th></tr></thead><tbody><tr><td><code>2026-10-13T23:22:30Z</code></td><td>assert</td><td><code class="run-evidence-identity" title="amaru_measurement_boundary_proven">amaru_measurement_boundary_proven</code></td><td>assertion_evaluated</td></tr><tr class="run-evidence-detail-row"><td colspan="4"><details class="run-payload-disclosure" data-evidence-payload="log"><summary>Payload (384 chars)</summary><pre class="run-payload-pre"><code>{"event":"assertion_evaluated","payload":"${'c'.repeat(384)}"}</code></pre></details></td></tr></tbody></table></div></details>
  <details class="operator-version-disclosure" data-operator-version-decision><summary>Operator support decision</summary><form><label>Decision<select><option>Supported</option></select></label><label>Reason<textarea></textarea></label><button>Record operator decision</button></form></details>
</main></body></html>`;

(async () => {
  const browser = await chromium.launch({headless: true, ...(browserLibrary === 'playwright-core' ? {channel: 'chrome'} : {})});
  try {
    // 600 CSS px exercises the 1200px desktop layout at 200% zoom/reflow.
    for (const viewport of [{width: 1440, height: 900}, {width: 1200, height: 800}, {width: 600, height: 800}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      await page.setContent(html);
      for (const style of styles) await page.addStyleTag({path: style});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/deck.js')});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/measurement-report.js'), type: 'module'});

      const trigger = page.locator('[data-run-details-trigger]');
      const verdictSummary = page.locator('[data-run-verdict-summary]');
      assert.match(await verdictSummary.textContent(), /Test verdict: pass.*Operational outcome: pass.*Bundle integrity: verified/);
      assert.equal(await verdictSummary.evaluate((node) => getComputedStyle(node).position), 'absolute');
      assert.equal(await page.locator('.dk-card[role="button"]').getAttribute('aria-label'), 'Run card for run-cleanup. Flip for manifest, hash chain and commands');
      const manifest = page.locator('#run-manifest');
      assert.equal(await manifest.evaluate((node) => node.open), false);
      await trigger.focus();
      await page.keyboard.press('Enter');
      assert.equal(await manifest.evaluate((node) => node.open), true);

      const measurementGroup = page.locator('[data-measurement-group="workload"]');
      assert.equal(await measurementGroup.evaluate((node) => node.open), false);
      await page.locator('[data-measurement-search]').fill('rate');
      assert.equal(await measurementGroup.evaluate((node) => node.open), true);
      await page.locator('[data-measurement-search]').fill('');
      await measurementGroup.evaluate((node) => { node.open = false; });
      await page.locator('[data-mviz-cat="workload"]').click();
      assert.equal(await measurementGroup.evaluate((node) => node.open), true);

      const evidence = page.locator('[data-run-section="execution-definition"]');
      const evidenceSummary = evidence.locator('summary');
      assert.equal(await evidence.evaluate((node) => node.open), false);
      await evidenceSummary.focus();
      await page.keyboard.press('Enter');
      assert.equal(await evidence.evaluate((node) => node.open), true);
      assert.match(await evidenceSummary.textContent(), /Scenario snapshot.*Execution definition.*FAIL.*1 gap/s);

      const payloads = page.locator('[data-evidence-payload]');
      assert.equal(await payloads.count(), 3);
      for (let index = 0; index < await payloads.count(); index += 1) {
        const payload = payloads.nth(index);
        await payload.evaluate((node) => { node.closest('.evidence-disclosure').open = true; });
        await payload.locator('summary').focus();
        await page.keyboard.press('Enter');
        assert.equal(await payload.evaluate((node) => node.open), true);
      }

      const layout = await page.evaluate(() => {
        const title = document.querySelector('.measurement-group__summary h3').getBoundingClientRect();
        const group = document.querySelector('.measurement-group').getBoundingClientRect();
        const card = document.querySelector('.dk-runlay > .dk-card').getBoundingClientRect();
        const summary = document.querySelector('.run-results__summary').getBoundingClientRect();
        const runlay = document.querySelector('.dk-runlay').getBoundingClientRect();
        const shell = document.querySelector('.shell-main').getBoundingClientRect();
        const evidenceSummary = document.querySelector('[data-run-section="execution-definition"] > .run-disclosure__summary');
        const measurementSummary = document.querySelector('.measurement-group__summary');
        const evidenceTitle = evidenceSummary.querySelector('.run-disclosure__title').getBoundingClientRect();
        const evidenceBounds = evidenceSummary.getBoundingClientRect();
        const measurementSummaryStyle = getComputedStyle(measurementSummary);
        const evidenceSummaryStyle = getComputedStyle(evidenceSummary);
        const disclosureBody = document.querySelector('[data-run-section="execution-definition"] .run-disclosure__body');
        const disclosureBounds = disclosureBody.getBoundingClientRect();
        const table = disclosureBody.querySelector('.config-table').getBoundingClientRect();
        const bodyStyle = getComputedStyle(disclosureBody);
        const probe = document.createElement('span');
        probe.style.color = 'var(--dk-gold)';
        document.body.append(probe);
        const gold = getComputedStyle(probe).color;
        probe.remove();
        const actions = [...document.querySelectorAll('.run-results__actions > *')].map((node) => node.getBoundingClientRect().height);
        const support = document.querySelector('[data-operator-version-decision]');
        const categoryButtons = [...document.querySelectorAll('.measurement-controls__categories button')];
        const categoryTops = new Set(categoryButtons.map((node) => Math.round(node.getBoundingClientRect().top)));
        const executionIdentity = document.querySelector('.run-execution-table .run-evidence-identity');
        const executionCell = executionIdentity.closest('td').getBoundingClientRect();
        const actionCards = [...document.querySelectorAll('.run-action')];
        const payloadRects = [...document.querySelectorAll('[data-evidence-payload]')].map((details) => {
          const pre = details.querySelector('.run-payload-pre');
          const row = details.closest('tr');
          const table = details.closest('table');
          const rect = pre.getBoundingClientRect();
          const tableRect = table.getBoundingClientRect();
          return {
            position:getComputedStyle(pre).position,
            contained:rect.left >= tableRect.left - 1 && rect.right <= tableRect.right + 1,
            rowContainsPayload:row.getBoundingClientRect().bottom >= rect.bottom - 1,
            rect:{left:rect.left,right:rect.right,width:rect.width},
            tableRect:{left:tableRect.left,right:tableRect.right,width:tableRect.width},
          };
        });
        return {
          centered: Math.abs((title.left + title.width / 2) - (group.left + group.width / 2)) < 2,
          summaryBelowCard: summary.top >= card.bottom - 1,
          runColumnCentered: Math.abs((runlay.left + runlay.width / 2) - (shell.left + shell.width / 2)) < 2,
          disclosureBackground: bodyStyle.backgroundImage,
          disclosureColor: bodyStyle.color,
          disclosureCodeColor: getComputedStyle(disclosureBody.querySelector('code')).color,
          measurementToggleColor: getComputedStyle(document.querySelector('.measurement-group__summary'), '::after').color,
          evidenceToggleColor: getComputedStyle(evidenceSummary, '::after').color,
          evidenceTitleCentered: Math.abs((evidenceTitle.left + evidenceTitle.width / 2) - (evidenceBounds.left + evidenceBounds.width / 2)) < 2,
          evidenceEyebrowPosition: getComputedStyle(evidenceSummary.querySelector('.eyebrow')).position,
          evidenceMetaVisible: getComputedStyle(evidenceSummary.querySelector('.run-disclosure__meta')).display !== 'none',
          headerBackgroundMatch: evidenceSummaryStyle.backgroundImage === measurementSummaryStyle.backgroundImage,
          headerBorderMatch: getComputedStyle(evidenceSummary.parentElement).borderColor === getComputedStyle(measurementSummary.parentElement).borderColor,
          headerPaddingMatch: evidenceSummaryStyle.paddingTop === measurementSummaryStyle.paddingTop && evidenceSummaryStyle.paddingBottom === measurementSummaryStyle.paddingBottom,
          headerHeightDelta: Math.abs(evidenceBounds.height - measurementSummary.getBoundingClientRect().height),
          gold,
          tableContained: table.right <= disclosureBounds.right + 1,
          executionIdentityContained: executionIdentity.getBoundingClientRect().right <= executionCell.right + 1,
          executionIdentitySingleLine: getComputedStyle(executionIdentity).whiteSpace === 'nowrap',
          actionCardsThemed: actionCards.every((node) => getComputedStyle(node).backgroundImage.includes('linear-gradient')),
          actionSummariesFullWidth: actionCards.every((node) => getComputedStyle(node.querySelector('.run-action__summary')).width !== '30%'),
          categorySingleLine: categoryTops.size === 1,
          payloadRects,
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          actions,
          supportOpen: support.open,
          font: getComputedStyle(document.querySelector('.run-results-basic')).fontFamily,
        };
      });
      assert.equal(layout.centered, true);
      assert.equal(layout.summaryBelowCard, true);
      assert.equal(layout.runColumnCentered, true);
      assert.notEqual(layout.disclosureBackground, 'none');
      assert.match(layout.disclosureBackground, /linear-gradient/);
      assert.match(layout.disclosureColor, /rgb/);
      assert.equal(layout.disclosureCodeColor, layout.gold);
      assert.equal(layout.measurementToggleColor, layout.gold);
      assert.equal(layout.evidenceToggleColor, layout.gold);
      assert.equal(layout.evidenceTitleCentered, true);
      assert.equal(layout.evidenceEyebrowPosition, 'absolute');
      assert.equal(layout.evidenceMetaVisible, true);
      assert.equal(layout.headerBackgroundMatch, true);
      assert.equal(layout.headerBorderMatch, true);
      assert.equal(layout.headerPaddingMatch, true);
      assert.ok(layout.headerHeightDelta <= 2, `header height delta ${layout.headerHeightDelta}px at ${viewport.width}`);
      assert.equal(layout.tableContained, true);
      assert.equal(layout.executionIdentityContained, true);
      assert.equal(layout.executionIdentitySingleLine, true);
      assert.equal(layout.actionCardsThemed, true);
      assert.equal(layout.actionSummariesFullWidth, true);
      if (viewport.width >= 1200) assert.equal(layout.categorySingleLine, true);
      assert.ok(layout.payloadRects.every((entry) => entry.position !== 'absolute'));
      assert.ok(
        layout.payloadRects.every((entry) => entry.contained && entry.rowContainsPayload),
        `payload geometry ${JSON.stringify(layout.payloadRects)} at ${viewport.width}`,
      );
      assert.ok(layout.overflow <= 1, `horizontal overflow ${layout.overflow}px at ${viewport.width}`);
      assert.ok(layout.actions.every((height) => height <= 44));
      assert.equal(layout.supportOpen, false);
      assert.match(layout.font, /IBM Plex Sans/i);
      if (viewport.width === 1200 && process.env.DWARF_RESULTS_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.DWARF_RESULTS_SCREENSHOT_DIR, {recursive: true});
        await page.locator('[data-run-section="execution-definition"]').screenshot({
          path: path.join(process.env.DWARF_RESULTS_SCREENSHOT_DIR, 'results-prebuild-execution-table.png'),
        });
        const actionDisclosure = page.locator('[data-run-section="actions"]');
        await actionDisclosure.evaluate((node) => { node.open = true; });
        await actionDisclosure.screenshot({
          path: path.join(process.env.DWARF_RESULTS_SCREENSHOT_DIR, 'results-prebuild-operator-actions.png'),
        });
        await page.locator('[data-run-section="floor-preview"]').screenshot({
          path: path.join(process.env.DWARF_RESULTS_SCREENSHOT_DIR, 'results-prebuild-expanded-floor-payload.png'),
        });
        await page.locator('[data-run-section="assertions"]').screenshot({
          path: path.join(process.env.DWARF_RESULTS_SCREENSHOT_DIR, 'results-prebuild-expanded-assertion-payload.png'),
        });
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
