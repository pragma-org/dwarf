const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-preflight-'));
const supplied=path.join(temp,'supplied');fs.mkdirSync(supplied);
fs.writeFileSync(path.join(supplied,'docker-compose.yaml'),'services:\n  node:\n    image: example/node:experiment\n    container_name: node\n    init: true\n');
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,run_plan,remote,antithesis_preparation,antithesis_image_build
import io,zipfile,os,json,shutil
from pathlib import Path
def forbidden(*a,**k):raise AssertionError('Execution invoked')
run_service.execute_run=forbidden;run_plan.resolve_run_plan=forbidden;remote.run_moog_create_test=forbidden
archive=io.BytesIO()
with zipfile.ZipFile(archive,'w') as z:z.writestr('docker-compose.yaml',(Path(os.environ['PREFLIGHT_SOURCE'])/'docker-compose.yaml').read_bytes())
antithesis_preparation._download=lambda url:archive.getvalue()
def docker(argv,**kwargs):
    if argv[1:3]==['image','inspect']:return json.dumps([{'Id':'sha256:'+'a'*64,'Os':'linux','Architecture':'amd64'}])
    if argv[1] in ('build','create','rm','image','compose'):return ''
    if argv[1]=='cp':
        source=Path(kwargs['cwd'])/argv[2].split(':/',1)[1];target=Path(argv[3])
        if source.is_dir():shutil.copytree(source,target)
        else:shutil.copy2(source,target)
        return ''
    raise AssertionError('Unexpected Docker command')
antithesis_image_build._docker=docker
server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.serve_dashboard_handler_factory('validation-test'))
print(server.server_port,flush=True);server.serve_forever()
`],{cwd:root,env:{...process.env,PYTHONPATH:'.:dwarf',PREFLIGHT_SOURCE:supplied,
ADA2_DWARF_STATE_DIR:path.join(temp,'state'),ADA2_DWARF_RUNS_DIR:path.join(temp,'runs'),ADA2_DWARF_BUNDLES_DIR:path.join(temp,'bundles'),ADA2_PROFILE_MANAGER_CONFIG:path.join(temp,'config.yaml')},stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',d=>stderr+=d);
(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timed out')),15000);server.stdout.once('data',d=>{clearTimeout(t);resolve(Number(d.toString().trim()))});server.once('error',reject)});
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname)});
 const get=id=>page.locator('#antithesis-'+id);await page.goto(`http://127.0.0.1:${port}/run/antithesis`);
 await page.waitForFunction(()=>document.querySelector('#antithesis-scenario').options.length>1);
 assert.equal(await get('validation-static').count(),1,'explicit package validation control must exist');
 assert.equal(await get('validation-static').isDisabled(),true);
 await get('scenario').selectOption('cardano-amaru-miniprotocol-security-local');
 await get('policy').selectOption('exact');await get('version-cardano-node').selectOption('10.6.2');await get('version-amaru').selectOption('10.11.20260918');
 const prepare=async()=>{await get('prepare').click();await page.waitForFunction(()=>document.querySelector('#antithesis-preparation-state').dataset.state==='staged')};
 const validate=async()=>{await get('validation-static').click();await page.waitForFunction(()=>document.querySelector('#antithesis-validation-state').dataset.state==='complete')};
 await prepare();await validate();
 let result=JSON.parse(await get('validation-receipt').textContent());
 assert.equal(result.report.state,'blocked');assert.ok(result.report.checks.some(r=>r.code==='image-build'&&r.state==='fail'));
 assert.equal(await get('submit').isDisabled(),true);
 await get('build-images').click();await page.waitForFunction(()=>document.querySelector('#antithesis-image-build-state').dataset.state==='built');
 const built=JSON.parse(await get('image-build-receipt').textContent());await get('validation-source').selectOption('build:'+built.id);await validate();
 result=JSON.parse(await get('validation-receipt').textContent());assert.equal(result.source.kind,'build');assert.ok(!result.report.checks.some(r=>r.code==='image-build'));
 // Each source continues through the same saved-package validator.
 const source=page.locator('[name="antithesis-package-source"][value="existing"]');
 await source.check();await get('compose-path').fill(supplied);await prepare();
 let lost;await page.route('**/api/antithesis-run/package/validate',async route=>{const response=await route.fetch();lost=(await response.json()).validation.id;await route.abort('failed')});
 await get('validation-static').click();await page.waitForFunction(()=>document.querySelector('#antithesis-validation-state').dataset.state==='error');
 await page.unroute('**/api/antithesis-run/package/validate');await get('validation-recover').click();
 await page.waitForFunction(()=>document.querySelector('#antithesis-validation-state').dataset.state==='complete');
 result=JSON.parse(await get('validation-receipt').textContent());assert.equal(result.id,lost);assert.equal(result.report.state,'review');
 assert.ok(result.report.checks.some(r=>r.code==='setup-event'&&r.state==='not_checked'));
 await get('validation-artifacts').click();await page.waitForFunction(()=>document.querySelector('#antithesis-validation-state').dataset.state==='complete');
 result=JSON.parse(await get('validation-receipt').textContent());assert.equal(result.mode,'artifacts');assert.ok(result.report.checks.some(r=>r.code==='candidate-files'&&r.state==='pass'));
 await page.locator('[name="antithesis-package-source"][value="url"]').check();await get('package-url').fill('https://example.org/scenario.zip');
 assert.equal(await get('validation-result').isVisible(),false);await prepare();await validate();
 assert.equal(JSON.parse(await get('validation-receipt').textContent()).report.state,'review');
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await get('validation-static').scrollIntoViewIfNeeded();assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);if(process.env.DWARF_CHECKPOINT_SCREENSHOTS)await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`validation-${width}.png`)});}
 assert.equal(await get('submit').isDisabled(),true);assert.equal(fs.existsSync(path.join(temp,'runs')),false);assert.deepEqual(errors,[]);
 assert.ok(posts.every(p=>p.startsWith('/api/antithesis-run/')));
 console.log('PASS: three input paths, findings, lost-response recovery, stale selections, mobile, no Local execution/submission');
}finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true})}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
