const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-image-build-'));
const control=path.join(temp,'control');fs.writeFileSync(control,'ok');
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,run_plan,remote,antithesis_image_build
from pathlib import Path
import json,time,os
def forbidden(*args,**kwargs):raise AssertionError('Execution invoked')
run_service.execute_run=forbidden;run_plan.resolve_run_plan=forbidden;remote.run_moog_create_test=forbidden
def docker(argv,**kwargs):
    assert argv[0]=='docker'
    if argv[1]=='build':
        time.sleep(.2)
        if Path(os.environ['BUILD_TEST_CONTROL']).read_text()=='fail':
            raise antithesis_image_build.BuildFailure('Docker operation failed (exit 7).')
        return ''
    assert argv[1:3]==['image','inspect']
    return json.dumps([{'Id':'sha256:'+'a'*64,'Os':'linux','Architecture':'amd64'}])
antithesis_image_build._docker=docker
server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.serve_dashboard_handler_factory('build-test'))
print(server.server_port,flush=True);server.serve_forever()
`],{cwd:root,env:{...process.env,PYTHONPATH:'.:dwarf',BUILD_TEST_CONTROL:control,
ADA2_DWARF_STATE_DIR:path.join(temp,'state'),ADA2_DWARF_RUNS_DIR:path.join(temp,'runs'),ADA2_DWARF_BUNDLES_DIR:path.join(temp,'bundles'),ADA2_PROFILE_MANAGER_CONFIG:path.join(temp,'config.yaml')},stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',d=>stderr+=d);
(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timed out')),15000);server.stdout.once('data',d=>{clearTimeout(t);resolve(Number(d.toString().trim()))});server.once('error',reject)});
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname)});
 const get=id=>page.locator('#antithesis-'+id);await page.goto(`http://127.0.0.1:${port}/run/antithesis`);
 await page.waitForFunction(()=>document.querySelector('#antithesis-scenario').options.length>1);
 assert.equal(await get('build-images').count(),1,'dedicated explicit build action must exist');
 assert.equal(await get('build-images').isDisabled(),true);
 await get('scenario').selectOption('cardano-amaru-miniprotocol-security-local');
 await get('policy').selectOption('exact');await get('version-cardano-node').selectOption('10.6.2');await get('version-amaru').selectOption('10.11.20260918');
 const prepare=async()=>{await get('prepare').click();await page.waitForFunction(()=>document.querySelector('#antithesis-preparation-state').dataset.state==='staged')};
 const waitState=async state=>page.waitForFunction(state=>document.querySelector('#antithesis-image-build-state').dataset.state===state,state);
 await prepare();assert.equal(await get('build-images').isDisabled(),false);
 assert.equal(posts.some(p=>p.endsWith('/build')),false,'staging must not build');
 const original=JSON.parse(await get('preparation-receipt').textContent());
 await get('build-images').click();await waitState('built');
 const receipt=JSON.parse(await get('image-build-receipt').textContent());
 assert.equal(receipt.preparation_id,original.id);assert.deepEqual(receipt.version_binding,original.version_binding);
 assert.equal(receipt.built_images.length,1);assert.equal(receipt.submission_ready,false);
 assert.equal(await get('submit').isDisabled(),true);
 const download=page.waitForEvent('download');await get('image-build-download').click();assert.match((await download).suggestedFilename(),/^antithesis-built-/);
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);if(process.env.DWARF_CHECKPOINT_SCREENSHOTS){await get('image-build-summary').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`image-build-${width}.png`)});}}
 await page.setViewportSize({width:1440,height:1000});
 await prepare();assert.equal(await get('image-build-result').isVisible(),false);
 let lostJob;
 await page.route('**/api/antithesis-run/package/build',async route=>{
   const response=await route.fetch();lostJob=(await response.json()).build.id;await route.abort('failed');
 });
 await get('build-images').click();await waitState('error');
 await page.unroute('**/api/antithesis-run/package/build');await page.waitForTimeout(400);
 await get('build-images').click();await waitState('built');
 assert.equal(JSON.parse(await get('image-build-receipt').textContent()).id,lostJob,'lost response must recover original job');
 await prepare();
 fs.writeFileSync(control,'fail');await get('build-images').click();await waitState('failed');
 assert.equal(await get('image-build-result').isVisible(),false);assert.equal(await get('build-images').isDisabled(),false);
 fs.writeFileSync(control,'ok');
 let release,seen;const held=new Promise(r=>release=r),captured=new Promise(r=>seen=r);
 await page.route('**/api/antithesis-run/package/build-status',async route=>{const response=await route.fetch();seen();await held;await route.fulfill({response})});
 await get('build-images').click();await captured;
 await get('version-amaru').selectOption('10.11.20260925');release();await page.waitForTimeout(500);
 assert.equal(await get('image-build-result').isVisible(),false);assert.equal(await get('build-images').isDisabled(),true);
 await page.unroute('**/api/antithesis-run/package/build-status');
 await page.locator('[name=antithesis-package-source][value=existing]').check();assert.equal(await get('build-images').isDisabled(),true);
 assert.equal(fs.existsSync(path.join(temp,'runs')),false);assert.deepEqual(errors,[]);
 assert.ok(posts.every(p=>p.startsWith('/api/antithesis-run/')));
 console.log('PASS: explicit build, immutable selections, progress/result/download, failure/retry, lost-response recovery, stale results, repeated prepare, imported-source exclusion, mobile layout, no Local execution/submission');
}finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true})}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
