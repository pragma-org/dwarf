/* Explicit image builds for immutable generated Antithesis preparations. */
(() => {
  'use strict';
  const root=document.querySelector('[data-antithesis-wizard]');
  if(!root)return;
  const get=id=>root.querySelector('#antithesis-'+id);
  let preparationId='',generation=0,busy=false,jobId=null,blobUrl=null,state='unprepared';
  function status(next,message){
    state=next;get('image-build-state').dataset.state=next;get('image-build-state').textContent=message;
    root.dataset.imageBuildSummary=message;
    root.dispatchEvent(new Event('antithesis-image-build-updated'));
  }
  function clear(){
    delete root.antithesisBuiltReceipt;
    if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}
    get('image-build-download').removeAttribute('href');
    get('image-build-download').removeAttribute('download');
    get('image-build-result').hidden=true;
  }
  function sync(){
    const next=root.dataset.preparationId||'';
    if(next!==preparationId){
      preparationId=next;generation+=1;jobId=null;clear();
      status('unprepared',next?'Generated images have not been built.':'Prepare a generated package first.');
    }
    const receipt=root.antithesisPreparationReceipt;
    const eligible=receipt?.id===preparationId&&receipt?.source?.kind==='build';
    const needsBuild=receipt?.build_requirements?.length>0;
    get('build-images').disabled=busy||!eligible||['built','not-required'].includes(state)||!root.dataset.antithesisToken;
    get('build-images').textContent=jobId&&state==='error'?'Check build status':eligible&&!needsBuild?'Save image receipt':'Build images';
    if(!busy&&state==='unprepared'&&receipt?.id===preparationId){
      const message=receipt.source.kind!=='build'?'Supplied packages are staged without running their builds.'
        :needsBuild?'Ready to build the generated images on this Dwarf host.':'No image builds are needed. Save an image receipt to continue to public verification; this does not run Docker.';
      get('image-build-state').textContent=message;
    }
  }
  async function api(action,payload){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await fetch('/api/antithesis-run/package/'+action,{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({token:root.dataset.antithesisToken,...payload}),signal:controller.signal});
      const body=await response.json();
      if(!response.ok||!body.ok)throw Error(body.error?.message||'Image build request failed.');
      return body;
    }finally{clearTimeout(timer);}
  }
  function checkJob(job,id){
    if(!job||!/^[a-f0-9]{32}$/.test(job.id)||job.preparation_id!==id
       ||!['queued','building','built','not-required','failed','interrupted'].includes(job.state)
       ||job.execution_ready!==false||job.submission_ready!==false)throw Error('Invalid image build status.');
  }
  get('build-images').addEventListener('click',async()=>{
    sync();if(get('build-images').disabled)return;
    const stamp=generation,id=preparationId;busy=true;clear();sync();
    try{
      // The client retains this ID even when the start response is lost.
      // Repeating start with the same ID recovers its saved job, never builds twice.
      jobId=jobId||Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');
      status('queued','Starting or recovering the generated image build…');
      const started=await api('build',{preparation_id:id,request_id:jobId});if(stamp!==generation)return;
      checkJob(started.build,id);if(started.build.id!==jobId)throw Error('Image build identity changed.');
      while(stamp===generation){
        const response=await api('build-status',{build_id:jobId});if(stamp!==generation)return;
        const job=response.build;checkJob(job,id);if(job.id!==jobId)throw Error('Image build identity changed.');
        if(['built','not-required'].includes(job.state)){
          const response=await api('build-result',{build_id:jobId});if(stamp!==generation)return;
          const result=response.preparation,receipt=result?.receipt;
          if(!receipt||receipt.id!==jobId||receipt.preparation_id!==id||receipt.state!==job.state
            ||receipt.execution_ready!==false||receipt.submission_ready!==false||!Array.isArray(receipt.built_images)
            ||typeof result.archive_base64!=='string')throw Error('Invalid built package receipt.');
          const bytes=Uint8Array.from(atob(result.archive_base64),c=>c.charCodeAt(0));
          blobUrl=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
          get('image-build-download').href=blobUrl;get('image-build-download').download=result.archive_name;
          get('image-build-receipt').textContent=JSON.stringify(receipt,null,2);
          get('image-build-summary').textContent=`Build ${jobId} · ${receipt.built_images.length} images built locally from preparation ${id}. Publication and remote validation remain separate.`;
          root.antithesisBuiltReceipt=receipt;
          get('image-build-result').hidden=false;status(job.state,job.message);break;
        }
        status(job.state,`Build ${jobId} · ${job.message}`);
        if(['failed','interrupted'].includes(job.state)){jobId=null;break;}
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    }catch(error){
      if(stamp===generation){clear();status('error',error.name==='AbortError'
        ?'Status request timed out. The build may continue on the host; check its status before retrying.':error.message);}
    }finally{busy=false;sync();}
  });
  root.addEventListener('antithesis-preparation-updated',sync);
  window.addEventListener('pageshow',sync);
  sync();
})();
