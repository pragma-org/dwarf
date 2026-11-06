/* Select operator-prepared builds; the controller adapts them. Never call the Local execution endpoints. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');
  if(!root)return;
  root.antithesisNodeArtifacts={};
  async function api(action,payload){
    const response=await fetch('/api/antithesis-run/package/'+action,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({token:root.dataset.antithesisToken,...payload})});
    const body=await response.json();
    if(!response.ok||!body.ok)throw Error(body.error?.message||'Prepared build selection failed');
    return body.artifact;
  }
  const variant=root.querySelector('#antithesis-build');
  for(const panel of root.querySelectorAll('[data-antithesis-node]')){
    const impl=panel.dataset.antithesisNode,select=panel.querySelector('[data-node-registration]');
    if(!select)continue;
    const status=panel.querySelector('[data-node-status]'),button=panel.querySelector('[data-node-select]');
    let generation=0,context='',busy=false;
    function wantedMode(){return variant.value==='instrumented'?'patched':'stock';}
    function sync(){
      for(const option of select.options){
        if(!option.value)continue;
        option.disabled=option.dataset.mode!==wantedMode();
      }
      if(select.selectedOptions[0]?.disabled)select.value='';
      const next=select.value+'|'+wantedMode();
      if(next!==context){context=next;generation++;busy=false;
        delete root.antithesisNodeArtifacts[impl];
        if(select.value)root.antithesisNodeArtifacts[impl]=null;
        status.textContent=select.value?'Prepared build chosen; click "Use prepared build" to adapt it.':'Catalog image in use.';
        root.dispatchEvent(new Event('input'));}
      button.disabled=busy||!select.value;
    }
    select.addEventListener('change',sync);variant.addEventListener('change',sync);
    button.addEventListener('click',async()=>{
      sync();if(button.disabled)return;
      const stamp=generation,registration=select.value;busy=true;button.disabled=true;
      try{
        let row=await api('node-select',{implementation:impl,registration_id:registration});
        while(stamp===generation){
          if(row.implementation!==impl||row.registration_id!==registration)throw Error('Prepared build identity changed');
          status.textContent=row.message;
          if(row.state==='complete'){
            root.antithesisNodeArtifacts[impl]=row.id;
            root.dispatchEvent(new Event('input'));break;
          }
          if(row.state!=='building')throw Error(row.message);
          await new Promise(resolve=>setTimeout(resolve,2000));
          if(stamp!==generation)break;
          row=await api('node-status',{artifact_id:row.id});
        }
      }catch(error){if(stamp===generation)status.textContent=error.message;}
      finally{if(stamp===generation){busy=false;button.disabled=!select.value;}}
    });
    sync();
  }
})();
