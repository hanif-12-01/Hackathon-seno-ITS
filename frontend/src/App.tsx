import {
  LayoutDashboard,
  ChartNoAxesCombined,
  ClipboardList,
  Truck,
  Files,
  PanelLeftClose,
  Menu,
  ArrowUpRight,
  RefreshCw,
  ShieldCheck,
  Globe,
  Eye,
} from 'lucide-react';
import {Overview, DashboardSkeleton, ActivityTimeline} from './Overview';
import {summarizeDistribution} from './lib/dashboard';
import {Logo} from './Logo';
import {useEffect, useRef, useState} from 'react';
import type {
  CaseSummary,
  CaseDetail,
  Plan,
  Capacity,
  Readiness,
  Receipt,
  Reconciliation,
  Eligibility,
  Audit,
  ForecastStatus,
  HistoricalSeries,
  ForecastPredictions,
  ForecastEvaluation,
} from './types';
import {ApiError, request, kg, rupiah, wib} from './lib/api';
import {
  Panel,
  Status,
  Metrics,
  PlanView,
  CapacityTable,
  ForecastIntelligenceView,
} from './components';
import {CaseForm} from './CaseForm';
import {Operations} from './lib/operations';
import {Checkpoints} from './Checkpoints';
import {apiBase} from './lib/config';
import {
  DistributionFulfillmentChart,
  WarehouseCapacityChart,
  ModelEvaluationComparisonChart,
} from './Charts';
import {ShipperReportView, ReceiverReportView} from './Reports';
import {GovDashboard} from './GovDashboard';
import {DriverView} from './DriverView';
import {notify} from './Toast';

const env = (
  import.meta as ImportMeta & {env: {VITE_API_BASE_URL?: string; PROD: boolean}}
).env;
const base = apiBase(env.VITE_API_BASE_URL, env.PROD);

// 5 Cohesive Conceptual Areas
export const navigationAreas = [
  'Beranda',
  'Intelijen Harga',
  'Perencanaan Distribusi',
  'Operasional Pengiriman',
  'Monitoring & Laporan',
] as const;

export type NavArea = (typeof navigationAreas)[number];

function normalizePage(hash: string): NavArea {
  let decoded = '';
  try {
    decoded = decodeURIComponent(hash.slice(1));
  } catch {
    return 'Beranda';
  }
  if (navigationAreas.includes(decoded as NavArea)) return decoded as NavArea;
  // Legacy page mapping
  if (decoded === 'Overview') return 'Beranda';
  if (decoded === 'AI Price Intelligence') return 'Intelijen Harga';
  if (
    decoded === 'Distribution Planning' ||
    decoded === 'Approval Center' ||
    decoded === 'Adaptive Recovery'
  ) {
    return 'Perencanaan Distribusi';
  }
  if (decoded === 'Control Tower' || decoded === 'Receiving & Reconciliation') {
    return 'Operasional Pengiriman';
  }
  if (decoded === 'Audit & Evidence') return 'Monitoring & Laporan';
  return 'Beranda';
}

class FormDataWithSubmitter extends globalThis.FormData {
  constructor(form: HTMLFormElement, source?: Event) {
    super(form);
    const submitter =
      source instanceof SubmitEvent ? source.submitter : document.activeElement;
    if (
      submitter instanceof HTMLButtonElement &&
      submitter.form === form &&
      submitter.name
    ) {
      this.set(submitter.name, submitter.value);
    }
  }
}

