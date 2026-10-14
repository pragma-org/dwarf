// Real DOM regression: every generic cap can expand without selecting Advanced.
// Requires Playwright and its Chromium browser (or DWARF_BROWSER_CHANNEL=chrome).
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
const script=path.resolve(__dirname,'../../dwarf/dashboard/static/js/basic-reduce.js');
const repeat=(n,fn)=>Array.from({length:n},(_,i)=>fn(i)).join('');
const fixtures=[
 ['collection','<table><tbody>'+repeat(12,i=>'<tr><td>'+i+'</td></tr>')+'</tbody></table>',4],
 ['sections',repeat(5,i=>'<section>Section '+i+'</section>'),2],
 ['disclosures',repeat(4,i=>'<details><summary>Detail '+i+'</summary></details>'),2],
 ['definitions','<dl>'+repeat(12,i=>'<dt>Term '+i+'</dt><dd>Description</dd>')+'</dl>',4],
 ['prose',repeat(12,i=>'<p>Paragraph '+i+'</p>'),4],
 ['repeated blocks',repeat(6,i=>'<article class="card">Card '+i+'</article>'),3],
];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.DWARF_BROWSER_CHANNEL?{channel:process.env.DWARF_BROWSER_CHANNEL}:{})});
 try{
  const page=await browser.newPage();
  for(const [name,html,capped] of fixtures){
   await page.setContent('<style>.is-basic-capped{display:none}</style><body data-ui="basic"><main>'+html+'</main></body>');
   await page.addScriptTag({path:script});
   assert.equal(await page.locator('.is-basic-capped').count(),capped,name+' initial compact state');
   assert.equal(await page.locator('button.b-reduce-more').count(),1,name+' expands using a button, not Advanced navigation');
   await page.locator('button.b-reduce-more').click();
   assert.equal(await page.locator('.is-basic-capped').count(),0,name+' reveals all retained content');
   assert.equal(await page.locator('.b-reduce-more').count(),0,name+' removes the spent control');
   assert.equal(await page.locator('body').getAttribute('data-ui'),'basic',name+' remains Basic');
   console.log('PASS '+name);
  }
  await page.setContent('<body data-ui="bento"><main>'+fixtures[0][1]+'</main></body>');
  await page.addScriptTag({path:script});
  assert.equal(await page.locator('.is-basic-capped,.b-reduce-more').count(),0,'Advanced is untouched');
  console.log('PASS Advanced unchanged');
  if(process.env.DWARF_DASHBOARD_URL){
   for(const view of ['basic','advanced']){
    await page.goto(process.env.DWARF_DASHBOARD_URL+'/run?view='+view,{waitUntil:'networkidle'});
    // Load the same module source as a classic script to call its internal
    // navigation helper without launching any run or changing server state.
    await page.addScriptTag({path:path.resolve(__dirname,'../../dwarf/dashboard/static/js/run-wizard.js')});
    const href=await page.evaluate(()=>addRunLink('navigation/check'));
    assert.equal(href,'/operate/runs/navigation%2Fcheck','evidence navigation must not inherit '+view);
    console.log('PASS '+view+' run evidence navigation defaults to Basic');
   }
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
