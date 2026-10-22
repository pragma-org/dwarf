/* Explicit publication and read-only delivery verification; never submits a test. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');if(!root)return;
  const get=id=>root.querySelector('#antithesis-publication-'+id);
  const randomId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
  let source='',generation=0,busy=false,handoff=null,pending=null,blob=null;
  const target=()=>({repository:get('repository').value.trim(),commit:get('commit').value.trim(),
    directory:get('directory').value.trim(),requester:get('requester').value.trim(),try_number:Number(get('try').value)});
  function status(state,message){get('state').dataset.state=state;get('state').textContent=message;}
  function clearExport(){
    delete root.antithesisPublicReceipt;
    root.dispatchEvent(new Event('antithesis-publication-updated'));
    if(blob){URL.revokeObjectURL(blob);blob=null;}
    get('download').removeAttribute('href');get('export').hidden=true;get('request-result').hidden=true;
  }
  function reset(){
    generation++;handoff=null;pending=null;clearExport();get('images').replaceChildren();
    status('unprepared','Build a generated package, then prepare its publication handoff.');controls();
  }
  function sync(){
    const id=root.antithesisBuiltReceipt?.id||'';
    if(id!==source){source=id;reset();}
    controls();
  }
  function controls(){
    get('create').disabled=busy||Boolean(pending)||!source||!get('prefix').value.trim()||!root.dataset.antithesisToken;
    get('push').disabled=busy||Boolean(pending)||!handoff||handoff.push_attempted||!handoff.images.length;
    get('verify-images').disabled=busy||Boolean(pending)||!handoff;
    const t=target();get('verify-git').disabled=busy||Boolean(pending)||!handoff?.inventory
      ||![t.repository,t.commit,t.directory,t.requester].every(Boolean)||!Number.isInteger(t.try_number)||t.try_number<1;
    get('resume').hidden=!pending||busy;
  }
  async function api(action,payload){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await fetch('/api/antithesis-run/package/publication-'+action,{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({token:root.dataset.antithesisToken,...payload}),signal:controller.signal});
      const value=await response.json();if(!response.ok||!value.ok)throw Error(value.error?.message||'Publication request failed.');return value;
    }finally{clearTimeout(timer);}
  }
  function check(h){
    if(!h||!/^[a-f0-9]{32}$/.test(h.id)||h.build_id!==source||h.registry_prefix!==get('prefix').value.trim()
      ||h.submission_ready!==false||!Array.isArray(h.images))throw Error('Publication receipt does not match this build and destination.');
  }
  async function display(h,stamp){
    check(h);handoff=h;clearExport();get('images').replaceChildren();
    for(const row of h.images){const li=document.createElement('li');li.textContent=`${row.service}: ${row.destination}${row.push_completed?' · pushed':''}${row.public?' · public identity verified':''}`;get('images').append(li);}
    if(h.inventory){
      const response=await api('result',{handoff_id:h.id});if(stamp!==generation)return;
      const result=response.preparation;check(result?.receipt);
      if(result.receipt.id!==h.id||typeof result.archive_base64!=='string')throw Error('Invalid publication package.');
      blob=URL.createObjectURL(new Blob([Uint8Array.from(atob(result.archive_base64),c=>c.charCodeAt(0))],{type:'application/zip'}));
      get('download').href=blob;get('download').download=result.archive_name;get('export').hidden=false;
      get('receipt').textContent=JSON.stringify(result.receipt,null,2);
      root.antithesisPublicReceipt=result.receipt;
      root.dispatchEvent(new Event('antithesis-publication-updated'));
    }
    if(h.request_plan){
      get('request').textContent=JSON.stringify(h.request_plan,null,2);
      get('request-summary').textContent=`Public Git files match. Local request checks: ${h.request_plan.state}. This is a preview; remote readiness is unverified.`;
      get('request-result').hidden=false;
    }
  }
  async function perform(operation){
    if(busy)return;const stamp=generation;busy=true;pending=operation;controls();get('request-result').hidden=true;
    try{
      status('working','Starting or recovering the publication operation…');
      if(operation.kind==='create'){
        const response=await api('create',operation.payload);if(stamp!==generation)return;
        await display(response.handoff,stamp);if(stamp!==generation)return;
        pending=null;status(handoff.state,handoff.message);return;
      }
      const started=await api('action',operation.payload);if(stamp!==generation)return;
      function checkOperation(op){
        if(op?.id!==operation.payload.request_id||op.handoff_id!==operation.payload.handoff_id
          ||op.action!==operation.payload.action||op.submission_ready!==false
          ||!['queued','running','succeeded','failed','interrupted'].includes(op.state))throw Error('Invalid publication operation status.');
      }
      checkOperation(started.operation);
      while(stamp===generation){
        const {operation:op}=await api('status',{handoff_id:operation.payload.handoff_id,operation_id:operation.payload.request_id});
        if(stamp!==generation)return;checkOperation(op);status('working',op.message);
        if(['succeeded','failed','interrupted'].includes(op.state)){
          const response=await api('read',{handoff_id:operation.payload.handoff_id});if(stamp!==generation)return;
          await display(response.handoff,stamp);if(stamp!==generation)return;
          pending=null;status(op.state==='succeeded'?handoff.state:'error',op.message);break;
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    }catch(error){if(stamp===generation)status('error',`${error.name==='AbortError'?'Response timed out.':error.message} Recover operation status before starting another operation.`);}
    finally{busy=false;controls();}
  }
  get('create').addEventListener('click',()=>perform({kind:'create',payload:{build_id:source,registry_prefix:get('prefix').value.trim(),request_id:randomId()}}));
  for(const [button,action] of [['push','push'],['verify-images','verify-images'],['verify-git','verify-git']]){
    get(button).addEventListener('click',()=>perform({kind:'action',payload:{handoff_id:handoff.id,action,target:action==='verify-git'?target():null,request_id:randomId()}}));
  }
  get('resume').addEventListener('click',()=>{if(pending)perform(pending);});
  get('prefix').addEventListener('input',reset);
  for(const name of ['repository','commit','directory','requester','try'])get(name).addEventListener('input',()=>{
    // A response for old Git inputs must never populate the current request preview.
    if(pending?.payload?.action==='verify-git'){generation++;pending=null;}
    get('request-result').hidden=true;controls();
  });
  const settings=root.querySelector('#antithesis-settings-form');
  settings?.addEventListener('input',()=>{
    if(pending?.payload?.action==='verify-git'){generation++;pending=null;}
    get('request-result').hidden=true;controls();
  });
  for(const [id,name] of [['repository','github_repo'],['commit','target_commit'],['directory','target_directory'],['requester','github_user']]){
    get(id).value=settings?.querySelector(`[name="${name}"]`)?.value||'';
  }
  root.addEventListener('antithesis-image-build-updated',sync);
  root.addEventListener('antithesis-preparation-updated',sync);sync();
})();
