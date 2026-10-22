const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const state=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-package-test-'));
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,run_plan
def forbidden(*args,**kwargs): raise AssertionError('Package preview invoked execution')
dashboard._cli_command=forbidden
run_service.execute_run=forbidden
run_plan.resolve_run_plan=forbidden
server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.serve_dashboard_handler_factory('package-test-token'))
print(server.server_port,flush=True)
server.serve_forever()
`],{cwd:root,env:{...process.env,PYTHONPATH:'.:dwarf',ADA2_DWARF_STATE_DIR:state,ADA2_PROFILE_MANAGER_CONFIG:path.join(state,'config.yaml'),ADA2_DWARF_RUNS_DIR:path.join(state,'runs'),ADA2_DWARF_BUNDLES_DIR:path.join(state,'bundles')},stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',d=>stderr+=d);
(async()=>{let browser;try{
  const port=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Startup timeout')),20000);
    server.once('error',e=>{clearTimeout(timer);reject(e)});
    server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()))});
  });
  browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='GET')posts.push(new URL(r.url()).pathname)});
  const origin=`http://127.0.0.1:${port}`, control=id=>page.locator('#antithesis-'+id);
  await page.goto(origin+'/run/antithesis');
  assert.equal(await control('preview-package').count(),1);
  await page.waitForFunction(()=>!document.querySelector('#antithesis-resolve-versions').disabled);
  assert.equal(await control('preview-package').isDisabled(),true);
  await control('scenario').selectOption('cardano-node-cbor-block-header-fuzz');
  const generate=async()=>{await control('preview-package').click();await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='incomplete')};
  await generate();
  assert.match(await control('engine-summary').innerText(),/chainsync.*block-header/);
  assert.equal(await control('input-dispositions').locator('[data-field="scenario_id"]').getAttribute('data-status'),'partial');
  for(const field of ['versions','build_variant','measurements','duration_hours','faults']){
    const row=control('input-dispositions').locator(`[data-field="${field}"]`);
    assert.equal(await row.getAttribute('data-status'),'recorded');
    assert.match(await row.innerText(),/Recorded only/);
  }
  await page.getByText('Generated services, images and configuration',{exact:true}).click();
  assert.match(await control('engine-services').innerText(),/dwarf-adversary/);
  for(const width of [1440,390,720]){
    await page.setViewportSize({width,height:1000});
    assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);
    if(process.env.DWARF_CHECKPOINT_SCREENSHOTS){
      await control('engine-summary').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`handoff-${width}.png`)});
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  assert.match(await control('package-issues').innerText(),/assertions/);
  await control('package-file').selectOption('environment/docker-compose.yaml');
  const compose=await control('package-file-content').textContent();
  assert.match(compose,/dwarf-adversary/);assert.doesNotMatch(compose,/decoder-fuzz:/);
  await control('package-file').selectOption('package-manifest.json');
  const manifest=JSON.parse(await control('package-file-content').textContent());
  assert.equal(manifest.execution_ready,false);assert.equal(manifest.request.scenario_id,'cardano-node-cbor-block-header-fuzz');
  assert.equal(manifest.request.token,undefined);
  assert.equal(await control('prepare').isDisabled(),false);assert.equal(await control('submit').isDisabled(),true);
  const download=page.waitForEvent('download');await control('package-download').click();
  const artifact=await download;assert.match(artifact.suggestedFilename(),/^antithesis-preview-.*\.zip$/);
  assert.ok(fs.statSync(await artifact.path()).size>1000);
  await control('duration').fill('2');assert.equal(await control('package-preview').isVisible(),false);
  assert.equal(await control('package-download').getAttribute('href'),null);
  // A reply for an earlier selection cannot restore its package/download.
  const route='**/api/antithesis-run/package/preview';let release,seen;
  const held=new Promise(r=>release=r),captured=new Promise(r=>seen=r);
  await page.route(route,async r=>{const response=await r.fetch();seen();await held;await r.fulfill({response})});
  await control('preview-package').click();await captured;await control('faults').selectOption('on');release();
  await page.waitForLoadState('networkidle');assert.equal(await control('package-preview').isVisible(),false);await page.unroute(route);
  await page.route(route,r=>r.fulfill({status:503,json:{ok:false,error:{message:'Test outage'}}}));
  await control('preview-package').click();await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='error');
  assert.equal(await control('preview-package').isDisabled(),false);await page.unroute(route);await generate();
  await page.route(route,async r=>{
    const response=await r.fetch(),payload=await response.json();delete payload.package.engine_handoff;
    await r.fulfill({json:payload});
  });
  await control('preview-package').click();
  await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='error');
  assert.match(await control('package-state').innerText(),/Invalid engine handoff/);
  assert.equal(await control('package-download').getAttribute('href'),null);
  assert.equal(await control('package-preview').isVisible(),false);
  await page.unroute(route);await generate();
  await page.locator('[name=antithesis-package-source][value=url]').check();
  assert.equal(await control('preview-package').isDisabled(),true);assert.equal(await control('package-preview').isVisible(),false);
  await page.locator('[name=antithesis-package-source][value=build]').check();
  await control('scenario').selectOption('cardano-node-cov-handshake-aflpp-smoke');await generate();
  assert.match(await control('engine-summary').innerText(),/No scenario environment/);
  assert.equal(await control('input-dispositions').locator('[data-field="topology"]').getAttribute('data-status'),'recorded');
  assert.equal(await control('engine-services').locator('li').count(),0);
  assert.match(await control('package-issues').innerText(),/No package adapter/);
  assert.equal(await control('package-file').locator('option[value="environment/docker-compose.yaml"]').count(),0);
  for(const width of [1440,390,720]){await page.setViewportSize({width,height:1000});assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false)}
  const endpoint=origin+'/api/antithesis-run/package/preview';
  assert.equal((await page.request.get(endpoint)).status(),405);
  assert.equal((await page.request.post(endpoint,{data:'x'.repeat(32769)})).status(),413);
  for(const data of ['{"x":NaN}','{"x":1e999}','['.repeat(500)+'0'+']'.repeat(500)])assert.equal((await page.request.post(endpoint,{data})).status(),400);
  assert.deepEqual(fs.readdirSync(state),[]);assert.deepEqual(errors,[]);
  assert.ok(posts.every(p=>p==='/api/antithesis-run/package/preview'));
  console.log('PASS: real package generation/download, incomplete components, source switching, stale replies, retry, HTTP limits, responsive layout, no state writes or execution');
}finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(state,{recursive:true,force:true})}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
