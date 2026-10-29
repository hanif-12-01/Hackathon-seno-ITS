import {ApiError,request} from './api.ts';

type Operation = {id:string;endpoint:string;fingerprint:string;body:Record<string,unknown>;state:'pending'|'uncertain'|'confirmed'|'rejected';result?:unknown};
export const canonical=(value:unknown):string=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
export class Operations {
 private running=false;
 private key:string;
 private base:string;
 private storage:Pick<Storage,'getItem'|'setItem'>;
 constructor(base:string,storage:Pick<Storage,'getItem'|'setItem'>){this.base=base;this.storage=storage;this.key=`pangannadi.operations.v1:${base}`;} public poisoned=false; private loaded=false; private snapshot:string|null=null;
 private read():Operation[]{
  if(this.poisoned)throw Error('Penyimpanan tidak aman; mutasi diblokir.');
  const raw=this.storage.getItem(this.key);if(this.loaded&&raw!==this.snapshot){this.poisoned=true;throw Error('Jurnal berubah di luar sesi; mutasi diblokir.');}this.loaded=true;this.snapshot=raw;if(raw===null)return [];
  const rows:Operation[]=JSON.parse(raw);
  if(!Array.isArray(rows)||rows.some(r=>!r||typeof r.id!=='string'||typeof r.endpoint!=='string'||!r.body||r.fingerprint!==canonical([r.endpoint,r.body])||!['pending','uncertain','confirmed','rejected'].includes(r.state)))throw Error('Jurnal operasi rusak; mutasi diblokir.');
  return rows;
 }
 private save(rows:Operation[]){try{const text=JSON.stringify(rows);if(this.storage.getItem(this.key)!==this.snapshot)throw Error('Jurnal berubah.');this.storage.setItem(this.key,text);if(this.storage.getItem(this.key)!==text)throw Error('Penyimpanan operasi gagal.');this.snapshot=text;}catch(e){this.poisoned=true;throw e;}}
 blocked(){return this.running||this.read().some(r=>r.state==='pending'||r.state==='uncertain');}
 async execute<T>(endpoint:string,input:Record<string,unknown>):Promise<T>{
  if('transaction_ref' in input)throw Error('Identitas operasi hanya dibuat oleh jurnal.');
  if(Array.isArray(input.shipment_ids))input={...input,shipment_ids:[...input.shipment_ids].sort()};
  if(this.running)throw Error('Operasi sedang berjalan.');this.running=true;
  try{
   const rows=this.read();if(rows.some(r=>r.state==='pending'||r.state==='uncertain'))throw Error('Hasil belum diketahui. Rekonsiliasi readback wajib; jangan kirim ulang.');
   const fingerprint=canonical([endpoint,input]);let op=rows.find(r=>{const {transaction_ref,...body}=r.body;return canonical([r.endpoint,body])===fingerprint;});
   if(op?.state==='confirmed')return op.result as T;
   if(!op){const id=crypto.randomUUID();const keyed=/\/(dispatch|cancel-reservation|receipts|reconcile|replan|checkpoints)$/.test(endpoint);const body=keyed?{...input,transaction_ref:id}:input;op={id,endpoint,body,fingerprint:canonical([endpoint,body]),state:'pending'};rows.push(op);}
   op.state='pending';this.save(rows);
   let result:T;
   try{result=await request<T>(this.base,endpoint,op.body);}
   catch(e){op.state=e instanceof ApiError&&e.status>=400&&e.status<500&&e.status!==408?'rejected':'uncertain';this.save(rows);if(op.state==='uncertain')throw Error('Hasil mutasi belum diketahui (jaringan/timeout/server). Rekonsiliasi readback; tidak ada retry otomatis.');throw e;}
   op.state='confirmed';op.result=result;this.save(rows);return result;
  }finally{this.running=false;}
 }
 async reconcile(){
  if(this.running)throw Error('Operasi sedang berjalan.');this.running=true;
  try{const rows=this.read();for(const op of rows.filter(r=>r.state==='pending'||r.state==='uncertain')){
   if(!('transaction_ref' in op.body))continue;
   try{const proof=await request<{endpoint:string;payload:Record<string,unknown>;result:unknown}>(this.base,`/operations/${encodeURIComponent(op.id)}`);
    if(proof.endpoint===op.endpoint&&canonical(proof.payload)===canonical(op.body)){op.state='confirmed';op.result=proof.result;this.save(rows);}
   }catch(e){if(!(e instanceof ApiError&&e.status===404))throw e;}
  }return !rows.some(r=>r.state==='pending'||r.state==='uncertain');}finally{this.running=false;}
 }
}
