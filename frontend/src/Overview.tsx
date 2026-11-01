import {
  ArrowUpRight,
  ArrowRight,
  CheckCircle2,
  PackageCheck,
  ShieldCheck,
  Truck,
  Warehouse,
  Circle,
  Clock3,
} from 'lucide-react';
import type {
  Audit,
  Capacity,
  CaseDetail,
  Plan,
  Receipt,
  Reconciliation,
} from './types';
import {kg, wib} from './lib/api';
import {Status} from './components';
import {DistributionFulfillmentChart} from './Charts';
import {summarizeDistribution} from './lib/dashboard';

const actions: Record<string, string> = {
  CASE_CREATED: 'Kasus distribusi dibuat',
  READINESS_VALIDATED: 'Kesiapan data diperiksa',
  PLAN_CREATED: 'Rencana alokasi dibuat',
  PLAN_OPTIMIZED: 'Alokasi dihitung',
  PLAN_APPROVED: 'Rencana disetujui',
  PLAN_REJECTED: 'Rencana ditolak',
  SHIPMENT_DISPATCHED: 'Pengiriman diberangkatkan',
  RESERVATION_CANCELLED: 'Reservasi dibatalkan',
  RECEIPT_RECORDED: 'Penerimaan dicatat',
  RECONCILIATION_FINALIZED: 'Penerimaan direkonsiliasi',
  REPLAN_GENERATED: 'Rencana pemulihan dibuat',
  RECOVERY_PLAN_GENERATED: 'Rencana pemulihan dibuat',
  CHECKPOINT_RECORDED: 'Checkpoint perjalanan dicatat',
};
export function ActivityTimeline({
  events,
  compact = false,
}: {
  events: Audit[];
  compact?: boolean;
}) {
  if (!events.length)
    return (
      <p className="muted">Belum ada aktivitas tercatat untuk kasus ini.</p>
    );
  return (
    <ol className="activity-timeline">
      {[...events]
        .sort((a, b) => b.id - a.id)
        .slice(0, compact ? 4 : events.length)
        .map((event) => {
          let detail: unknown = event.details;
          try {
            detail = JSON.parse(event.details);
          } catch {}
          return (
            <li key={event.id}>
              <span className="activity-node">
                <CheckCircle2 size={15} />
              </span>
              <div>
                <strong>
                  {actions[event.action_type] ??
                    event.action_type.replaceAll('_', ' ').toLowerCase()}
                </strong>
                <p>
                  {event.actor_id} <span>·</span> {wib(event.timestamp)}
                </p>
                {!compact && (
                  <details>
                    <summary>Lihat detail teknis</summary>
                    <pre>
                      {typeof detail === 'string'
                        ? detail
                        : JSON.stringify(detail, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            </li>
          );
        })}
    </ol>
  );
}
export function DashboardSkeleton() {
  return (
    <div
      className="dashboard-skeleton"
      role="status"
      aria-label="Sedang memuat data distribusi"
    >
      <span className="sr-only">Sedang memuat data…</span>
      <div className="skeleton-line" />
      <div className="kpi-grid">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton-kpi" />
        ))}
      </div>
      <div className="overview-grid">
        <div className="skeleton-chart" />
        <div className="skeleton-chart" />
      </div>
    </div>
  );
}
export function Overview({
  data,
  plans,
  capacities,
  receipt,
  recon,
  audit,
  summary,
}: {
  data: CaseDetail;
  plans: Plan[];
  capacities: Capacity[];
  receipt: Receipt | null;
  recon: Reconciliation | null;
  audit: Audit[];
  summary: ReturnType<typeof summarizeDistribution>;
}) {
  const recovery = plans.find((p) => p.version === 2);
  const percent =
    data.verified_demand > 0 && receipt
      ? Math.min(100, (receipt.sound_weight_kg / data.verified_demand) * 100)
      : null;
  const next =
    recovery?.approval_status === 'APPROVED'
      ? 'Reservasi pemulihan tercatat'
      : recovery
        ? 'Tinjau rencana pemulihan'
        : recon?.unmet_demand_kg
          ? 'Susun rencana pemulihan'
          : receipt
            ? 'Tinjau penerimaan'
            : plans.length
              ? 'Pantau pelaksanaan pengiriman'
              : 'Verifikasi kebutuhan dan sumber';
  return (
    <div className="overview-content">
      <div className="section-intro">
        <div>
          <span className="eyebrow">RINGKASAN OPERASIONAL</span>
          <h2>Setiap kilogram, terpantau.</h2>
        </div>
        <span className="context-pill">
          <Circle size={7} fill="currentColor" /> {data.commodity_name}
        </span>
      </div>
      <div className="kpi-grid">
        <div className="kpi">
          <span>
            Target kebutuhan <Warehouse size={17} />
          </span>
          <strong>{kg(data.verified_demand)}</strong>
          <small>
            Kebutuhan tujuan{' '}
            {data.is_demand_verified ? 'terverifikasi' : 'belum terverifikasi'}
          </small>
        </div>
        <div className="kpi kpi-accent">
          <span>
            Diterima layak <PackageCheck size={18} />
          </span>
          <strong>{receipt ? kg(receipt.sound_weight_kg) : '—'}</strong>
          <small>
            {percent !== null
              ? `${percent.toLocaleString('id-ID', {maximumFractionDigits: 1})}% dari target kebutuhan`
              : 'Menunggu pemeriksaan penerimaan'}
          </small>
        </div>
        <div className="kpi">
          <span>
            Kekurangan aktual <Circle size={15} />
          </span>
          <strong>{recon ? kg(recon.unmet_demand_kg) : '—'}</strong>
          <small>
            {recon ? 'Berdasarkan rekonsiliasi final' : 'Belum direkonsiliasi'}
          </small>
        </div>
        <div className="kpi">
          <span>
            Reservasi pemulihan <ShieldCheck size={18} />
          </span>
          <strong>{kg(summary.recoveryReservedKg)}</strong>
          <small>
            {recovery
              ? `Plan v2 · ${recovery.approval_status === 'APPROVED' ? 'disetujui' : 'menunggu keputusan'} · belum diterima`
              : 'Belum ada rencana pemulihan'}
          </small>
        </div>
      </div>
      <div className="overview-grid">
        <div className="fulfillment-primary">
          <DistributionFulfillmentChart data={summary} />
          {!recon && (
            <p className="muted chart-boundary">
              Kekurangan aktual ditampilkan setelah rekonsiliasi final. Alokasi
              dan reservasi bukan penerimaan.
            </p>
          )}
        </div>
        <section className="decision-panel">
          <span className="eyebrow">LANGKAH BERIKUTNYA</span>
          <span className="decision-icon">
            <ShieldCheck size={29} strokeWidth={1.3} />
          </span>
          <h2>{next}</h2>
          <p>
            {recovery?.approval_status === 'APPROVED'
              ? 'Stok dan kuota untuk pemulihan telah direservasi. Jumlah ini belum dihitung sebagai barang berangkat atau diterima.'
              : 'Tinjau kondisi dan batas operasional sebelum melanjutkan. Setiap keputusan tetap memerlukan persetujuan manusia.'}
          </p>
          <div className="decision-facts">
            <span>
              Status kasus
              <Status value={data.status} />
            </span>
            <span>
              Verifikasi kebutuhan
              <strong>
                {data.is_demand_verified ? 'Terverifikasi' : 'Perlu diperiksa'}
              </strong>
            </span>
            <span>
              Rekonsiliasi
              <strong>{recon ? 'Sudah difinalisasi' : 'Belum final'}</strong>
            </span>
          </div>
          <a className="button" href="#Perencanaan%20Distribusi">
            Tinjau perencanaan <ArrowRight size={17} />
          </a>
        </section>
      </div>
      <div className="overview-bottom">
        <section className="panel warehouse-summary">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">SUMBER PASOKAN</span>
              <h2>Kesiapan gudang</h2>
            </div>
            <a
              href="#Operasional%20Pengiriman"
              aria-label="Lihat operasional gudang"
            >
              <ArrowUpRight size={20} />
            </a>
          </div>
          {capacities.length ? (
            capacities.map((c) => (
              <article className="warehouse-row" key={c.warehouse_id}>
                <span className="warehouse-symbol">
                  <Warehouse size={22} />
                </span>
                <div>
                  <h3>{c.warehouse_id}</h3>
                  <p>
                    Stok aman bebas <strong>{kg(c.safe_capacity)}</strong>
                  </p>
                  <span className="warehouse-meter">
                    <span
                      style={{
                        width: `${c.physical_stock ? Math.min(100, (c.safe_capacity / c.physical_stock) * 100) : 0}%`,
                      }}
                    />
                  </span>
                </div>
                <div>
                  <strong>{kg(c.remaining_dispatch_capacity)}</strong>
                  <small>Sisa kuota harian</small>
                </div>
              </article>
            ))
          ) : (
            <p className="muted">Data kapasitas belum tersedia.</p>
          )}
          <p className="muted">
            Kapasitas terkini telah memperhitungkan reservasi aktif; cadangan
            tidak dijumlahkan ulang.
          </p>
        </section>
        <section className="panel recent-activity">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">JEJAK KEPUTUSAN</span>
              <h2>Aktivitas terbaru</h2>
            </div>
            <Clock3 size={19} />
          </div>
          <ActivityTimeline events={audit} compact />
          <a className="text-link" href="#Monitoring%20%26%20Laporan">
            Lihat laporan & riwayat <ArrowUpRight size={16} />
          </a>
        </section>
      </div>
      <div className="overview-report-link">
        <FilesIcon />
        <div>
          <strong>Dokumentasi yang dapat ditelusuri</strong>
          <p>
            Manifest pengirim, hasil penerimaan, dan riwayat keputusan dalam
            satu tempat.
          </p>
        </div>
        <a className="button secondary" href="#Monitoring%20%26%20Laporan">
          Buka laporan <ArrowUpRight size={16} />
        </a>
      </div>
    </div>
  );
}
function FilesIcon() {
  return <Truck size={24} strokeWidth={1.4} />;
}
