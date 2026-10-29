import {randomUUID} from 'node:crypto';
import {mkdirSync,existsSync,writeFileSync} from 'node:fs';
import {resolve,join} from 'node:path';

export function evidenceDirectory(root,runId=process.env.M7C_RUN_ID || `RUN-${new Date().toISOString().replace(/[-: .]/g,'').replace('Z','000Z')}-${randomUUID().replaceAll('-','')}`){
 if(!/^RUN-\d{8}T\d{12}Z-[0-9a-f]{32}$/.test(runId))throw new Error('Invalid UTC/UUID run ID');
 const directory=resolve(root,runId);
 if(existsSync(directory))throw new Error(`Evidence directory already exists: ${directory}`);
 return directory;
}
export function claimDirectory(directory){mkdirSync(directory,{recursive:false});}
export function saveScreenshot(directory,name,buffer){
 if(!/^[a-z0-9_-]+\.png$/.test(name))throw new Error('Invalid screenshot name');
 if(existsSync(join(directory,'manifest.json')) || existsSync(join(directory,'manifest.sha256')))throw new Error('Finalized evidence directory');
 writeFileSync(join(directory,name),buffer,{flag:'wx'});
}
export async function screenshot(page,name){
 const directory=process.env.M7C_E2E_DIRECTORY;
 if(!directory)throw new Error('Missing claimed evidence directory');
 saveScreenshot(directory,name,await page.screenshot({fullPage:true}));
}
export const apiOrigin=process.env.E2E_API_ORIGIN || 'http://127.0.0.1:8000';
if(!/^https?:\/\/[^/]+$/.test(apiOrigin) || new URL(apiOrigin).username || new URL(apiOrigin).password)throw new Error('E2E_API_ORIGIN must be an HTTP origin without credentials');
