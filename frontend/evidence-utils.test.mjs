import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {evidenceDirectory,claimDirectory,saveScreenshot} from './evidence-utils.mjs';

test('API origins accept same-origin /api gateway and reject credentials or other paths',()=>{
 for(const [value,valid] of [['https://localhost:8443/api',true],['http://127.0.0.1:8000',true],['https://user:pass@localhost/api',false],['https://localhost/other',false],['https://localhost/api?x=1',false],['https://localhost?token=x',false],['https://localhost#fragment',false]]){
  const result=spawnSync(process.execPath,['--input-type=module','-e',`import ${JSON.stringify(new URL('./evidence-utils.mjs',import.meta.url).href)}`],{env:{...process.env,E2E_API_ORIGIN:value}});
  assert.equal(result.status===0,valid,value);
 }
});

test('unique UTC UUID paths, exclusive claim and screenshot, finalization guard',()=>{
 const root=mkdtempSync(join(tmpdir(),'m7c-'));
 try{
  const first=evidenceDirectory(root),second=evidenceDirectory(root);
  assert.notEqual(first,second);
  claimDirectory(first);
  assert.throws(()=>claimDirectory(first));
  assert.throws(()=>evidenceDirectory(root,first.split(/[\\/]/).at(-1)));
  saveScreenshot(first,'screen.png',Buffer.from('first'));
  assert.throws(()=>saveScreenshot(first,'screen.png',Buffer.from('second')));
  assert.equal(readFileSync(join(first,'screen.png'),'utf8'),'first');
  assert.throws(()=>saveScreenshot(first,'../outside.png',Buffer.from('no')));
  writeFileSync(join(first,'manifest.json'),'{}');
  assert.throws(()=>saveScreenshot(first,'later.png',Buffer.from('no')));
 }finally{rmSync(root,{recursive:true,force:true});}
});
