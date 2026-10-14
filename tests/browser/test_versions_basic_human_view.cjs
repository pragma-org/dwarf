const assert = require('node:assert/strict');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const python = String.raw`
import os, tempfile
from pathlib import Path
from profile_manager import dashboard
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger, subject_key

state_root = Path(tempfile.mkdtemp(prefix="dwarf-versions-browser-"))
os.environ["ADA2_DWARF_VERSION_CATALOG_STATE_DIR"] = str(state_root)
ledger = OperatorVersionDecisionLedger(state_root)
state = ledger.read()
subject = {
  "implementation": "amaru", "version": "10.11.20260912",
  "image_reference": "ghcr.io/pragma-org/amaru@sha256:" + "a" * 64,
  "manifest_digest": "sha256:" + "a" * 64,
  "config_image_id": "sha256:" + "b" * 64,
  "scenario_id": "portable-stock", "scenario_sha256": "c" * 64,
  "scope": "amaru-only", "topology_profile_id": "profile-r",
  "deployment_identity_sha256": "d" * 64, "mixed_pair": None,
}
decision = {
  "decision_id": "decision-browser", "subject_key": subject_key(subject),
  "subject": subject, "decision": "supported", "reason": "Reviewed retained run.",
  "actor": "local-operator", "decided_at": "2026-10-11T12:00:00Z",
  "evidence": {"run_id": "20261011T120000Z-browser", "manifest_sha256": "e" * 64,
               "operational_outcome": "completed", "assertion_verdict": "pass"},
}
ledger.append_decision(decision, expected_revision=state["revision"])
print(dashboard.render_route_html("/operate/versions", token="synthetic-browser-token"))
`;
const rendered = spawnSync('python3', ['-c', python], {
  cwd: root,
  env: {...process.env, PYTHONPATH: 'dwarf'},
  encoding: 'utf8',
  maxBuffer: 16 * 1024 * 1024,
});
if (rendered.status !== 0) throw new Error(rendered.stderr);
const numericSortFixtures = `
  <tr data-version-release-row data-release-id="edge-11.1.2" data-version-search="edge 11.1.2" data-version-implementation="amaru" data-version-channel="edge" data-version-qualification="unknown" data-version-sort-implementation="Amaru" data-version-sort-release="11.1.2" data-version-sort-artifact="Unknown" data-version-sort-qualification="amaru-only unknown" data-version-sort-default="No" data-version-sort-technical="source-edge-2">
    <td><span class="version-identity-pill version-identity-pill--amaru" data-version-implementation-pill>Amaru</span></td><td><span class="version-identity-pill" data-version-value-pill>11.1.2</span></td><td><span data-artifact-eligibility="unknown" title="Artifact eligibility unknown" aria-label="Artifact eligibility: Artifact eligibility unknown">Unknown</span></td><td>amaru-only · unknown</td><td><span data-version-default="no">No</span></td><td><button type="button" data-version-detail-toggle aria-expanded="false" aria-controls="edge-detail-2">Details</button></td>
  </tr><tr id="edge-detail-2" data-version-detail-row data-version-detail-for="edge-11.1.2" hidden><td colspan="6"><div data-version-technical><code>source-edge-2</code></div></td></tr>
  <tr data-version-release-row data-release-id="edge-11.1.10" data-version-search="edge 11.1.10" data-version-implementation="amaru" data-version-channel="edge" data-version-qualification="unknown" data-version-sort-implementation="Amaru" data-version-sort-release="11.1.10" data-version-sort-artifact="Unknown" data-version-sort-qualification="amaru-only unknown" data-version-sort-default="No" data-version-sort-technical="source-edge-10">
    <td><span class="version-identity-pill version-identity-pill--amaru" data-version-implementation-pill>Amaru</span></td><td><span class="version-identity-pill" data-version-value-pill>11.1.10</span></td><td><span data-artifact-eligibility="unknown" title="Artifact eligibility unknown" aria-label="Artifact eligibility: Artifact eligibility unknown">Unknown</span></td><td>amaru-only · unknown</td><td><span data-version-default="no">No</span></td><td><button type="button" data-version-detail-toggle aria-expanded="false" aria-controls="edge-detail-10">Details</button></td>
  </tr><tr id="edge-detail-10" data-version-detail-row data-version-detail-for="edge-11.1.10" hidden><td colspan="6"><div data-version-technical><code>source-edge-10</code></div></td></tr>
  <tr data-version-release-row data-release-id="edge-long-release" data-version-search="edge long release" data-version-implementation="amaru" data-version-channel="edge" data-version-qualification="unknown" data-version-sort-implementation="Amaru" data-version-sort-release="v10.11.20260912-30-gd3a6dafc" data-version-sort-artifact="Unknown" data-version-sort-qualification="amaru-only unknown" data-version-sort-default="No" data-version-sort-technical="source-edge-long">
    <td><span class="version-identity-pill version-identity-pill--amaru" data-version-implementation-pill>Amaru</span></td><td><span class="version-identity-pill" data-version-value-pill title="v10.11.20260912-30-gd3a6dafc" aria-label="Release version: v10.11.20260912-30-gd3a6dafc">v10.11.20260912-30-gd3a6dafc</span></td><td><span data-artifact-eligibility="unknown" title="Artifact eligibility unknown" aria-label="Artifact eligibility: Artifact eligibility unknown">Unknown</span></td><td>amaru-only · unknown</td><td><span data-version-default="no">No</span></td><td><button type="button" data-version-detail-toggle aria-expanded="false" aria-controls="edge-detail-long">Details</button></td>
  </tr><tr id="edge-detail-long" data-version-detail-row data-version-detail-for="edge-long-release" hidden><td colspan="6"><div data-version-technical><code>v10.11.20260912-30-gd3a6dafc</code><code>source-edge-long</code></div></td></tr>`;
