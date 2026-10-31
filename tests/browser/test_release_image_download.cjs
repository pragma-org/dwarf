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
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
ledger = OperatorVersionDecisionLedger()
ledger.append_registration({'registration_id':'registration-test-build','implementation':'amaru',
  'version':'10.11.20261029','image_reference':'sha256:'+'b'*64,'config_image_id':'sha256:'+'b'*64,
  'manifest_digest':None,'executable_digest':'sha256:'+'c'*64,'platform':'linux/amd64',
  'runtime_interface':'extracted-binary','controller_installation_id':'test',
  'inspected_at':'2026-10-31T12:00:00Z',
  'provenance':{'source_revision':'d'*40,'qualification':'unverified','public':None}},
  expected_revision=ledger.read()['revision'])
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
    let started = 0, polls = 0, runMutations = 0, initialCheckFailed = false;
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
        let state = body.get('version') === '11.1.2' ? 'available' : 'missing';
        if (body.get('version') === '10.11.20261029' && !initialCheckFailed) {
          initialCheckFailed = true;
          return route.fulfill({status:409,json:{error:'Controller is unavailable'}});
        }
        if (body.get('version') === '10.11.20261029') {
          if (url.pathname.endsWith('/download')) {started++; state='running';}
          else if (started) {polls++; state=polls>1?'available':'running';}
        }
        return route.fulfill({json:{state, run_url:'/run?version_policy=exact&amaru_version=10.11.20261029&release_digest=sha256:'+'a'.repeat(64)}});
      }
      if (url.pathname === '/api/versions/prepare/status') return route.fulfill({json:{state:'not-started',variants:{}}});
      if (url.pathname.startsWith('/api/')) {
        if (url.pathname.includes('prepare') || url.pathname.includes('start')) runMutations++;
        return route.fulfill({status:409,json:{ok:false,error:{message:'Select a scenario',field:'scenario_id'}}});
      }
      return route.fulfill({contentType:'text/html',body:url.pathname==='/run'?pages.run:pages.versions});
    });
    await page.goto('http://dwarf.test/operate/versions');
    const row = page.locator('[data-release-download][data-version="10.11.20261029"]');
    await row.locator('[data-download-image]').waitFor();
    await row.locator('[data-download-image]').filter({hasText:'Retry'}).waitFor();
    assert.match(await row.locator('[data-download-status]').textContent(), /Couldn’t check availability/);
    await row.locator('[data-download-image]').click();
    await row.locator('[data-download-status]').filter({hasText:'Not installed locally.'}).waitFor();
    const installed = page.locator('[data-release-download][data-version="11.1.2"]');
    await installed.locator('[data-use-release]').waitFor({state:'visible'});
    assert(await installed.locator('[data-download-image]').isHidden());
    assert.match(await installed.locator('[data-download-status]').textContent(), /Available locally/);
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
    await page.goto('http://dwarf.test/run?operator_registration_id=registration-test-build&build_image=sha256:'+'b'.repeat(64));
    await page.waitForFunction(() => document.querySelector('#run-operator-registration')?.value === 'registration-test-build');
    assert.equal(runMutations, 0);
    await page.goto('http://dwarf.test/run?operator_registration_id=registration-test-build&build_image=sha256:'+'e'.repeat(64));
    await page.locator('[data-run-errors]').waitFor({state:'visible'});
    assert.match(await page.locator('[data-run-errors]').textContent(), /requested build/);
    assert.deepEqual(errors, []);
    console.log('PASS: actual availability, read-only retry, download, progress, reload, exact version handoff, no topology mutation');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
