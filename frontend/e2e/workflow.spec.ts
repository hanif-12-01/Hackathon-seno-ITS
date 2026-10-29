import {test,expect} from '@playwright/test';
import {apiOrigin,screenshot} from '../evidence-utils.mjs';
import {rupiah} from '../src/lib/api';
import type {ForecastPredictions,ForecastEvaluation} from '../src/types';
test('UI01–15 live FastAPI workflow: create through recovery approval, AI Price Intelligence, persistence and mobile',async({page,request})=>{
 const suffix=Date.now().toString();
 const go=async(name:string)=>{await page.locator('nav').getByRole('link',{name:new RegExp(name+'$')}).click();};
 await page.goto('/');await expect(page.getByText('API terhubung',{exact:true})).toBeVisible();
 await go('Distribution Planning');
 await page.locator('[name=destination]').fill(`Browser ${suffix}`);await page.locator('[name=demand]').fill('8000');await page.locator('[name=creator]').fill('browser-planner');await page.locator('[name=verified]').check();
 for(const [i,values]of [[0,[35000,20000,5000,6000,0,0,1200]],[1,[22000,15000,3000,4000,0,0,1500]]] as const){await page.locator(`[name=warehouse-${i}]`).fill(`UI-${suffix}-${i}`);for(const [j,key]of ['physical_stock','local_demand','safety_stock','daily_dispatch_capacity','actual_dispatched_today','active_undispatched_reservations','cost_per_kg'].entries())await page.locator(`[name=${key}-${i}]`).fill(String(values[j]));}
 await page.getByRole('button',{name:'Buat kasus',exact:true}).click();await expect(page.locator('.case-strip')).toContainText(`Browser ${suffix}`);
 const caseId=await page.getByLabel('Distribution case',{exact:true}).inputValue();await page.getByLabel('Aktor operasional',{exact:true}).fill('browser-operator');
 await page.getByRole('button',{name:'Verifikasi Data Readiness'}).click();await expect(page.getByRole('button',{name:'Generate Plan v1'})).toBeEnabled();await page.getByRole('button',{name:'Generate Plan v1'}).click();await expect(page.getByRole('heading',{name:/Plan v1/})).toBeVisible();await expect(page.locator('main')).toContainText('10.200.000');
 await screenshot(page,'optimization.png');
 await go('Approval Center');await page.getByRole('button',{name:'Approve Plan v1'}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'Konfirmasi keputusan'}).click();await expect(page.locator('.case-strip')).toContainText('APPROVED');await screenshot(page,'approval.png');
 await go('Control Tower');
 for(const [i,actual]of [[0,'6000'],[1,'1950']] as const){const panel=page.locator('section').filter({has:page.getByRole('heading',{name:new RegExp(`UI-${suffix}-${i} —`)})});await panel.locator('[name=actual]').fill(actual);await panel.getByRole('button',{name:`Simpan dispatch UI-${suffix}-${i}`}).click();await expect(panel.locator('[name=actual]')).toHaveCount(0);}
 const b=page.locator('section').filter({has:page.getByRole('heading',{name:new RegExp(`UI-${suffix}-1 —`)})});await b.locator('[name=cancelled]').fill('50');await b.locator('[name=reason]').fill('Kapasitas muat simulasi');await b.getByRole('button',{name:/Batalkan reservasi/}).click();await expect(b.locator('[name=cancelled]')).toBeDisabled();await expect(page.locator('main')).toContainText('20.050 kg');await screenshot(page,'control-tower.png');
 await go('Receiving & Reconciliation');await page.locator('[name=arrived]').fill('7820');await page.locator('[name=rejected]').fill('110');await page.getByRole('button',{name:'Simpan receipt'}).click();await expect(page.locator('main')).toContainText('7.710 kg');page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Finalize reconciliation'}).click();await expect(page.locator('main')).toContainText('290 kg');await screenshot(page,'reconciliation.png');
 await go('Adaptive Recovery');await page.getByRole('button',{name:'Generate Recovery Plan'}).click();await expect(page.getByRole('heading',{name:/^Plan v2/})).toBeVisible();await expect(page.locator('main')).toContainText('435.000');await expect(page.getByRole('heading',{name:'Perbandingan Plan v1 / Plan v2',exact:true})).toBeVisible();await screenshot(page,'recovery-v2.png');await page.getByRole('button',{name:'Approve Plan v2'}).click();await page.getByRole('button',{name:'Konfirmasi keputusan'}).click();await expect(page.locator('.case-strip')).toContainText('RECOVERY_RESERVED');
 const c=await(await request.get(`${apiOrigin}/cases/${caseId}`)).json();expect(c.plans).toHaveLength(2);expect(c.reservations.find((r:any)=>r.plan_id===c.plans.find((p:any)=>p.version===2).plan_id).reserved_stock_kg).toBe(290);
 const final=await(await request.get(`${apiOrigin}/cases/${caseId}/reconciliation`)).json();expect(final.unmet_demand_kg).toBe(290);const v2=await(await request.get(`${apiOrigin}/plans/${c.plans.find((p:any)=>p.version===2).plan_id}`)).json();expect(v2.shipments[0].actual_dispatched_quantity_kg).toBe(0);
 // Actual conflicting backend decision, not mocked success.
 const conflict=await request.post(`${apiOrigin}/plans/${v2.plan_id}/approve`,{data:{actor_id:'browser-conflict',decision:'REJECT'}});expect(conflict.status()).toBe(409);
 await go('Audit & Evidence');await expect(page.locator('.timeline li')).not.toHaveCount(0);
 await go('Overview');await expect(page.locator('main')).toContainText('290 kg');await screenshot(page,'dashboard.png');
 await page.reload();await page.getByLabel('Distribution case',{exact:true}).selectOption(caseId);await expect(page.locator('.case-strip')).toContainText('RECOVERY_RESERVED');
 await go('AI Price Intelligence');
 await expect(page.getByRole('heading',{name:'AI Price Intelligence — Provenance, Kesegaran Data & Sinyal Risiko'})).toBeVisible();
  const predictionResponse=await request.get(`${apiOrigin}/forecasting/predictions`);expect(predictionResponse.ok()).toBe(true);
  const predictions:ForecastPredictions=await predictionResponse.json();
  const evaluationResponse=await request.get(`${apiOrigin}/forecasting/evaluation`);expect(evaluationResponse.ok()).toBe(true);
  const evaluation:ForecastEvaluation=await evaluationResponse.json();
  await expect(page.locator('main')).toContainText(predictions.data_source);
  await expect(page.locator('main')).toContainText(predictions.model_version);
  expect(predictions.provenance_verification_status).toBeTruthy();
  await expect(page.getByTestId('forecast-provenance-status')).toContainText(predictions.provenance_verification_status);
  expect(predictions.overall_risk_signal.triggers_automatic_dispatch).toBe(false);
  for(const forecast of predictions.forecasts){
   const row=page.getByTestId(`forecast-row-h${forecast.horizon_days}`);
   for(const price of [forecast.predicted_price,forecast.xgboost_predicted_price,forecast.naive_predicted_price])await expect(row).toContainText(rupiah(price));
   expect(forecast.target_source_record_present).toBe(false);expect(forecast.actual_observation_available).toBe(false);expect(forecast.target_confirmed_market_observation).toBeNull();
   expect(forecast.evaluation_reference.applies_directly_to_target_date).toBe(false);
   expect(forecast.target_schedule_matches_evaluation_scope).toBe(forecast.evaluation_reference.target_schedule_matches_evaluation_scope);
   expect(forecast.risk_signal.triggers_automatic_dispatch).toBe(false);
   const disclosure=page.getByTestId(`forecast-target-disclosure-h${forecast.horizon_days}`);
   await expect(disclosure).toContainText('target_source_record_present=false');await expect(disclosure).toContainText('actual_observation_available=false');await expect(disclosure).toContainText('target_confirmed_market_observation=null');
   await expect(disclosure).toContainText('MAE historis tidak berlaku langsung');
   await expect(disclosure).toContainText(forecast.target_schedule_matches_evaluation_scope?'Jadwal sesuai cakupan evaluasi, bukan validasi target spesifik':'Jadwal di luar cakupan evaluasi');
   const ev=evaluation.evaluation_by_horizon[String(forecast.horizon_days)];expect(ev.is_independent_holdout_for_revised_policy).toBe(false);
   const metric=page.getByTestId(`eval-metric-row-h${forecast.horizon_days}`);await expect(metric).toContainText(rupiah(ev.test_metrics.xgboost_mae));await expect(metric).toContainText(rupiah(ev.test_metrics.naive_mae));
  }
  await expect(page.getByTestId('forecast-holdout-disclosure')).toContainText('Semua horizon H+1/H+3/H+7');
  await expect(page.getByTestId('forecast-holdout-disclosure')).toContainText('H+7 memerlukan validasi prospektif');
  await expect(page.locator('main')).toContainText('tidak memicu dispatch otomatis');
  await expect(page.getByTestId('eval-metric-row-h7')).toContainText('POST_TEST_EXPLORATORY_POLICY_REVISION');
 await expect(page.getByRole('img',{name:'Grafik harga historis dan prediksi masa depan'})).toBeVisible();
 await expect(page.getByRole('img',{name:'Grafik evaluasi aktual vs prediksi periode uji'})).toBeVisible();
 await page.getByRole('button',{name:'Evaluasi H+7 (7 Hari Kalender)'}).click();
 await screenshot(page,'ai-readiness.png');
 await screenshot(page,'ai-forecasting-m7b1.png');
 await page.setViewportSize({width:390,height:844});
 await expect(page.getByRole('heading',{name:'AI Price Intelligence — Provenance, Kesegaran Data & Sinyal Risiko'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.keyboard.press('Tab');
 await screenshot(page,'mobile.png');
});
