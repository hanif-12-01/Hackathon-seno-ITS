import {test,expect} from '@playwright/test';
import {apiOrigin,screenshot} from '../evidence-utils.mjs';

async function prepared(request:any){
 const n=crypto.randomUUID();
 const response=await request.post(`${apiOrigin}/cases`,{data:{destination_region:`NET ${n}`,verified_demand:25,is_demand_verified:true,created_by:'network-test',deadline_hours:48,warehouses:[{warehouse_id:`NET-${n}`,physical_stock:100,local_demand:0,safety_stock:0,daily_dispatch_capacity:100,cost_per_kg:10,snapshot_time:new Date().toISOString()}]}});
 expect(response.status()).toBe(201);const {case_id}=await response.json();
 expect((await request.post(`${apiOrigin}/cases/${case_id}/validate`)).ok()).toBe(true);
 const plan=await(await request.post(`${apiOrigin}/cases/${case_id}/optimize`)).json();
 return {caseId:case_id,plan};
}
async function open(page:any,id:string,section='Approval Center'){
 await page.goto(`/#${encodeURIComponent(section)}`);
 await page.getByLabel('Distribution case',{exact:true}).selectOption(id);
 await expect(page.locator('.case-strip')).toContainText(id);
 await page.getByLabel('Aktor operasional',{exact:true}).fill('network-operator');
}

test('NET01 pre-send abort; NET03 reload pending stays locked without POST; unkeyed approval unknown',async({page,request})=>{
 const {caseId,plan}=await prepared(request);await open(page,caseId);
 let posts=0;await page.route(`**/plans/${plan.plan_id}/approve`,async route=>{posts++;await route.abort('failed');});
 await page.getByRole('button',{name:'Approve Plan v1'}).click();await page.getByRole('button',{name:'Konfirmasi keputusan'}).click();
 await expect(page.getByRole('button',{name:'Rekonsiliasi readback (tanpa retry mutasi)'})).toBeVisible();
 expect((await(await request.get(`${apiOrigin}/plans/${plan.plan_id}`)).json()).approval_status).toBe('PENDING_APPROVAL');
 await page.evaluate(()=>{for(const key of Object.keys(localStorage)){if(key.startsWith('pangannadi.operations.v1:')){const rows=JSON.parse(localStorage.getItem(key)!);for(const row of rows)if(row.state==='uncertain')row.state='pending';localStorage.setItem(key,JSON.stringify(rows));}}});
 await page.reload();await expect(page.getByRole('button',{name:'Rekonsiliasi readback (tanpa retry mutasi)'})).toBeVisible();
 await page.getByRole('button',{name:'Rekonsiliasi readback (tanpa retry mutasi)'}).click();
 expect(posts).toBe(1);await expect(page.getByLabel('Distribution case',{exact:true})).toBeDisabled();
 await screenshot(page,'net01-net03-unknown.png');
});

test('NET02 actual dispatch commit response lost; exact keyed readback; NET04 double click; NET07 dialog cancel',async({page,request})=>{
 const {caseId,plan}=await prepared(request);await open(page,caseId);
 await page.getByRole('button',{name:'Approve Plan v1'}).click();await page.getByRole('button',{name:'Kembali',exact:true}).click();
 expect((await(await request.get(`${apiOrigin}/plans/${plan.plan_id}`)).json()).approval_status).toBe('PENDING_APPROVAL');
 await page.getByRole('button',{name:'Approve Plan v1'}).click();await page.getByRole('button',{name:'Konfirmasi keputusan'}).click();await expect(page.locator('.case-strip')).toContainText('APPROVED');
 await page.locator('nav a').filter({hasText:'Control Tower'}).click();
 const current=await(await request.get(`${apiOrigin}/plans/${plan.plan_id}`)).json();const shipment=current.shipments[0];
 let posts=0;let key='';let committed:any;
 await page.route(`**/shipments/${shipment.shipment_id}/dispatch`,async route=>{posts++;key=route.request().postDataJSON().transaction_ref;const response=await route.fetch();expect(response.status()).toBe(200);committed=await response.json();await route.abort('failed');});
 await page.locator('[name=actual]').fill('25');
 await page.getByRole('button',{name:/Simpan dispatch/}).evaluate((button:HTMLButtonElement)=>{button.click();button.click();});
 await expect(page.getByRole('status').filter({hasText:'Operasi terkonfirmasi melalui readback backend.'})).toBeVisible();
 expect(posts).toBe(1);expect(committed.actual_dispatched_quantity_kg).toBe(25);
 const proof=await(await request.get(`${apiOrigin}/operations/${key}`)).json();expect(proof.result).toEqual(committed);expect(proof.endpoint).toBe(`/shipments/${shipment.shipment_id}/dispatch`);
 const state=await(await request.get(`${apiOrigin}/shipments/${shipment.shipment_id}`)).json();expect(state.actual_dispatched_quantity_kg).toBe(25);
 await page.reload();await expect(page.getByLabel('Distribution case',{exact:true})).toBeEnabled();expect(posts).toBe(1);
 const authority=await(await request.get(`${apiOrigin}/warehouses/${shipment.warehouse_id}/capacity`)).json();expect(authority.physical_stock).toBe(75);
 const attemptedReset=await request.post(`${apiOrigin}/cases`,{data:{destination_region:'NET08 attempted inventory reset',created_by:'network-test',verified_demand:1,is_demand_verified:true,deadline_hours:48,warehouses:[{warehouse_id:shipment.warehouse_id,physical_stock:999999,local_demand:0,safety_stock:0,daily_dispatch_capacity:999999,cost_per_kg:10}]}});expect(attemptedReset.status()).toBe(201);
 expect(await(await request.get(`${apiOrigin}/warehouses/${shipment.warehouse_id}/capacity`)).json()).toEqual(authority);
 await page.getByLabel('Distribution case',{exact:true}).selectOption(caseId);await expect(page.locator('main')).toContainText('75 kg');
 await screenshot(page,'net02-net04-net08-confirmed.png');
});

test('NET06 unavailable API fail closed; keyboard and mobile targeted',async({page})=>{
 await page.route(`${apiOrigin}/**`,route=>route.abort('connectionrefused'));
 await page.goto('/');await expect(page.getByText('API tidak terhubung',{exact:true})).toBeVisible();
 await page.keyboard.press('Tab');await expect(page.locator(':focus')).toHaveText('Lewati navigasi');
 await page.keyboard.press('Enter');await expect(page.locator('main')).toBeFocused();
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await screenshot(page,'net06-keyboard-mobile.png');
});
