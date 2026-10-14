const assert = require('node:assert/strict');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const rendered = spawnSync('python3', ['-c', [
  'from profile_manager import dashboard',
  'print(dashboard.render_route_html("/"))',
].join(';')], {
  cwd: root,
  env: {...process.env, PYTHONPATH: 'dwarf'},
  encoding: 'utf8',
  maxBuffer: 4 * 1024 * 1024,
});
if (rendered.status !== 0) throw new Error(rendered.stderr);
const html = rendered.stdout.replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, '');
const styles = [
  'dwarf/dashboard/static/css/plex.css',
  'dwarf/dashboard/static/css/tokens.css',
  'dwarf/dashboard/static/css/themes.css',
  'dwarf/dashboard/static/css/base.css',
  'dwarf/dashboard/static/css/basic.css',
  'dwarf/dashboard/static/css/deck.css',
].map((file) => path.join(root, file));

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const testCase of [
      {name: 'desktop', viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1, columns: 2},
      {name: 'mobile', viewport: {width: 390, height: 844}, deviceScaleFactor: 1, columns: 1},
      {name: 'zoom-200', viewport: {width: 720, height: 500}, deviceScaleFactor: 2, columns: 1},
    ]) {
      const context = await browser.newContext({viewport: testCase.viewport, deviceScaleFactor: testCase.deviceScaleFactor});
      const page = await context.newPage();
      await page.setContent(html);
      for (const style of styles) await page.addStyleTag({path: style});
      await page.addScriptTag({path: path.join(root, 'dwarf/dashboard/static/js/deck.js')});

      assert.equal(await page.locator('[data-dk-flip]').count(), 2);
      assert.equal(await page.getByText('III · Future').count(), 0);
      assert.equal(await page.getByText('Open gaps').count(), 0);
      assert.equal(await page.getByText('Home · three-card reading').count(), 0);
      assert.equal(await page.getByText(/Evidence is only collectible/).count(), 0);
      assert.equal((await page.locator('.dk-note').textContent()).trim(), 'Owned by PRAGMA');
      assert.equal(await page.locator('h1[data-topology-headline]').count(), 1);
      assert.deepEqual(await page.locator('h1[data-topology-headline]').evaluate((node) => {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return {className: node.className, width: box.width, height: box.height, position: style.position, overflow: style.overflow};
      }), {className: 'visually-hidden', width: 1, height: 1, position: 'absolute', overflow: 'hidden'});
      assert.match(await page.locator('[data-topology-primary-state]').textContent(), /healthy|ready|unknown|unavailable/i);

      const layout = await page.locator('.dk-spread--two').evaluate((spread) => {
        const parent = spread.getBoundingClientRect();
        const cards = [...spread.children].map((node) => node.getBoundingClientRect());
        return {parent, cards, scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth};
      });
      assert.equal(layout.cards.length, 2);
      assert.ok(layout.scrollWidth <= layout.clientWidth, `${testCase.name} does not overflow`);
      if (testCase.columns === 2) {
        assert.ok(Math.abs((layout.cards[0].left + layout.cards[1].right) / 2 - (layout.parent.left + layout.parent.width / 2)) < 2, 'two-card union is centered');
        assert.ok(Math.abs(layout.cards[0].top - layout.cards[1].top) < 2);
      } else {
        assert.ok(layout.cards[1].top > layout.cards[0].bottom, `${testCase.name} stacks cards`);
        for (const card of layout.cards) assert.ok(Math.abs((card.left + card.right) / 2 - (layout.parent.left + layout.parent.width / 2)) < 2, `${testCase.name} centers each card`);
      }

      const firstCard = page.locator('[data-dk-flip]').first();
      await firstCard.focus();
      await firstCard.press('Enter');
      assert.equal(await firstCard.getAttribute('aria-pressed'), 'true', 'keyboard flip remains functional');
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
