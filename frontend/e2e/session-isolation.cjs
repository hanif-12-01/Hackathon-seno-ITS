// Local-only account-switch smoke. Run: node frontend/e2e/session-isolation.cjs
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8766';
const aliceKey='pangannadi.operations.v2:%2Fapi:review.operator';
async function login(page,user,password){await page.goto(base+'/#login');await page.getByLabel('Nama akun operator',{exact:true}).fill(user);await page.getByLabel('Kata sandi',{exact:true}).fill(password);await page.getByRole('button',{name:'Masuk ke ruang kerja',exact:true}).click();await page.locator('.overview-content').waitFor();await page.waitForFunction(()=>!document.querySelector('.dashboard-skeleton'));}
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext();const page=await context.newPage();
 const errors=[];const operations=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().includes('/operations/'))operations.push(r.url());});
 try{
  await login(page,'review.operator','local-review-only');
  await page.evaluate(key=>{
   const body={transaction_ref:'qa-uncertain-alice',actor_id:'review.operator'};
   const endpoint='/cases/QA-ONLY/replan';
   const canonical=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
   localStorage.setItem(key,JSON.stringify([{id:'qa-uncertain-alice',endpoint,body,fingerprint:canonical([endpoint,body]),state:'uncertain'}]));
   sessionStorage.setItem('pangannadi:last-case:v2:%2Fapi:review.operator','ALICE-ONLY-SELECTION');
  },aliceKey);
  await page.getByRole('button',{name:'Keluar',exact:true}).click();
  assert.equal(await page.locator('.app').count(),0);
  const before=operations.length;
  await login(page,'review.second','local-second-only');
  assert.equal(operations.length,before,'Bob must not reconcile Alice journal');
  assert(!(await page.locator('main').innerText()).includes('Hasil transaksi belum dapat dipastikan'));
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('pangannadi:last-case:v2:%2Fapi:review.second')),await page.getByLabel('Distribution case',{exact:true}).inputValue());
  await page.getByRole('navigation',{name:'Navigasi 5 area'}).getByRole('link',{name:'Perencanaan Distribusi',exact:true}).click();
  await page.getByRole('button',{name:'Periksa kesiapan data',exact:true}).click();
  await page.waitForFunction(()=>localStorage.getItem('pangannadi.operations.v2:%2Fapi:review.second')?.includes('confirmed'));
  const bob=await page.evaluate(()=>JSON.parse(localStorage.getItem('pangannadi.operations.v2:%2Fapi:review.second')));
  assert(bob.every(op=>!JSON.stringify(op).includes('qa-uncertain-alice')));
  await page.getByRole('button',{name:'Keluar',exact:true}).click();
  await login(page,'review.operator','local-review-only');
  await page.getByRole('button',{name:'Periksa status transaksi',exact:true}).waitFor();
  assert(operations.some(url=>url.endsWith('/operations/qa-uncertain-alice')));
  assert.equal(await page.evaluate(key=>JSON.parse(localStorage.getItem(key))[0].state,aliceKey),'uncertain');
  await page.getByRole('button',{name:'Keluar',exact:true}).click();
  await page.reload();assert.equal(await page.locator('.app').count(),0);
  assert.equal(await page.evaluate(()=>sessionStorage.length),0,'Logout clears each operator selection');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'PASS',checks:8,scope:'Actual local API two operators; synthetic uncertain journal injection; no cloud',pageErrors:errors}));
 }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
