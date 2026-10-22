/* Prepare node artifacts separately; never call the Local execution endpoints. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');
  if(!root)return;
  root.antithesisNodeArtifacts={};
  async function api(action,payload){
    const response=await fetch('/api/antithesis-run/package/'+action,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({token:root.dataset.antithesisToken,...payload})});
    const body=await response.json();
    if(!response.ok||!body.ok)throw Error(body.error?.message||'Node preparation failed');
    return body.artifact;
  }
  for(const panel of root.querySelectorAll('[data-antithesis-node]')){
    const impl=panel.dataset.antithesisNode,field=name=>panel.querySelector(`[data-node-field="${name}"]`);
    const status=panel.querySelector('[data-node-status]'),button=panel.querySelector('[data-node-prepare]');
    let generation=0,job=null,context='',busy=false;
    function request(){return {implementation:impl,kind:field('kind').value,location:field('location').value.trim(),
      version:field('version').value.trim(),revision:field('revision').value.trim(),base_image:field('base_image').value.trim(),
      mode:root.querySelector('#antithesis-build').value};}
    function sync(){
      panel.querySelector('[data-node-inputs]').hidden=field('kind').value==='catalog';
      const next=JSON.stringify(request());
      if(next!==context){context=next;generation++;job=null;busy=false;
        delete root.antithesisNodeArtifacts[impl];
        if(field('kind').value!=='catalog')root.antithesisNodeArtifacts[impl]=null;
        status.textContent='No artifact prepared for these inputs.';}
      button.disabled=busy||field('kind').value==='catalog';
    }
    panel.addEventListener('input',sync);panel.addEventListener('change',sync);
    root.querySelector('#antithesis-build').addEventListener('change',sync);
    panel.querySelector('[data-node-release]').addEventListener('click',()=>{
      const release=(root.antithesisResolvedReleases||[]).find(r=>r.implementation===impl);
      if(!release){status.textContent='Resolve version choices first to select an exact upstream source.';return;}
      field('kind').value='source';field('version').value=release.version;field('revision').value=release.source_revision;
      field('location').value=impl==='amaru'?'https://github.com/pragma-org/amaru.git':'https://github.com/IntersectMBO/cardano-node.git';
      sync();root.dispatchEvent(new Event('input'));
    });
    button.addEventListener('click',async()=>{
      sync();if(button.disabled)return;
      const stamp=generation,selected=request();busy=true;button.disabled=true;
      job=job||Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
      try{
        let row=await api('node-start',{request:selected,request_id:job});
        while(stamp===generation){
          if(row.id!==job||row.implementation!==impl)throw Error('Node preparation identity changed');
          status.textContent=row.message;
          if(row.state==='complete'){
            root.antithesisNodeArtifacts[impl]=row.id;
            root.dispatchEvent(new Event('input'));break;
          }
          if(row.state!=='building'){job=null;throw Error(row.message);}
          await new Promise(resolve=>setTimeout(resolve,2000));
          if(stamp!==generation)break;
          row=await api('node-status',{artifact_id:job});
        }
      }catch(error){if(stamp===generation)status.textContent=error.message;}
      finally{if(stamp===generation){busy=false;button.disabled=false;}}
    });
    sync();
  }
})();
