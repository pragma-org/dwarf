const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const rendered = spawnSync('python3', ['-c', `
import os, sys, tempfile, json
sys.path.insert(0, 'tests')
from test_release_image_downloads import candidate_catalog
from profile_manager import version_discovery
from profile_manager.data import operate_versions
from profile_manager import dashboard
os.environ['ADA2_DWARF_RUNTIME_ROOT'] = tempfile.mkdtemp()
os.environ['DWARF_HOST_ARCH'] = 'amd64'
version_discovery.load_effective_version_catalog = candidate_catalog
operate_versions.load_effective_version_catalog = candidate_catalog
print(json.dumps({'versions': dashboard.render_route_html('/operate/versions', token='test-token'),
                  'run': dashboard.render_route_html('/run', token='test-token')}))
`], {cwd: root, env: {...process.env, PYTHONPATH: 'dwarf'}, encoding: 'utf8', maxBuffer: 16*1024*1024});
assert.equal(rendered.status, 0, rendered.stderr);
const pages = JSON.parse(rendered.stdout);
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    let started = 0, polls = 0, runMutations = 0;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://dwarf.test/**', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, 'dwarf/dashboard', url.pathname);
        if (fs.existsSync(file)) return route.fulfill({path:file});
        return route.fulfill({status:404,body:''});
      }
      if (url.pathname.startsWith('/api/versions/download')) {
        assert.equal(req.method(), 'POST');
        assert(!url.search.includes('token'));
        const body = new URLSearchParams(req.postData());
        assert.equal(body.get('token'), 'test-token');
        assert.match(body.get('digest'), /^sha256:[0-9a-f]{64}$/);
        let state = 'missing';
        if (body.get('version') === '10.11.20261029') {
          if (url.pathname.endsWith('/download')) {started++; state='running';}
          else if (started) {polls++; state=polls>1?'available':'running';}
        }
        return route.fulfill({json:{state, run_url:'/run?version_policy=exact&amaru_version=10.11.20261029&release_digest=sha256:'+'a'.repeat(64)}});
      }
      if (url.pathname.startsWith('/api/')) {
        if (url.pathname.includes('prepare') || url.pathname.includes('start')) runMutations++;
        return route.fulfill({status:409,json:{ok:false,error:{message:'Select a scenario',field:'scenario_id'}}});
      }
      return route.fulfill({contentType:'text/html',body:url.pathname==='/run'?pages.run:pages.versions});
    });
    await page.goto('http://dwarf.test/operate/versions');
    const row = page.locator('[data-release-download][data-version="10.11.20261029"]');
    await row.locator('[data-download-image]').waitFor();
    assert.equal(started, 0, 'viewing Versions must never download');
    await row.locator('[data-download-image]').click();
    assert(await row.locator('[data-download-image]').isDisabled());
    await row.locator('[data-use-release]').waitFor({state:'visible',timeout:10000});
    assert.equal(started, 1);
    assert.match(await row.locator('[data-download-status]').textContent(), /Available locally/);
    await page.reload();
    await row.locator('[data-use-release]').waitFor({state:'visible'});
    assert.equal(started, 1, 'reload must follow the retained job');
    await row.locator('[data-use-release]').click();
    await page.waitForURL('**/run?**');
    await page.waitForFunction(() => document.querySelector('#run-amaru-version')?.value==='10.11.20261029');
    assert.equal(await page.locator('#run-version-policy').inputValue(), 'exact');
    assert.equal(runMutations, 0, 'handoff must not prepare or start a topology');
    await page.goto('http://dwarf.test/run?amaru_version=does-not-exist');
    await page.locator('[data-run-errors]').waitFor({state:'visible'});
    assert.match(await page.locator('[data-run-errors]').textContent(), /requested release/i);
    assert(await page.locator('[data-run-action="prepare"]').first().isDisabled());
    await page.goto('http://dwarf.test/run?amaru_version=10.11.20261029&release_digest=sha256:'+'c'.repeat(64));
    await page.locator('[data-run-errors]').waitFor({state:'visible'});
    assert.match(await page.locator('[data-run-errors]').textContent(), /image has changed/);
    assert.deepEqual(errors, []);
    console.log('PASS: download, progress, reload, exact version handoff, no topology mutation');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
