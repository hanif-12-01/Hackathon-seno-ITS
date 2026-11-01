import {useState, type ReactNode} from 'react';
import {
  ChartNoAxesCombined,
  RefreshCw,
  ShieldCheck,
  ArrowUpRight,
  Info,
} from 'lucide-react';
import type {
  Plan,
  Capacity,
  ForecastStatus,
  HistoricalSeries,
  ForecastPredictions,
  ForecastEvaluation,
} from './types';
import {
  kg,
  rupiah,
  horizonLabel,
  describeUnavailableReason,
  describeTargetSchedule,
} from './lib/api';
import {PriceForecastChart, ForecastEvaluationSeriesChart} from './Charts';
const statusNames: Record<string, string> = {
  APPROVED: 'Disetujui',
  PENDING_APPROVAL: 'Menunggu persetujuan',
  REJECTED: 'Ditolak',
  OPTIMAL: 'Optimal',
  FEASIBLE: 'Layak',
  FINALIZED: 'Final',
  RECORDED: 'Tercatat',
  DRAFT: 'Draf',
  VERIFIED: 'Terverifikasi',
  UNVERIFIED: 'Belum terverifikasi',
  DISPATCHED: 'Diberangkatkan',
  READY_TO_SHIP: 'Siap berangkat',
  CLOSED: 'Ditutup',
  RECOVERY_RESERVED: 'Pemulihan direservasi',
  RECOVERY_PENDING_APPROVAL: 'Menunggu persetujuan pemulihan',
  PASSED: 'Lolos',
  FAILED: 'Perlu diperiksa',
  FRESH: 'Mutakhir',
  STALE: 'Perlu pembaruan',
  UNAVAILABLE: 'Belum tersedia',
  NOT_FINALIZED: 'Belum final',
  STABLE_NORMAL: 'Stabil',
  SPIKE_RISK: 'Risiko kenaikan',
  ELEVATED: 'Meningkat',
  xgboost_residual_v1: 'XGBoost',
  naive_persistence_v1: 'Naive baseline',
};
export function Status({value}: {value: string}) {
  return (
    <span
      title={value}
      className={`badge ${/FAILED|REJECT|CONFLICT|UNAVAILABLE|NOT_TRAINED|SPIKE_RISK|ERROR/.test(value) ? 'danger' : /PENDING|DRAFT|PARTIAL|DEFICIT|STALE|ELEVATED/.test(value) ? 'caution' : /APPROVED|PASSED|FINALIZED|OPTIMAL|VALIDATED|READY|FRESH|STABLE_NORMAL/.test(value) ? 'good' : 'info'}`}
    >
      {statusNames[value] ?? value.replaceAll('_', ' ')}
    </span>
  );
}
export function Panel({title, children}: {title: string; children: ReactNode}) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
export function Metrics({items}: {items: [string, string][]}) {
  return (
    <div className="metrics">
      {items.map(([label, value]) => (
        <div className="metric" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}
export function CapacityTable({rows}: {rows: Capacity[]}) {
  return (
    <Panel title="Kapasitas gudang terkini">
      <div className="table-scroll" tabIndex={0}>
        <table>
          <thead>
            <tr>
              {[
                'Gudang',
                'Stok fisik',
                'Stok aman bebas',
                'Berangkat hari ini',
                'Reservasi stok',
                'Reservasi kuota',
                'Sisa kuota',
                'Hari WIB',
              ].map((x) => (
                <th key={x}>{x}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.warehouse_id}>
                <th>{r.warehouse_id}</th>
                <td>{kg(r.physical_stock)}</td>
                <td>{kg(r.safe_capacity)}</td>
                <td>{kg(r.actual_dispatched_today)}</td>
                <td>{kg(r.active_stock_reservations)}</td>
                <td>{kg(r.active_dispatch_reservations_today)}</td>
                <td>{kg(r.remaining_dispatch_capacity)}</td>
                <td>{r.operational_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <p>Belum ada data gudang.</p>}
    </Panel>
  );
}
export function PlanView({plan}: {plan: Plan}) {
  return (
    <Panel
      title={
        plan.version === 1
          ? '03 / Rencana distribusi awal'
          : 'Rencana pemulihan · Versi 2'
      }
    >
      <div className="statuses">
        <Status value={plan.approval_status} />
        <span>
          Hasil perhitungan <Status value={plan.solver_status} />
        </span>
        <Status value={plan.fulfillment_status} />
      </div>
      <Metrics
        items={[
          ['Alokasi', kg(plan.allocated_kg)],
          ['Kebutuhan belum teralokasi', kg(plan.unmet_demand_kg)],
          ['Estimasi biaya', rupiah(plan.planned_cost)],
        ]}
      />
      <div className="table-scroll" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th>Gudang sumber</th>
              <th>Alokasi (kg)</th>
              <th>Tarif / kg</th>
              <th>Subtotal</th>
            </tr>
          </thead>
          <tbody>
            {plan.lines.map((l) => (
              <tr key={l.warehouse_id}>
                <th>{l.warehouse_id}</th>
                <td>{kg(l.allocated_kg)}</td>
                <td>{rupiah(l.cost_per_kg)}</td>
                <td>
                  {rupiah(
                    l.subtotal ??
                      l.subtotal_cost ??
                      l.allocated_kg * l.cost_per_kg,
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        Alokasi dan reservasi bukan bukti barang berangkat atau diterima. Biaya
        rencana merupakan estimasi.
      </p>
      <details>
        <summary>Referensi rencana</summary>
        <code>{plan.plan_id}</code>
      </details>
    </Panel>
  );
}
export function ForecastIntelligenceView({
  loading,
  status,
  historical,
  predictions,
  evaluation,
  errorMsg,
  onReload,
}: {
  loading: boolean;
  status: ForecastStatus | null;
  historical: HistoricalSeries | null;
  predictions: ForecastPredictions | null;
  evaluation: ForecastEvaluation | null;
  errorMsg?: string;
  onReload: () => void;
}) {
  const [evalHorizon, setEvalHorizon] = useState<1 | 3 | 7>(1);
  if (loading)
    return (
      <section
        className="panel"
        role="status"
        aria-label="Memuat intelijen harga"
      >
        <div className="dashboard-skeleton">
          <div className="skeleton-line" />
          <div className="skeleton-chart" />
        </div>
        <span className="sr-only">Sedang memuat data harga…</span>
      </section>
    );
  if (!status?.available || !historical || !predictions || !evaluation)
    return (
      <Panel title="Intelijen harga">
        <div className="empty">
          <ChartNoAxesCombined size={42} strokeWidth={1.3} />
          <h3>Data prakiraan belum tersedia</h3>
          <p>
            Prakiraan ditampilkan setelah data harga dan model yang sesuai
            tersedia. Pengiriman tetap memerlukan verifikasi kebutuhan fisik.
          </p>
          <button className="secondary" onClick={onReload}>
            <RefreshCw size={16} /> Coba muat kembali
          </button>
          <details>
            <summary>Informasi ketersediaan</summary>
            <p data-testid="forecast-unavailable-reason">
              {describeUnavailableReason(
                status?.status ?? 'API_ERROR',
                status?.reason || errorMsg,
              )}
            </p>
          </details>
        </div>
      </Panel>
    );
  const evalRows = evaluation.test_predictions_vs_actual.filter(
    (r) => r.horizon_days === evalHorizon,
  );
  return (
    <div className="intelligence-page">
      <section className="panel intelligence-context">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">INTELIJEN HARGA / JAWA TIMUR</span>
            <h2>{predictions.commodity}</h2>
            <p className="muted">
              {predictions.geographic_scope} · {predictions.data_source}
            </p>
          </div>
          <button className="secondary" onClick={onReload}>
            <RefreshCw size={15} /> Perbarui
          </button>
        </div>
        <div className="intelligence-stat-row">
          <div>
            <span>Harga record terakhir</span>
            <strong>
              {rupiah(predictions.origin_observed_price)}
              <small>/kg</small>
            </strong>
          </div>
          <div>
            <span>Tanggal record sumber</span>
            <strong>{predictions.latest_observation_date}</strong>
          </div>
          <div>
            <span>Kesegaran data</span>
            <Status value={predictions.data_freshness.freshness_status} />
          </div>
          <div>
            <span>Sinyal kewaspadaan</span>
            <Status value={predictions.overall_risk_signal.risk_level} />
          </div>
        </div>
      </section>
      {!predictions.data_freshness.is_fresh && (
        <div role="alert" className="message error">
          <strong>Data perlu diperbarui.</strong>{' '}
          {predictions.data_freshness.freshness_note} Prakiraan berikut tetap
          menggunakan tanggal record yang tercantum.
        </div>
      )}
      <Panel title="Dinamika harga & arah prakiraan">
        <PriceForecastChart historical={historical} predictions={predictions} />
        <p className="muted">
          <Info size={14} /> Harga memberi sinyal kewaspadaan. Ketersediaan
          fisik harus diverifikasi sebelum alokasi.
        </p>
      </Panel>
      <div className="forecast-cards">
        {predictions.forecasts.map((f) => (
          <article
            className="forecast-card"
            key={f.horizon_days}
            data-testid={`forecast-row-h${f.horizon_days}`}
          >
            <div className="forecast-card-top">
              <span>H+{f.horizon_days}</span>
              <small>{f.target_date}</small>
            </div>
            <p className="muted">Prakiraan {f.horizon_days} hari kalender</p>
            <strong>
              {rupiah(f.predicted_price)}
              <small>/kg</small>
            </strong>
            <div className="forecast-change">
              {f.predicted_change_rp >= 0 ? '+' : ''}
              {rupiah(f.predicted_change_rp)}{' '}
              <span>({f.predicted_change_pct}%)</span>
            </div>
            <div className="forecast-model">
              <span>Model yang disajikan</span>
              <Status value={f.selected_model} />
            </div>
            <details>
              <summary>Asal model & cakupan target</summary>
              <div
                data-testid={`forecast-target-disclosure-h${f.horizon_days}`}
              >
                <p>
                  {describeTargetSchedule(
                    f.target_schedule_expectation,
                    f.actual_observation_available,
                    f.evaluation_reference.applies_directly_to_target_date,
                    f.target_schedule_matches_evaluation_scope,
                  )}
                </p>
                <p>{f.target_validation_disclosure}</p>
                <p>
                  Model XGBoost {rupiah(f.xgboost_predicted_price)} · Baseline{' '}
                  {rupiah(f.naive_predicted_price)}
                </p>
              </div>
            </details>
          </article>
        ))}
      </div>
      <div
        className="intelligence-disclosure"
        data-testid="forecast-provenance-status"
      >
        <ShieldCheck size={21} />
        <div>
          <strong>Transparansi sumber & batas penggunaan</strong>
          <p>
            Record harga PIHPS mengonfirmasi data yang diterbitkan; status
            survei fisik per tanggal belum diketahui. Akhir pekan yang diisi
            secara kausal bukan observasi pasar baru.
          </p>
          <details>
            <summary>Lihat metadata sumber</summary>
            <p>
              {predictions.provenance_verification_status} ·{' '}
              {predictions.model_version}
            </p>
            <p>{predictions.operational_boundary_note}</p>
          </details>
        </div>
      </div>
      <Panel title="Evaluasi model · bukti yang dapat ditinjau">
        <p className="muted" data-testid="forecast-holdout-disclosure">
          Hasil historis bersifat deskriptif dan belum merupakan holdout
          independen untuk kebijakan model yang direvisi. XGBoost tidak selalu
          lebih akurat; model Naive digunakan pada H+7 sesuai kebijakan yang
          dicatat. Validasi prospektif masih diperlukan.
        </p>
        <div className="subnav">
          {([1, 3, 7] as const).map((h) => (
            <button
              key={h}
              aria-pressed={evalHorizon === h}
              className={evalHorizon === h ? '' : 'secondary'}
              onClick={() => setEvalHorizon(h)}
            >
              Evaluasi H+{h}
            </button>
          ))}
        </div>
        <ForecastEvaluationSeriesChart
          evaluation={evaluation}
          horizon={evalHorizon}
        />
        <div className="table-scroll" tabIndex={0}>
          <table>
            <thead>
              <tr>
                <th>Horizon</th>
                <th>Sampel</th>
                <th>MAE Naive</th>
                <th>MAE XGBoost</th>
                <th>Model yang disajikan</th>
              </tr>
            </thead>
            <tbody>
              {['1', '3', '7'].map((h) => {
                const ev = evaluation.evaluation_by_horizon[h];
                return ev ? (
                  <tr key={h} data-testid={`eval-metric-row-h${h}`}>
                    <th>{horizonLabel(ev.horizon_days)}</th>
                    <td>
                      {ev.sample_counts.test_observed_pairs} pasang hari kerja
                    </td>
                    <td>{rupiah(ev.test_metrics.naive_mae)}</td>
                    <td>{rupiah(ev.test_metrics.xgboost_mae)}</td>
                    <td>
                      <Status value={ev.selected_model} />
                    </td>
                  </tr>
                ) : null;
              })}
            </tbody>
          </table>
        </div>
        <details className="evaluation-method">
          <summary>Metodologi, kebijakan model, dan rincian evaluasi</summary>
          <p>
            Split kronologis: train sampai{' '}
            {evaluation.split_configuration.train_cutoff_date}, validasi{' '}
            {evaluation.split_configuration.validation_start_date}–
            {evaluation.split_configuration.validation_cutoff_date}, uji{' '}
            {evaluation.split_configuration.test_start_date}–
            {evaluation.split_configuration.test_cutoff_date}. Cakupan utama
            adalah pasangan record hari kerja.
          </p>
          {['1', '3', '7'].map((h) => {
            const ev = evaluation.evaluation_by_horizon[h];
            return ev ? (
              <p key={h}>
                <strong>
                  H+{h} · {ev.selection_governance_status}
                </strong>
                <br />
                {ev.selection_rationale}
              </p>
            ) : null;
          })}
          <div className="table-scroll" tabIndex={0}>
            <table>
              <thead>
                <tr>
                  <th>Tanggal target</th>
                  <th>Record sumber</th>
                  <th>XGBoost</th>
                  <th>Naive</th>
                </tr>
              </thead>
              <tbody>
                {evalRows.slice(-15).map((v) => (
                  <tr key={`${v.origin_date}-${v.target_date}`}>
                    <td>{v.target_date}</td>
                    <td>{rupiah(v.actual_price)}</td>
                    <td>{rupiah(v.xgboost_predicted_price)}</td>
                    <td>{rupiah(v.naive_predicted_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </Panel>
    </div>
  );
}
