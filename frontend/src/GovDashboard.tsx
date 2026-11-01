import {useState} from 'react';
import {
  ArrowLeft,
  ArrowUpRight,
  Building2,
  ClipboardList,
  Eye,
  FileSearch,
  Layers3,
  ShieldCheck,
  Warehouse,
} from 'lucide-react';
import {Logo} from './Logo';
import type {
  CaseDetail,
  Capacity,
  Receipt,
  Reconciliation,
  Audit,
} from './types';
import {kg, wib} from './lib/api';
import {Status} from './components';
import {DistributionFulfillmentChart, WarehouseCapacityChart} from './Charts';
import './portals.css';

interface GovDashboardProps {
  caseData: CaseDetail | null;
  capacities: Capacity[];
  receipt: Receipt | null;
  recon: Reconciliation | null;
  auditLogs: Audit[];
  onBackToMain: () => void;
}

function actionLabel(action: string) {
  const labels: Record<string, string> = {
    CASE_CREATED: 'Kebutuhan distribusi dicatat',
    DATA_READINESS_VALIDATED: 'Kesiapan data diperiksa',
    PLAN_OPTIMIZED: 'Rencana alokasi dihitung',
    PLAN_APPROVED: 'Rencana distribusi disetujui',
    SHIPMENT_DISPATCHED: 'Keberangkatan dicatat',
    RECEIPT_RECORDED: 'Penerimaan dicatat',
    RECONCILIATION_FINALIZED: 'Rekonsiliasi difinalisasi',
    REPLAN_GENERATED: 'Rencana pemulihan dibuat',
  };
  return labels[action] ?? action.replaceAll('_', ' ').toLowerCase();
}

function auditDetails(details: string) {
  try {
    return JSON.stringify(JSON.parse(details), null, 2);
  } catch {
    return details;
  }
}

