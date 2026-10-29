import {test,expect} from '@playwright/test';
import {apiOrigin,screenshot} from '../evidence-utils.mjs';

test('P1 manual checkpoint real timeline, lost response exact recovery, reload, 409/422',async({page,request})=>{
 const n=crypto.randomUUID();const warehouse=`CP-${n}`;
 const created=await request.post(`${apiOrigin}/cases`,{data:{destination_region:`Checkpoint ${n}`,verified_demand:25,is_demand_verified:true,created_by:'simulation',warehouses:[{warehouse_id:warehouse,physical_stock:100,local_demand:0,safety_stock:0,daily_dispatch_capacity:100,cost_per_kg:1}]}});expect(created.status()).toBe(201);
 const {case_id}=await created.json();const plan=await(await request.post(`${apiOrigin}/cases/${case_id}/optimize`)).json();
 expect((await request.post(`${apiOrigin}/plans/${plan.plan_id}/approve`,{data:{actor_id:'simulation'}})).status()).toBe(200);
 const shipment=(await(await request.get(`${apiOrigin}/plans/${plan.plan_id}`)).json()).shipments[0];const path=`/shipments/${shipment.shipment_id}/checkpoints`;
 await page.goto('/#Control%20Tower');await page.getByLabel('Distribution case',{exact:true}).selectOption(case_id);await page.getByLabel('Aktor operasional',{exact:true}).fill('simulation');
 const section=page.getByRole('region',{name:`Checkpoint ${warehouse}`,exact:true});
 await expect(section.getByText('Belum ada checkpoint.',{exact:true})).toBeVisible();await expect(section.getByRole('button',{name:`Simpan checkpoint ${warehouse}`,exact:true})).toBeDisabled();
 expect((await request.post(`${apiOrigin}/shipments/${shipment.shipment_id}/dispatch`,{data:{actor_id:'simulation',transaction_ref:`dispatch-${n}`,planned_quantity_kg:25,actual_dispatched_quantity_kg:25}})).status()).toBe(200);
 await page.getByRole('button',{name:'Muat ulang',exact:true}).click();await expect(section.getByRole('button',{name:`Simpan checkpoint ${warehouse}`,exact:true})).toBeEnabled();
 const before=await(await request.get(`${apiOrigin}/warehouses/${warehouse}/capacity`)).json();let posts=0;let key='';let committed:any;
 await page.route(`**${path}`,async route=>{if(route.request().method()!=='POST'){await route.continue();return;}posts++;key=route.request().postDataJSON().transaction_ref;const response=await route.fetch();expect(response.status()).toBe(201);committed=await response.json();await route.abort('failed');});
 await section.getByLabel(`Jenis checkpoint ${warehouse}`,{exact:true}).selectOption('AT_CHECKPOINT');await section.getByLabel(`Catatan checkpoint ${warehouse}`,{exact:true}).fill('Manual checkpoint observation');
 await section.getByRole('button',{name:`Simpan checkpoint ${warehouse}`,exact:true}).evaluate((button:HTMLButtonElement)=>{button.click();button.click()});
 await expect(page.getByRole('status').filter({hasText:'Operasi terkonfirmasi melalui readback backend.'})).toBeVisible();await expect(section.getByRole('list',{name:'Timeline checkpoint'})).toContainText('Manual checkpoint observation');expect(posts).toBe(1);
 expect((await(await request.get(`${apiOrigin}/operations/${key}`)).json()).result).toEqual(committed);
 await page.unroute(`**${path}`);await page.reload();await page.getByLabel('Distribution case',{exact:true}).selectOption(case_id);await page.getByLabel('Aktor operasional',{exact:true}).fill('simulation');await expect(section.getByRole('list',{name:'Timeline checkpoint'})).toContainText('AT_CHECKPOINT');
 expect(await(await request.get(`${apiOrigin}/warehouses/${warehouse}/capacity`)).json()).toEqual(before);
 await page.route(`**${path}`,async route=>{if(route.request().method()!=='POST'){await route.continue();return;}const body=route.request().postDataJSON();await route.continue({postData:JSON.stringify({...body,event_type:'ARRIVED'})});});
 const invalid=page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST');await section.getByRole('button',{name:`Simpan checkpoint ${warehouse}`,exact:true}).click();expect((await invalid).status()).toBe(422);await expect(page.getByRole('alert')).toContainText('Validasi gagal: body.event_type');await page.unroute(`**${path}`);
 expect((await request.post(`${apiOrigin}/cases/${case_id}/receipts`,{data:{inspector_id:'simulation',transaction_ref:`receipt-${n}`,plan_id:plan.plan_id,shipment_ids:[shipment.shipment_id],total_dispatched_kg:25,arrived_weight_kg:25,rejected_weight_kg:0}})).status()).toBe(201);
 const conflict=page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST');await section.getByRole('button',{name:`Simpan checkpoint ${warehouse}`,exact:true}).click();expect((await conflict).status()).toBe(409);await expect(page.getByRole('alert')).toContainText('Konflik');await expect(section.getByRole('listitem')).toHaveCount(1);
 await screenshot(page,'p1-checkpoint-timeline.png');
});
