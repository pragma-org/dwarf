const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dwarf-version-test-'));
const server = spawn('python3', ['-u', '-c', `
from http.server import ThreadingHTTPServer
from profile_manager import dashboard, run_service, run_plan
from profile_manager.data import operate_run_wizard
def forbidden(*args, **kwargs):
    raise AssertionError('Remote version assessment must not invoke Local execution')
dashboard._cli_command = forbidden
run_service.execute_run = forbidden
run_plan.resolve_run_plan = forbidden
operate_run_wizard.resolve_run_plan = forbidden
server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.serve_dashboard_handler_factory('version-test-token'))
print(server.server_port, flush=True)
server.serve_forever()
`], {cwd: root, env: {...process.env, PYTHONPATH: '.:dwarf',
  ADA2_PROFILE_MANAGER_CONFIG: path.join(stateDir, 'config.yaml'),
  ADA2_DWARF_STATE_DIR: stateDir, ADA2_DWARF_RUNS_DIR: path.join(stateDir, 'runs'),
  ADA2_DWARF_BUNDLES_DIR: path.join(stateDir, 'bundles')}, stdio: ['ignore','pipe','pipe']});
let stderr = '';
server.stderr.on('data', data => {stderr += data;});
(async () => {
  let browser;
  try {
    const port = await new Promise((resolve,reject) => {
      const timer=setTimeout(()=>reject(new Error('Startup timeout')),20000);
      server.once('error',error=>{clearTimeout(timer);reject(error)});
      server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`))});
      server.stdout.once('data',data=>{clearTimeout(timer);resolve(Number(data.toString().trim()))});
    });
    const origin=`http://127.0.0.1:${port}`;
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[], requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.method()!=='GET')requests.push(new URL(request.url()).pathname)});
    const control=name=>page.locator('#antithesis-'+name);
    const waitState=expected=>page.waitForFunction(expected=>document.querySelector('#antithesis-versions-state').dataset.state===expected,expected);
    const resolve=async expected=>{await control('resolve-versions').click();await waitState(expected)};
    const result=async()=>JSON.parse(await control('version-details').textContent());
    await page.goto(origin+'/run/antithesis');
    await page.waitForFunction(()=>!document.querySelector('#antithesis-resolve-versions').disabled);
    await control('policy').selectOption('exact');
    await control('version-amaru').selectOption('10.11.20260918');
    await resolve('pending');
    assert.equal((await result()).releases[0].source_revision,'aedfe797a5b8ef00d8b362be40b47a52c3b4a379');
    assert.equal((await result()).service_bindings.length,0);
    assert.equal((await result()).execution_ready,false);
    // Resolve the existing remote topology, then inspect target/support roles.
    await page.locator('#antithesis-topology-override > summary').click();
    await page.locator('[name=topology-source][value=new]').check();
    await control('config-name').fill('Version inspection');
    await control('template').selectOption('amaru-control-v1');
    await control('resolve-topology').click();
    await page.waitForFunction(()=>document.querySelector('#antithesis-topology-state').dataset.state==='resolved');
    await resolve('pending');
    const bindings=(await result()).service_bindings;
    assert.equal(bindings.length,14);
    assert.equal(bindings.filter(s=>s.requested_release).length,2);
    assert.ok(bindings.filter(s=>s.implementation==='cardano-node').every(s=>s.requested_release===null));
    assert.equal(await control('prepare').isDisabled(),true);
    assert.equal(await control('submit').isDisabled(),true);
    await control('policy').selectOption('latest-confirmed');
    await resolve('pending');
    assert.equal((await result()).release_resolution.qualification.source,'local-version-catalog');
    assert.equal((await result()).release_resolution.qualification.status,'confirmed');
    assert.equal((await result()).remote_qualification,'unverified');
    assert.equal((await result()).releases.length,1);
    assert.ok((await result()).issues.some(i=>i.code==='local-qualification-context'));
    assert.ok((await control('policy').locator('option:checked').textContent()).includes('Local catalog default'));
    await control('policy').selectOption('latest-stable');
    await resolve('pending');
    assert.equal((await result()).releases.length,1);
    await control('policy').selectOption('exact');
    // Late responses cannot restore stale choices.
    let release,seen;
    const held=new Promise(resolve=>release=resolve), captured=new Promise(resolve=>seen=resolve);
    const url='**/api/antithesis-run/versions/resolve';
    await page.route(url,async route=>{const response=await route.fetch();seen();await held;await route.fulfill({response})});
    await control('resolve-versions').click();await captured;
    await control('version-amaru').selectOption('10.11.0');
    release();await page.waitForLoadState('networkidle');
    assert.equal(await control('versions-preview').isVisible(),false);
    assert.equal(await page.locator('[data-antithesis-wizard]').getAttribute('data-version-assessment-digest'),null);
    await page.unroute(url);
    // Server failure keeps choices and allows retry.
    await page.route(url,route=>route.fulfill({status:503,json:{ok:false,error:{message:'Test outage'}}}));
    await resolve('error');
    assert.equal(await control('version-amaru').inputValue(),'10.11.0');
    await page.unroute(url);await resolve('pending');
    // Catalog reload must preserve a newer edit made while the response was pending.
    let releaseCatalog,catalogSeen;
    const hold=new Promise(resolve=>releaseCatalog=resolve), catalogCaptured=new Promise(resolve=>catalogSeen=resolve);
    const catalogURL='**/api/antithesis-run/versions/catalog';
    await page.route(catalogURL,async route=>{const response=await route.fetch();catalogSeen();await hold;await route.fulfill({response})});
    await control('reload-versions').click();await catalogCaptured;
    await control('version-amaru').selectOption('10.11.20260918');
    releaseCatalog();await page.waitForFunction(()=>!document.querySelector('#antithesis-resolve-versions').disabled);
    assert.equal(await control('version-amaru').inputValue(),'10.11.20260918');
    await page.unroute(catalogURL);await resolve('pending');
    for(const width of [1440,390,720]){
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.locator('#run-stage-versions').evaluate(node=>node.scrollWidth>node.clientWidth),false);
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#run-stage-versions').screenshot({path:'/tmp/dwarf-version-assessment.png'});
    await control('reset-topology').click();
    assert.equal(await control('versions-preview').isVisible(),false);
    assert.equal(await page.locator('[data-antithesis-wizard]').getAttribute('data-version-assessment-digest'),null);
    assert.equal((await page.request.get(origin+'/api/antithesis-run/versions/resolve')).status(),405);
    assert.equal((await page.request.post(origin+'/api/antithesis-run/versions/resolve',{data:'x'.repeat(16385)})).status(),413);
    assert.equal((await page.request.post(origin+'/api/antithesis-run/versions/resolutions',{data:{}})).status(),404);
    const invalidContext=JSON.stringify({...(await result()).requested,topology:{source:'local-profile',profile_id:'PLACEHOLDER'}});
    for(const data of ['{"x":'+ '1'.repeat(5000)+'}', '{"x":NaN}', '['.repeat(2000)+']'.repeat(2000),
      ...['1e999','-1e999','['.repeat(500)+'0'+']'.repeat(500)].map(value=>invalidContext.replace('"PLACEHOLDER"',value))]){
      const response=await page.request.post(origin+'/api/antithesis-run/versions/resolve',{data});
      assert.equal(response.status(),400);assert.equal((await response.json()).ok,false);
    }
    assert.deepEqual(fs.readdirSync(stateDir),[]);
    assert.ok(requests.every(url=>['/api/antithesis-run/versions/resolve','/api/antithesis-run/topology/resolve'].includes(url)));
    assert.deepEqual(errors,[]);
    console.log('PASS: real HTTP version resolution, shared confirmed default with Local evidence label, exact releases, target/support separation, stale responses, errors/retry, catalog race, responsive layout, no state writes or Local execution');
  } finally {
    if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(stateDir,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error,stderr);process.exitCode=1});
