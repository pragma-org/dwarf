const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),temporary=fs.mkdtempSync(path.join(os.tmpdir(),'dwarf-preparation-'));
const source=path.join(temporary,'source'),state=path.join(temporary,'state');
fs.mkdirSync(source);fs.writeFileSync(path.join(source,'docker-compose.yaml'),'services:\n  node:\n    image: custom/node:experiment\n');
const server=spawn('python3',['-u','-c',`
from http.server import ThreadingHTTPServer
from profile_manager import dashboard,run_service,run_plan,remote,antithesis_preparation
import io,zipfile
def forbidden(*args,**kwargs): raise AssertionError('Preparation invoked execution')
dashboard._cli_command=forbidden
run_service.execute_run=forbidden
run_plan.resolve_run_plan=forbidden
remote.run_moog_create_test=forbidden
from scripts import qualify_node_versions as qualification
qualification._run=forbidden
qualification.run_qualification=forbidden
def archive(url):
    assert url == 'https://example.org/test.zip'
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z: z.writestr('docker-compose.yaml','services:\\n  node:\\n    image: url/node:unknown\\n')
    return output.getvalue()
antithesis_preparation._download=archive
server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.serve_dashboard_handler_factory('prepare-test'))
print(server.server_port,flush=True)
server.serve_forever()
`],{cwd:root,env:{...process.env,PYTHONPATH:'.:dwarf',ADA2_DWARF_STATE_DIR:state,
ADA2_PROFILE_MANAGER_CONFIG:path.join(temporary,'config.yaml'),ADA2_DWARF_RUNS_DIR:path.join(temporary,'runs'),
ADA2_DWARF_BUNDLES_DIR:path.join(temporary,'bundles')},
stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',d=>stderr+=d);
(async()=>{let browser;try{
  const port=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Server startup timed out')),15000);
    server.once('error',reject);server.stdout.once('data',d=>{clearTimeout(timer);resolve(Number(d.toString().trim()))});
  });
  browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname)});
  const origin=`http://127.0.0.1:${port}`,get=id=>page.locator('#antithesis-'+id);
  await page.goto(origin+'/run/antithesis');
  await page.waitForFunction(()=>document.querySelector('#antithesis-scenario').options.length>1);
  assert.equal(await get('prepare').isDisabled(),true);
  await get('scenario').selectOption('cardano-node-cbor-block-header-fuzz');
  await page.waitForFunction(()=>!document.querySelector('#antithesis-resolve-versions').disabled);
  await get('policy').selectOption('exact');
  await get('version-cardano-node').selectOption('10.7.1');
  assert.equal(await get('prepare').isDisabled(),false);
  const prepare=async()=>{await get('prepare').click();await page.waitForFunction(()=>document.querySelector('#antithesis-preparation-state').dataset.state==='staged')};
  await prepare();
  assert.match(await get('preparation-issues').innerText(),/assertions|assertion/);
  assert.equal(await get('submit').isDisabled(),true);
  const receipt=JSON.parse(await get('preparation-receipt').textContent());
  assert.equal(receipt.source.kind,'build');assert.equal(receipt.execution_ready,false);
  assert.equal(receipt.version_binding.status,'applied');
  const target=receipt.version_binding.services.find(s=>s.role==='target');
  assert.equal(target.service,'relay2');assert.equal(target.requested_version,'10.7.1');
  assert.match(await get('preparation-summary').textContent(),/Version binding: applied/);
  const read=await page.request.post(origin+'/api/antithesis-run/package/read',{data:{token:'prepare-test',preparation_id:receipt.id}});
  assert.equal(read.status(),200);assert.equal((await read.json()).preparation.receipt.id,receipt.id);
  const download=page.waitForEvent('download');await get('preparation-download').click();
  assert.match((await download).suggestedFilename(),/^antithesis-staged-.*\.zip$/);
  await get('version-cardano-node').selectOption('10.6.2');
  assert.equal(await get('preparation-result').isVisible(),false);
  await get('preview-package').click();
  await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='incomplete');
  assert.equal(await get('input-dispositions').locator('[data-field="versions"]').getAttribute('data-status'),'applied');
  await get('package-file').selectOption('package-manifest.json');
  const preview=JSON.parse(await get('package-file-content').textContent());
  assert.equal(preview.version_binding.selected_releases[0].version,'10.6.2');
  await prepare();
  const changed=JSON.parse(await get('preparation-receipt').textContent());
  const changedTarget=changed.version_binding.services.find(s=>s.role==='target');
  assert.equal(changedTarget.image,preview.version_binding.services.find(s=>s.role==='target').image);
  assert.notEqual(changedTarget.image,target.image);
  await get('policy').selectOption('latest-confirmed');
  await get('resolve-versions').click();
  await page.waitForFunction(()=>document.querySelector('#antithesis-versions-state').dataset.state==='pending');
  const assessment=JSON.parse(await get('version-details').textContent());
  assert.equal(assessment.release_resolution.qualification.source,'local-version-catalog');
  await prepare();
  const confirmed=JSON.parse(await get('preparation-receipt').textContent());
  assert.deepEqual(confirmed.version_binding.release_resolution,assessment.release_resolution);
  assert.equal(confirmed.version_binding.status,'applied');
  assert.equal(confirmed.version_binding.release_resolution.qualification.remote_status,'unverified');
  assert.equal(await get('submit').isDisabled(),true);
  if(process.env.DWARF_CHECKPOINT_SCREENSHOTS){
    await page.getByText('Inspect preparation receipt',{exact:true}).click();
    for(const width of [1440,390,720]){
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);
      await get('preparation-summary').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`version-binding-${width}.png`)});
    }
    await page.getByText('Inspect preparation receipt',{exact:true}).click();
    await page.setViewportSize({width:1440,height:1000});
  }
  for(const [scenario,protocol,shape] of [
    ['block','blockfetch','block'], ['tx-body','txsubmission','tx-body'],
    ['certificate','txsubmission','certificate'], ['auxiliary-data','txsubmission','auxiliary-data'],
  ]){
    await get('scenario').selectOption(`cardano-node-cbor-${scenario}-fuzz`);
    await page.waitForFunction(()=>!document.querySelector('#antithesis-resolve-versions').disabled);
    await get('policy').selectOption('exact');
    await get('version-cardano-node').selectOption('10.6.2');
    await prepare();
    const bound=JSON.parse(await get('preparation-receipt').textContent());
    const contract=bound.version_binding.target_contract;
    assert.equal(bound.version_binding.status,'applied');
    assert.equal(contract.service,'relay2');assert.equal(contract.protocol,protocol);
    assert.equal(contract.shape,shape);assert.equal(contract.evidence,'emitted-configuration');
    assert.equal(contract.topology_file,`environment/relay-${protocol==='blockfetch'?'eclipse':'dwarf'}-topology.json`);
    assert.equal(bound.version_binding.services.find(s=>s.role==='target').requested_version,'10.6.2');
    assert.equal(bound.execution_ready,false);assert.equal(await get('submit').isDisabled(),true);
  }
  for(const [scenario,scope] of [
    ['amaru-cbor-block-fuzz','amaru-only'],
    ['cardano-amaru-relay-bootstrap-control-local','mixed'],
  ]){
    await get('scenario').selectOption(scenario);
    await get('policy').selectOption('exact');
    await get('version-cardano-node').selectOption(scope==='mixed'?'10.6.2':'');
    await get('version-amaru').selectOption('10.11.20260925');
    await get('preview-package').click();
    await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='incomplete');
    assert.match(await get('engine-summary').textContent(),/control harness.*Scenario workload/);
    assert.equal(await get('input-dispositions').locator('[data-field="scenario_id"]').getAttribute('data-status'),'recorded');
    await prepare();
    const first=JSON.parse(await get('preparation-receipt').textContent());
    assert.equal(first.source.generator,'existing-control-harness-preview');
    assert.equal(first.version_binding.status,'applied');
    assert.equal(first.version_binding.target_contract.scope,scope);
    const target=first.version_binding.services.find(s=>s.service==='amaru-relay-1');
    assert.equal(target.requested_version,'10.11.20260925');
    assert.equal(target.target_binary.mode,'extracted-binary');
    assert.notEqual(target.target_binary.image,target.image);
    if(scope==='mixed')assert.equal(first.version_binding.services.find(s=>s.service==='relay1').requested_version,'10.6.2');
    await get('version-amaru').selectOption('10.11.20260918');
    assert.equal(await get('preparation-result').isVisible(),false);
    await prepare();
    const second=JSON.parse(await get('preparation-receipt').textContent());
    const secondTarget=second.version_binding.services.find(s=>s.service==='amaru-relay-1');
    assert.equal(secondTarget.requested_version,'10.11.20260918');
    assert.notEqual(secondTarget.target_binary.image,target.target_binary.image);
    assert.equal(secondTarget.bootstrap_image,target.bootstrap_image);
    assert.equal(second.execution_ready,false);assert.equal(await get('submit').isDisabled(),true);
  }
  await get('scenario').selectOption('cardano-amaru-miniprotocol-security-local');
  await get('version-cardano-node').selectOption('10.6.2');
  await get('preview-package').click();
  await page.waitForFunction(()=>document.querySelector('#antithesis-package-state').dataset.state==='incomplete');
  assert.match(await get('engine-summary').textContent(),/paired mini-protocol workload.*build required/i);
  assert.equal(await get('input-dispositions').locator('[data-field="scenario_id"]').getAttribute('data-status'),'partial');
  await prepare();
  const workload=JSON.parse(await get('preparation-receipt').textContent());
  assert.equal(workload.workload_binding.status,'applied');
  assert.equal(workload.version_binding.services.find(s=>s.service==='sm-amaru-victim').requested_version,'10.11.20260918');
  assert.equal(workload.version_binding.services.find(s=>s.service==='sm-cardano-victim').requested_version,'10.6.2');
  assert.equal(workload.build_requirements[0].service,'sm-workload');
  assert.match(await get('preparation-summary').textContent(),/Workload wiring: applied/);
  assert.equal(await get('submit').isDisabled(),true);
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);
    if(process.env.DWARF_CHECKPOINT_SCREENSHOTS){
      await get('engine-summary').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`miniprotocol-${width}.png`)});
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  await get('duration').fill('3');assert.equal(await get('preparation-result').isVisible(),false);
  assert.equal(await get('preparation-download').getAttribute('href'),null);
  await page.locator('[name=antithesis-package-source][value=existing]').check();
  await get('compose-path').fill(source);await get('image-references').fill('custom/node:experiment');
  await prepare();
  const imported=JSON.parse(await get('preparation-receipt').textContent());
  assert.equal(imported.source.kind,'existing');
  assert.equal(imported.version_binding.status,'recorded');
  assert.equal(imported.images[0].reference,'custom/node:experiment');
  await get('compose-path').fill(source+'-missing');await get('prepare').click();
  await page.waitForFunction(()=>document.querySelector('#antithesis-preparation-state').dataset.state==='error');
  assert.equal(await get('prepare').isDisabled(),false);
  await page.locator('[name=antithesis-package-source][value=url]').check();
  await get('package-url').fill('https://example.org/test.zip');await prepare();
  assert.equal(JSON.parse(await get('preparation-receipt').textContent()).source.kind,'url');
  await page.getByText('Inspect preparation receipt',{exact:true}).click();
  for(const width of [1440,390,720]){
    await page.setViewportSize({width,height:1000});
    assert.equal(await page.locator('.shell-main').evaluate(n=>n.scrollWidth>n.clientWidth),false);
    if(process.env.DWARF_CHECKPOINT_SCREENSHOTS){
      await get('preparation-state').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(process.env.DWARF_CHECKPOINT_SCREENSHOTS,`preparation-${width}.png`)});
    }
  }
  const route='**/api/antithesis-run/package/prepare';let release,seen;
  const held=new Promise(r=>release=r),captured=new Promise(r=>seen=r);
  await page.route(route,async r=>{const response=await r.fetch();seen();await held;await r.fulfill({response})});
  await get('prepare').click();await captured;await get('package-url').fill('https://example.org/changed.zip');release();
  await page.waitForLoadState('networkidle');assert.equal(await get('preparation-result').isVisible(),false);
  await page.unroute(route);
  assert.equal((await page.request.post(origin+'/api/antithesis-run/package/prepare',{data:{token:'wrong',request:{},source:{}}})).status(),403);
  assert.ok(fs.readdirSync(path.join(state,'antithesis/preparations')).length>=3);
  assert.equal(fs.existsSync(path.join(temporary,'runs')),false);
  assert.ok(posts.every(p=>p.startsWith('/api/antithesis-run/package/') || p==='/api/antithesis-run/versions/resolve'));
  assert.deepEqual(errors,[]);
  console.log('PASS: Amaru/mixed target binaries and fixed bootstrap dependencies, all five native modes bind staged targets, selected versions change native preview/staged target, supplied images preserved, three sources, receipt/readback/download, invalidation/stale replies, auth, responsive layout, no execution');
}finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(temporary,{recursive:true,force:true})}})().catch(e=>{console.error(e,stderr);process.exitCode=1});
