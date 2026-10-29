import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Operations,canonical} from '../lib/operations.ts';
const storage=()=>{const map=new Map<string,string>();return {getItem:(k:string)=>map.get(k)??null,setItem:(k:string,v:string)=>{map.set(k,v);}};};
const ok=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status});

test('all mutation endpoints: durable identity, exact confirmed replay, payload isolation',async()=>{
 const original=fetch;
 try{for(const endpoint of ['/cases','/cases/c/validate','/cases/c/optimize','/plans/p/approve','/shipments/s/dispatch','/shipments/s/cancel-reservation','/cases/c/receipts','/cases/c/reconcile','/cases/c/replan','/shipments/s/checkpoints']){
  const s=storage();let calls=0;const bodies:Record<string,unknown>[]=[];
  globalThis.fetch=async(_,init)=>{calls++;bodies.push(JSON.parse(String(init?.body)));return ok({saved:true});};
  await new Operations('http://test',s).execute(endpoint,{actor_id:'a',decision:'APPROVE'});
  await new Operations('http://test',s).execute(endpoint,{decision:'APPROVE',actor_id:'a'});assert.equal(calls,1,endpoint);
  await new Operations('http://test',s).execute(endpoint,{actor_id:'a',decision:'REJECT'});assert.equal(calls,2);
  if(bodies[0].transaction_ref)assert.notEqual(bodies[0].transaction_ref,bodies[1].transaction_ref);
 }}finally{globalThis.fetch=original;}
});

test('unknown network/timeout/5xx persists on reload; only exact backend proof clears; no POST retry',async()=>{
 const original=fetch;
 try{for(const failure of [()=>Promise.reject(new TypeError('network')),()=>Promise.reject(new DOMException('timeout','TimeoutError')),async()=>ok({detail:'server'},500),async()=>new Response('timeout',{status:408}),async()=>new Response('bad success',{status:200})]){
  const s=storage();const op=new Operations('http://test',s);let body:Record<string,unknown>={};let posts=0;
  globalThis.fetch=async(_,init)=>{posts++;body=JSON.parse(String(init?.body));return failure();};
  await assert.rejects(op.execute('/cases/c/reconcile',{actor_id:'a',receipt_id:'r'}),/belum diketahui/);
  const restored=new Operations('http://test',s);assert.equal(restored.blocked(),true);
  await assert.rejects(restored.execute('/cases',{}),/belum diketahui/);assert.equal(posts,1);
  globalThis.fetch=async()=>ok({detail:'absent'},404);assert.equal(await restored.reconcile(),false);
  globalThis.fetch=async()=>ok({endpoint:'/cases/c/reconcile',payload:{...body,receipt_id:'wrong'},result:{saved:true}});assert.equal(await restored.reconcile(),false);
  globalThis.fetch=async()=>ok({endpoint:'/cases/c/reconcile',payload:body,result:{saved:true}});assert.equal(await restored.reconcile(),true);
  assert.deepEqual(await restored.execute('/cases/c/reconcile',{receipt_id:'r',actor_id:'a'}),{saved:true});
 }}finally{globalThis.fetch=original;}
});

test('409/422 definitive, same identity retry; synchronous double click lock',async()=>{
 const original=fetch;
 try{for(const status of [409,422]){const s=storage();const op=new Operations('http://test',s);const bodies:unknown[]=[];
  globalThis.fetch=async(_,init)=>{bodies.push(JSON.parse(String(init?.body)));return ok({detail:'reason'},status);};
  await assert.rejects(op.execute('/cases/c/replan',{actor_id:'a'}));assert.equal(op.blocked(),false);
  await assert.rejects(op.execute('/cases/c/replan',{actor_id:'a'}));assert.equal(canonical(bodies[0]),canonical(bodies[1]));
 }
 const op=new Operations('http://test',storage());let resolve!:(r:Response)=>void;globalThis.fetch=()=>new Promise(r=>{resolve=r;});
 const first=op.execute('/cases',{});await assert.rejects(op.execute('/cases',{}),/sedang berjalan/);resolve(ok({id:'c'}));await first;
 }finally{globalThis.fetch=original;}
});

test('storage corrupt/blocked/quota/external clear fails closed without POST',async()=>{
 const original=fetch;let calls=0;globalThis.fetch=async()=>{calls++;return ok({});};
 try{
  for(const s of [{getItem:()=>'{',setItem:()=>{}},{getItem:()=>null,setItem:()=>{throw Error('quota');}},{getItem:()=>{throw Error('denied');},setItem:()=>{}}])await assert.rejects(new Operations('http://test',s).execute('/cases',{}));
  assert.equal(calls,0);
  const s=storage();const op=new Operations('http://test',s);await op.execute('/cases',{});s.setItem('pangannadi.operations.v1:http://test','[]');await assert.rejects(op.execute('/cases',{new:true}),/Jurnal berubah/);assert.equal(calls,1);
 }finally{globalThis.fetch=original;}
});

test('checkpoint keyed loss recovery survives reload; endpoint/payload exact, no mutation retry',async()=>{
 const original=fetch;const s=storage();let body:Record<string,unknown>={};let posts=0;
 try{globalThis.fetch=async(_,init)=>{posts++;body=JSON.parse(String(init?.body));throw Error('response lost');};
 const input={actor_id:'simulation',event_type:'AT_CHECKPOINT',note:null};
 await assert.rejects(new Operations('http://test',s).execute('/shipments/s/checkpoints',input));assert.equal(typeof body.transaction_ref,'string');
 const restored=new Operations('http://test',s);
 globalThis.fetch=async()=>ok({endpoint:'/shipments/other/checkpoints',payload:body,result:{checkpoint_id:1}});assert.equal(await restored.reconcile(),false);
 globalThis.fetch=async()=>ok({endpoint:'/shipments/s/checkpoints',payload:body,result:{checkpoint_id:1}});assert.equal(await restored.reconcile(),true);
 assert.deepEqual(await restored.execute('/shipments/s/checkpoints',input),{checkpoint_id:1});assert.equal(posts,1);
 }finally{globalThis.fetch=original;}
});

test('create/optimize/decision ambiguity never inferred from similar state or absent proof',async()=>{
 const original=fetch;try{for(const endpoint of ['/cases','/cases/c/optimize','/plans/p/approve']){
 const s=storage();globalThis.fetch=async()=>{throw Error('lost response');};await assert.rejects(new Operations('http://test',s).execute(endpoint,{}));
 globalThis.fetch=async()=>{throw Error('must not infer');};assert.equal(await new Operations('http://test',s).reconcile(),false);
 }}finally{globalThis.fetch=original;}
});
