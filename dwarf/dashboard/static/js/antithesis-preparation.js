/* Antithesis asset staging only. No Local execution or remote submission. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');
  if(!root)return;
  const get=id=>root.querySelector('#antithesis-'+id);
  let context='',generation=0,busy=false,blobUrl=null;
  function selected(){
    const request=root.antithesisPackageRequest();
    const source=request.package_source==='url'?{url:get('package-url').value.trim()}
      :request.package_source==='existing'?{compose_path:get('compose-path').value.trim(),
        image_references:get('image-references').value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean)}:{};
    return {request,source};
  }
  function status(state,message){
    get('preparation-state').dataset.state=state;get('preparation-state').textContent=message;
    root.dataset.preparationSummary=message;
    root.dispatchEvent(new Event('antithesis-preparation-updated'));
  }
  function clear(){
    if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}
    get('preparation-download').removeAttribute('href');
    get('preparation-download').removeAttribute('download');
    get('preparation-result').hidden=true;
    delete root.dataset.preparationId;
    delete root.antithesisPreparationReceipt;
  }
  function sync(){
    const selection=selected(),next=JSON.stringify(selection);
    if(next!==context){
      context=next;generation+=1;clear();
      status('unprepared',busy?'Selections changed. The earlier request may still finish; its result will not be applied.':'No saved package for these selections.');
    }
    const {request,source}=selection;
    const sourceReady=request.package_source==='build'?Boolean(request.scenario_id)
      :request.package_source==='url'?/^https:\/\/[^/]+/i.test(source.url)
      :Boolean(source.compose_path||source.image_references.length);
    get('prepare').disabled=(request.package_source==='build'&&Object.values(root.antithesisNodeArtifacts||{}).some(id=>!id))||busy||!root.dataset.antithesisToken||!sourceReady
      ||!Number.isInteger(request.duration_hours)||request.duration_hours<1;
  }
  get('prepare').addEventListener('click',async()=>{
    sync();if(get('prepare').disabled)return;
    clear();const stamp=++generation,payload={token:root.dataset.antithesisToken,...selected()};
    busy=true;sync();status('preparing','Acquiring and checking assets; saving a preparation receipt…');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
    try{
      const response=await fetch('/api/antithesis-run/package/prepare',{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
      const body=await response.json();if(stamp!==generation)return;
      if(!response.ok||!body.ok)throw Error(body.error?.message||'Package preparation failed.');
      const result=body.preparation,receipt=result?.receipt;
      if(!receipt||receipt.state!=='staged'||receipt.execution_ready!==false||receipt.submission_ready!==false
          ||!/^[a-f0-9]{32}$/.test(receipt.id)||!Array.isArray(receipt.issues)||!Array.isArray(receipt.inventory)
          ||typeof result.archive_base64!=='string')throw Error('Invalid preparation receipt.');
      const binding=receipt.version_binding;
      if(!binding||!['applied','partial','recorded'].includes(binding.status))throw Error('Invalid version binding receipt.');
      get('preparation-summary').textContent=`Receipt ${receipt.id} · ${receipt.inventory.filter(f=>f.kind==='file').length} files · saved on the Dwarf host. Version binding: ${binding.status}. ${receipt.workload_binding?.status==='applied'?'Workload wiring: applied; workload image build required. ':''}Images and runtime behavior are not verified.`;
      get('preparation-issues').replaceChildren(...receipt.issues.map(issue=>{
        const li=document.createElement('li');li.textContent=issue.message;return li;
      }));
      get('preparation-receipt').textContent=JSON.stringify(receipt,null,2);
      const bytes=Uint8Array.from(atob(result.archive_base64),c=>c.charCodeAt(0));
      blobUrl=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
      get('preparation-download').href=blobUrl;get('preparation-download').download=result.archive_name;
      root.antithesisPreparationReceipt=receipt;
      root.dataset.preparationId=receipt.id;get('preparation-result').hidden=false;
      status('staged',`Assets staged · ${receipt.issues.length} findings · not submitted.`);
    }catch(error){
      if(stamp===generation){clear();status('error',error.name==='AbortError'
        ?'Preparation response timed out. A snapshot may have been saved; no submission occurred.':error.message);}
    }finally{clearTimeout(timer);busy=false;sync();}
  });
  root.addEventListener('input',sync);root.addEventListener('change',sync);
  root.addEventListener('antithesis-topology-updated',sync);
  window.addEventListener('pageshow',event=>{if(event.persisted){context='';sync();}});
  sync();
})();
