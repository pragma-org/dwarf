/* Antithesis-only, in-memory package generation. No build or publication. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');
  if(!root)return;
  const get=id=>root.querySelector('#antithesis-'+id), value=id=>get(id).value;
  let generation=0, context='', blobUrl=null, files={}, busy=false;
  function request(){
    const policy=value('policy'),packageSource=root.querySelector('[name="antithesis-package-source"]:checked').value;
    return {package_source:packageSource,
      topology:root.antithesisTopologyRequest(),topology_digest:root.dataset.topologyDigest||null,
      versions:{policy,versions:Object.fromEntries(['cardano-node','amaru']
        .filter(impl=>policy==='exact'&&value('version-'+impl)).map(impl=>[impl,value('version-'+impl)])),
        build_variant:value('build'),experimental_intent:get('experimental').checked,
        ...(packageSource==='build' && root.antithesisNodeArtifacts && Object.keys(root.antithesisNodeArtifacts).length ? {artifacts:root.antithesisNodeArtifacts} : {})},
      scenario_id:value('scenario')||null,
      measurements:{profile_id:value('measurement-profile')||null,
        tap_ids:[...root.querySelectorAll('[name="antithesis-tap"]:checked')].map(t=>t.value)},
      duration_hours:value('duration')===''?null:Number(value('duration')),faults:value('faults')};
  }
  root.antithesisPackageRequest=request;
  function status(state,message){
    get('package-state').dataset.state=state;get('package-state').textContent=message;
    root.dataset.packageSummary=message;
    root.dispatchEvent(new Event('antithesis-package-updated'));
  }
  function clear(){
    generation+=1;busy=false;files={};
    if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}
    get('package-download').removeAttribute('href');get('package-download').removeAttribute('download');
    get('package-preview').hidden=true;delete root.dataset.packageDigest;
  }
  function sync(){
    const current=request(),next=JSON.stringify(current);
    if(next!==context){context=next;clear();status('unresolved',
      current.package_source!=='build'?'Use Prepare package below to acquire and stage supplied assets.':
      !current.scenario_id?'Select a scenario to generate a package preview.':'Generate a preview for the current selections. No images will be built or published.');}
    get('preview-package').disabled=Object.values(root.antithesisNodeArtifacts||{}).some(id=>!id)||busy||current.package_source!=='build'||!current.scenario_id;
  }
  function showFile(){get('package-file-content').textContent=files[value('package-file')]||'';}
  function showHandoff(handoff){
    const labels={package_source:'Package source',scenario_id:'Scenario',topology:'Topology',
      versions:'Node versions',build_variant:'Stock / instrumented',experimental_intent:'Experimental intent',
      measurements:'Measurement taps',duration_hours:'Duration',faults:'Fault policy'};
    const statuses={applied:'Applied to preview',partial:'Partly applied',recorded:'Recorded only'};
    get('engine-summary').textContent=handoff.workload_binding?.status==='applied'
      ? 'Existing mixed harness · paired mini-protocol workload and observing test commands connected · workload image build required. Partial scenario translation; runtime unverified.'
      : handoff.renderer==='control-harness'
      ? 'Existing Amaru/mixed control harness · node-version preparation. Scenario workload and observing assertions are not connected.'
      : handoff.environment_emitted
      ? `Existing native scenario generator · ${handoff.adversary?.protocol||'protocol unspecified'} / ${handoff.adversary?.shape||'shape unspecified'} · partial scenario translation.`
      : 'No scenario environment was generated. Selections remain intent; inspect the gaps below.';
    get('input-dispositions').replaceChildren(...handoff.inputs.map(input=>{
      const row=document.createElement('tr');row.dataset.field=input.field;row.dataset.status=input.status;
      for(const [index,text] of [labels[input.field]||input.field,statuses[input.status],input.detail].entries()){
        const cell=document.createElement(index===0?'th':'td');if(index===0)cell.scope='row';
        cell.textContent=text;row.append(cell);
      }
      return row;
    }));
    get('engine-services').replaceChildren(...handoff.services.map(service=>{
      const li=document.createElement('li');li.textContent=`${service.name}: ${service.image||'no image reference'}`;return li;
    }));
    get('engine-configs').textContent=handoff.config_files.length
      ? 'Included configuration: '+handoff.config_files.join(', '):'No scenario configuration emitted.';
  }
  get('package-file').addEventListener('change',showFile);
  get('preview-package').addEventListener('click',async()=>{
    sync();clear();const selected=request(),stamp=generation;busy=true;sync();
    status('generating','Generating package files for review…');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await fetch('/api/antithesis-run/package/preview',{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(selected),signal:controller.signal});
      const payload=await response.json();if(stamp!==generation)return;
      if(!response.ok||!payload.ok)throw new Error(payload.error?.message||'Package generation failed.');
      const result=payload.package;
      if(!result||result.state!=='incomplete'||result.execution_ready!==false||!Array.isArray(result.issues)
          ||!result.files||typeof result.files!=='object'||typeof result.archive_base64!=='string'
          ||!/^sha256:[a-f0-9]{64}$/.test(result.package_digest))throw new Error('Invalid package preview response.');
      const handoff=result.engine_handoff;
      if(!handoff||typeof handoff.environment_emitted!=='boolean'||!Array.isArray(handoff.inputs)
          ||!handoff.inputs.length||!Array.isArray(handoff.services)||!Array.isArray(handoff.config_files)
          ||!handoff.inputs.every(input=>input&&typeof input.field==='string'&&typeof input.detail==='string'
            &&['applied','partial','recorded'].includes(input.status)))throw new Error('Invalid engine handoff response.');
      files=result.files;
      showHandoff(handoff);
      get('package-issues').replaceChildren(...result.issues.map(issue=>{const li=document.createElement('li');li.textContent=issue.message;return li;}));
      get('package-file').replaceChildren(...Object.keys(files).sort().map(name=>{
        const option=document.createElement('option');option.value=name;option.textContent=name;return option;
      }));
      if(Object.hasOwn(files,'package-manifest.json'))get('package-file').value='package-manifest.json';
      showFile();
      const bytes=Uint8Array.from(atob(result.archive_base64),c=>c.charCodeAt(0));
      blobUrl=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
      get('package-download').href=blobUrl;get('package-download').download=result.archive_name;
      root.dataset.packageDigest=result.package_digest;get('package-preview').hidden=false;
      status('incomplete',`${Object.keys(files).length} files generated · ${result.issues.length} implementation gaps · preview only, not prepared.`);
    }catch(error){if(stamp===generation){clear();status('error',error.name==='AbortError'?'Package generation timed out. Retry.':error.message);}}
    finally{clearTimeout(timer);if(stamp===generation)busy=false;sync();}
  });
  root.addEventListener('input',sync);root.addEventListener('change',sync);
  root.addEventListener('antithesis-topology-updated',sync);
  window.addEventListener('pageshow',event=>{if(event.persisted){context='';sync();}});
  sync();
})();
