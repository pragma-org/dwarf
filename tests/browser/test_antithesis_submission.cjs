const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-submit-'));
const source=path.join(temp,'package');fs.mkdirSync(source);
fs.writeFileSync(path.join(source,'docker-compose.yaml'),'services:\n  node:\n    image: ghcr.io/example/node:experimental\n    container_name: node\n    init: true\n');
const control=path.join(temp,'control');fs.writeFileSync(control,'pending');
const calls=path.join(temp,'calls');
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,run_plan,remote,antithesis_submission as s
from profile_manager.config import DeploymentConfig
from pathlib import Path
import os,json,time
run_service.execute_run=lambda *a,**k:(_ for _ in ()).throw(AssertionError('LOCAL execution'))
run_plan.resolve_run_plan=lambda *a,**k:(_ for _ in ()).throw(AssertionError('LOCAL planning'))
config=DeploymentConfig.from_dict({'moog':s.moog.normalize_moog_config({'token_id':'test-token'})})
s.load_settings=lambda:config
s.verify_git=lambda *a:{'anonymous':True}
s.verify_image=lambda *a:{'anonymous':True,'platform':'linux/amd64'}
def query(*a):
    time.sleep(.15)
    return {'checks':[{'id':'requester','state':'ok','detail':'Requester verified'}],'facts':[]}
s.query_requester=query
def submit(*a,**k):
    with open(os.environ['SUBMIT_CALLS'],'a') as f:f.write(json.dumps(k)+'\\n')
    time.sleep(.15)
    return remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'TOP_SECRET','TOP_SECRET')
remote.run_moog_create_test=submit
def facts(config,t):
    phase=Path(os.environ['SUBMIT_CONTROL']).read_text()
    if phase=='error':raise RuntimeError('TOP_SECRET')
    return [{'id':'f'*64,'key':{'type':'test-run','platform':'github','repository':dict(zip(['organization','repo'],t['repository'].split('/'))),'requester':t['requester'],'commitId':t['commit'],'directory':t['directory'],'try':t['try_number']},'value':{'phase':phase,'outcome':'failure' if phase=='finished' else None,'url':'https://secret/?auth=TOP_SECRET'}}]