const html = rendered.stdout
  .replace('</tbody>', `${numericSortFixtures}</tbody>`)
  .replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, '')
  .replace(/<script type="module">[\s\S]*?<\/script>/g, '');
const instrumentedHtml = html.replace(
  '<head>',
  `<head><base href="http://dwarf.test/"><script>
    window.__pageLoadFetches = [];
    window.fetch = async (url, options) => {
      window.__pageLoadFetches.push({url: String(url), method: options && options.method});
      return new Response(JSON.stringify({ok: false}), {status: 503, headers: {'Content-Type': 'application/json'}});
    };
  </script>`,
);
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

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 900}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      await page.setContent(instrumentedHtml);
      for (const style of styles) await page.addStyleTag({path: style});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/responsive-tables.js')});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/basic-reduce.js')});

      const rows = page.locator('[data-version-release-row]');
      const rowCount = await rows.count();
      assert.ok(rowCount > 100, 'dynamic catalog renders every retained release');
      assert.equal(new Set(await rows.evaluateAll((nodes) => nodes.map((node) => node.dataset.releaseId))).size, rowCount);
      const implementationPills = page.locator('[data-version-implementation-pill]');
      assert.deepEqual(
        new Set(await implementationPills.allTextContents()),
        new Set(['Amaru', 'Cardano']),
        'implementation pills use concise human labels without changing row identities',
      );
      const implementationStyles = await page.evaluate(() => Object.fromEntries(
        ['amaru', 'cardano'].map((implementation) => {
          const node = document.querySelector(`.version-identity-pill--${implementation}`);
          const style = getComputedStyle(node);
          return [implementation, {color: style.color, background: style.backgroundColor, border: style.borderColor}];
        }),
      ));
      assert.notDeepEqual(implementationStyles.amaru, implementationStyles.cardano, 'Amaru and Cardano identity pills have distinct theme treatments');
      const unverifiedEligibility = page.locator('[data-artifact-eligibility="identity-recorded"]').first();
      assert.equal((await unverifiedEligibility.textContent()).trim(), 'Unverified');
      assert.equal(await unverifiedEligibility.getAttribute('title'), 'Immutable identity recorded · public/platform eligibility unverified');
      assert.equal(await unverifiedEligibility.getAttribute('aria-label'), 'Artifact eligibility: Immutable identity recorded · public/platform eligibility unverified');
      const unknownEligibility = page.locator('[data-artifact-eligibility="unknown"]').first();
      assert.equal((await unknownEligibility.textContent()).trim(), 'Unknown');
      assert.equal(await unknownEligibility.getAttribute('title'), 'Artifact eligibility unknown');
      const verifiedEligibility = page.locator('[data-artifact-eligibility="public-platform-eligible"]').first();
      assert.equal((await verifiedEligibility.textContent()).trim(), 'Verified');
      assert.match(await verifiedEligibility.getAttribute('title'), /^Public immutable artifact · /);
      assert.equal(
        await verifiedEligibility.getAttribute('aria-label'),
        `Artifact eligibility: ${await verifiedEligibility.getAttribute('title')}`,
      );
      assert.equal(await page.locator('[data-version-value-pill]').count(), rowCount, 'every exact version is rendered once as an identity pill');
      const longVersion = page.locator('[data-release-id="edge-long-release"] [data-version-value-pill]');
      assert.equal(await longVersion.getAttribute('title'), 'v10.11.20260912-30-gd3a6dafc');
      assert.equal(await longVersion.getAttribute('aria-label'), 'Release version: v10.11.20260912-30-gd3a6dafc');
      const longVersionBounds = await longVersion.evaluate((pill) => {
        const cell = pill.closest('td');
        const pillBox = pill.getBoundingClientRect();
        const cellBox = cell.getBoundingClientRect();
        return {
          pillLeft: pillBox.left,
          pillRight: pillBox.right,
          cellLeft: cellBox.left,
          cellRight: cellBox.right,
          overflow: getComputedStyle(pill).overflow,
          textOverflow: getComputedStyle(pill).textOverflow,
        };
      });
      assert.ok(longVersionBounds.pillLeft >= longVersionBounds.cellLeft - 1, `long release starts inside its cell: ${JSON.stringify(longVersionBounds)}`);
      assert.ok(longVersionBounds.pillRight <= longVersionBounds.cellRight + 1, `long release ends inside its cell: ${JSON.stringify(longVersionBounds)}`);
      assert.equal(longVersionBounds.overflow, 'hidden');
      assert.equal(longVersionBounds.textOverflow, 'ellipsis');
      assert.ok(await page.locator('[data-version-default="no"]').count() > 0, 'non-default releases use the concise No label');
      assert.ok(await page.locator('[data-version-default="yes"]').count() > 0, 'actual default scopes remain explicit');
      assert.equal(await page.locator('[data-version-default="no"]').first().evaluate((node) => node.classList.contains('version-status--neutral')), true);
      assert.equal(await page.locator('[data-version-default="yes"]').first().evaluate((node) => node.classList.contains('version-status--default')), true);
      assert.equal(await page.locator('[data-version-detail-row]').count(), rowCount, 'each release keeps one associated full-width detail row');
      assert.equal(await page.locator('[data-versions-human-view] .dk-card').count(), 0, 'duplicated release cards are removed');
      assert.deepEqual(await page.locator('[data-implementation-summary]').evaluateAll((nodes) => nodes.map((node) => node.dataset.implementation)), ['amaru', 'cardano-node']);
      assert.deepEqual(await page.evaluate(() => window.__pageLoadFetches), [], 'page load makes no discovery request');
      assert.equal(await page.locator('.version-table-wrap > .version-table').count(), 1, 'the table keeps its intentional outer scroll container without a duplicate responsive wrapper');
      const initialEligibility = await rows.evaluateAll((nodes) => nodes.map((node) => node.querySelector('[data-artifact-eligibility]')?.dataset.artifactEligibility));
      const firstNotVerified = initialEligibility.findIndex((value) => value !== 'public-platform-eligible');
      assert.ok(firstNotVerified > 0, 'landing order starts with artifact-eligible Verified releases');
      assert.equal(initialEligibility.slice(firstNotVerified).includes('public-platform-eligible'), false, 'Verified landing partition is stable and contiguous');
      const initialIds = await rows.evaluateAll((nodes) => nodes.map((node) => node.dataset.releaseId));
      assert.deepEqual(initialIds.slice(-3), ['edge-11.1.2', 'edge-11.1.10', 'edge-long-release'], 'initial server ordering remains untouched until a header is activated');
      const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
      for (const key of ['implementation', 'release', 'artifact', 'qualification', 'default', 'technical']) {
        const button = page.locator(`[data-version-sort="${key}"]`);
        await button.click();
        assert.equal(await button.locator('xpath=..').getAttribute('aria-sort'), 'ascending');
        const ascending = await rows.evaluateAll((nodes, sortKey) => nodes.map((node) => node.dataset[`versionSort${sortKey[0].toUpperCase()}${sortKey.slice(1)}`]), key);
        assert.deepEqual(ascending, [...ascending].sort((left, right) => collator.compare(left, right)), `${key} first click sorts ascending`);
        if (key === 'release') {
          const sortedIds = await rows.evaluateAll((nodes) => nodes.map((node) => node.dataset.releaseId));
          assert.ok(sortedIds.indexOf('edge-11.1.2') < sortedIds.indexOf('edge-11.1.10'), 'natural version sorting places 11.1.2 before 11.1.10');
        }
        await button.click();
        assert.equal(await button.locator('xpath=..').getAttribute('aria-sort'), 'descending');
        const descending = await rows.evaluateAll((nodes, sortKey) => nodes.map((node) => node.dataset[`versionSort${sortKey[0].toUpperCase()}${sortKey.slice(1)}`]), key);
        assert.deepEqual(descending, [...descending].sort((left, right) => collator.compare(right, left)), `${key} second click sorts descending`);
        assert.ok(await rows.evaluateAll((nodes) => nodes.every((node) => node.nextElementSibling?.dataset.versionDetailFor === node.dataset.releaseId)), `${key} sorting keeps every release paired with its detail row`);
      }
      const implementationHeader = page.locator('[data-version-sort="implementation"]');
      assert.equal(await implementationHeader.evaluate((node) => getComputedStyle(node).whiteSpace), 'nowrap');
      const firstReleaseId = await rows.first().getAttribute('data-release-id');
      const firstRow = page.locator(`[data-version-release-row][data-release-id="${firstReleaseId}"]`);
      const firstToggle = firstRow.locator('[data-version-detail-toggle]');
      const firstDetailRow = page.locator(`[data-version-detail-for="${firstReleaseId}"]`);
      assert.equal(await firstToggle.getAttribute('aria-expanded'), 'false');
      assert.equal(await firstDetailRow.isVisible(), false);
      await firstToggle.focus();
      assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'BUTTON');
      await page.keyboard.press('Enter');
      assert.equal(await firstToggle.getAttribute('aria-expanded'), 'true');
      assert.equal(await firstDetailRow.isVisible(), true);
      const detailGeometry = await firstDetailRow.evaluate((row) => ({
        cellWidth: row.querySelector('td').getBoundingClientRect().width,
        tableWidth: row.closest('table').getBoundingClientRect().width,
        codeWrap: getComputedStyle(row.querySelector('code')).overflowWrap,
      }));
      assert.ok(detailGeometry.cellWidth >= detailGeometry.tableWidth - 2, `expanded release details span the readable table width: ${JSON.stringify(detailGeometry)}`);
      assert.equal(detailGeometry.codeWrap, 'anywhere');
      await page.locator('[data-version-sort="release"]').click();
      assert.equal(await firstToggle.getAttribute('aria-expanded'), 'true', 'sorting preserves an expanded release state');
      assert.equal(await firstDetailRow.isVisible(), true, 'sorting moves the expanded release and detail together');

      const allIds = await rows.evaluateAll((nodes) => nodes.map((node) => node.dataset.releaseId));
      await page.locator('[data-version-query]').fill('10.11.20260925');
      assert.equal(await rows.evaluateAll((nodes) => nodes.filter((node) => !node.hidden).length), 1);
      assert.equal(await firstDetailRow.isVisible(), false, 'filtering a release also hides its expanded detail row');
      await page.locator('[data-version-implementation-filter]').selectOption('amaru');
      await page.locator('[data-version-clear]').click();
      assert.deepEqual(await rows.evaluateAll((nodes) => nodes.filter((node) => !node.hidden).map((node) => node.dataset.releaseId)), allIds);
      assert.equal(await firstDetailRow.isVisible(), true, 'clearing filters restores the explicitly expanded detail row');
      await firstToggle.click();
      assert.equal(await firstToggle.getAttribute('aria-expanded'), 'false');
      assert.equal(await firstDetailRow.isVisible(), false);

      const matrix = page.locator('[data-version-pair-matrix]');
      assert.ok(await matrix.locator('[data-pair-id]').count() > 0);
      assert.ok(await matrix.locator('[data-pair-recorded="false"]').count() > 0);
      assert.equal(new Set(await matrix.locator('[data-pair-id]').evaluateAll((nodes) => nodes.map((node) => node.dataset.pairId))).size, await matrix.locator('[data-pair-id]').count());
      assert.ok(await matrix.locator('[data-version-pair-group]').count() > 0, 'pair cards are grouped by Cardano version');
      const pairCards = matrix.locator('[data-version-pair-card]');
      assert.equal(
        await pairCards.count(),
        await matrix.locator('[data-version-pair-group]').evaluateAll((groups) => groups.reduce((total, group) => total + group.querySelectorAll('[data-version-pair-card]').length, 0)),
        'each exact pair intersection renders once',
      );
      assert.equal((await matrix.textContent()).includes('No recorded proof'), false, 'unrecorded cards do not repeat the section explanation');
      const recordedDetail = matrix.locator('[data-pair-id]').first();
      await recordedDetail.locator('summary').click();
      assert.equal(await recordedDetail.getAttribute('open'), '');
      assert.ok((await recordedDetail.textContent()).includes('Exact Cardano version'));
      assert.ok((await recordedDetail.textContent()).includes('Exact Amaru version'));
      const matrixGeometry = await matrix.evaluate((node) => ({clientWidth: node.clientWidth, scrollWidth: node.scrollWidth}));
      assert.ok(matrixGeometry.scrollWidth <= matrixGeometry.clientWidth, `pair cards never require horizontal scrolling: ${JSON.stringify(matrixGeometry)}`);
      const overflowingPairCards = await pairCards.evaluateAll((cards) => cards.filter((card) => card.scrollWidth > card.clientWidth + 1).length);
      assert.equal(overflowingPairCards, 0, 'pair cards contain long exact identities and opened details');
      if (viewport.width <= 390) {
        assert.ok(await page.locator('.version-table-wrap').evaluate((node) => node.scrollWidth > node.clientWidth), 'release table exposes a contained horizontal scroll range');
      } else {
        const desktopTable = await page.locator('.version-table-wrap').evaluate((node) => ({
          clientWidth: node.clientWidth,
          scrollWidth: node.scrollWidth,
          tableWidth: node.querySelector('table').getBoundingClientRect().width,
          columns: Array.from(node.querySelectorAll('thead th')).map((cell) => ({
            label: cell.textContent.trim(),
            width: cell.getBoundingClientRect().width,
          })),
        }));
        assert.ok(desktopTable.scrollWidth <= desktopTable.clientWidth, `ordinary desktop release table does not require horizontal scrolling: ${JSON.stringify(desktopTable)}`);
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), 'table and matrix do not overflow the page');

      const primaryNavigation = page.locator('.shell-nav a');
      assert.deepEqual(await primaryNavigation.allTextContents(), ['Start run', 'Operate', 'Learn']);
      assert.deepEqual(
        await primaryNavigation.evaluateAll((links) => links.map((link) => getComputedStyle(link, '::before').display)),
        ['none', 'none', 'none'],
        'shared top navigation is text-only without decorative card pseudo-elements',
      );

      const operatorRevision = page.locator('[data-operator-revision]');
      assert.equal(await operatorRevision.getAttribute('open'), null);
      const revisionSummary = (await operatorRevision.locator('summary').textContent()).trim();
      assert.match(revisionSummary, /^Revision sha256:[0-9a-f]{12}…$/);
      assert.equal(await operatorRevision.locator('code').isVisible(), false);

      const captures = await page.evaluate(() => {
        window.__versionSubmits = [];
        document.querySelectorAll('[data-versions-human-view] form').forEach((form) => form.addEventListener('submit', (event) => {
          event.preventDefault();
          const data = new FormData(form);
          window.__versionSubmits.push({action: form.getAttribute('action'), keys: [...data.keys()].sort()});
        }));
        window.__refreshes = [];
        window.__imports = [];
        window.setTimeout = () => 0;
        window.__alerts = [];
        window.alert = (message) => window.__alerts.push(message);
        window.fetch = async (url, options) => {
          const path = new URL(url, 'http://dwarf.test').pathname;
          if (options.body instanceof FormData) {
            window.__imports.push({path, kind: 'upload', keys: [...options.body.keys()].sort()});
            return new Response(JSON.stringify({ok: true, state: 'running', import_id: 'import-0123456789abcdef01234567', execution_id: 'execution-0123456789abcdef0123456789abcdef'}), {status: 202, headers: {'Content-Type': 'application/json'}});
          }
          const params = new URLSearchParams(options.body);
          if (path.endsWith('/status')) {
            window.__imports.push({path, kind: 'status', keys: [...params.keys()].sort()});
            return new Response(JSON.stringify({ok: true, state: 'registered', registration_id: 'registration-0123456789abcdef01234567', compatibility: 'unknown-until-run'}), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          const implementation = params.get('implementation');
          window.__refreshes.push({path, implementation, keys: [...params.keys()].sort()});
          if (implementation === 'cardano-node') {
            return new Response(JSON.stringify({ok: false, error: 'source unavailable'}), {status: 503, headers: {'Content-Type': 'application/json'}});
          }
          return new Response(JSON.stringify({ok: true}), {status: 202, headers: {'Content-Type': 'application/json'}});
        };
        return true;
      });
      assert.equal(captures, true);
      assert.deepEqual(await page.evaluate(() => window.__refreshes), [], 'page load makes no discovery request');
      await page.locator('[data-binary-import-section] > summary').click();
      const importForm = page.locator('[data-binary-import-form]');
      await importForm.locator('input[name="version"]').fill('developer-browser');
      await importForm.locator('input[name="artifact"]').setInputFiles({name: 'developer-build', mimeType: 'application/octet-stream', buffer: Buffer.from('fixture')});
      await page.evaluate(() => {
        const form = document.querySelector('[data-binary-import-form]');
        form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
      });
      await page.waitForFunction(() => document.querySelector('[data-binary-import-status]').textContent.includes('registered'));
      assert.deepEqual(await page.evaluate(() => window.__imports), [
        {path: '/api/versions/import-binary', kind: 'upload', keys: ['artifact', 'declared_source_revision', 'expected_revision', 'implementation', 'token', 'version']},
        {path: '/api/versions/import-binary/status', kind: 'status', keys: ['execution_id', 'import_id', 'token']},
      ], 'double submit starts one upload and status polling keeps credentials in the body');
      await page.locator('[data-version-registration-section] > summary').click();
      const registration = page.locator('form[action="/api/versions/register"]');
      await registration.locator('input[name="version"]').fill('local-browser');
      await registration.locator('input[name="image"]').fill('local/image:browser');
      await registration.locator('button[type="submit"]').click();
      await page.locator('[data-version-history-section] > summary').click();
      await page.locator('[data-version-decision] > summary').first().click();
      await page.locator('form[action="/api/versions/preferences"] button').first().click();
      const amaruRefresh = page.locator('[data-version-refresh][data-implementation="amaru"]');
      const cardanoRefresh = page.locator('[data-version-refresh][data-implementation="cardano-node"]');
      await amaruRefresh.click();
      await page.waitForTimeout(20);
      assert.equal(await amaruRefresh.textContent(), 'Fetching…');
      await cardanoRefresh.click();
      await page.waitForFunction(() => document.querySelector('[data-version-refresh][data-implementation="cardano-node"]').textContent === 'Fetch Latest Version');
      assert.equal(await cardanoRefresh.textContent(), 'Fetch Latest Version');
      const actions = await page.evaluate(() => ({submits: window.__versionSubmits, refreshes: window.__refreshes, alerts: window.__alerts}));
      assert.ok(actions.submits.some((entry) => JSON.stringify(entry) === JSON.stringify({action: '/api/versions/register', keys: ['declared_source_revision', 'expected_revision', 'image', 'implementation', 'token', 'version']})));
      assert.ok(actions.submits.some((entry) => JSON.stringify(entry) === JSON.stringify({action: '/api/versions/preferences', keys: ['action', 'decision_id', 'expected_revision', 'token']})));
      assert.deepEqual(actions.refreshes, [
        {path: '/api/versions/refresh', implementation: 'amaru', keys: ['implementation', 'token']},
        {path: '/api/versions/refresh', implementation: 'cardano-node', keys: ['implementation', 'token']},
      ]);
      assert.deepEqual(actions.alerts, ['source unavailable']);
      await page.close();
    }

    const zoomPage = await browser.newPage({viewport: {width: 720, height: 900}});
    await zoomPage.setContent(html);
    for (const style of styles) await zoomPage.addStyleTag({path: style});
    assert.ok(await zoomPage.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), '200%-effective width stays page-contained');
    assert.ok(await zoomPage.locator('[data-version-release-row]').count() > 100);
    await zoomPage.close();
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
