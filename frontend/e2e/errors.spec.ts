import {test,expect} from '@playwright/test';
import {apiOrigin,screenshot} from '../evidence-utils.mjs';
test('live conflict and validation reasons, rejection persisted, AI unavailable fallback, keyboard navigation',async({page,request})=>{
 const n=Date.now();const body={destination_region:`Conflict ${n}`,verified_demand:25,is_demand_verified:true,created_by:'test',warehouses:[{warehouse_id:`ERR-${n}`,physical_stock:100,local_demand:0,safety_stock:0,daily_dispatch_capacity:100,cost_per_kg:10}]};
 const created=await(await request.post(`${apiOrigin}/cases`,{data:body})).json();const id=created.case_id;
 await request.post(`${apiOrigin}/cases/${id}/validate`);const plan=await(await request.post(`${apiOrigin}/cases/${id}/optimize`)).json();
 await page.goto('/#Approval%20Center');await page.getByLabel('Distribution case',{exact:true}).selectOption(id);await page.getByLabel('Aktor operasional',{exact:true}).fill('conflict-operator');await page.getByRole('button',{name:'Approve Plan v1'}).click();
 const rejected=await request.post(`${apiOrigin}/plans/${plan.plan_id}/approve`,{data:{actor_id:'other-operator',decision:'REJECT'}});expect(rejected.status()).toBe(200);
 await page.getByRole('button',{name:'Konfirmasi keputusan'}).click();await expect(page.getByRole('alert')).toContainText('Konflik');await expect(page.locator('.case-strip')).toContainText('REJECTED');await screenshot(page,'conflict.png');
 // A second real case, demand not verified. Gate failure renders; optimize stays disabled.
 const unverified=await(await request.post(`${apiOrigin}/cases`,{data:{...body,destination_region:`Unverified ${n}`,is_demand_verified:false,warehouses:[{...body.warehouses[0],warehouse_id:`UNVERIFIED-${n}`}]}})).json();
 await page.getByRole('button',{name:'Muat ulang'}).click();await page.getByLabel('Distribution case',{exact:true}).selectOption(unverified.case_id);await page.locator('nav a').filter({hasText:'Distribution Planning'}).click();await page.getByRole('button',{name:'Verifikasi Data Readiness'}).click();await expect(page.locator('main')).toContainText('FAILED');await expect(page.getByRole('button',{name:'Generate Plan v1'})).toBeDisabled();
 // Server 422 is obtained over HTTP, never replaced with fake business output.
 const invalid=await request.post(`${apiOrigin}/cases`,{data:{...body,verified_demand:0.5}});expect(invalid.status()).toBe(422);
 // Verify AI unavailable state renders honestly when status reports MODEL_NOT_TRAINED
 await page.route('**/forecasting/status', route => route.fulfill({status:200, contentType:'application/json', body:JSON.stringify({available:false, status:'MODEL_NOT_TRAINED', reason:'Artefak belum dilatih', commodity:'Cabai Rawit Merah', geographic_scope:'Provinsi Jawa Timur'})}));
 await page.locator('nav a').filter({hasText:'AI Price Intelligence'}).click();
 await page.getByRole('button',{name:'Muat ulang prediksi'}).click();
 await expect(page.getByRole('heading',{name:'AI FORECASTING NOT CONFIGURED'})).toBeVisible();
 await expect(page.getByTestId('forecast-unavailable-reason')).toContainText('Model not trained');
 await page.unroute('**/forecasting/status');
 await page.keyboard.press('Tab');expect(await page.evaluate(()=>document.activeElement?.tagName)).not.toBe('BODY');
});