export function GovDashboard({
  caseData,
  capacities,
  receipt,
  recon,
  auditLogs,
  onBackToMain,
}: GovDashboardProps) {
  const [tab, setTab] = useState<'overview' | 'capacity' | 'history'>(
    'overview',
  );
  const initial = caseData?.plans.find((plan) => plan.version === 1);
  const recovery = caseData?.plans.find((plan) => plan.version === 2);
  const reserved =
    caseData?.reservations
      .filter(
        (item) =>
          item.plan_id === recovery?.plan_id && item.status === 'ACTIVE',
      )
      .reduce((sum, item) => sum + item.reserved_stock_kg, 0) ?? 0;
  const shipped = initial?.shipments?.reduce(
    (sum, shipment) => sum + shipment.actual_dispatched_quantity_kg,
    0,
  );
  const show = (value: number | undefined | null) =>
    value == null ? '—' : kg(value);
  const kpis = [
    {
      label: 'Kebutuhan terverifikasi',
      value: show(caseData?.verified_demand),
      hint: 'Target wilayah penerima',
      icon: ClipboardList,
    },
    {
      label: 'Diterima layak',
      value: show(receipt?.sound_weight_kg),
      hint: 'Setelah pemeriksaan mutu',
      icon: ShieldCheck,
    },
    {
      label: 'Kekurangan aktual',
      value: show(recon?.unmet_demand_kg),
      hint: 'Hasil rekonsiliasi fisik',
      icon: Layers3,
    },
    {
      label: 'Reservasi pemulihan',
      value: caseData ? kg(reserved) : '—',
      hint: 'Belum berarti telah dikirim',
      icon: Warehouse,
    },
  ];
  return (
    <div className="portal-page government-page">
      <div className="portal-shell">
        <div className="portal-topbar">
          <div className="portal-brand">
            <Logo />
            <span>
              PanganNadi<small>ADAPTIVE OPERATIONS</small>
            </span>
          </div>
          <button className="portal-back" type="button" onClick={onBackToMain}>
            <ArrowLeft size={16} /> Kembali ke platform
          </button>
        </div>
        <div className="portal-preview-notice" role="note">
          <Eye size={18} />
          <span>
            <strong>Pratinjau desain pengawasan.</strong> Peran pemerintah belum
            tersedia pada server. Halaman ini tidak memberi hak akses baru dan
            tidak mengirim perubahan.
          </span>
        </div>
        <div className="oversight-intro">
          <div>
            <p className="portal-eyebrow">PUSAT PENGAWASAN PANGAN</p>
            <h1>
              Informasi terhubung.
              <br />
              Keputusan terlacak.
            </h1>
            <p>
              Tinjau pemenuhan pasokan, kapasitas gudang, dan jejak keputusan
              dalam satu ruang pengawasan.
            </p>
          </div>
          <div className="oversight-context">
            <Building2 size={28} />
            <span>CAKUPAN PEMANTAUAN</span>
            <strong>
              {caseData?.destination_region ?? 'Belum ada kasus terpilih'}
            </strong>
            <small>
              {caseData
                ? 'Data berasal dari sesi operator • Simulasi'
                : 'Akses data mengikuti otorisasi server'}
            </small>
          </div>
        </div>
        <div
          className="oversight-tabs"
          role="tablist"
          aria-label="Bagian pengawasan"
        >
          {(
            [
              ['overview', 'Ringkasan', Eye],
              ['capacity', 'Kapasitas gudang', Warehouse],
              ['history', 'Riwayat & bukti', FileSearch],
            ] as const
          ).map(([id, label, Icon]) => (
            <button
              type="button"
              key={id}
              role="tab"
              id={`oversight-tab-${id}`}
              aria-controls={`oversight-panel-${id}`}
              aria-selected={tab === id}
              onClick={() => setTab(id)}
            >
              <Icon size={17} />
              {label}
            </button>
          ))}
        </div>
        <div
          role="tabpanel"
          id={`oversight-panel-${tab}`}
          aria-labelledby={`oversight-tab-${tab}`}
          tabIndex={0}
        >
          {tab === 'overview' && (
            <>
              <div className="oversight-kpis">
                {kpis.map(({label, value, hint, icon: Icon}) => (
                  <div className="oversight-kpi" key={label}>
                    <div>
                      <span>{label}</span>
                      <Icon size={19} />
                    </div>
                    <strong>{value}</strong>
                    <small>{hint}</small>
                  </div>
                ))}
              </div>
              <div className="oversight-grid">
                <section className="portal-surface">
                  <div className="portal-section-heading">
                    <div>
                      <span className="portal-eyebrow">
                        PEMENUHAN DISTRIBUSI
                      </span>
                      <h2>Dari rencana ke penerimaan</h2>
                    </div>
                    <ArrowUpRight size={20} />
                  </div>
                  {caseData && receipt && recon && shipped != null ? (
                    <DistributionFulfillmentChart
                      data={{
                        targetDemandKg: caseData.verified_demand,
                        allocatedKg: initial?.allocated_kg ?? 0,
                        actualDispatchedKg: shipped,
                        arrivedKg: receipt.arrived_weight_kg,
                        rejectedKg: receipt.rejected_weight_kg,
                        soundKg: receipt.sound_weight_kg,
                        unmetDemandKg: recon.unmet_demand_kg,
                        recoveryReservedKg: reserved,
                      }}
                    />
                  ) : (
                    <div className="portal-empty">
                      <Layers3 size={40} strokeWidth={1.2} />
                      <h3>Data pemantauan belum dimuat</h3>
                      <p>
                        Grafik tampil ketika kasus, pengiriman, dan penerimaan
                        telah tersedia dari sesi yang berwenang.
                      </p>
                    </div>
                  )}
                </section>
                <section className="portal-surface oversight-decision">
                  <ShieldCheck size={25} />
                  <span className="portal-eyebrow">INTEGRITAS KEPUTUSAN</span>
                  <h2>Pengawasan dimulai dari data yang tepat.</h2>
                  <p>
                    Persetujuan, pengiriman, dan penerimaan adalah kejadian yang
                    berbeda. Reservasi pemulihan tidak dihitung sebagai barang
                    diterima.
                  </p>
                  <div className="oversight-decision-row">
                    <span>Status kasus</span>
                    {caseData ? (
                      <Status value={caseData.status} />
                    ) : (
                      <strong>Belum tersedia</strong>
                    )}
                  </div>
                  <div className="oversight-decision-row">
                    <span>Rencana pemulihan</span>
                    <strong>
                      {recovery?.approval_status === 'APPROVED'
                        ? 'Disetujui'
                        : recovery
                          ? 'Perlu ditinjau'
                          : 'Belum tersedia'}
                    </strong>
                  </div>
                  <p className="portal-footnote">
                    Data pada purwarupa merupakan simulasi. Halaman ini bukan
                    laporan resmi pemerintah.
                  </p>
                </section>
              </div>
            </>
          )}
          {tab === 'capacity' && (
            <section className="portal-surface">
              <div className="portal-section-heading">
                <div>
                  <span className="portal-eyebrow">KEAMANAN PASOKAN</span>
                  <h2>Kapasitas gudang saat ini</h2>
                </div>
                <Warehouse size={23} />
              </div>
              {capacities.length ? (
                <WarehouseCapacityChart capacities={capacities} />
              ) : (
                <div className="portal-empty">
                  <Warehouse size={40} strokeWidth={1.2} />
                  <h3>Belum ada data kapasitas</h3>
                  <p>
                    Stok fisik, cadangan, dan reservasi ditampilkan dari
                    perhitungan otoritatif setelah akses data tersedia.
                  </p>
                </div>
              )}
            </section>
          )}
          {tab === 'history' && (
            <section className="portal-surface">
              <div className="portal-section-heading">
                <div>
                  <span className="portal-eyebrow">RIWAYAT & BUKTI</span>
                  <h2>Jejak keputusan distribusi</h2>
                </div>
                <FileSearch size={23} />
              </div>
              {auditLogs.length ? (
                <ol className="portal-audit-list">
                  {auditLogs.map((log) => (
                    <li key={log.id}>
                      <span className="portal-audit-dot" />
                      <div>
                        <h3>{actionLabel(log.action_type)}</h3>
                        <p>
                          {log.actor_id} <span>· {wib(log.timestamp)}</span>
                        </p>
                        <details>
                          <summary>Lihat detail teknis</summary>
                          <pre>{auditDetails(log.details)}</pre>
                        </details>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="portal-empty">
                  <FileSearch size={40} strokeWidth={1.2} />
                  <h3>Riwayat belum tersedia</h3>
                  <p>
                    Aktivitas akan tampil dari catatan server. Tidak ada riwayat
                    atau identitas aktor yang dibuat di halaman ini.
                  </p>
                </div>
              )}
            </section>
          )}
        </div>
        <footer className="portal-bottom">
          <span>PanganNadi Adaptive Operations</span>
          <span>Purwarupa pengawasan • Akses hanya baca pada antarmuka</span>
        </footer>
      </div>
    </div>
  );
}
