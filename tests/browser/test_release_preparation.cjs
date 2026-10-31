const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    let starts = 0, polls = 0;
    await page.route('**/api/versions/prepare**', route => {
      const req = route.request();
      assert.equal(req.method(), 'POST');
      assert.equal(new URLSearchParams(req.postData()).get('token'), 'secret');
      assert.equal(new URLSearchParams(req.postData()).get('source_revision'), 'b'.repeat(40));
      if (req.url().endsWith('/prepare')) starts++;
      else polls++;
      const done = starts && polls > 1;
      return route.fulfill({json:{state:done?'completed':starts?'running':'not-started', variants:done?{
        stock:{state:'ready',run_url:'/run?operator_registration_id=build-stock',message:'Compile and image checks passed'},
        instrumented:{state:'blocked',message:'No exact instrumentation patch'}
      }:{stock:{state:starts?'building':'pending'},instrumented:{state:'pending'}}}});
    });
    await page.route('http://dwarf.test/', route => route.fulfill({contentType:'text/html',body:`<input data-version-refresh-token value="secret"><div data-release-prepare data-source-revision="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" data-implementation="amaru" data-version="one" data-digest="sha256:abc"><button data-prepare-release>Download &amp; build</button><output data-prepare-status></output><div data-build-variants></div></div>`}));
    await page.goto('http://dwarf.test/');
    await page.addScriptTag({content:fs.readFileSync('dwarf/dashboard/static/js/release-preparations.js','utf8')});
    await page.locator('[data-prepare-status]').filter({hasText:'Build stock'}).waitFor();
    assert.equal(starts,0);
    await page.locator('[data-prepare-release]').click();
    await page.locator('a').filter({hasText:'Use stock build'}).waitFor();
    assert.equal(starts,1);
    assert.match(await page.locator('[data-build-variants]').textContent(), /No exact instrumentation patch/);
    assert.equal(await page.locator('a').count(),1);
    console.log('PASS: two build states, partial success, explicit click only, exact stock handoff');
  } finally { await browser.close(); }
})().catch(err=>{console.error(err);process.exitCode=1;});
