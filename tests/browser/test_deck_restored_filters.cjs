const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const script = path.resolve(__dirname, '../../dwarf/dashboard/static/js/deck.js');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.DWARF_BROWSER_CHANNEL ? {channel: process.env.DWARF_BROWSER_CHANNEL} : {}),
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`<!doctype html><body data-ui="basic">
      <section data-dk-grid>
        <input data-dk-grid-search value="">
        <span data-dk-grid-count>3 primitives</span>
        <a data-dk-item data-search="alpha first">Alpha</a>
        <a data-dk-item data-search="beta second">Beta</a>
        <a data-dk-item data-search="gamma third">Gamma</a>
      </section>
    </body>`);
    await page.addScriptTag({path: script});

    // Browsers can restore a form value after deferred scripts have initialized
    // a history-restored page. `pageshow` is the boundary that must reconcile
    // the visible cards with that restored value.
    await page.locator('[data-dk-grid-search]').evaluate((input) => { input.value = 'beta'; });
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));

    const visible = await page.locator('[data-dk-item]:not([hidden])').allTextContents();
    assert.deepEqual(visible, ['Beta'], 'history-restored search is reapplied to the card grid');
    assert.equal(
      await page.locator('[data-dk-grid-count]').textContent(),
      '1 of 3 primitives',
      'history-restored count matches the visible cards',
    );

    // Chromium can restore the form value immediately after dispatching
    // pageshow. The deferred reconciliation must observe that later value.
    await page.locator('[data-dk-grid-search]').fill('');
    await page.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
      document.querySelector('[data-dk-grid-search]').value = 'gamma';
    });
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
    assert.deepEqual(
      await page.locator('[data-dk-item]:not([hidden])').allTextContents(),
      ['Gamma'],
      'post-pageshow form restoration is reconciled on the next frame',
    );
    console.log('PASS history-restored deck search');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
