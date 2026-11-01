export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: unknown) {
    const reason = Array.isArray(detail) ? detail.map(e => `${e.loc?.join('.') ?? 'input'}: ${e.msg}`).join('; ') : typeof detail === 'string' ? detail : JSON.stringify(detail);
    super(`${status === 409 ? 'Konflik — muat ulang data sebelum mencoba kembali' : status === 422 ? 'Validasi gagal' : `HTTP ${status}`}: ${reason}`);
    this.status = status;
  }
}

let authorization = '';
let sessionVersion = 0;
export const currentSessionVersion = () => sessionVersion;
export function clearCredentials() { authorization = ''; sessionVersion++; }
export function setCredentials(username: string, password: string) {
  if (!username || username.includes(':')) throw new Error('Nama akun tidak valid');
  authorization = 'Basic ' + btoa(Array.from(new TextEncoder().encode(`${username}:${password}`), byte => String.fromCharCode(byte)).join(''));
  sessionVersion++;
}

export async function request<T>(base: string, path: string, body?: unknown): Promise<T> {
  const version = sessionVersion;
  const target = `${base.replace(/\/$/, '')}${path}`;
  if (authorization && new URL(target, location.href).origin !== location.origin) throw new Error('Login hanya untuk API same-origin');
  let response: Response;
  try {
    response = await fetch(target, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      redirect: 'error',
      headers: {'X-PanganNadi-UI': '1', ...(body === undefined ? {} : {'Content-Type': 'application/json'}), ...(authorization ? {Authorization: authorization} : {})},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
  } catch (err: unknown) {
    if (err instanceof TypeError && String(err).toLowerCase().includes('redirect')) {
      throw new ApiError(302, 'Sesi Vercel SSO dialihkan. Muat ulang halaman untuk autentikasi Vercel.');
    }
    throw err;
  }
  if (version !== sessionVersion) throw new Error('Sesi berubah; respons sesi sebelumnya diabaikan.');
  const text = await response.text();
  if (version !== sessionVersion) throw new Error('Sesi berubah; respons sesi sebelumnya diabaikan.');
  if (response.status === 401 && typeof window !== 'undefined') {
    clearCredentials();
    window.dispatchEvent(new Event('pangannadi-login-required'));
  }
  let data;
  try { data = JSON.parse(text); } catch { if (!response.ok) throw new ApiError(response.status, 'Respons bukan JSON'); throw new Error('Respons sukses tidak dapat dibaca; hasil belum diketahui.'); }
  if (!response.ok) throw new ApiError(response.status, data.detail ?? data);
  return data as T;
}

export const kg = (n: number) => `${new Intl.NumberFormat('id-ID').format(n)} kg`;
export const rupiah = (n: number | string) => new Intl.NumberFormat('id-ID', {style:'currency',currency:'IDR',maximumFractionDigits:2}).format(Number(n));
export const wib = (value: string) => new Intl.DateTimeFormat('id-ID', {dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Jakarta'}).format(new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)) + ' WIB';

export const horizonLabel = (days: number) => `H+${days} (${days} Hari Kalender)`;

export function describeTargetSchedule(
  schedule: string,
  actualAvailable: boolean,
  appliesDirectly?: boolean,
  scheduleMatches?: boolean,
): string {
  const recordLabel = actualAvailable ? 'Record sumber tersedia (bukan konfirmasi survei)' : 'Record sumber belum tersedia';
  const scheduleLabel = schedule === 'NON_SURVEY_WEEKEND_SCHEDULE' ? 'Estimasi Kalender Akhir Pekan (Non-Survei PIHPS)' : schedule === 'HOLIDAY_WEEKDAY_SCHEDULE' ? 'Hari Libur Nasional (Status Survei Tidak Pasti)' : 'Jadwal Publikasi Hari Kerja PIHPS';
  const scopeLabel = scheduleMatches === true ? 'Jadwal sesuai cakupan evaluasi, bukan validasi target spesifik' : scheduleMatches === false ? 'Jadwal di luar cakupan evaluasi' : 'Kesesuaian jadwal belum diketahui';
  return `${scheduleLabel} • ${recordLabel} • ${scopeLabel} • ${appliesDirectly === false ? 'MAE historis tidak berlaku langsung pada tanggal target' : 'MAE historis bukan bukti validasi langsung target masa depan'}`;
}

export function describeUnavailableReason(status: string, reason?: string): string {
  const map: Record<string, string> = {
    DATASET_UNAVAILABLE: 'Dataset unavailable — file dataset harga historis belum ditemukan.',
    MODEL_NOT_TRAINED: 'Model not trained — metadata dan hasil pelatihan model belum tersedia.',
    ARTIFACT_UNAVAILABLE: 'Artifact unavailable — file artefak model terlatih tidak ditemukan.',
    API_ERROR: 'API error — gagal mengambil data dari endpoint forecasting backend.',
  };
  return `${map[status] ?? `Status: ${status}`}${reason ? ` (${reason})` : ''}`;
}

export interface ChartCoord {date: string; price: number; x: number; y: number; kind?: 'historical'|'forecast'|'actual'|'xgboost'|'naive'}

export function mapChartSeries(
  items: {date: string; price: number; kind?: ChartCoord['kind']}[],
  width = 760,
  height = 240,
  pad = 28,
  priceDomain?: [number, number],
): {coords: ChartCoord[]; polyline: string; minPrice: number; maxPrice: number} {
  if (!items.length) return {coords: [], polyline: '', minPrice: 0, maxPrice: 0};
  const prices = items.map(i => i.price);
  const minPrice = priceDomain ? priceDomain[0] : Math.min(...prices);
  const maxPrice = priceDomain ? priceDomain[1] : Math.max(...prices);
  const span = Math.max(maxPrice - minPrice, 1);
  const innerW = Math.max(width - pad * 2, 1);
  const innerH = Math.max(height - pad * 2, 1);
  const coords = items.map((item, idx) => {
    const x = items.length === 1 ? width / 2 : Number((pad + (idx / (items.length - 1)) * innerW).toFixed(2));
    const y = Number((pad + (1 - (item.price - minPrice) / span) * innerH).toFixed(2));
    return {...item, x, y};
  });
  return {
    coords,
    polyline: coords.map(c => `${c.x},${c.y}`).join(' '),
    minPrice,
    maxPrice,
  };
}
