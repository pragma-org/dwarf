const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-image-build-'));
const control=path.join(temp,'control');fs.writeFileSync(control,'ok');
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,run_plan,remote,antithesis_image_build,antithesis_publication
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
    if argv[1] in ('tag','push'):return ''
    assert argv[1:3]==['image','inspect']
    return json.dumps([{'Id':'sha256:'+'a'*64,'Os':'linux','Architecture':'amd64'}])
antithesis_image_build._docker=docker
def verify_image(reference,expected_id=None):
    if Path(os.environ['BUILD_TEST_CONTROL']).read_text()=='private':
        from profile_manager.antithesis_package import PackageError
        raise PackageError('Image is private. Make the package public first.')
    return dict(reference=reference.split('@')[0].split(':')[0]+'@sha256:'+'c'*64,platform='linux/amd64',anonymous=True,expected_identity_matched=bool(expected_id))
def verify_git(repository,commit,directory,files):
    return dict(repository=repository,commit=commit,directory=directory,file_count=len(files),anonymous=True)
antithesis_publication.verify_image=verify_image;antithesis_publication.verify_git=verify_git
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
 assert.equal(await get('publication-create').count(),1,'publication handoff controls must exist');
 await get('publication-prefix').fill('ghcr.io/example/dwarf');
 const waitPublication=state=>page.waitForFunction(s=>document.querySelector('#antithesis-publication-state').dataset.state===s,state);
 await get('publication-create').click();await waitPublication('planned');
 assert.equal(posts.some(p=>p.endsWith('/publication-action')),false,'creating a handoff must not push');
 let lostOperation;
 await page.route('**/api/antithesis-run/package/publication-action',async route=>{
   const response=await route.fetch();lostOperation=(await response.json()).operation.id;await route.abort('failed');
 });
 await get('publication-push').click();await waitPublication('error');
 await page.unroute('**/api/antithesis-run/package/publication-action');
 await get('publication-resume').click();await waitPublication('awaiting-visibility');
 assert.equal(await get('publication-push').isDisabled(),true);
 fs.writeFileSync(control,'private');await get('publication-verify-images').click();await waitPublication('error');
 assert.equal(await get('publication-download').isVisible(),false);
 fs.writeFileSync(control,'ok');await get('publication-verify-images').click();await waitPublication('images-public');
 const download=page.waitForEvent('download');await get('publication-download').click();assert.match((await download).suggestedFilename(),/^antithesis-public-/);
 await get('publication-repository').fill('Example/dwarf');await get('publication-commit').fill('e'.repeat(40));
 await get('publication-directory').fill('antithesis/example');await get('publication-requester').fill('tester');
 await get('publication-verify-git').click();await waitPublication('review');
 assert.match(await get('publication-request').textContent(),/requester create-test/);
 assert.equal(await get('submit').isDisabled(),true);
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);if(process.env.DWARF_CHECKPOINT_SCREENSHOTS){await get('publication-verify-git').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`publication-${width}.png`)});}}
 await page.setViewportSize({width:1440,height:1000});
 let releaseGit,seenGit;const holdGit=new Promise(r=>releaseGit=r),capturedGit=new Promise(r=>seenGit=r);
 await page.route('**/api/antithesis-run/package/publication-status',async route=>{const response=await route.fetch();seenGit();await holdGit;await route.fulfill({response})});
 await get('publication-verify-git').click();await capturedGit;
 await page.locator('#antithesis-settings-form [name=github_user]').evaluate(n=>{n.value='another-user';n.dispatchEvent(new Event('input',{bubbles:true}))});
 releaseGit();await page.waitForFunction(()=>!document.querySelector('#antithesis-publication-verify-git').disabled);assert.equal(await get('publication-request-result').isVisible(),false,'settings edits must invalidate a pending request preview');
 await page.unroute('**/api/antithesis-run/package/publication-status');
 await get('publication-commit').fill('f'.repeat(40));assert.equal(await get('publication-request-result').isVisible(),false);
 await get('publication-prefix').fill('ghcr.io/example/other');assert.equal(await get('publication-push').isDisabled(),true);
 assert.equal(await get('publication-download').isVisible(),false);
 await get('version-amaru').selectOption('10.11.20260925');assert.equal(await get('publication-create').isDisabled(),true);
 await get('scenario').selectOption('cardano-node-cbor-block-header-fuzz');await get('policy').selectOption('profile-default');await prepare();
 assert.equal(await get('build-images').isDisabled(),false,'zero-build packages need a saved image receipt path');
 await get('build-images').click();await waitState('not-required');
 await get('publication-create').click();await waitPublication('planned');assert.equal(await get('publication-push').isDisabled(),true);
 await get('publication-verify-images').click();await waitPublication('images-public');
 assert.equal(fs.existsSync(path.join(temp,'runs')),false);assert.deepEqual(errors,[]);
 assert.ok(posts.every(p=>p.startsWith('/api/antithesis-run/')));
 console.log('PASS: explicit publication, no implicit push, lost-response recovery, manual visibility/retry, download, Git/MOOG preview, stale inputs, mobile layout, no Local execution/submission');
}finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true})}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
