/* Evidence for a selected immutable Antithesis snapshot; never submits a run. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');if(!root)return;
  const get=id=>root.querySelector('#antithesis-validation-'+id);
  let signature='',generation=0,busy=false,pending=null,optionsKey='';
  const selection=()=>{const [kind,id]=get('source').value.split(':');return {kind,id};};
  const randomId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
  function status(state,text){get('state').dataset.state=state;get('state').textContent=text;}
  function controls(){
    for(const id of ['static','artifacts'])get(id).disabled=busy||Boolean(pending)||!get('source').value||!root.dataset.antithesisToken;
    get('recover').hidden=!pending||busy;
  }
  function sync(){
    const choices=[['preparation',root.antithesisPreparationReceipt,'Staged inputs'],['build',root.antithesisBuiltReceipt,'Built package'],['publication',root.antithesisPublicReceipt,'Public export']]
      .filter(([,receipt])=>receipt?.id);
    const nextOptions=JSON.stringify(choices.map(([kind,r])=>[kind,r.id]));
    if(nextOptions!==optionsKey){
      const previous=get('source').value;optionsKey=nextOptions;
      get('source').replaceChildren(...choices.map(([kind,r,label])=>new Option(`${label} · ${r.id}`,`${kind}:${r.id}`)));
      if(!choices.length)get('source').append(new Option('Prepare a package first',''));
      if([...get('source').options].some(o=>o.value===previous))get('source').value=previous;
    }
    const next=JSON.stringify([root.antithesisPackageRequest(),root.dataset.preparationId||'',get('source').value]);
    if(next!==signature){signature=next;generation++;pending=null;get('result').hidden=true;
      status('unchecked',get('source').value?'This saved snapshot has not been checked here.':'Prepare a package first.');}
    controls();
  }
  async function api(action,payload){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await fetch('/api/antithesis-run/package/'+action,{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({token:root.dataset.antithesisToken,...payload}),signal:controller.signal});
      const value=await response.json();if(!response.ok||!value.ok)throw Error(value.error?.message||'Validation request failed.');return value.validation;
    }finally{clearTimeout(timer);}
  }
  async function perform(operation){
    if(busy)return;const stamp=generation;pending=operation;busy=true;get('result').hidden=true;controls();
    function checked(job){
      if(job?.id!==operation.request_id||job.mode!==operation.mode||job.source?.kind!==operation.source.kind||job.source?.id!==operation.source.id
        ||job.submission_ready!==false||job.execution_ready!==false||!['queued','running','complete','failed','interrupted'].includes(job.state))throw Error('Validation receipt does not match the selected snapshot.');
      return job;
    }
    try{
      status('running','Checking or recovering the saved validation…');
      let job=checked(await api('validate',operation));if(stamp!==generation)return;
      while(stamp===generation){
        if(!['queued','running'].includes(job.state)){
          pending=null;
          if(job.state==='complete'){
            const report=job.report;
            if(!Array.isArray(report?.checks)||report.submission_ready!==false||!['review','blocked'].includes(report.state))throw Error('Incomplete package-check report.');
            get('receipt').textContent=JSON.stringify(job,null,2);
            get('summary').textContent=`${report.counts.fail} failed · ${report.counts.warn} warnings · ${report.counts.not_checked} not checked · ${report.counts.pass} passed. These checks do not establish runtime readiness.`;
            get('findings').replaceChildren(...report.checks.filter(r=>r.state!=='pass').map(row=>{
              const li=document.createElement('li');li.textContent=`${row.state.replace('_',' ')} · ${row.contract} · ${row.location}: ${row.detail}`;return li;
            }));get('result').hidden=false;
          }
          status(job.state,`Validation ${job.id} · ${job.message}`);break;
        }
        await new Promise(resolve=>setTimeout(resolve,500));
        if(stamp!==generation)return;
        job=checked(await api('validation-status',{validation_id:operation.request_id}));if(stamp!==generation)return;
      }
    }catch(error){if(stamp===generation)status('error',`${error.name==='AbortError'?'Response timed out.':error.message} Recover the saved validation before starting another check.`);}
    finally{busy=false;sync();}
  }
  for(const [id,mode] of [['static','static'],['artifacts','artifacts']])get(id).addEventListener('click',()=>{
    sync();if(!get(id).disabled)perform({source:selection(),mode,request_id:randomId()});
  });
  get('recover').addEventListener('click',()=>{if(pending)perform(pending);});
  for(const event of ['input','change','antithesis-preparation-updated','antithesis-image-build-updated','antithesis-publication-updated'])root.addEventListener(event,sync);
  window.addEventListener('pageshow',sync);sync();
})();
