import './style.css';
import {lazy, Suspense, useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {
  ArrowLeft,
  ArrowUpRight,
  Eye,
  LockKeyhole,
  LogOut,
  ShieldCheck,
  Truck,
  UserRound,
} from 'lucide-react';
import {LandingPage} from './LandingPage';
import {Logo} from './Logo';
import {ApiError, clearCredentials, request, setCredentials} from './lib/api';
import {apiBase} from './lib/config';
import {ToastProvider, notify} from './Toast';
import './auth.css';
const App = lazy(() => import('./App').then((m) => ({default: m.App})));
const GovDashboard = lazy(() =>
  import('./GovDashboard').then((m) => ({default: m.GovDashboard})),
);
const DriverView = lazy(() =>
  import('./DriverView').then((m) => ({default: m.DriverView})),
);
const env = (
  import.meta as ImportMeta & {env: {VITE_API_BASE_URL?: string; PROD: boolean}}
).env;
const base = apiBase(env.VITE_API_BASE_URL, env.PROD);
type View = 'landing' | 'login' | 'app' | 'gov' | 'driver';
type Principal = {principal: string; role: string};
const workspaceRoutes = [
  'Beranda',
  'Intelijen Harga',
  'Perencanaan Distribusi',
  'Operasional Pengiriman',
  'Monitoring & Laporan',
];
function workspaceRoute() {
  try {
    return workspaceRoutes.includes(decodeURIComponent(location.hash.slice(1)));
  } catch {
    return false;
  }
}
function getView(): View {
  const hash = location.hash;
  return hash === '#login' || workspaceRoute()
    ? 'login'
    : hash === '#pengawasan'
      ? 'gov'
      : hash === '#pengemudi'
        ? 'driver'
        : 'landing';
}

function MainContainer() {
  const [view, setView] = useState<View>(getView);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function navigate(next: View) {
    setView(next);
    if (next === 'app') history.pushState(null, '', '#Beranda');
    else
      history.pushState(
        null,
        '',
        next === 'landing'
          ? '#hero'
          : next === 'login'
            ? '#login'
            : next === 'gov'
              ? '#pengawasan'
              : '#pengemudi',
      );
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  useEffect(() => {
    const onUnauthorized = () => {
      setPrincipal(null);
      setError('Sesi Anda berakhir. Silakan masuk kembali.');
      setView('login');
      history.replaceState(null, '', '#login');
    };
    const onPop = () => {
      setView(principal && workspaceRoute() ? 'app' : getView());
    };
    window.addEventListener('pangannadi-login-required', onUnauthorized);
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('pangannadi-login-required', onUnauthorized);
      window.removeEventListener('popstate', onPop);
    };
  }, [principal]);
  async function login(form: HTMLFormElement) {
    const values = new FormData(form);
    setBusy(true);
    setError('');
    setCredentials(
      String(values.get('username')),
      String(values.get('password')),
    );
    try {
      const session = await request<Principal>(base, '/session');
      if (session.role !== 'operator')
        throw new ApiError(403, 'Akun tidak memiliki akses operator.');
      setPrincipal(session);
      setView('app');
      history.pushState(null, '', '#Beranda');
      notify('Berhasil masuk ke ruang kerja.', 'success');
      form.reset();
    } catch (err) {
      clearCredentials();
      setError(
        err instanceof ApiError && err.status === 401
          ? 'Nama akun atau kata sandi tidak sesuai.'
          : err instanceof ApiError && err.status === 429
            ? 'Terlalu banyak percobaan. Tunggu sebentar lalu coba kembali.'
            : err instanceof ApiError && err.status === 403
              ? 'Akun ini belum memiliki akses operator.'
              : 'Layanan masuk belum tersedia. Periksa jaringan dan coba kembali.',
      );
    } finally {
      setBusy(false);
    }
  }
  function logout() {
    try {
      sessionStorage.removeItem(`pangannadi:last-case:v2:${encodeURIComponent(base)}:${encodeURIComponent(principal?.principal ?? '')}`);
    } catch {}
    clearCredentials();
    setPrincipal(null);
    setError('');
    navigate('landing');
    notify('Anda telah keluar.', 'info');
  }
  // Unsupported principals receive empty design previews. No private API fetches or driver writes.
  if (view === 'gov')
    return (
      <GovDashboard
        caseData={null}
        capacities={[]}
        receipt={null}
        recon={null}
        auditLogs={[]}
        onBackToMain={() => navigate('landing')}
      />
    );
  if (view === 'driver')
    return (
      <DriverView
        shipments={[]}
        onSaveCheckpoint={async () => {
          throw new Error('Akses pengemudi belum tersedia.');
        }}
        onBackToMain={() => navigate('landing')}
      />
    );
  if (view === 'app' && principal)
    return (
      <>
        <div className="operator-session-bar">
          <div className="operator-session-context">
            <span className="operator-mobile-brand">
              <Logo />
            </span>
            <span>
              <ShieldCheck size={13} /> {principal.principal}
            </span>
          </div>
          <div className="operator-session-actions">
            <button type="button" onClick={() => navigate('landing')}>
              Halaman depan <ArrowUpRight size={12} />
            </button>
            <button type="button" onClick={logout}>
              <LogOut size={12} /> Keluar
            </button>
          </div>
        </div>
        <App
          key={principal.principal}
          principal={principal.principal}
          onOpenLanding={() => navigate('landing')}
        />
      </>
    );
  if (view === 'login')
    return (
      <div className="auth-page">
        <aside className="auth-story">
          <div className="auth-wordmark">
            <Logo />
            <span>
              PanganNadi<small>ADAPTIVE OPERATIONS</small>
            </span>
          </div>
          <div>
            <span className="eyebrow">KEPUTUSAN YANG TERHUBUNG</span>
            <h1>
              Pasokan terjaga.
              <br />
              Keputusan terukur.
            </h1>
            <p>
              Ruang kerja untuk merencanakan, memantau, dan menyesuaikan
              distribusi pangan dengan lebih bertanggung jawab.
            </p>
            <div className="auth-assurance">
              <ShieldCheck size={21} />
              <span>
                Data menjadi dasar.
                <br />
                Manusia memegang keputusan.
              </span>
            </div>
          </div>
          <small>Proof of concept · Seno Hackathon ITS 2026</small>
        </aside>
        <main className="auth-main">
          <button
            className="auth-back secondary"
            onClick={() => navigate('landing')}
          >
            <ArrowLeft size={17} /> Kembali ke halaman depan
          </button>
          <div className="auth-form-card">
            <div className="auth-mobile-brand">
              <Logo />
              <span>PanganNadi</span>
            </div>
            <span className="eyebrow">RUANG KERJA OPERATOR</span>
            <h2>Selamat datang kembali.</h2>
            <p>Masuk untuk melanjutkan pengelolaan distribusi.</p>
            {error && (
              <div role="alert" className="message error">
                {error}
              </div>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void login(e.currentTarget);
              }}
            >
              <label>
                Nama akun operator
                <div className="auth-input">
                  <UserRound size={18} />
                  <input
                    aria-label="Nama akun operator"
                    name="username"
                    autoComplete="username"
                    required
                    maxLength={100}
                    placeholder="Nama akun Anda"
                  />
                </div>
              </label>
              <label>
                Kata sandi
                <div className="auth-input">
                  <LockKeyhole size={18} />
                  <input
                    aria-label="Kata sandi"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    maxLength={2000}
                    placeholder="Masukkan kata sandi"
                  />
                </div>
              </label>
              <button type="submit" disabled={busy}>
                {busy ? 'Sedang memverifikasi…' : 'Masuk ke ruang kerja'}
                <ArrowUpRight size={17} />
              </button>
            </form>
            <p className="auth-security">
              <LockKeyhole size={14} /> Akses diverifikasi oleh server. Jangan
              bagikan akun Anda.
            </p>
            <div className="auth-preview-links">
              <span>Jelajahi konsep produk</span>
              <button onClick={() => navigate('gov')}>
                <Eye size={15} /> Pengawasan
              </button>
              <button onClick={() => navigate('driver')}>
                <Truck size={15} /> Pengemudi
              </button>
            </div>
          </div>
          <p className="auth-footer">
            Lingkungan simulasi · Akses hanya untuk pengguna berwenang.
          </p>
        </main>
      </div>
    );
  return (
    <LandingPage
      apiHealth=""
      onOpenWorkspace={() => navigate(principal ? 'app' : 'login')}
      onOpenGovView={() => navigate('gov')}
      onOpenDriverView={() => navigate('driver')}
    />
  );
}
export function Root() {
  return (
    <ToastProvider>
      <Suspense
        fallback={
          <div className="empty" role="status">
            Sedang memuat ruang kerja…
          </div>
        }
      >
        <MainContainer />
      </Suspense>
    </ToastProvider>
  );
}
createRoot(document.getElementById('root')!).render(<Root />);
