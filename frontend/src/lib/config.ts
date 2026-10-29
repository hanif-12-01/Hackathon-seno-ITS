export function apiBase(value: string | undefined, production: boolean): string {
  const base = value?.trim() || (production ? '' : 'http://127.0.0.1:8000');
  const url = new URL(base);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Invalid VITE_API_BASE_URL');
  if (production && url.protocol !== 'https:') throw new Error('Production VITE_API_BASE_URL requires HTTPS');
  return base.replace(/\/$/, '');
}
