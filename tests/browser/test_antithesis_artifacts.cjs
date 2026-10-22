const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const state=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-antithesis-artifacts-'));
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,antithesis_artifacts as a
def forbidden(*args,**kwargs):raise AssertionError('Local execution touched')
run_service.execute_run=forbidden
rows={}
def start(request,identifier):
    if identifier in rows:return rows[identifier]
    row=dict(id=identifier,state='failed' if not rows else 'complete',implementation=request['implementation'],
        version=request['version'],source_revision=None,mode='stock',runtime_interface='extracted-binary',
        image_id='sha256:'+'b'*64,reference='dwarf-antithesis-fixture:prepared',message='Fixture node result')
    rows[identifier]=row
    return row
a.start=start
a.read=lambda identifier:rows[identifier]
server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.serve_dashboard_handler_factory('test-token'))
print(server.server_port,flush=True)
server.serve_forever()
`],{cwd:root,env:{...process.env,PYTHONPATH:'.:dwarf',ADA2_DWARF_STATE_DIR:state,
ADA2_PROFILE_MANAGER_CONFIG:path.join(state,'config.yaml'),ADA2_DWARF_RUNS_DIR:path.join(state,'runs'),ADA2_DWARF_BUNDLES_DIR:path.join(state,'bundles')},stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',d=>stderr+=d);
(async()=>{let browser;try{
 const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup')),20000);
 server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()))});});
 browser=await chromium.launch({headless:true});const page=await browser.newPage();const errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/node-start'))requests.push(r.postDataJSON())});
 await page.goto(`http://127.0.0.1:${port}/run/antithesis`);
 await page.locator('#antithesis-policy').selectOption('latest-stable');
 await page.locator('#antithesis-scenario').selectOption('cardano-amaru-miniprotocol-security-local');
 await page.getByText('Experimental node source, binary or image',{exact:true}).click();
 const panel=page.locator('[data-antithesis-node="amaru"]');
 await panel.locator('[data-node-field="kind"]').selectOption('binary');
 await panel.locator('[data-node-field="location"]').fill('/tmp/experimental-amaru');
 await panel.locator('[data-node-field="version"]').fill('custom');
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),true);
 assert.equal(await page.locator('#antithesis-prepare').isDisabled(),true);
 await panel.locator('[data-node-prepare]').click();
 await page.waitForFunction(()=>!document.querySelector('[data-antithesis-node="amaru"] [data-node-prepare]').disabled);
 assert.equal(await page.locator('#antithesis-prepare').isDisabled(),true);
 await panel.locator('[data-node-prepare]').click();
 await page.waitForFunction(()=>Boolean(document.querySelector('[data-antithesis-wizard]').antithesisNodeArtifacts.amaru));
 assert.equal(requests.length,2);assert.notEqual(requests[0].request_id,requests[1].request_id);
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),false);
 await page.locator('#antithesis-preview-package').click();
 await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='incomplete');
 await page.locator('#antithesis-package-file').selectOption('environment/docker-compose.yaml');
 assert.match(await page.locator('#antithesis-package-file-content').textContent(),/dwarf-antithesis-fixture:prepared/);
 await panel.locator('[data-node-field="location"]').fill('/tmp/different-binary');
 assert.equal(await page.locator('#antithesis-prepare').isDisabled(),true);
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),true);
 assert.equal(await page.locator('#antithesis-package-preview').isHidden(),true);
 assert.deepEqual(errors,[]);
 console.log('PASS: pending artifacts block fallback, terminal failure retries a new job, completed artifact reaches mixed package, changed input invalidates selection; Local execution untouched');
}finally{if(browser)await browser.close();server.kill();fs.rmSync(state,{recursive:true,force:true});}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
