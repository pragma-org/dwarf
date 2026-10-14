const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

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

const html = `<!doctype html><html><body data-ui="basic"><main class="shell-main">
  <p class="empty-section-statement empty-section-statement--runs">
    No forensic runs recorded yet.
    <span class="empty-section-statement__cite">scanned <code>dwarf/runs/</code>, 0 runs with manifests</span>
  </p>
  <section class="basic-only dk runs-basic" aria-label="Runs as a hand of cards">
    <h2 class="dk-lead">1 passed, 1 failed, 0 errored.</h2>
    <p class="dk-plain">One card per run, newest first.</p>
    <div class="dk-hand"><a class="dk-mini pass" href="/operate/runs/run-pass" aria-label="pass run">♦</a><a class="dk-mini fail" href="/operate/runs/run-fail" aria-label="fail run">▲</a></div>
  </section>
</main></body></html>`;

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
      const page = await browser.newPage({viewport});
      await page.setContent(html);
      for (const style of styles) await page.addStyleTag({path: style});
      const result = await page.evaluate(() => {
        const empty = document.querySelector('.empty-section-statement--runs');
        const cite = empty.querySelector('.empty-section-statement__cite');
        const lead = document.querySelector('.runs-basic .dk-lead');
        const card = document.querySelector('.runs-basic .dk-mini');
        const style = getComputedStyle(empty);
        const citeStyle = getComputedStyle(cite);
        const leadStyle = getComputedStyle(lead);
        const cardStyle = getComputedStyle(card);
        const probe = document.createElement('i');
        probe.style.background = 'var(--dk-table)'; document.body.append(probe);
        const table = getComputedStyle(probe).backgroundColor; probe.remove();
        const goldProbe = document.createElement('i');
        goldProbe.style.color = 'var(--dk-gold)'; document.body.append(goldProbe);
        const gold = getComputedStyle(goldProbe).color; goldProbe.remove();
        return {
          family: style.fontFamily,
          fontSize: style.fontSize,
          background: style.backgroundColor,
          borderRadius: style.borderRadius,
          citeFamily: citeStyle.fontFamily,
          citeColor: citeStyle.color,
          leadFamily: leadStyle.fontFamily,
          cardHeight: card.getBoundingClientRect().height,
          table,
          gold,
        };
      });
      assert.match(result.family, /IBM Plex Sans/i);
      assert.equal(result.fontSize, '16px');
      assert.equal(result.background, result.table);
      assert.notEqual(result.borderRadius, '0px');
      assert.match(result.citeFamily, /mono/i);
      assert.equal(result.citeColor, result.gold);
      assert.match(result.leadFamily, /IBM Plex Sans/i);
      assert.ok(result.cardHeight >= 36);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
