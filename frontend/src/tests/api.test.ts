import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ApiError, clearCredentials, setCredentials, kg, rupiah, wib, request, horizonLabel, describeUnavailableReason, describeTargetSchedule, mapChartSeries} from '../lib/api.ts';

test('same-origin requests retain credentials, memory auth isolated, logout clears credentials', async () => {
  const original = globalThis.fetch;
  Object.defineProperty(globalThis, 'location', {configurable: true, value: new URL('https://stage.example.test/')});
  let calls = 0;
  globalThis.fetch = async (_input, init) => {
    calls++;
    assert.equal(init?.credentials, 'same-origin');
    assert.equal(init?.redirect, 'error');
    const headers = init?.headers as Record<string, string>;
    if (calls === 1) assert.equal(headers.Authorization, 'Basic ' + btoa('alice:secret'));
    else assert.equal(headers.Authorization, undefined);
    return new Response('{}');
  };
  try {
    setCredentials('alice', 'secret');
    await request('/api', '/session');
    await assert.rejects(request('https://other.example.test', '/session'), /same-origin/);
    assert.equal(calls, 1);
    clearCredentials();
    await request('/api', '/session');
    assert.equal(calls, 2);
  } finally { clearCredentials(); globalThis.fetch = original; }
});

test('redirect error classified as 302 and distinct from 401', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new TypeError('Failed to fetch (redirect loop / redirect blocked)');
  };
  try {
    await assert.rejects(
      request('/api', '/session'),
      (err: unknown) => err instanceof ApiError && err.status === 302 && /Vercel SSO/.test(err.message)
    );
  } finally {
    globalThis.fetch = original;
  }
});

test('401 response dispatches login required event and clears credentials', async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as unknown as {window?: EventTarget}).window;
  let eventFired = false;
  const target = new EventTarget();
  (globalThis as unknown as {window?: EventTarget}).window = target;
  target.addEventListener('pangannadi-login-required', () => { eventFired = true; });
  globalThis.fetch = async () => new Response(JSON.stringify({detail: 'Authentication required'}), {status: 401});
  try {
    setCredentials('alice', 'wrong-pass');
    await assert.rejects(request('/api', '/session'), (err: unknown) => err instanceof ApiError && err.status === 401);
    assert.equal(eventFired, true);
  } finally {
    (globalThis as unknown as {window?: EventTarget}).window = originalWindow;
    clearCredentials();
    globalThis.fetch = originalFetch;
  }
});

test('409/422 retain actionable backend reasons', () => {
  assert.match(new ApiError(409,'Capacity changed').message, /Konflik.*Capacity changed/);
  assert.match(new ApiError(422,[{loc:['body','verified_demand'],msg:'integer kg'}]).message, /body.verified_demand: integer kg/);
});

test('Indonesian units and naive UTC timestamps', () => {
  assert.equal(kg(1234),'1.234 kg');
  assert.match(rupiah('435000'), /435.000/);
  assert.equal(wib('2026-10-28T00:00:00'),wib('2026-10-28T00:00:00Z'));
  assert.match(wib('2026-10-28T00:00:00'), /07.00 WIB/);
});

test('isolated fetch mock: server error cannot become successful data', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({detail:'reserved'}),{status:409});
  try { await assert.rejects(request('http://test','/plans/one/approve',{}), /Konflik.*reserved/); }
  finally { globalThis.fetch = original; }
});

test('forecast horizon labels and unavailable state reasons', () => {
  assert.equal(horizonLabel(1), 'H+1 (1 Hari Kalender)');
  assert.equal(horizonLabel(3), 'H+3 (3 Hari Kalender)');
  assert.equal(horizonLabel(7), 'H+7 (7 Hari Kalender)');
  assert.match(describeUnavailableReason('DATASET_UNAVAILABLE', 'missing csv'), /Dataset unavailable.*missing csv/);
  assert.match(describeUnavailableReason('MODEL_NOT_TRAINED'), /Model not trained/);
  assert.match(describeUnavailableReason('ARTIFACT_UNAVAILABLE'), /Artifact unavailable/);
  assert.match(describeUnavailableReason('API_ERROR', 'HTTP 503'), /API error.*HTTP 503/);
  assert.match(describeTargetSchedule('NON_SURVEY_WEEKEND_SCHEDULE', false, false, false), /Akhir Pekan.*Non-Survei PIHPS.*Record sumber belum tersedia.*di luar cakupan.*MAE historis tidak berlaku langsung/);
  assert.match(describeTargetSchedule('WEEKDAY_PUBLICATION_SCHEDULE', false, false, true), /Jadwal Publikasi Hari Kerja PIHPS.*belum tersedia.*sesuai cakupan evaluasi, bukan validasi target spesifik.*tidak berlaku langsung/);
  assert.match(describeTargetSchedule('WEEKDAY_PUBLICATION_SCHEDULE', false, false, false), /di luar cakupan evaluasi.*tidak berlaku langsung/);
  assert.match(describeTargetSchedule('HOLIDAY_WEEKDAY_SCHEDULE', true, false, true), /Status Survei Tidak Pasti.*Record sumber tersedia \(bukan konfirmasi survei\)/);
  assert.match(describeTargetSchedule('WEEKDAY_PUBLICATION_SCHEDULE', true), /bukan konfirmasi survei.*belum diketahui.*bukan bukti validasi langsung/);
});

test('historical and forecast chart coordinate mapping', () => {
  const empty = mapChartSeries([]);
  assert.equal(empty.coords.length, 0);
  assert.equal(empty.polyline, '');

  const mapped = mapChartSeries([
    {date: '2026-10-26', price: 50000, kind: 'historical'},
    {date: '2026-10-27', price: 55000, kind: 'historical'},
    {date: '2026-10-29', price: 60000, kind: 'forecast'},
  ], 760, 240, 28);
  assert.equal(mapped.coords.length, 3);
  assert.equal(mapped.minPrice, 50000);
  assert.equal(mapped.maxPrice, 60000);
  assert.equal(mapped.coords[0].x, 28);
  assert.equal(mapped.coords[2].x, 732);
  assert.ok(mapped.coords[2].y < mapped.coords[0].y);
  assert.match(mapped.polyline, /^28,\d+/);
});