s.query_facts=facts
server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.serve_dashboard_handler_factory('submission-test'))
print(server.server_port,flush=True);server.serve_forever()
`],{cwd:root,env:{...process.env,PYTHONPATH:'.:dwarf',SUBMIT_CONTROL:control,SUBMIT_CALLS:calls,ADA2_DWARF_STATE_DIR:path.join(temp,'state'),ADA2_DWARF_RUNS_DIR:path.join(temp,'runs'),ADA2_DWARF_BUNDLES_DIR:path.join(temp,'bundles'),ADA2_PROFILE_MANAGER_CONFIG:path.join(temp,'config.yaml')},stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',x=>stderr+=x);
(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timeout')),15000);server.stdout.once('data',d=>{clearTimeout(t);resolve(Number(d.toString().trim()))});server.once('error',reject)});
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[],posts=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname)});
 const get=id=>page.locator('#antithesis-'+id);
 await page.goto(`http://127.0.0.1:${port}/run/antithesis`);
 assert.equal(await get('submission-check').count(),1,'readiness action exists');
 await page.waitForFunction(()=>document.querySelector('#antithesis-scenario').options.length>1);
 await get('scenario').selectOption('cardano-amaru-miniprotocol-security-local');
 await page.locator('[name="antithesis-package-source"][value="existing"]').check();await get('compose-path').fill(source);
 await get('prepare').click();await page.waitForFunction(()=>document.querySelector('#antithesis-preparation-state').dataset.state==='staged');
 for(const [name,value] of Object.entries({repository:'Example/dwarf',commit:'e'.repeat(40),directory:'antithesis/example',requester:'tester'}))await get('publication-'+name).fill(value);
 const state=async value=>page.waitForFunction(v=>document.querySelector('#antithesis-submission-state').dataset.state===v,value);
 const check=async()=>{await get('submission-check').click();await state('ready');};
 await get('publication-commit').fill('invalid');await get('submission-check').click();await state('error');
 await get('publication-commit').fill('e'.repeat(40));assert.equal(await get('submission-check').isDisabled(),false);
 // A lost check that never reached the server is recoverable as definitively absent.
 await page.route('**/api/antithesis-run/package/readiness',route=>route.abort('failed'));
 await get('submission-check').click();await state('error');
 await page.unroute('**/api/antithesis-run/package/readiness');
 await get('submission-recover').click();await state('missing');
 assert.equal(await get('submission-check').isDisabled(),false);
 // Hold a readiness response while the target changes.
 let release,received;const held=new Promise(resolve=>release=resolve),arrived=new Promise(resolve=>received=resolve);
 await page.route('**/api/antithesis-run/package/readiness',async route=>{const response=await route.fetch();received();await held;await route.fulfill({response})});
 await get('submission-check').click();await arrived;await get('publication-try').fill('3');release();await state('stale');
 assert.equal(await get('submit').isDisabled(),true);await page.unroute('**/api/antithesis-run/package/readiness');
 await get('publication-try').fill('1');
 await check();assert.equal(fs.existsSync(calls),false,'readiness cannot submit');
 assert.equal(await get('submit').isDisabled(),false);
 const oldCheck=await get('submission-lookup').inputValue();
 // A settings save must not be bypassed by recovering old ready status.
 await page.locator('[data-antithesis-wizard]').evaluate(n=>n.dispatchEvent(new CustomEvent('antithesis-settings-updated',{bubbles:true})));
 assert.equal(await get('submit').isDisabled(),true);
 await get('submission-recover').click();await state('stale');assert.equal(await get('submit').isDisabled(),true);
 await check();await get('submission-lookup').fill(oldCheck);
 await get('submission-recover').click();await state('stale');assert.equal(await get('submit').isDisabled(),true,'fresh check cannot authorize a different recovered check');
 await get('publication-try').fill('2');assert.equal(await get('submit').isDisabled(),true);await check();
 // Lose the acknowledgement after the server accepts the explicit click.
 await page.route('**/api/antithesis-run/package/submit',async route=>{await route.fetch();await route.abort('failed')});
 await get('submit').click();await state('error');assert.equal(await get('submit').isDisabled(),true);
 await page.unroute('**/api/antithesis-run/package/submit');
 await get('submission-recover').click();await state('submitted');
 const row=JSON.parse(await get('submission-receipt').textContent());
 assert.equal(row.test_run_id,'f'.repeat(64));assert.equal(fs.readFileSync(calls,'utf8').trim().split('\n').length,1);
 await get('submission-refresh').click();await state('pending');
 fs.writeFileSync(control,'running');await get('submission-refresh').click();await state('running');
 fs.writeFileSync(control,'finished');await get('submission-refresh').click();await state('completed');
 assert.match(await get('submission-state').textContent(),/findings/);
 fs.writeFileSync(control,'error');await get('submission-refresh').click();await state('completed');
 assert.match(await get('submission-state').textContent(),/refresh failed/i);
 assert.equal((await page.locator('body').innerText()).includes('TOP_SECRET'),false);
 await page.reload();assert.equal(await get('submission-lookup').inputValue(),row.id);
 await get('submission-recover').click();await state('completed');assert.equal(await get('submit').isDisabled(),true);
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await get('submission-state').scrollIntoViewIfNeeded();assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);if(process.env.DWARF_CHECKPOINT_SCREENSHOTS)await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`submission-${width}.png`)});}
 assert.equal(fs.readFileSync(calls,'utf8').trim().split('\n').length,1);assert.deepEqual(errors,[]);assert.ok(posts.every(p=>p.startsWith('/api/antithesis-run/')));
 console.log('PASS: readiness, changed target, one explicit submission, lost-response/reload recovery, pending/running/completed-with-findings, refresh failure, mobile; no Local execution');
}finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true})}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
