import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {evidenceDirectory,claimDirectory,saveScreenshot} from './evidence-utils.mjs';

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
