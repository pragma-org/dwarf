const assert = require('node:assert/strict');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const rendered = spawnSync('python3', ['-c', [
  'from profile_manager import dashboard',
  'print(dashboard.render_route_html("/run", token="synthetic-browser-token"))',
].join(';')], {
  cwd: root,
  env: {...process.env, PYTHONPATH: 'dwarf'},
  encoding: 'utf8',
  maxBuffer: 16 * 1024 * 1024,
});
if (rendered.status !== 0) throw new Error(rendered.stderr);
const html = rendered.stdout.replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, '');
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
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      await page.setContent(html);
      for (const style of styles) await page.addStyleTag({path: style});
      await page.evaluate(() => {
        window.__runRequests = [];
        window.__resolveMode = 'reject';
        window.__eligiblePlan = JSON.parse(document.querySelector('#run-wizard-bootstrap').textContent).default_plan;
        window.fetch = async (url, options) => {
          window.__runRequests.push({url, body: options?.body});
          if (window.__resolveMode === 'eligible') {
            const request = JSON.parse(options.body);
            return new Response(JSON.stringify({
              ok: true,
              plan: {...window.__eligiblePlan, request: {...window.__eligiblePlan.request, seed: request.seed}, availability: {state: 'ready', reason: 'All exact artifacts are available.'}},
              plan_digest: `eligible-${request.seed}`,
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          return new Response(JSON.stringify({ok: false, error: {message: 'unexpected test request'}}), {
            status: 409, headers: {'Content-Type': 'application/json'},
          });
        };
      });
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/scenario-discovery.js'), type: 'module'});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/basic-reduce.js')});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/run-wizard.js'), type: 'module'});
      await page.waitForFunction(() => document.querySelector('[data-run-summary="profile"]')?.textContent.includes('Amaru'));

      assert.equal(await page.locator('[data-scenario-card]').count(), 351);
      assert.deepEqual(await page.locator('#run-profile optgroup').evaluateAll((groups) => groups.map((group) => group.label)), [
        'Amaru test target', 'Cardano test target', 'Mixed test targets',
      ]);
      assert.deepEqual(await page.locator('#run-profile optgroup').evaluateAll((groups) => groups.map((group) => group.querySelectorAll('option').length)), [13, 12, 8]);
      assert.equal(await page.locator('#run-profile option[value]').count(), 34, 'Scenario default plus 33 canonical profiles remain selectable');
      assert.equal(await page.locator('#run-profile').inputValue(), '', 'scenario-owned default remains selected');
      assert.equal(await page.locator('#run-readiness-text').getAttribute('aria-live'), 'polite');
      assert.equal(await page.locator('.run-readiness-logo').getAttribute('aria-labelledby'), 'run-readiness-text');
      const hiddenReadiness = await page.locator('#run-readiness-text').evaluate((node) => {
        const style = getComputedStyle(node);
        const box = node.getBoundingClientRect();
        return {position: style.position, width: box.width, height: box.height, overflow: style.overflow, text: node.textContent};
      });
      assert.deepEqual({...hiddenReadiness, text: undefined}, {
        position: 'absolute', width: 1, height: 1, overflow: 'hidden', text: undefined,
      }, 'readiness updates remain in the accessibility tree without taking visual space');
      assert.match(hiddenReadiness.text, /Configuration|Choose|Prepare|Run/, 'setLogoState continues to update the live caption');
      assert.equal(await page.getByText('Use a confirmed policy or exact catalogued pins.', {exact: false}).count(), 0);
      assert.equal(await page.locator('#run-profile option[value="profile-r-amaru-measurement-stock-control"]').textContent(), 'Stock measurements — Amaru control');
      assert.match(await page.locator('#run-profile option[value="profile-r-amaru-measurement-stock-control"]').getAttribute('data-full-label'), /10\.11\.20260912.*stock measurement control/i);
      assert.equal(await page.locator('[data-scenario-disclosure^="subject:"]').count(), 8);
      assert.equal(await page.locator('#run-scenario').inputValue(), 'amaru-measurement-e2e-stock');
      assert.match(await page.locator('[data-run-summary="profile"]').textContent(), /Amaru 10\.11\.20260912.*stock measurement control.*Amaru/i);
      assert.equal(await page.locator('[data-run-summary="target"] dd').last().textContent(), 'profile-r-amaru-measurement-stock-control');
      assert.match(await page.locator('[data-run-summary="versions"]').textContent(), /10\.11\.20260912.*confirmed/i);
      assert.equal(await page.locator('#run-measurements').inputValue(), '', 'blank control means the scenario-owned measurement default');
      assert.match(await page.locator('[data-run-summary="plan"]').textContent(), /"id": "amaru-security-default"/);
      assert.equal(await page.locator('[data-measurement-tap-id]:checked').count(), 11);
      assert.equal(await page.locator('[data-version-acknowledgement]').isHidden(), true);
      assert.equal(await page.locator('[data-run-reproducibility]').getAttribute('open'), null);
      assert.equal(await page.locator('[data-run-action="start"]').isEnabled(), false);
      assert.match(await page.locator('[data-run-launch-status]').textContent(), /Prepare the exact topology/i);
      assert.equal(await page.locator('[data-run-summary="primitives"] code').count() > 0, true);
      assert.equal(await page.locator('[data-run-summary="primitives"] details').first().getAttribute('open'), null);
      assert.equal(await page.locator('[data-run-summary="readiness"] details').first().getAttribute('open'), null);
      const readinessText = await page.locator('[data-run-summary="readiness"]').textContent();
      assert.match(readinessText, /Confirm DWARF runtime and controller availability/);
      assert.doesNotMatch(readinessText, /runtime and controller are available|images match the exact version plan/i);
      assert.deepEqual(await page.evaluate(() => window.__runRequests), []);
      const unavailable = await page.locator('[data-run-action="prepare"]').evaluate((node) => {
        const style = getComputedStyle(node);
        return {disabled: node.disabled, backgroundColor: style.backgroundColor, color: style.color, borderColor: style.borderColor};
      });
      assert.deepEqual(unavailable, {disabled: true, backgroundColor: 'rgb(21, 16, 31)', color: 'rgb(177, 168, 196)', borderColor: 'rgb(77, 66, 102)'}, 'unavailable plan remains gray');
      await page.evaluate(() => { window.__resolveMode = 'eligible'; });
      await page.locator('[data-run-reproducibility] > summary').click();
      await page.locator('[data-run-seed]').fill('41');
      assert.equal(await page.locator('[data-run-action="prepare"]').isEnabled(), false, 'pending resolution is gray before the eligible response');
      await page.waitForFunction(() => window.__runRequests.length === 1, null, {timeout: 1000});
      const eligibleResult = await page.evaluate(() => ({
        disabled: document.querySelector('[data-run-action="prepare"]').disabled,
        status: document.querySelector('[data-run-prepare-status]').textContent,
        error: document.querySelector('[data-run-errors]').textContent,
      }));
      assert.equal(eligibleResult.disabled, false, JSON.stringify(eligibleResult));

      const boxes = await page.evaluate(() => {
        const progress = document.querySelector('.run-wizard__progress').getBoundingClientRect();
        const progressCards = [...document.querySelectorAll('.run-wizard__progress li')].map((node) => node.getBoundingClientRect());
        const layout = document.querySelector('.run-wizard__layout').getBoundingClientRect();
        const search = document.querySelector('[data-discovery-query]').getBoundingClientRect();
        const health = document.querySelector('.scenario-discovery__maintenance').getBoundingClientRect();
        const review = document.querySelector('.run-wizard__review-grid').getBoundingClientRect();
        const facts = document.querySelector('[data-run-summary="target"]').getBoundingClientRect();
        const primaryFacet = document.querySelector('.scenario-discovery__facet--primary button').getBoundingClientRect();
        const clear = document.querySelector('.scenario-discovery__clear').getBoundingClientRect();
        const operationSections = [...document.querySelectorAll('[data-run-summary="primitives"] .run-wizard__explanations > section')].map((node) => node.getBoundingClientRect());
        const readinessSections = [...document.querySelectorAll('[data-run-summary="readiness"] .run-wizard__explanations > section')].map((node) => node.getBoundingClientRect());
        const prepare = document.querySelector('[data-run-action="prepare"]');
        const prepareBox = prepare.getBoundingClientRect();
        const prepareStyle = getComputedStyle(prepare);
        return {progress, progressCards, layout, search, health, review, facts, primaryFacet, clear,
          operationSections, readinessSections, prepareBox,
          prepareStyle: {fontSize: prepareStyle.fontSize, fontFamily: prepareStyle.fontFamily, letterSpacing: prepareStyle.letterSpacing,
            alignItems: prepareStyle.alignItems, justifyContent: prepareStyle.justifyContent,
            backgroundColor: prepareStyle.backgroundColor, color: prepareStyle.color, borderColor: prepareStyle.borderColor,
            paddingTop: prepareStyle.paddingTop, paddingRight: prepareStyle.paddingRight}};
      });
      assert.ok(boxes.progress.width <= 881 && boxes.layout.width <= 881);
      assert.ok(Math.abs(boxes.progress.x - boxes.layout.x) < 2);
      const progressUnionCenter = (boxes.progressCards[0].left + boxes.progressCards.at(-1).right) / 2;
      assert.ok(Math.abs(progressUnionCenter - (boxes.layout.left + boxes.layout.width / 2)) < 2, 'the six stage cards center over the wizard column');
      assert.ok(boxes.review.y - (boxes.facts.y + boxes.facts.height) >= 16, 'review grid cannot clip the scenario facts');
      assert.ok(boxes.primaryFacet.height >= 44, 'primary facet retains a 44px hit target');
      assert.ok(boxes.clear.height >= 44, 'Clear retains a 44px hit target');
      const compactLimit = viewport.width > 640 ? 80 : 100;
      const operationHeight = Math.max(...boxes.operationSections.map((box) => box.height));
      const readinessHeight = Math.max(...boxes.readinessSections.map((box) => box.height));
      assert.ok(operationHeight <= compactLimit, `scenario operation rows stay compact (${operationHeight}px)`);
      assert.ok(readinessHeight <= compactLimit, `Prepare requirements stay compact (${readinessHeight}px)`);
      assert.ok(boxes.prepareBox.width >= 68 && boxes.prepareBox.width <= 78, `desktop-style Prepare stays near 70–76px (${boxes.prepareBox.width}px)`);
      if (viewport.width > 640) assert.ok(boxes.prepareBox.height >= 30 && boxes.prepareBox.height <= 34, `desktop Prepare is compact (${boxes.prepareBox.height}px)`);
      else assert.ok(boxes.prepareBox.height >= 44, 'mobile Prepare keeps a 44px touch target');
      assert.equal(boxes.prepareStyle.fontSize, '14px');
      assert.match(boxes.prepareStyle.fontFamily, /IBM Plex Sans/i);
      assert.ok(['normal', '0px'].includes(boxes.prepareStyle.letterSpacing));
      assert.equal(boxes.prepareStyle.alignItems, 'center');
      assert.equal(boxes.prepareStyle.justifyContent, 'center');
      assert.equal(boxes.prepareStyle.backgroundColor, 'rgb(230, 198, 127)', 'eligible exact plan uses the gold fill');
      assert.equal(boxes.prepareStyle.color, 'rgb(26, 18, 36)', 'eligible exact plan uses dark readable text');
      assert.equal(boxes.prepareStyle.borderColor, 'rgb(230, 198, 127)');
      assert.equal(boxes.prepareStyle.paddingTop, viewport.width > 640 ? '4px' : '0px');
      assert.ok(Number.parseFloat(boxes.prepareStyle.paddingRight) >= 8 && Number.parseFloat(boxes.prepareStyle.paddingRight) <= 10);
      assert.equal(await page.locator('[data-run-action="prepare"]').textContent(), 'Prepare');
      assert.equal(await page.locator('[data-run-action="prepare"]').getAttribute('aria-label'), 'Prepare exact configuration');
      assert.equal(await page.locator('[data-run-summary="primitives"] .run-wizard__explanations > details').count(), 1, 'operation IDs share one compact disclosure');
      assert.equal(await page.locator('[data-run-summary="readiness"] .run-wizard__explanations > details').count(), 1, 'Prepare requirements share one compact disclosure');
      const technical = page.locator('[data-run-summary="primitives"] .run-wizard__explanations > details');
      await technical.locator('summary').click();
      if (viewport.width > 640) {
        const seed = page.locator('[data-run-seed]');
        await page.evaluate(() => { window.__resolveMode = 'reject'; });
        await seed.fill('42');
        const pending = await page.locator('[data-run-action="prepare"]').evaluate((node) => {
          const style = getComputedStyle(node);
          return {disabled: node.disabled, backgroundColor: style.backgroundColor, color: style.color, borderColor: style.borderColor};
        });
        assert.deepEqual(pending, {disabled: true, backgroundColor: 'rgb(21, 16, 31)', color: 'rgb(177, 168, 196)', borderColor: 'rgb(77, 66, 102)'}, 'pending resolution is gray immediately');
        await page.waitForTimeout(250);
        assert.equal(await page.evaluate(() => window.__runRequests.length), 2, 'each seed edit resolves once');
        const rejected = await page.locator('[data-run-action="prepare"]').evaluate((node) => {
          const style = getComputedStyle(node);
          return {disabled: node.disabled, backgroundColor: style.backgroundColor, color: style.color, borderColor: style.borderColor};
        });
        assert.deepEqual(rejected, pending, 'invalid resolution remains gray');
      }
      assert.equal(await technical.locator('code').count(), 2, 'all operation identities remain available');
      assert.equal(await technical.locator('code').first().isVisible(), true, 'technical operation identity remains visible when expanded');
      assert.ok(await technical.locator('summary').evaluate((node) => node.getBoundingClientRect().height >= 32), 'technical summary remains keyboard-sized');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), 'expanded technical identity does not overflow');
      await technical.locator('summary').click();
      if (viewport.width > 640) assert.ok(Math.abs(boxes.search.bottom - boxes.health.bottom) < 2, 'search and health toggle share the desktop row');
      else assert.ok(boxes.health.y >= boxes.search.y + boxes.search.height, 'health toggle stacks below search on mobile');
      const profile = page.locator('#run-profile');
      const pickerContract = await profile.evaluate((node) => {
        const selected = node.options[node.selectedIndex];
        const ordinary = node.querySelector('optgroup option');
        const group = node.querySelector('optgroup');
        const groupStyle = getComputedStyle(group);
        const optionStyle = getComputedStyle(ordinary);
        const selectedStyle = getComputedStyle(selected);
        return {
          baseSelect: CSS.supports('appearance', 'base-select'),
          pickerSelector: CSS.supports('selector(::picker(select))'),
          appearance: getComputedStyle(node).appearance,
          optionColor: optionStyle.color,
          optionBackground: optionStyle.backgroundColor,
          selectedColor: selectedStyle.color,
          selectedBackground: selectedStyle.backgroundColor,
          groupColor: groupStyle.color,
          groupBackground: groupStyle.backgroundColor,
          tagName: node.tagName,
          showPicker: typeof node.showPicker,
        };
      });
      assert.equal(pickerContract.tagName, 'SELECT');
      assert.equal(pickerContract.showPicker, 'function');
      assert.equal(pickerContract.optionColor, 'rgb(238, 232, 246)');
      assert.equal(pickerContract.optionBackground, 'rgb(38, 29, 54)');
      assert.equal(pickerContract.selectedColor, 'rgb(26, 18, 36)');
      assert.equal(pickerContract.selectedBackground, 'rgb(230, 198, 127)');
      assert.equal(pickerContract.groupColor, 'rgb(230, 198, 127)');
      assert.equal(pickerContract.groupBackground, 'rgb(21, 16, 31)');
      if (pickerContract.baseSelect && pickerContract.pickerSelector) {
        assert.equal(pickerContract.appearance, 'base-select', 'supporting Chromium uses the customizable real-select picker');
      }
      await profile.focus();
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'run-profile', 'native select remains focusable');
      assert.equal(await profile.evaluate((node) => node.tagName), 'SELECT');
      const firstCanonicalProfile = await profile.locator('option[value]:not([value=""])').first().getAttribute('value');
      await profile.click();
      if (pickerContract.baseSelect && pickerContract.pickerSelector) {
        const openedPicker = await profile.locator('option').evaluateAll((options) => {
          const boxes = options.map((option) => option.getBoundingClientRect()).filter((box) => box.width || box.height);
          return {
            left: Math.min(...boxes.map((box) => box.left)),
            right: Math.max(...boxes.map((box) => box.right)),
            minHeight: Math.min(...boxes.map((box) => box.height)),
            viewportWidth: window.innerWidth,
          };
        });
        assert.ok(openedPicker.left >= 0, `opened picker starts in the viewport (${openedPicker.left}px)`);
        assert.ok(openedPicker.right <= openedPicker.viewportWidth, `opened picker stays in the viewport (${openedPicker.right}/${openedPicker.viewportWidth}px)`);
        assert.ok(openedPicker.minHeight >= 44, `opened picker options keep a 44px target (${openedPicker.minHeight}px)`);
      }
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('Enter');
      assert.equal(await profile.inputValue(), firstCanonicalProfile, 'keyboard commits a canonical option in enhanced Chromium');
      await profile.selectOption('');
      await profile.selectOption('profile-a-haskell-peersharing-disabled');
      assert.equal(await page.locator('[data-scenario-id="amaru-measurement-e2e-stock"]').getAttribute('aria-disabled'), 'true', 'existing compatibility filtering uses the unchanged target counts');
      await profile.selectOption('');
      assert.equal(await profile.inputValue(), '', 'native picker restores the scenario-owned default');
      assert.equal(await page.locator('[data-scenario-id="amaru-measurement-e2e-stock"]').getAttribute('aria-disabled'), 'false');
      await page.addStyleTag({content: 'body[data-ui="basic"] #run-profile { appearance:auto!important; }'});
      assert.equal(await profile.evaluate((node) => getComputedStyle(node).appearance), 'auto', 'unsupported browsers retain the real native-select fallback');
      await profile.selectOption('profile-r-amaru-measurement-stock-control');
      assert.equal(await profile.inputValue(), 'profile-r-amaru-measurement-stock-control', 'fallback select preserves canonical form values');
      await profile.selectOption('');
      await page.waitForTimeout(250);
      assert.equal(await page.evaluate(() => window.__runRequests.every((request) => String(request.url).includes('/api/run/resolve'))), true, 'picker display checks never Prepare or Run');
      await page.close();
    }
    const zoomPage = await browser.newPage({viewport: {width: 720, height: 900}});
    await zoomPage.setContent(html);
    for (const style of styles) await zoomPage.addStyleTag({path: style});
    const zoomProfile = zoomPage.locator('#run-profile');
    await zoomProfile.click();
    const zoomPicker = await zoomProfile.locator('option').evaluateAll((options) => {
      const boxes = options.map((option) => option.getBoundingClientRect()).filter((box) => box.width || box.height);
      return {
        left: Math.min(...boxes.map((box) => box.left)),
        right: Math.max(...boxes.map((box) => box.right)),
        minHeight: Math.min(...boxes.map((box) => box.height)),
        viewportWidth: window.innerWidth,
      };
    });
    assert.ok(zoomPicker.left >= 0 && zoomPicker.right <= zoomPicker.viewportWidth, `200%-effective-width picker stays contained (${zoomPicker.left}..${zoomPicker.right}/${zoomPicker.viewportWidth}px)`);
    assert.ok(zoomPicker.minHeight >= 44, `200%-effective-width picker retains 44px options (${zoomPicker.minHeight}px)`);
    await zoomPage.keyboard.press('Escape');
    await zoomPage.close();
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