export function App({
  principal,
  onOpenLanding,
}: {
  principal?: string;
  onOpenLanding?: () => void;
}) {
  const [page, setPage] = useState<NavArea>(normalizePage(location.hash));
  const [reportTab, setReportTab] = useState<'shipper' | 'receiver' | 'audit'>(
    'shipper',
  );
  const [health, setHealth] = useState('Memeriksa sistem…');
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selected, setSelected] = useState('');
  const [data, setData] = useState<CaseDetail | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [capacityRows, setCapacityRows] = useState<Capacity[]>([]);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [recon, setRecon] = useState<Reconciliation | null>(null);
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);

  // AI Forecasting state
  const [fStatus, setFStatus] = useState<ForecastStatus | null>(null);
  const [fHist, setFHist] = useState<HistoricalSeries | null>(null);
  const [fPred, setFPred] = useState<ForecastPredictions | null>(null);
  const [fEval, setFEval] = useState<ForecastEvaluation | null>(null);
  const [fLoading, setFLoading] = useState(false);
  const [fError, setFError] = useState('');

  // Gate & Workflow state
  const [gate, setGate] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [operationsTab, setOperationsTab] = useState<'shipments' | 'receiving'>(
    'shipments',
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [actor, setActor] = useState(principal ?? '');
  const [decision, setDecision] = useState<{
    plan: Plan;
    value: 'APPROVE' | 'REJECT';
  } | null>(null);
  const [showGovView, setShowGovView] = useState(false);
  const [showDriverView, setShowDriverView] = useState(false);

  const dialog = useRef<HTMLDialogElement>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; generation.current++; }, []);
  const operations = useRef<Operations | null>(null);
  const mutationLock = useRef(false);
  const [uncertain, setUncertain] = useState(true);

  const store = () =>
    operations.current ??
    (operations.current = new Operations(base, localStorage, principal));
  const lastCaseKey = `pangannadi:last-case:v2:${encodeURIComponent(base)}:${encodeURIComponent(principal ?? 'local-simulation')}`;
  const keyed = (body: Record<string, unknown>) => body;

  const api = <T,>(path: string, body?: unknown) =>
    body === undefined
      ? request<T>(base, path)
      : store().execute<T>(path, body as Record<string, unknown>);

  const optional = async <T,>(path: string) => {
    try {
      return await api<T>(path);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  };

  async function reconcileOperations() {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setUncertain(true);
    try {
      const clear = await store().reconcile();
      setUncertain(!clear);
      if (!clear) {
        setError(
          'Hasil transaksi belum dapat dipastikan. Sistem mengunci mutasi demi integritas data.',
        );
      } else {
        if (selected) await refresh();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      mutationLock.current = false;
    }
  }

  useEffect(() => {
    reconcileOperations().catch((e) => setError(String(e)));
  }, []);

  async function refreshForecast() {
    setFLoading(true);
    setFError('');
    try {
      const st = await api<ForecastStatus>('/forecasting/status');
      setFStatus(st);
      if (!st.available) {
        setFHist(null);
        setFPred(null);
        setFEval(null);
        return;
      }
      const [hist, pred, ev] = await Promise.all([
        api<HistoricalSeries>(
          '/forecasting/historical?limit=90&observed_only=true',
        ),
        api<ForecastPredictions>('/forecasting/predictions'),
        api<ForecastEvaluation>('/forecasting/evaluation'),
      ]);
      setFHist(hist);
      setFPred(pred);
      setFEval(ev);
    } catch (e) {
      setFError(e instanceof Error ? e.message : String(e));
      setFHist(null);
      setFPred(null);
      setFEval(null);
    } finally {
      setFLoading(false);
    }
  }

  async function refresh(id = selected) {
    const token = ++generation.current;
    let selecting = false;
    setLoading(true);
    try {
      const list = await api<CaseSummary[]>('/cases');
      if (token !== generation.current) return;
      setCases(list);
      let remembered = '';
      try {
        remembered = sessionStorage.getItem(lastCaseKey) ?? '';
      } catch {}
      const chosen = list.some((c) => c.id === id)
        ? id
        : list.some((c) => c.id === remembered)
          ? remembered
          : (list.find((c) => c.id === 'CASE-EFD6A1CC')?.id ??
            [...list].sort((a, b) =>
              b.created_at.localeCompare(a.created_at),
            )[0]?.id ??
            '');
      if (chosen !== selected) {
        selecting = true;
        setSelected(chosen);
        return;
      }
      if (!chosen) {
        setData(null);
        setPlans([]);
        setCapacityRows([]);
        setReceipt(null);
        setRecon(null);
        setEligibility(null);
        setAudit([]);
        setHistory([]);
        return;
      }
      id = chosen;
      try {
        sessionStorage.setItem(lastCaseKey, chosen);
      } catch {}
      const c = await api<CaseDetail>(`/cases/${encodeURIComponent(id)}`);
      // Sequenced calls to avoid row-lock contention on rate limit tables
      const p = await Promise.all(
        c.plans.map((p) => api<Plan>(`/plans/${p.plan_id}`)),
      );
      const cap = await Promise.all(
        c.snapshots.map((s) =>
          api<Capacity>(
            `/warehouses/${encodeURIComponent(s.warehouse_id)}/capacity`,
          ),
        ),
      );
      const r = await optional<Receipt>(`/cases/${id}/receipts`);
      const rc = await optional<Reconciliation>(`/cases/${id}/reconciliation`);
      const el = await api<Eligibility>(`/cases/${id}/recovery-eligibility`);
      const a = await api<Audit[]>(`/cases/${id}/audit`);
      const h = await api<unknown[]>(`/cases/${id}/replanning-history`);

      if (token !== generation.current) return;
      setData(c);
      setPlans(p.sort((a, b) => a.version - b.version));
      setCapacityRows(cap);
      setReceipt(r);
      setRecon(rc);
      setEligibility(el);
      setAudit(a);
      setHistory(h);
      setUpdatedAt(new Date().toISOString());
    } finally {
      if (token === generation.current && !selecting) setLoading(false);
    }
  }

  useEffect(() => {
    api<{status: string}>('/health')
      .then((r) =>
        setHealth(
          r.status === 'ok' ? 'API Terhubung (200 OK)' : 'API Tidak Siap',
        ),
      )
      .catch((e) => {
        setHealth('API Tidak Terhubung');
        setError(String(e));
      });
    refreshForecast().catch(() => {});
    const listener = () => {
      setPage(normalizePage(location.hash));
      setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    addEventListener('keydown', escape);
    addEventListener('hashchange', listener);
    return () => {
      removeEventListener('hashchange', listener);
      removeEventListener('keydown', escape);
    };
  }, []);

  useEffect(() => {
    setData(null);
    setGate(null);
    setError('');
    setNotice('');
    refresh(selected).catch((e) => setError(String(e)));
  }, [selected]);

  useEffect(() => {
    if (decision) dialog.current?.showModal();
  }, [decision]);

  async function mutate(
    action: () => Promise<unknown>,
    successMsg = 'Data berhasil disimpan.',
  ) {
    if (mutationLock.current || uncertain) return;
    mutationLock.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await action();
      if (!mounted.current) return;
      const createdCase =
        result && typeof result === 'object' && 'createdCaseId' in result
          ? String(result.createdCaseId)
          : null;
      setNotice(successMsg);
      notify(successMsg, 'success');
      try {
        if (createdCase) setSelected(createdCase);
        else await refresh();
      } catch (e) {
        setNotice(
          'Perubahan tersimpan; pembaruan tampilan memerlukan muat ulang.',
        );
        throw e;
      }
    } catch (e) {
      if (!mounted.current) return;
      const errText =
        e instanceof Error
          ? e.message
          : 'Kami belum dapat menyimpan perubahan. Periksa kembali status data.';
      setError(errText);
      notify(errText, 'error');
      if (e instanceof ApiError && e.status === 409)
        await refresh().catch(() => {});
    } finally {
      if (!mounted.current) return;
      try {
        if (store().blocked()) {
          setUncertain(true);
          if (await store().reconcile()) {
            setError('');
            setNotice('Perubahan berhasil dikonfirmasi.');
            notify('Perubahan berhasil dikonfirmasi.', 'info');
            await refresh();
          }
        }
        setUncertain(store().blocked());
      } catch (e) {
        setUncertain(true);
        setError(`Hasil belum diketahui / readback gagal: ${String(e)}`);
      }
      mutationLock.current = false;
      setBusy(false);
    }
  }

  const initial = plans.find((p) => p.version === 1);
  const latest = plans.at(-1);
  const shipments = initial?.shipments ?? [];
  const dispatched = shipments.filter(
    (s) => s.actual_dispatched_quantity_kg > 0,
  );
  const actorValid = actor.trim().length > 0;
  const readyToReceive =
    data?.status === 'DISPATCHED' &&
    shipments.every((s) => s.remaining_reserved_stock_kg === 0) &&
    dispatched.length > 0;

  // Driver checkpoint action handler
  const handleDriverCheckpoint = async (
    shipmentId: string,
    eventType: string,
    note: string,
  ) => {
    const transactionRef =
      'TR-CHK-' + Math.random().toString(36).substring(2, 9);
    await api(`/shipments/${shipmentId}/checkpoints`, {
      transaction_ref: transactionRef,
      event_type: eventType,
      note: note.trim() || null,
      actor_id: actor.trim(),
    });
    await refresh();
  };

  // Switch to Government Oversight View
  if (showGovView) {
    return (
      <GovDashboard
        caseData={data}
        capacities={capacityRows}
        receipt={receipt}
        recon={recon}
        auditLogs={audit}
        onBackToMain={() => setShowGovView(false)}
      />
    );
  }

  // Switch to Driver View
  if (showDriverView) {
    return (
      <DriverView
        shipments={shipments}
        onSaveCheckpoint={handleDriverCheckpoint}
        onBackToMain={() => setShowDriverView(false)}
      />
    );
  }

  // Fulfillment numbers for visual chart
  const fulfillmentData = summarizeDistribution(data, plans, receipt, recon);
  const navIcons = [
    LayoutDashboard,
    ChartNoAxesCombined,
    ClipboardList,
    Truck,
    Files,
  ];

  return (
    <div className="app">
      <a className="skip" href="#main">
        Lewati navigasi
      </a>

      {menuOpen && (
        <button
          className="nav-backdrop"
          aria-label="Tutup navigasi"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        className={menuOpen ? 'sidebar is-open' : 'sidebar'}
        aria-label="Navigasi platform"
      >
        <a className="brand" href="#Beranda">
          <Logo />
          <span>
            PanganNadi<small>ADAPTIVE OPERATIONS</small>
          </span>
        </a>
        <button
          className="sidebar-close secondary"
          aria-label="Tutup menu"
          onClick={() => setMenuOpen(false)}
        >
          <PanelLeftClose size={20} />
        </button>
        <p className="nav-label">RUANG KERJA</p>
        <nav aria-label="Navigasi 5 area">
          {navigationAreas.map((p, i) => {
            const Icon = navIcons[i];
            return (
              <a
                key={p}
                href={`#${encodeURIComponent(p)}`}
                onClick={() => setMenuOpen(false)}
                aria-current={page === p ? 'page' : undefined}
              >
                <Icon size={20} strokeWidth={1.7} />
                <span>{p}</span>
              </a>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-assurance">
            <ShieldCheck size={21} />
            <div>
              Keputusan tetap di tangan Anda
              <small>Setiap alokasi memerlukan persetujuan.</small>
            </div>
          </div>
          <p className="nav-label">TAMPILAN PRODUK</p>
          <button
            className="sidebar-link"
            onClick={() => {
              setMenuOpen(false);
              setShowGovView(true);
            }}
          >
            <Eye size={18} /> Pratinjau pengawasan <ArrowUpRight size={14} />
          </button>
          <button
            className="sidebar-link"
            onClick={() => {
              setMenuOpen(false);
              setShowDriverView(true);
            }}
          >
            <Truck size={18} /> Pratinjau pengemudi <ArrowUpRight size={14} />
          </button>
          {onOpenLanding && (
            <button className="sidebar-link" onClick={onOpenLanding}>
              <Globe size={18} /> Tentang PanganNadi <ArrowUpRight size={14} />
            </button>
          )}
          <div className="sidebar-person">
            <span>
              {(principal || actor || 'OP').slice(0, 2).toUpperCase()}
            </span>
            <div>
              {principal || 'Operator simulasi'}
              <small>Lingkungan proof of concept</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <div className="demo">
          <span>
            <span className="simulation-dot" /> LINGKUNGAN SIMULASI
          </span>
          <span>Proof of concept · Seno ITS 2026</span>
        </div>
        <header className="app-header">
          <div className="app-title">
            <button
              className="mobile-menu secondary"
              aria-label="Buka menu navigasi"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <Menu size={21} />
            </button>
            <div>
              <span className="eyebrow">
                RUANG KERJA / {page.toUpperCase()}
              </span>
              <h1>{page === 'Beranda' ? 'Kendali distribusi pangan' : page}</h1>
              <p>
                {page === 'Beranda'
                  ? 'Satu pandangan. Keputusan yang lebih terukur.'
                  : page === 'Intelijen Harga'
                    ? 'Pahami sinyal pasar sebelum mengambil keputusan.'
                    : page === 'Perencanaan Distribusi'
                      ? 'Dari kebutuhan terverifikasi ke alokasi yang aman.'
                      : page === 'Operasional Pengiriman'
                        ? 'Pantau keberangkatan, perjalanan, dan penerimaan.'
                        : 'Telusuri pelaksanaan dan bukti setiap keputusan.'}
              </p>
            </div>
          </div>
          <div className="header-actions">
            <span className="connection-state" title={health}>
              <span
                className={
                  health.includes('Terhubung')
                    ? 'connection-dot online'
                    : 'connection-dot'
                }
              />
              {health.includes('Terhubung')
                ? 'Data terhubung'
                : 'Koneksi terbatas'}
            </span>
            <button
              type="button"
              className="secondary refresh-button"
              aria-label="Perbarui data"
              disabled={busy || loading}
              onClick={() => {
                setError('');
                refresh().catch((e) => setError(String(e)));
              }}
            >
              <RefreshCw size={16} className={loading ? 'spin' : ''} />
              <span>Perbarui</span>
            </button>
          </div>
        </header>
        <div
          className="toolbar"
          role="region"
          aria-label="Toolbar pemilihan kasus"
        >
          <label className="case-picker">
            Kasus distribusi
            <select
              aria-label="Distribution case"
              value={selected}
              disabled={busy || loading}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">
                {loading ? 'Sedang memuat kasus…' : 'Pilih kasus distribusi'}
              </option>
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.destination_region} · {c.id}
                </option>
              ))}
            </select>
          </label>
          {!principal && (
            <label>
              Identitas aktor simulasi
              <input
                aria-label="Aktor operasional"
                value={actor}
                maxLength={100}
                onChange={(e) => setActor(e.target.value)}
                placeholder="Nama operator uji lokal"
              />
            </label>
          )}
          <div className="refresh-time">
            <span>Pembaruan terakhir</span>
            <strong>{updatedAt ? wib(updatedAt) : 'Menunggu data'}</strong>
          </div>
        </div>
        {/* Main Content Body */}
        <main id="main" tabIndex={-1}>
          {uncertain && (
            <div role="alert" className="message error">
              <span>
                Hasil operasi sebelumnya sedang direkonsiliasi. Aksi kritis
                dikunci demi keselamatan data.
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => reconcileOperations()}
                style={{fontSize: 11, padding: '4px 8px'}}
              >
                Periksa status transaksi
              </button>
            </div>
          )}

          {error && (
            <div role="alert" className="message error">
              <strong>
                {error.includes('401')
                  ? 'Sesi perlu diperbarui.'
                  : error.includes('409')
                    ? 'Data telah berubah. Tinjau kembali sebelum melanjutkan.'
                    : error.includes('422')
                      ? 'Ada data yang perlu diperiksa.'
                      : 'Permintaan belum dapat diselesaikan.'}
              </strong>
              <details>
                <summary>Lihat keterangan</summary>
                <p>{error}</p>
              </details>
            </div>
          )}
          {notice && (
            <div role="status" className="message success">
              {notice}
            </div>
          )}
          {busy && (
            <div role="status" className="message info">
              {busy ? 'Sedang menyimpan perubahan…' : 'Sedang memuat data…'}
            </div>
          )}

          {/* Active Case Strip */}
          {data && !loading && (
            <div className="case-strip">
              <div>
                <strong>{data.destination_region}</strong>
                <small>
                  {data.id} · {data.commodity_name} · {wib(data.created_at)}
                </small>
              </div>
              <Status value={data.status} />
            </div>
          )}

          {/* ========================================================
              AREA 1: BERANDA (OVERVIEW & EXECUTIVE SUMMARY)
          ======================================================== */}
          {page === 'Beranda' &&
            (loading ? (
              <DashboardSkeleton />
            ) : data ? (
              <Overview
                data={data}
                plans={plans}
                capacities={capacityRows}
                receipt={receipt}
                recon={recon}
                audit={audit}
                summary={fulfillmentData}
              />
            ) : !error ? (
              <section className="empty panel">
                <ClipboardList size={42} strokeWidth={1.2} />
                <span className="eyebrow">MULAI DARI KEBUTUHAN</span>
                <h2>
                  {cases.length
                    ? 'Pilih kasus distribusi'
                    : 'Ruang kerja siap digunakan'}
                </h2>
                <p>
                  {cases.length
                    ? 'Pilih kasus untuk melihat alokasi, pengiriman, dan hasil penerimaan.'
                    : 'Belum ada kasus yang tercatat. Mulai dengan kebutuhan tujuan dan ketersediaan gudang.'}
                </p>
                <a className="button" href="#Perencanaan%20Distribusi">
                  Buat kasus distribusi <ArrowUpRight size={17} />
                </a>
              </section>
            ) : (
              <section className="panel empty">
                <ShieldCheck size={36} />
                <h2>Data belum dapat dimuat</h2>
                <p>
                  Kasus yang tersimpan tetap dipertahankan. Periksa koneksi lalu
                  coba perbarui data.
                </p>
                <button
                  onClick={() => {
                    setError('');
                    refresh().catch((e) => setError(String(e)));
                  }}
                >
                  Coba lagi
                </button>
              </section>
            ))}

          {/* ========================================================
              AREA 2: INTELIJEN HARGA (AI PRICE FORECASTING)
          ======================================================== */}
          {page === 'Intelijen Harga' && (
            <>
              <ForecastIntelligenceView
                loading={fLoading}
                status={fStatus}
                historical={fHist}
                predictions={fPred}
                evaluation={fEval}
                errorMsg={fError}
                onReload={() => {
                  refreshForecast().catch(() => {});
                }}
              />
              {fEval && <ModelEvaluationComparisonChart evaluation={fEval} />}
            </>
          )}

          {/* ========================================================
              AREA 3: PERENCANAAN DISTRIBUSI (GATE, CP-SAT & RECOVERY)
          ======================================================== */}
          {page === 'Perencanaan Distribusi' && (
            <>
              {/* Case Creation */}
              <details className="case-creation" open={!data}>
                <summary>
                  <ClipboardList size={18} />{' '}
                  {data
                    ? 'Buat kasus distribusi lain'
                    : '01 / Kebutuhan dan sumber'}
                </summary>
                <CaseForm
                  principal={principal}
                  busy={busy || uncertain || loading}
                  create={async (body) => {
                    await mutate(async () => {
                      const created = await api<{case_id: string}>(
                        '/cases',
                        body,
                      );
                      return {createdCaseId: created.case_id};
                    }, 'Kasus distribusi baru berhasil dibuat.');
                  }}
                />
              </details>

              <ol
                className="workflow-steps"
                tabIndex={0}
                aria-label="Tahapan perencanaan"
              >
                {[
                  'Kebutuhan',
                  'Verifikasi',
                  'Optimasi',
                  'Persetujuan',
                  'Pemulihan',
                ].map((label, i) => (
                  <li
                    key={label}
                    className={
                      (i === 0 && data) ||
                      (i === 1 && (gate?.is_ready || initial)) ||
                      (i === 2 && initial) ||
                      (i === 3 && initial?.approval_status === 'APPROVED') ||
                      (i === 4 && recon)
                        ? 'is-done'
                        : ''
                    }
                  >
                    <span>{String(i + 1).padStart(2, '0')}</span>
                    {label}
                  </li>
                ))}
              </ol>
              {data && (
                <>
                  {/* Data Readiness Gate */}
                  <Panel title="02 / Verifikasi kesiapan data">
                    <p style={{fontSize: 13, color: 'var(--text-muted)'}}>
                      Verifikasi independen bahwa kapasitas stok aman dan kuota
                      pengiriman harian memadai sebelum menjalankan optimasi:
                    </p>
                    <div className="actions">
                      <button
                        type="button"
                        disabled={uncertain || busy}
                        onClick={() =>
                          mutate(
                            async () =>
                              setGate(
                                await api<Readiness>(
                                  `/cases/${selected}/validate`,
                                  {},
                                ),
                              ),
                            'Kesiapan data berhasil diperiksa.',
                          )
                        }
                      >
                        Periksa kesiapan data
                      </button>
                      <button
                        type="button"
                        className="gold-button"
                        disabled={
                          uncertain || busy || !gate?.is_ready || !!initial
                        }
                        onClick={() =>
                          mutate(
                            () => api(`/cases/${selected}/optimize`, {}),
                            'Rencana alokasi awal berhasil dihitung.',
                          )
                        }
                      >
                        Hitung alokasi awal
                      </button>
                    </div>

                    {gate && (
                      <div style={{marginTop: 16}}>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 10,
                            marginBottom: 8,
                          }}
                        >
                          <strong>Hasil Validasi:</strong>
                          <Status value={gate.is_ready ? 'PASSED' : 'FAILED'} />
                        </div>
                        {gate.validation_details.map((r) => (
                          <div
                            key={r.rule}
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              fontSize: 13,
                              padding: '4px 0',
                              borderBottom: '1px solid var(--border-subtle)',
                            }}
                          >
                            <span>{r.rule}</span>
                            <Status value={r.status} />
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>

                  {/* Plan v1 Allocation View */}
                  {initial && (
                    <>
                      <PlanView plan={initial} />
                      {initial.approval_status === 'PENDING_APPROVAL' && (
                        <div className="actions" style={{marginBottom: 20}}>
                          <button
                            type="button"
                            disabled={uncertain || busy || !actorValid}
                            onClick={() =>
                              setDecision({plan: initial, value: 'APPROVE'})
                            }
                          >
                            Setujui rencana awal
                          </button>
                          <button
                            type="button"
                            className="danger-button"
                            disabled={uncertain || busy || !actorValid}
                            onClick={() =>
                              setDecision({plan: initial, value: 'REJECT'})
                            }
                          >
                            Tolak rencana awal
                          </button>
                        </div>
                      )}
                    </>
                  )}

                  {/* Adaptive Recovery Section */}
                  <Panel title="05 / Pemulihan adaptif">
                    <div className="two-columns">
                      <div>
                        <p>
                          <strong>Evaluasi Kelayakan Recovery:</strong>{' '}
                          <Status
                            value={
                              eligibility?.evaluation_status ?? 'UNAVAILABLE'
                            }
                          />
                        </p>
                        <p>
                          Alasan Sistem:{' '}
                          {eligibility?.reason ??
                            'Memerlukan rekonsiliasi final penerimaan.'}
                        </p>
                        <p>
                          Defisit Terverifikasi:{' '}
                          <strong>
                            {recon
                              ? kg(recon.unmet_demand_kg)
                              : 'Belum direkonsiliasi'}
                          </strong>
                        </p>
                      </div>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'flex-end',
                        }}
                      >
                        <button
                          type="button"
                          className="gold-button"
                          disabled={
                            uncertain ||
                            busy ||
                            !actorValid ||
                            !eligibility?.eligible ||
                            !recon ||
                            plans.some((p) => p.version === 2)
                          }
                          onClick={() =>
                            mutate(
                              () =>
                                api(
                                  `/cases/${selected}/replan`,
                                  keyed({
                                    actor_id: actor.trim(),
                                    source_reconciliation_id:
                                      recon!.reconciliation_id,
                                  }),
                                ),
                              'Rencana pemulihan adaptif (Plan v2) berhasil dibuat.',
                            )
                          }
                        >
                          Hitung rencana pemulihan
                        </button>
                      </div>
                    </div>

                    {plans
                      .filter((p) => p.version === 2)
                      .map((p2) => (
                        <div
                          key={p2.plan_id}
                          style={{
                            marginTop: 20,
                            borderTop: '1px solid var(--border-subtle)',
                            paddingTop: 16,
                          }}
                        >
                          <PlanView plan={p2} />
                          {p2.approval_status === 'PENDING_APPROVAL' && (
                            <div className="actions">
                              <button
                                type="button"
                                disabled={uncertain || busy || !actorValid}
                                onClick={() =>
                                  setDecision({plan: p2, value: 'APPROVE'})
                                }
                              >
                                Setujui rencana pemulihan
                              </button>
                              <button
                                type="button"
                                className="danger-button"
                                disabled={uncertain || busy || !actorValid}
                                onClick={() =>
                                  setDecision({plan: p2, value: 'REJECT'})
                                }
                              >
                                Tolak rencana pemulihan
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                  </Panel>
                </>
              )}
            </>
          )}

          {/* ========================================================
              AREA 4: OPERASIONAL PENGIRIMAN (DISPATCH, CHECKPOINT, RECEIVING)
          ======================================================== */}
          {page === 'Operasional Pengiriman' && (
            <>
              <div className="subnav" aria-label="Tampilan operasional">
                <button
                  aria-pressed={operationsTab === 'shipments'}
                  className={operationsTab === 'shipments' ? '' : 'secondary'}
                  onClick={() => setOperationsTab('shipments')}
                >
                  <Truck size={17} /> Pengiriman & perjalanan
                </button>
                <button
                  aria-pressed={operationsTab === 'receiving'}
                  className={operationsTab === 'receiving' ? '' : 'secondary'}
                  onClick={() => setOperationsTab('receiving')}
                >
                  <ClipboardList size={17} /> Penerimaan & rekonsiliasi
                </button>
              </div>
              <div hidden={operationsTab !== 'shipments'}>
                {/* Capacity Overview & Chart */}
                <WarehouseCapacityChart capacities={capacityRows} />
                <CapacityTable rows={capacityRows} />

                {/* Manifest Dispatch Execution */}
                {data && (
                  <Panel title="Manifest & keberangkatan">
                    {!shipments.length && (
                      <p>
                        Manifest pengiriman akan dibuat oleh sistem setelah
                        rencana distribusi disetujui.
                      </p>
                    )}
                    {shipments.map((s) => (
                      <div
                        key={s.shipment_id}
                        data-shipment-id={s.shipment_id}
                        style={{
                          background: 'var(--ivory-muted)',
                          border: '1px solid var(--border-light)',
                          borderRadius: 'var(--radius-md)',
                          padding: 18,
                          marginBottom: 16,
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginBottom: 10,
                          }}
                        >
                          <strong style={{fontSize: 15}}>
                            {s.warehouse_id} — {s.shipment_id}
                          </strong>
                          <Status value={s.manifest_status} />
                        </div>
                        <Metrics
                          items={[
                            ['Rencana', kg(s.planned_quantity_kg)],
                            [
                              'Aktual Berangkat',
                              kg(s.actual_dispatched_quantity_kg),
                            ],
                            [
                              'Dibatalkan',
                              kg(s.unshipped_cancelled_quantity_kg),
                            ],
                            [
                              'Sisa Reservasi',
                              kg(s.remaining_reserved_stock_kg),
                            ],
                            [
                              'Estimasi Biaya',
                              rupiah(s.estimated_line_dispatched_cost),
                            ],
                          ]}
                        />
                        {s.dispatch_timestamp && (
                          <p style={{fontSize: 12, color: 'var(--text-soft)'}}>
                            Waktu Keberangkatan: {wib(s.dispatch_timestamp)}
                          </p>
                        )}

                        {s.remaining_reserved_stock_kg > 0 && (
                          <form
                            onSubmit={(e) => {
                              e.preventDefault();
                              const f = new FormDataWithSubmitter(
                                e.currentTarget,
                                e.nativeEvent,
                              );
                              const cancel = f.get('action') === 'cancel';
                              if (
                                !confirm(
                                  cancel
                                    ? `Batalkan reservasi ${String(f.get('cancelled'))} kg dari ${s.warehouse_id}?`
                                    : `Konfirmasi keberangkatan ${String(f.get('actual'))} kg dari ${s.warehouse_id}? Stok fisik akan diperbarui.`,
                                )
                              )
                                return;
                              mutate(
                                () =>
                                  api(
                                    `/shipments/${s.shipment_id}/${cancel ? 'cancel-reservation' : 'dispatch'}`,
                                    keyed({
                                      actor_id: actor.trim(),
                                      ...(cancel
                                        ? {}
                                        : {
                                            planned_quantity_kg:
                                              s.planned_quantity_kg,
                                            actual_dispatched_quantity_kg:
                                              Number(f.get('actual')),
                                          }),
                                      unshipped_cancelled_quantity_kg: Number(
                                        f.get('cancelled'),
                                      ),
                                      cancellation_reason: String(
                                        f.get('reason'),
                                      ).trim(),
                                    }),
                                  ),
                                cancel
                                  ? 'Reservasi berhasil dibatalkan.'
                                  : 'Pengiriman berhasil dikonfirmasi berangkat.',
                              );
                            }}
                          >
                            <fieldset
                              disabled={
                                uncertain ||
                                busy ||
                                !actorValid ||
                                s.remaining_reserved_stock_kg === 0
                              }
                              style={{border: 'none', padding: 0}}
                            >
                              <div className="form-grid">
                                {s.actual_dispatched_quantity_kg === 0 &&
                                  s.manifest_status === 'READY_TO_SHIP' && (
                                    <label>
                                      Aktual Berangkat (kg):
                                      <input
                                        name="actual"
                                        type="number"
                                        min="1"
                                        max={s.remaining_reserved_stock_kg}
                                        step="1"
                                        required
                                      />
                                    </label>
                                  )}
                                <label>
                                  Pembatalan Kuota (kg):
                                  <input
                                    name="cancelled"
                                    type="number"
                                    min="0"
                                    max={s.remaining_reserved_stock_kg}
                                    step="1"
                                    defaultValue="0"
                                    required
                                  />
                                </label>
                                <label>
                                  Alasan Pembatalan:
                                  <input
                                    name="reason"
                                    maxLength={2000}
                                    placeholder="Wajib diisi bila ada pembatalan"
                                  />
                                </label>
                              </div>
                              <div className="actions">
                                {s.manifest_status === 'READY_TO_SHIP' && (
                                  <button name="action" value="dispatch">
                                    Konfirmasi keberangkatan {s.warehouse_id}
                                  </button>
                                )}
                                <button
                                  type="submit"
                                  className="secondary"
                                  name="action"
                                  value="cancel"
                                >
                                  Batalkan Kuota {s.warehouse_id}
                                </button>
                              </div>
                            </fieldset>
                          </form>
                        )}

                        {/* Driver Checkpoint Timeline */}
                        <details className="shipment-checkpoint-details">
                          <summary>Riwayat perjalanan & checkpoint</summary>
                          <Checkpoints
                            shipment={s}
                            actor={actor}
                            blocked={uncertain || busy || !actorValid}
                            load={() =>
                              api(`/shipments/${s.shipment_id}/checkpoints`)
                            }
                            save={(body) =>
                              mutate(
                                () =>
                                  api(
                                    `/shipments/${s.shipment_id}/checkpoints`,
                                    keyed(body),
                                  ),
                                'Laporan checkpoint berhasil disimpan.',
                              )
                            }
                          />
                        </details>
                      </div>
                    ))}
                  </Panel>
                )}
              </div>
              <div hidden={operationsTab !== 'receiving'}>
                {/* Receiving & Final Reconciliation */}
                {data && (
                  <Panel title="Penerimaan & rekonsiliasi">
                    {!receipt ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          const f = new FormDataWithSubmitter(
                            e.currentTarget,
                            e.nativeEvent,
                          );
                          mutate(
                            () =>
                              api(
                                `/cases/${selected}/receipts`,
                                keyed({
                                  inspector_id: actor.trim(),
                                  plan_id: initial!.plan_id,
                                  shipment_ids: dispatched.map(
                                    (s) => s.shipment_id,
                                  ),
                                  total_dispatched_kg: dispatched.reduce(
                                    (n, s) =>
                                      n + s.actual_dispatched_quantity_kg,
                                    0,
                                  ),
                                  arrived_weight_kg: Number(f.get('arrived')),
                                  rejected_weight_kg: Number(f.get('rejected')),
                                  inspection_notes: String(f.get('notes')),
                                }),
                              ),
                            'Laporan penerimaan dan inspeksi berhasil disimpan.',
                          );
                        }}
                      >
                        <p>
                          Pemeriksaan mutu dan penimbangan barang tiba di titik
                          tujuan:
                        </p>
                        <fieldset
                          disabled={
                            uncertain || busy || !actorValid || !readyToReceive
                          }
                          style={{border: 'none', padding: 0}}
                        >
                          <div className="form-grid">
                            <label>
                              Berat Fisik Tiba (kg):
                              <input
                                name="arrived"
                                type="number"
                                min="0"
                                step="1"
                                max={dispatched.reduce(
                                  (n, s) => n + s.actual_dispatched_quantity_kg,
                                  0,
                                )}
                                required
                              />
                            </label>
                            <label>
                              Ditolak Sortasi/Defek (kg):
                              <input
                                name="rejected"
                                type="number"
                                min="0"
                                step="1"
                                required
                              />
                            </label>
                            <label>
                              Catatan Hasil Inspeksi:
                              <textarea
                                name="notes"
                                maxLength={2000}
                                placeholder="Kondisi kemasan, suhu, kesegaran"
                              />
                            </label>
                          </div>
                          <button type="submit">Simpan hasil penerimaan</button>
                        </fieldset>
                      </form>
                    ) : (
                      <div>
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginBottom: 12,
                          }}
                        >
                          <strong>
                            Referensi penerimaan: {receipt.receipt_id}
                          </strong>
                          <Status value={receipt.receipt_status} />
                        </div>
                        <Metrics
                          items={[
                            [
                              'Aktual Dispatch',
                              kg(receipt.total_dispatched_kg),
                            ],
                            ['Berat Tiba', kg(receipt.arrived_weight_kg)],
                            ['Ditolak Sortasi', kg(receipt.rejected_weight_kg)],
                            [
                              'Diterima Layak (Sound)',
                              kg(receipt.sound_weight_kg),
                            ],
                            [
                              'Selisih timbangan transit',
                              kg(receipt.transit_discrepancy_kg),
                            ],
                          ]}
                        />

                        {!recon && (
                          <div className="actions" style={{marginTop: 16}}>
                            <button
                              type="button"
                              className="gold-button"
                              disabled={uncertain || busy || !actorValid}
                              onClick={() => {
                                if (
                                  confirm(
                                    'Finalisasi rekonsiliasi penerimaan? Data tidak dapat diubah setelah final.',
                                  )
                                ) {
                                  mutate(
                                    () =>
                                      api(
                                        `/cases/${selected}/reconcile`,
                                        keyed({
                                          actor_id: actor.trim(),
                                          receipt_id: receipt.receipt_id,
                                        }),
                                      ),
                                    'Rekonsiliasi final berhasil diselesaikan.',
                                  );
                                }
                              }}
                            >
                              Finalisasi Rekonsiliasi & Evaluasi Defisit
                            </button>
                          </div>
                        )}
                      </div>
                    )}

                    {recon && (
                      <div
                        style={{
                          marginTop: 20,
                          borderTop: '1px solid var(--border-subtle)',
                          paddingTop: 16,
                        }}
                      >
                        <h4>
                          Hasil Rekonsiliasi Final ({recon.reconciliation_id})
                        </h4>
                        <div
                          style={{
                            display: 'flex',
                            gap: 10,
                            alignItems: 'center',
                            marginBottom: 12,
                          }}
                        >
                          <span>Status Rekonsiliasi:</span>
                          <Status value={recon.finalization_status} />
                        </div>
                        <Metrics
                          items={[
                            ['Kebutuhan Sasaran', kg(recon.target_demand_kg)],
                            ['Total Diterima Layak', kg(recon.total_sound_kg)],
                            [
                              'Kekurangan Pasokan (Defisit)',
                              kg(recon.unmet_demand_kg),
                            ],
                          ]}
                        />
                      </div>
                    )}
                  </Panel>
                )}
              </div>
            </>
          )}

          {/* ========================================================
              AREA 5: MONITORING & LAPORAN (REPORTS & AUDIT EXPLORER)
          ======================================================== */}
          {page === 'Monitoring & Laporan' && (
            <>
              {/* Report Sub-Tabs */}
              <div className="subnav">
                <button
                  type="button"
                  className={reportTab === 'shipper' ? '' : 'secondary'}
                  onClick={() => setReportTab('shipper')}
                >
                  Laporan Pengirim (Manifest)
                </button>
                <button
                  type="button"
                  className={reportTab === 'receiver' ? '' : 'secondary'}
                  onClick={() => setReportTab('receiver')}
                >
                  Laporan Penerima (BAP)
                </button>
                <button
                  type="button"
                  className={reportTab === 'audit' ? '' : 'secondary'}
                  onClick={() => setReportTab('audit')}
                >
                  Detail kasus · Riwayat & bukti
                </button>
              </div>

              {reportTab === 'shipper' && (
                <ShipperReportView caseData={data} shipments={shipments} />
              )}

              {reportTab === 'receiver' && (
                <ReceiverReportView caseData={data} receipt={receipt} />
              )}

              {reportTab === 'audit' && (
                <Panel title="Detail kasus · Riwayat & bukti">
                  <p className="muted">
                    Keputusan dan perubahan tercatat sesuai respons sistem.
                    Detail teknis tersedia per aktivitas.
                  </p>
                  <ActivityTimeline events={audit} />
                </Panel>
              )}
            </>
          )}
        </main>

        <footer
          style={{
            padding: '16px 36px',
            borderTop: '1px solid var(--border-light)',
            fontSize: 12,
            color: 'var(--text-soft)',
            display: 'flex',
            justifyContent: 'space-between',
          }}
        >
          <span>PanganNadi Adaptive Operations • Seno Hackathon ITS 2026</span>
          <span>FastAPI • Neon PostgreSQL • OR-Tools CP-SAT • XGBoost</span>
        </footer>
      </div>

      {/* Decision Dialog Modal */}
      <dialog
        aria-labelledby="decision-title"
        ref={dialog}
        onCancel={() => setDecision(null)}
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const notes = String(
              new FormDataWithSubmitter(e.currentTarget, e.nativeEvent).get(
                'notes',
              ),
            );
            const d = decision!;
            dialog.current?.close();
            setDecision(null);
            mutate(
              () =>
                api(`/plans/${d.plan.plan_id}/approve`, {
                  actor_id: actor.trim(),
                  decision: d.value,
                  notes,
                }),
              `Keputusan ${d.value} untuk Plan v${d.plan.version} berhasil dicatat.`,
            );
          }}
        >
          <h2 id="decision-title">
            {decision?.value === 'APPROVE'
              ? 'Setujui rencana distribusi?'
              : 'Tolak rencana distribusi?'}
          </h2>
          <p style={{fontSize: 14, color: 'var(--forest-900)'}}>
            Plan v{decision?.plan.version} • ID:{' '}
            <code>{decision?.plan.plan_id}</code>
          </p>
          <p style={{fontSize: 13, color: 'var(--text-muted)'}}>
            Persetujuan (Approval) mengunci reservasi stok dan kuota pengiriman
            secara atomik. Keputusan ini tercatat permanen di jejak audit.
          </p>
          <label>
            Catatan Pertimbangan:
            <textarea
              name="notes"
              rows={3}
              placeholder="Alasan pertimbangan operasional"
              maxLength={2000}
            />
          </label>
          <div className="actions" style={{marginTop: 18}}>
            <button type="submit" disabled={uncertain || busy || !actorValid}>
              {decision?.value === 'APPROVE'
                ? 'Ya, setujui rencana'
                : 'Ya, tolak rencana'}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                dialog.current?.close();
                setDecision(null);
              }}
            >
              Batal
            </button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
