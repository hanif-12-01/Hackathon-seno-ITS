import {useState, type ReactNode} from 'react';
import type {Plan, Capacity, ForecastStatus, HistoricalSeries, ForecastPredictions, ForecastEvaluation} from './types';
import {kg, rupiah, horizonLabel, describeUnavailableReason, describeTargetSchedule, mapChartSeries} from './lib/api';

export function Status({value}:{value:string}) {
  return <span className={`badge ${/FAILED|REJECT|CONFLICT|UNAVAILABLE|NOT_TRAINED|SPIKE_RISK|ERROR/.test(value)?'danger':/PENDING|DRAFT|PARTIAL|DEFICIT|STALE|ELEVATED/.test(value)?'caution':/APPROVED|PASSED|FINALIZED|OPTIMAL|VALIDATED|READY|FRESH|STABLE_NORMAL/.test(value)?'good':'info'}`}>{value}</span>;
}

export function Panel({title,children}:{title:string;children:ReactNode}) {
  return <section className="panel"><h2>{title}</h2>{children}</section>;
}

export function Metrics({items}:{items:[string,string][]}) {
  return <div className="metrics">{items.map(([label,value])=><div className="metric" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>;
}

export function CapacityTable({rows}:{rows:Capacity[]}) {
  return <Panel title="Kapasitas gudang terkini"><div className="table-scroll"><table><thead><tr>{['Gudang','Stok fisik','Stok aman bebas','Dispatch hari ini','Reservasi stok','Reservasi dispatch','Sisa kuota','Hari WIB'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r.warehouse_id}><th>{r.warehouse_id}</th><td>{kg(r.physical_stock)}</td><td>{kg(r.safe_capacity)}</td><td>{kg(r.actual_dispatched_today)}</td><td>{kg(r.active_stock_reservations)}</td><td>{kg(r.active_dispatch_reservations_today)}</td><td>{kg(r.remaining_dispatch_capacity)}</td><td>{r.operational_date}</td></tr>)}</tbody></table></div>{!rows.length&&<p>Belum ada data gudang.</p>}</Panel>;
}

export function PlanView({plan}:{plan:Plan}) {
  return <Panel title={`Plan v${plan.version} — ${plan.plan_id}`}><div className="statuses"><Status value={plan.approval_status}/><span>Solver <Status value={plan.solver_status}/></span><span>Alokasi <Status value={plan.fulfillment_status}/></span></div><Metrics items={[["Alokasi",kg(plan.allocated_kg)],["Unmet demand",kg(plan.unmet_demand_kg)],["Biaya rencana",rupiah(plan.planned_cost)]]}/><div className="table-scroll"><table><thead><tr><th>Gudang</th><th>Alokasi</th><th>Tarif / kg</th><th>Subtotal</th></tr></thead><tbody>{plan.lines.map(l=><tr key={l.warehouse_id}><th>{l.warehouse_id}</th><td>{kg(l.allocated_kg)}</td><td>{rupiah(l.cost_per_kg)}</td><td>{rupiah(l.subtotal??l.subtotal_cost??0)}</td></tr>)}</tbody></table></div><p className="muted">Hasil alokasi bukan bukti barang diterima. Biaya rencana bukan tagihan faktur.</p></Panel>;
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
  const [evalHorizon, setEvalHorizon] = useState<1|3|7>(1);

  if (loading && !status && !predictions) {
    return <Panel title="AI Price Intelligence"><div role="status" className="message">Memuat data AI Price Intelligence dari backend…</div></Panel>;
  }

  if (!status?.available || !historical || !predictions || !evaluation) {
    const code = status?.status ?? 'API_ERROR';
    const detail = describeUnavailableReason(code, status?.reason || errorMsg);
    return (
      <Panel title="AI FORECASTING NOT CONFIGURED">
        <div className="empty">
          <span className="empty-mark">AI</span>
          <h3>Prediksi belum tersedia</h3>
          <p data-testid="forecast-unavailable-reason">{detail}</p>
          <p>Status diagnostik: <Status value={code}/></p>
          <p>Kemungkinan penyebab yang diperiksa sistem: Dataset unavailable • Model not trained • Artifact unavailable • Stale data • API error.</p>
          <strong>Sinyal harga bukan bukti verified physical demand.</strong>
          <div className="actions"><button type="button" className="secondary" onClick={onReload}>Periksa ulang status AI</button></div>
        </div>
      </Panel>
    );
  }

  const histItems = historical.series.map(pt => ({date: pt.observation_date, price: pt.price, kind: 'historical' as const}));
  const futureItems = [
    ...(histItems.length ? [histItems[histItems.length - 1]] : []),
    ...predictions.forecasts.map(f => ({date: f.target_date, price: f.predicted_price, kind: 'forecast' as const})),
  ];
  const allForecastPrices = [...histItems.map(i => i.price), ...predictions.forecasts.flatMap(f => [f.predicted_price, f.xgboost_predicted_price, f.naive_predicted_price])];
  const fDomain: [number, number] = [Math.min(...allForecastPrices), Math.max(...allForecastPrices)];
  const combinedSeries = mapChartSeries([...histItems, ...predictions.forecasts.map(f => ({date: f.target_date, price: f.predicted_price, kind: 'forecast' as const}))], 760, 240, 28, fDomain);
  const histCoords = combinedSeries.coords.filter(c => c.kind === 'historical');
  const futCoords = combinedSeries.coords.slice(Math.max(histCoords.length - 1, 0));

  const evalRows = evaluation.test_predictions_vs_actual.filter(r => r.horizon_days === evalHorizon);
  const evalPrices = evalRows.flatMap(r => [r.actual_price, r.xgboost_predicted_price, r.naive_predicted_price]);
  const eDomain: [number, number] = evalPrices.length ? [Math.min(...evalPrices), Math.max(...evalPrices)] : [0, 1];
  const actualChart = mapChartSeries(evalRows.map(r => ({date: r.target_date, price: r.actual_price, kind: 'actual'})), 760, 220, 28, eDomain);
  const xgbChart = mapChartSeries(evalRows.map(r => ({date: r.target_date, price: r.xgboost_predicted_price, kind: 'xgboost'})), 760, 220, 28, eDomain);
  const naiveChart = mapChartSeries(evalRows.map(r => ({date: r.target_date, price: r.naive_predicted_price, kind: 'naive'})), 760, 220, 28, eDomain);

  return (
    <>
      <Panel title="AI Price Intelligence — Provenance, Kesegaran Data & Sinyal Risiko">
        <div className="statuses">
          <span>Status Layanan <Status value={status.status}/></span>
          <span>Data Freshness <Status value={predictions.data_freshness.freshness_status}/></span>
          <span>Sinyal Risiko <Status value={predictions.overall_risk_signal.risk_level}/></span>
          <button type="button" className="secondary" onClick={onReload}>Muat ulang prediksi</button>
        </div>
        {!predictions.data_freshness.is_fresh && (
          <div role="alert" className="message error">
            Peringatan Data Stale: {predictions.data_freshness.freshness_note} Timestamp dataset tidak dimanipulasi otomatis.
          </div>
        )}
        <Metrics items={[
          ["Komoditas & Cakupan", `${predictions.commodity} · ${predictions.geographic_scope}`],
          ["Sumber Data", predictions.data_source],
          ["Record Sumber Terakhir", `${predictions.latest_observation_date} (${rupiah(predictions.origin_observed_price)}/${predictions.unit.split('/')[1] || 'kg'})`],
          ["Versi Model", `${predictions.model_version} (${historical.total_observed_records} record Senin–Jumat)`],
        ]}/>
        <p className="muted" data-testid="forecast-provenance-status">
          <strong>Status Provenance Record Sumber (R1):</strong> <Status value={predictions.provenance_verification_status}/> — Record Senin–Jumat PIHPS mengonfirmasi keberadaan <em>source record</em> (<code>SOURCE_RECORD_PRESENT</code>), namun payload publik tidak menyertakan metadata pelaksanaan survei lapangan per tanggal (<code>UNKNOWN_OBSERVATION_PROVENANCE</code>).
        </p>
        <p className="muted">{predictions.operational_boundary_note}</p>
      </Panel>

      <Panel title="Prediksi Harga Masa Depan (Horizon 1, 3, dan 7 Hari Kalender)">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Horizon</th>
                <th>Origin Date</th>
                <th>Target Date</th>
                <th>Status Target & Cakupan Evaluasi</th>
                <th>Prediksi Utama</th>
                <th>Model Serving</th>
                <th>Prediksi XGBoost</th>
                <th>Baseline Naive</th>
                <th>Perubahan</th>
                <th>Sinyal Risiko</th>
              </tr>
            </thead>
            <tbody>
              {predictions.forecasts.map(f => (
                <tr key={f.horizon_days} data-testid={`forecast-row-h${f.horizon_days}`}>
                  <th>{horizonLabel(f.horizon_days)}</th>
                  <td>{f.forecast_origin_date}</td>
                  <td>{f.target_date} ({f.target_day_of_week}{!f.target_is_weekday_market ? ' · Akhir Pekan Non-Survei' : ''})</td>
                  <td data-testid={`forecast-target-disclosure-h${f.horizon_days}`}>
                    <Status value={f.target_schedule_expectation}/>
                    <br/>
                    <small>{describeTargetSchedule(f.target_schedule_expectation, f.actual_observation_available, f.evaluation_reference.applies_directly_to_target_date, f.target_schedule_matches_evaluation_scope)}</small>
                     <br/><small>target_source_record_present={String(f.target_source_record_present)} • actual_observation_available={String(f.actual_observation_available)} • target_confirmed_market_observation={f.target_confirmed_market_observation === null ? 'null (status survei tidak diketahui)' : String(f.target_confirmed_market_observation)}</small>
                  </td>
                  <td><strong>{rupiah(f.predicted_price)}</strong>/{f.unit.split('/')[1] || 'kg'}</td>
                  <td><Status value={f.selected_model}/></td>
                  <td>{rupiah(f.xgboost_predicted_price)}</td>
                  <td>{rupiah(f.naive_predicted_price)}</td>
                  <td>{f.predicted_change_rp >= 0 ? '+' : ''}{rupiah(f.predicted_change_rp)} ({f.predicted_change_pct >= 0 ? '+' : ''}{f.predicted_change_pct}%)</td>
                  <td><Status value={f.risk_signal.risk_level}/></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {predictions.forecasts.map(f => (
          <p key={`disc-${f.horizon_days}`} className="muted">
            <strong>Disclosure H+{f.horizon_days} ({f.target_date}):</strong> {f.target_validation_disclosure}
          </p>
        ))}
        <p className="muted">
          <strong>Metodologi Risk Signal ({predictions.overall_risk_signal.signal_method}):</strong> {predictions.overall_risk_signal.explanation} (Bukan probabilitas terkalibrasi; tidak memicu dispatch otomatis).
        </p>
      </Panel>

      <Panel title={`Grafik Harga Historis (${historical.returned_records_count} Record Sumber Terakhir) & Titik Prediksi Masa Depan`}>
        <div className="chart-box">
          <div className="chart-legend">
            <span><strong>Garis Hijau Solid:</strong> Record Sumber Historis PIHPS ({historical.series[0]?.observation_date} s.d. {historical.latest_observation_date})</span>
            <span><strong>Garis Putus-Putus Oranye:</strong> Prediksi Masa Depan (H+1, H+3, H+7 setelah {predictions.forecast_origin_date})</span>
            <span>Rentang: {rupiah(combinedSeries.minPrice)} – {rupiah(combinedSeries.maxPrice)}</span>
          </div>
          <svg viewBox="0 0 760 240" role="img" aria-label="Grafik harga historis dan prediksi masa depan" className="price-chart">
            <polyline fill="none" stroke="#176a50" strokeWidth="2.5" points={histCoords.map(c => `${c.x},${c.y}`).join(' ')} />
            <polyline fill="none" stroke="#b86b11" strokeWidth="2.5" strokeDasharray="6 4" points={futCoords.map(c => `${c.x},${c.y}`).join(' ')} />
            {futCoords.slice(1).map((c, idx) => (
              <g key={c.date}>
                <circle cx={c.x} cy={c.y} r="5" fill="#b86b11" />
                <text x={Math.min(c.x - 95, 635)} y={Math.max(Math.min(c.y + (idx === 0 ? 18 : idx === 1 ? -6 : -20), 228), 16)} fontSize="10.5" fontWeight="600" fill="#714108">
                  H+{predictions.forecasts[idx]?.horizon_days}: {rupiah(c.price)}
                </text>
              </g>
            ))}
          </svg>
        </div>
        <p className="muted">Data historis record sumber dan titik prediksi masa depan dipisahkan secara visual dengan label eksplisit.</p>
      </Panel>

      <Panel title="Evaluasi Kronologis Model: Naive Persistence Baseline vs XGBoost (Evaluasi Historis Deskriptif)">
        <p className="muted">
          Metode Split: <strong>{evaluation.split_configuration.method}</strong> • Train ≤ {evaluation.split_configuration.train_cutoff_date} • Validation {evaluation.split_configuration.validation_start_date} s.d. {evaluation.split_configuration.validation_cutoff_date} • Test {evaluation.split_configuration.test_start_date} s.d. {evaluation.split_configuration.test_cutoff_date} (Seed {evaluation.split_configuration.random_seed}). Cakupan evaluasi utama: <strong>SOURCE_RECORD_WEEKDAY_PAIRS_ONLY</strong> (tidak mencakup target Sabtu/Minggu).
        </p>
        <p className="muted" data-testid="forecast-holdout-disclosure">Semua horizon H+1/H+3/H+7: evaluasi retrospektif bukan independent holdout untuk kebijakan revisi; selisih test hanya deskriptif, bukan bukti signifikansi. H+7 memerlukan validasi prospektif sebelum klaim kebijakan terverifikasi: origin setelah 2026-10-28, minimal 60 origin record sumber yang memenuhi syarat per horizon, bobot/fitur/hyperparameter/kebijakan serving tetap, tanpa interim peeking atau tuning, dinilai setelah target matang. Protokol dibekukan, belum diukur; perubahan kebijakan memerlukan jendela baru yang belum disentuh.</p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Horizon</th>
                <th>Sampel Uji</th>
                <th>Val Naive MAE</th>
                <th>Val XGBoost MAE</th>
                <th>Test Naive MAE</th>
                <th>Test XGBoost MAE</th>
                <th>Test Naive RMSE</th>
                <th>Test XGBoost RMSE</th>
                <th>Perbaikan Test MAE</th>
                <th>Val-Selected Model</th>
                <th>Serving Model & Governance</th>
              </tr>
            </thead>
            <tbody>
              {['1', '3', '7'].map(hKey => {
                const ev = evaluation.evaluation_by_horizon[hKey];
                if (!ev) return null;
                return (
                  <tr key={hKey} data-testid={`eval-metric-row-h${hKey}`}>
                    <th>{horizonLabel(ev.horizon_days)}</th>
                    <td>{ev.sample_counts.test_observed_pairs} pasang hari kerja</td>
                    <td>{rupiah(ev.validation_metrics.naive.mae)}</td>
                    <td>{rupiah(ev.validation_metrics.xgboost.mae)}</td>
                    <td>{rupiah(ev.test_metrics.naive_mae)}</td>
                    <td>{rupiah(ev.test_metrics.xgboost_mae)}</td>
                    <td>{rupiah(ev.test_metrics.naive.rmse)}</td>
                    <td>{rupiah(ev.test_metrics.xgboost.rmse)}</td>
                    <td>{ev.test_metrics.absolute_improvement_rp >= 0 ? '-' : '+'}{rupiah(Math.abs(ev.test_metrics.absolute_improvement_rp))} ({ev.test_metrics.relative_improvement_pct === null ? 'N/A — MAE Naive nol' : `${ev.test_metrics.relative_improvement_pct >= 0 ? '+' : ''}${ev.test_metrics.relative_improvement_pct}%`})</td>
                    <td><Status value={ev.validation_selected_model ?? ev.selected_model}/></td>
                    <td>
                      <Status value={ev.selected_model}/>
                      <br/>
                      <small>{ev.selection_governance_status ?? 'VALIDATION_ONLY_SELECTION'}</small>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {['1', '3', '7'].map(hKey => {
          const ev = evaluation.evaluation_by_horizon[hKey];
          return ev ? <p key={hKey} className="muted"><strong>H+{hKey}:</strong> {ev.selection_rationale}</p> : null;
        })}
      </Panel>

      <Panel title="Perbandingan Aktual vs Prediksi pada Periode Evaluasi Uji (2026-08-01 s.d. 2026-10-28)">
        <div className="actions">
          {([1, 3, 7] as const).map(h => (
            <button key={h} type="button" className={evalHorizon === h ? '' : 'secondary'} onClick={() => setEvalHorizon(h)}>
              Evaluasi {horizonLabel(h)}
            </button>
          ))}
        </div>
        <div className="chart-box">
          <div className="chart-legend">
            <span><strong>Hitam/Hijau Tua:</strong> Harga Record Sumber ({evalRows.length} titik uji)</span>
            <span><strong>Hijau:</strong> Prediksi XGBoost (MAE {rupiah(evaluation.evaluation_by_horizon[String(evalHorizon)]?.test_metrics.xgboost_mae ?? 0)})</span>
            <span><strong>Oranye Putus-Putus:</strong> Prediksi Naive Baseline (MAE {rupiah(evaluation.evaluation_by_horizon[String(evalHorizon)]?.test_metrics.naive_mae ?? 0)})</span>
          </div>
          <svg viewBox="0 0 760 220" role="img" aria-label="Grafik evaluasi aktual vs prediksi periode uji" className="price-chart">
            <polyline fill="none" stroke="#103a2f" strokeWidth="2.5" points={actualChart.polyline} />
            <polyline fill="none" stroke="#1f8a65" strokeWidth="2" points={xgbChart.polyline} />
            <polyline fill="none" stroke="#b86b11" strokeWidth="1.8" strokeDasharray="4 4" points={naiveChart.polyline} />
          </svg>
        </div>
        <details>
          <summary>Tabel rincian evaluasi aktual vs prediksi H+{evalHorizon} ({evalRows.length} observasi uji)</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Origin Date</th>
                  <th>Target Date</th>
                  <th>Harga Record Sumber</th>
                  <th>Prediksi XGBoost</th>
                  <th>Galat XGBoost</th>
                  <th>Prediksi Naive</th>
                  <th>Galat Naive</th>
                </tr>
              </thead>
              <tbody>
                {evalRows.slice(-15).map(r => (
                  <tr key={`${r.origin_date}-${r.target_date}`}>
                    <td>{r.origin_date}</td>
                    <td>{r.target_date}</td>
                    <td>{rupiah(r.actual_price)}</td>
                    <td>{rupiah(r.xgboost_predicted_price)}</td>
                    <td>{rupiah(r.xgboost_abs_error)}</td>
                    <td>{rupiah(r.naive_predicted_price)}</td>
                    <td>{rupiah(r.naive_abs_error)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </Panel>
    </>
  );
}
