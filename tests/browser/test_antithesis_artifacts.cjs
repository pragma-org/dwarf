const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const state=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-antithesis-artifacts-'));
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,antithesis_artifacts as a
from profile_manager.data import antithesis_run_wizard as wizard, antithesis_package as endpoint
def forbidden(*args,**kwargs):raise AssertionError('Local execution touched')
run_service.execute_run=forbidden
STOCK='registration-release-build-'+'1'*32+'-stock'
PATCHED='registration-release-build-'+'2'*32+'-instrumented'
class Ledger:
    def __init__(self,*a,**k):pass
    def read(self):return {'revision':'r','auto_port_proofs':[],'registrations':[
        {'registration_id':STOCK,'implementation':'amaru','version':'10.11.20260918','config_image_id':'sha256:'+'a'*64,
         'provenance':{'source_revision':'a'*40},'source_build':{'variant':'stock','execution_id':'release-build-'+'1'*32,'build_result_sha256':'sha256:'+'c'*64}},
        {'registration_id':PATCHED,'implementation':'amaru','version':'10.11.20260918','config_image_id':'sha256:'+'b'*64,
         'provenance':{'source_revision':'a'*40},'source_build':{'variant':'instrumented','execution_id':'release-build-'+'2'*32,'build_result_sha256':'sha256:'+'c'*64,
         'patch_set_sha256':'sha256:'+'d'*64,'measurement_revision':'nanoseconds-v4','instrumentation_evidence_sha256':'sha256:'+'e'*64}}]}
wizard.OperatorVersionDecisionLedger=Ledger
endpoint._prepared_registration=lambda rid:{'implementation':'amaru','source_build':{'variant':'stock'}}
rows={};attempts={}
def from_registration(registration_id,*,controller=None):
    identifier=a.artifact_id_for(registration_id)
    attempts[registration_id]=attempts.get(registration_id,0)+1
    if identifier in rows and rows[identifier]['state']=='complete':return rows[identifier]
    state='failed' if attempts[registration_id]==1 else 'complete'
    row=dict(id=identifier,state=state,implementation='amaru',registration_id=registration_id,version='10.11.20260918',
        source_revision='a'*40,mode='stock',runtime_interface='extracted-binary',image_id='sha256:'+'b'*64,
        reference='dwarf-antithesis-fixture:prepared',message='Fixture node result '+state)
    rows[identifier]=row
    return row
a.from_registration=from_registration
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
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/node-select'))requests.push(r.postDataJSON())});
 await page.goto(`http://127.0.0.1:${port}/run/antithesis`);
 await page.locator('#antithesis-policy').selectOption('latest-stable');
 await page.locator('#antithesis-scenario').selectOption('cardano-amaru-miniprotocol-security-local');
 await page.getByText('Select prepared build',{exact:true}).click();
 const panel=page.locator('[data-antithesis-node="amaru"]');
 const STOCK='registration-release-build-'+'1'.repeat(32)+'-stock',PATCHED='registration-release-build-'+'2'.repeat(32)+'-instrumented';
 // The build variant filters the options: patched builds are disabled under Stock.
 const optionDisabled=value=>page.evaluate(v=>document.querySelector(`[data-antithesis-node="amaru"] option[value="${v}"]`).disabled,value);
 assert.equal(await optionDisabled(PATCHED),true);
 assert.equal(await optionDisabled(STOCK),false);
 await panel.locator('[data-node-registration]').selectOption(STOCK);
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),true);
 assert.equal(await page.locator('#antithesis-prepare').isDisabled(),true);
 await panel.locator('[data-node-select]').click();
 await page.waitForFunction(()=>!document.querySelector('[data-antithesis-node="amaru"] [data-node-select]').disabled);
 assert.equal(await page.locator('#antithesis-prepare').isDisabled(),true);
 assert.match(await panel.locator('[data-node-status]').textContent(),/failed/);
 await panel.locator('[data-node-select]').click();
 await page.waitForFunction(()=>Boolean(document.querySelector('[data-antithesis-wizard]').antithesisNodeArtifacts.amaru));
 assert.equal(requests.length,2);assert.deepEqual(requests.map(r=>r.registration_id),[STOCK,STOCK]);
 assert.equal(requests[0].implementation,'amaru');
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),false);
 await page.locator('#antithesis-preview-package').click();
 await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='incomplete');
 await page.locator('#antithesis-package-file').selectOption('environment/docker-compose.yaml');
 assert.match(await page.locator('#antithesis-package-file-content').textContent(),/dwarf-antithesis-fixture:prepared/);
 // Switching the variant invalidates the selection (the stock build no longer matches).
 await page.locator('#antithesis-build').selectOption('instrumented');
 assert.equal(await panel.locator('[data-node-registration]').inputValue(),'');
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),false);
 assert.equal(await page.locator('#antithesis-package-preview').isHidden(),true);
 await panel.locator('[data-node-registration]').selectOption(PATCHED);
 assert.equal(await page.locator('#antithesis-preview-package').isDisabled(),true);
 assert.deepEqual(errors,[]);
 console.log('PASS: variant filters prepared builds, pending selection blocks fallback, failed adapt retries, completed artifact reaches mixed package, variant change invalidates selection; Local execution untouched');
}finally{if(browser)await browser.close();server.kill();fs.rmSync(state,{recursive:true,force:true});}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
