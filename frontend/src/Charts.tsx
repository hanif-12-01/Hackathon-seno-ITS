import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {ArrowRight, PackageCheck, ShieldCheck, Warehouse} from 'lucide-react';
import {kg, rupiah} from './lib/api';
import type {
  ForecastEvaluation,
  ForecastPredictions,
  HistoricalSeries,
  Capacity,
} from './types';
import './charts.css';

export interface FulfillmentData {
  targetDemandKg: number;
  allocatedKg: number;
  actualDispatchedKg: number;
  arrivedKg: number;
  rejectedKg: number;
  soundKg: number;
  unmetDemandKg: number;
  recoveryReservedKg: number;
}
const palette = {
  forest: '#1B5544',
  sage: '#A5B99D',
  gold: '#C99A51',
  slate: '#7C8D8A',
};
const number = new Intl.NumberFormat('id-ID', {maximumFractionDigits: 1});
const shortDate = (timestamp: number) =>
  new Intl.DateTimeFormat('id-ID', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  }).format(new Date(timestamp));
const longDate = (timestamp: number) =>
  new Intl.DateTimeFormat('id-ID', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(timestamp));
const day = (value: string) => Date.parse(value + 'T00:00:00Z');
const shortRupiah = (value: number) =>
  value >= 1000 ? `${number.format(value / 1000)} rb` : number.format(value);
const axisStyle = {fontSize: 13, fill: '#65766F'};
const tooltipStyle = {
  border: '1px solid #DDE5DF',
  borderRadius: 12,
  boxShadow: '0 12px 32px #102B2414',
  fontSize: 13,
  padding: '12px 16px',
};

export function DistributionFulfillmentChart({data}: {data: FulfillmentData}) {
  const stages = [
    {label: 'Rencana v1', value: data.allocatedKg, color: '#A5B99D'},
    {label: 'Berangkat', value: data.actualDispatchedKg, color: '#688D7E'},
    {label: 'Tiba', value: data.arrivedKg, color: '#437461'},
    {label: 'Layak diterima', value: data.soundKg, color: '#1B5544'},
  ];
  const rows: [string, number][] = [
    ['Target kebutuhan', data.targetDemandKg],
    ...stages.map((row) => [row.label, row.value] as [string, number]),
    ['Ditolak sortasi', data.rejectedKg],
    ['Kekurangan aktual', data.unmetDemandKg],
    ['Reservasi pemulihan', data.recoveryReservedKg],
  ];
  const max = Math.max(1, ...rows.map(([, value]) => value));
  const percentage =
    data.targetDemandKg > 0
      ? number.format((data.soundKg / data.targetDemandKg) * 100) + '%'
      : '—';
  return (
    <section
      className="pn-chart fulfillment-chart"
      aria-label="Grafik pemenuhan distribusi pangan"
    >
      <div className="pn-chart-heading">
        <div>
          <span className="pn-chart-eyebrow">Realisasi distribusi</span>
          <h3>Dari rencana ke penerimaan</h3>
        </div>
        <span className="pn-chart-unit">Kilogram</span>
      </div>
      <div className="fulfillment-highlight">
        <strong>{percentage}</strong>
        <span>
          kebutuhan telah diterima layak
          <small>
            {kg(data.soundKg)} dari target {kg(data.targetDemandKg)}
          </small>
        </span>
        <PackageCheck size={28} aria-hidden="true" />
      </div>
      <div className="pn-chart-canvas fulfillment-canvas">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart
            data={stages}
            layout="vertical"
            margin={{top: 12, right: 18, left: 2, bottom: 0}}
            accessibilityLayer
          >
            <CartesianGrid
              horizontal={false}
              stroke="#E7ECE7"
              strokeDasharray="3 5"
            />
            <XAxis
              type="number"
              domain={[0, max]}
              tick={axisStyle}
              tickFormatter={(value) => number.format(value)}
              axisLine={false}
              tickLine={false}
              tickCount={4}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={112}
              tick={{...axisStyle, fill: '#27473B'}}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{fill: '#F2F5F0'}}
              contentStyle={tooltipStyle}
              formatter={(value) => [kg(Number(value)), 'Kuantitas']}
            />
            {data.targetDemandKg > 0 && (
              <ReferenceLine
                x={data.targetDemandKg}
                stroke="#76867F"
                strokeDasharray="4 4"
              />
            )}
            <Bar
              dataKey="value"
              name="Kuantitas"
              barSize={22}
              radius={[0, 5, 5, 0]}
              isAnimationActive={false}
            >
              {stages.map((stage) => (
                <Cell key={stage.label} fill={stage.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="fulfillment-ledger">
        <div>
          <span>Ditolak sortasi</span>
          <strong>{kg(data.rejectedKg)}</strong>
        </div>
        <div>
          <span>Kekurangan aktual</span>
          <strong>{kg(data.unmetDemandKg)}</strong>
        </div>
        <div className="recovery-ledger">
          <span>
            <ShieldCheck size={15} aria-hidden="true" />
            Reservasi pemulihan
          </span>
          <strong>{kg(data.recoveryReservedKg)}</strong>
          <small>Belum diberangkatkan</small>
        </div>
      </div>
      <p className="pn-chart-note">
        Setiap batang menunjukkan tahap berbeda, bukan jumlah yang dijumlahkan.
        Reservasi pemulihan tidak menambah barang layak yang telah diterima.
      </p>
      <details className="pn-chart-data">
        <summary>Lihat angka distribusi</summary>
        <table>
          <caption>Rincian distribusi dalam kilogram</caption>
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{kg(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

export function WarehouseCapacityChart({capacities}: {capacities: Capacity[]}) {
  if (!capacities.length)
    return (
      <div className="pn-chart pn-chart-empty">
        <Warehouse size={26} aria-hidden="true" />
        <h3>Kapasitas belum tersedia</h3>
        <p>Data gudang muncul setelah informasi kapasitas berhasil dimuat.</p>
      </div>
    );
  return (
    <section
      className="pn-chart warehouse-chart"
      aria-label="Grafik perbandingan kapasitas gudang"
    >
      <div className="pn-chart-heading">
        <div>
          <span className="pn-chart-eyebrow">Kesiapan sumber</span>
          <h3>Kapasitas gudang terkini</h3>
        </div>
        <span className="pn-chart-unit">Data kapasitas</span>
      </div>
      <div className="warehouse-chart-grid">
        {capacities.map((c) => {
          const max = Math.max(
            c.physical_stock,
            c.safe_capacity,
            c.active_stock_reservations,
            1,
          );
          const bars = [
            {label: 'Stok fisik', value: c.physical_stock, color: '#A5B99D'},
            {label: 'Aman bebas', value: c.safe_capacity, color: '#1B5544'},
            {
              label: 'Reservasi aktif',
              value: c.active_stock_reservations,
              color: '#C99A51',
            },
          ];
          return (
            <article className="warehouse-summary" key={c.warehouse_id}>
              <div className="warehouse-summary-heading">
                <span className="warehouse-mark">
                  <Warehouse size={21} aria-hidden="true" />
                </span>
                <div>
                  <h4>{c.warehouse_id}</h4>
                  <span>Hari operasional {c.operational_date} WIB</span>
                </div>
              </div>
              <div className="warehouse-bars">
                {bars.map((row) => (
                  <div key={row.label}>
                    <div className="warehouse-bar-label">
                      <span>{row.label}</span>
                      <strong>{kg(row.value)}</strong>
                    </div>
                    <div
                      className="warehouse-bar-track"
                      role="img"
                      aria-label={`${row.label}: ${kg(row.value)}`}
                    >
                      <span
                        style={{
                          width: `${(Math.max(0, row.value) / max) * 100}%`,
                          background: row.color,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
              <dl className="warehouse-quota">
                <div>
                  <dt>Berangkat hari ini</dt>
                  <dd>{kg(c.actual_dispatched_today)}</dd>
                </div>
                <div>
                  <dt>Reservasi kuota hari ini</dt>
                  <dd>{kg(c.active_dispatch_reservations_today)}</dd>
                </div>
                <div>
                  <dt>Sisa kuota pengiriman</dt>
                  <dd>{kg(c.remaining_dispatch_capacity)}</dd>
                </div>
              </dl>
            </article>
          );
        })}
      </div>
      <p className="pn-chart-note">
        Stok aman bebas dan sisa kuota berasal dari perhitungan kapasitas
        terkini. Reservasi aktif sudah diperhitungkan; jangan dikurangkan
        kembali. Rincian komitmen lokal dan cadangan minimum belum disediakan
        dalam respons kapasitas ini.
      </p>
    </section>
  );
}

export function ModelEvaluationComparisonChart({
  evaluation,
}: {
  evaluation: ForecastEvaluation;
}) {
  const rows = [1, 3, 7].flatMap((h) => {
    const row = evaluation.evaluation_by_horizon[String(h)];
    return row
      ? [
          {
            horizon: `H+${h}`,
            naive: row.test_metrics.naive_mae,
            xgboost: row.test_metrics.xgboost_mae,
            row,
          },
        ]
      : [];
  });
  return (
    <section
      className="pn-chart model-comparison"
      aria-label="Grafik evaluasi model prediksi"
    >
      <div className="pn-chart-heading">
        <div>
          <span className="pn-chart-eyebrow">Evaluasi historis</span>
          <h3>Galat prediksi per horizon</h3>
        </div>
        <span className="pn-chart-unit">MAE · Rp/kg</span>
      </div>
      <p className="pn-chart-intro">
        Nilai lebih rendah menunjukkan galat rata-rata yang lebih kecil pada
        periode evaluasi ini.
      </p>
      <div className="pn-chart-legend">
        <span>
          <i style={{background: palette.slate}} />
          Naive
        </span>
        <span>
          <i style={{background: palette.forest}} />
          XGBoost
        </span>
      </div>
      <div className="pn-chart-canvas evaluation-canvas">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart
            data={rows}
            margin={{left: 0, top: 10, right: 6, bottom: 0}}
            accessibilityLayer
          >
            <CartesianGrid
              vertical={false}
              stroke="#E7ECE7"
              strokeDasharray="3 5"
            />
            <XAxis
              dataKey="horizon"
              axisLine={false}
              tickLine={false}
              tick={axisStyle}
            />
            <YAxis
              tickFormatter={shortRupiah}
              width={55}
              axisLine={false}
              tickLine={false}
              tick={axisStyle}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              cursor={{fill: '#F2F5F0'}}
              formatter={(value, name) => [rupiah(Number(value)) + '/kg', name]}
            />
            <Bar
              name="Naive"
              dataKey="naive"
              fill={palette.slate}
              radius={[4, 4, 0, 0]}
              maxBarSize={36}
              isAnimationActive={false}
            />
            <Bar
              name="XGBoost"
              dataKey="xgboost"
              fill={palette.forest}
              radius={[4, 4, 0, 0]}
              maxBarSize={36}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="model-comparison-results">
        {rows.map((item) => (
          <div key={item.horizon}>
            <strong>{item.horizon}</strong>
            <span>
              {item.naive < item.xgboost
                ? 'Naive memiliki MAE lebih rendah'
                : item.xgboost < item.naive
                  ? 'XGBoost memiliki MAE lebih rendah'
                  : 'MAE kedua model setara'}
            </span>
            <small>
              Model yang digunakan:{' '}
              {item.row.serving_model || item.row.selected_model}
            </small>
          </div>
        ))}
      </div>
      <p className="pn-chart-note">
        Perbedaan historis ini bersifat deskriptif. Evaluasi terdokumentasi
        belum merupakan holdout independen untuk kebijakan pemilihan model yang
        direvisi; keunggulan pada data baru masih perlu dibuktikan.
      </p>
      <details className="pn-chart-data">
        <summary>Lihat nilai MAE</summary>
        <table>
          <thead>
            <tr>
              <th>Horizon</th>
              <th>Naive</th>
              <th>XGBoost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.horizon}>
                <th scope="row">{row.horizon}</th>
                <td>{rupiah(row.naive)}/kg</td>
                <td>{rupiah(row.xgboost)}/kg</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

export function PriceForecastChart({
  historical,
  predictions,
}: {
  historical: HistoricalSeries;
  predictions: ForecastPredictions;
}) {
  const keyed = new Map<
    number,
    {
      date: number;
      sourcePrice?: number;
      filledPrice?: number;
      forecastPrice?: number;
      provenance?: string;
    }
  >();
  for (const row of historical.series)
    keyed.set(day(row.observation_date), {
      date: day(row.observation_date),
      ...(row.is_observed
        ? {sourcePrice: row.price}
        : {filledPrice: row.price}),
      provenance: row.is_observed
        ? 'Record sumber PIHPS; survei fisik belum terkonfirmasi'
        : 'Pengisian kalender dari record sebelumnya',
    });
  const origin = day(predictions.forecast_origin_date);
  keyed.set(origin, {
    ...keyed.get(origin),
    date: origin,
    forecastPrice: predictions.origin_observed_price,
  });
  for (const f of predictions.forecasts) {
    const date = day(f.target_date);
    keyed.set(date, {
      ...keyed.get(date),
      date,
      forecastPrice: f.predicted_price,
      provenance: `Prakiraan ${f.selected_model}; bukan harga aktual`,
    });
  }
  const rows = [...keyed.values()].sort((a, b) => a.date - b.date);
  if (!rows.length)
    return (
      <div className="pn-chart pn-chart-empty">
        <p>Seri harga belum tersedia.</p>
      </div>
    );
  return (
    <section
      className="pn-chart price-forecast"
      aria-label="Grafik harga historis dan prediksi masa depan"
    >
      <div className="pn-chart-heading">
        <div>
          <span className="pn-chart-eyebrow">Tren & prakiraan</span>
          <h3>Pergerakan harga cabai rawit</h3>
        </div>
        <span className="pn-chart-unit">Rupiah/kg</span>
      </div>
      <div className="pn-chart-legend">
        <span>
          <i style={{background: palette.forest}} />
          Record sumber
        </span>
        <span>
          <i className="legend-ring" />
          Pengisian kalender
        </span>
        <span>
          <i className="legend-dashed" />
          Prakiraan masa depan
        </span>
      </div>
      <div className="pn-chart-canvas price-canvas">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <ComposedChart
            data={rows}
            margin={{left: 0, top: 20, right: 20, bottom: 6}}
            accessibilityLayer
          >
            <CartesianGrid
              vertical={false}
              stroke="#E7ECE7"
              strokeDasharray="3 5"
            />
            <XAxis
              dataKey="date"
              type="number"
              scale="time"
              domain={['dataMin', 'dataMax']}
              tickFormatter={shortDate}
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
              minTickGap={38}
            />
            <YAxis
              domain={['auto', 'auto']}
              width={58}
              tickFormatter={shortRupiah}
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(value) => longDate(Number(value))}
              formatter={(value, name) => [rupiah(Number(value)) + '/kg', name]}
            />
            <ReferenceLine x={origin} stroke="#B1BBB3" strokeDasharray="4 5" />
            <Line
              name="Record sumber"
              dataKey="sourcePrice"
              stroke={palette.forest}
              strokeWidth={2.5}
              dot={false}
              activeDot={{r: 5}}
              connectNulls
              isAnimationActive={false}
            />
            <Scatter
              name="Pengisian kalender"
              dataKey="filledPrice"
              fill={palette.slate}
              isAnimationActive={false}
            />
            <Line
              name="Prakiraan masa depan"
              dataKey="forecastPrice"
              stroke={palette.gold}
              strokeDasharray="6 5"
              strokeWidth={2.5}
              dot={{r: 4, fill: '#fff', strokeWidth: 2}}
              connectNulls
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="forecast-horizon-key">
        {predictions.forecasts.map((f) => (
          <div key={f.horizon_days}>
            <span>
              H+{f.horizon_days} <ArrowRight size={13} aria-hidden="true" />{' '}
              {shortDate(day(f.target_date))}
            </span>
            <strong>
              {rupiah(f.predicted_price)}
              <small>/kg</small>
            </strong>
            <small>
              {f.selected_model.includes('naive')
                ? 'Baseline Naive'
                : 'XGBoost'}
              {f.target_schedule_expectation === 'NON_SURVEY_WEEKEND_SCHEDULE'
                ? ' · estimasi akhir pekan'
                : ''}
            </small>
          </div>
        ))}
      </div>
      <p className="pn-chart-note">
        Garis putus-putus adalah prakiraan, bukan observasi. Titik kalender
        diisi secara kausal saat record sumber tidak tersedia. Tidak ada rentang
        ketidakpastian yang dikarang.
      </p>
      <details className="pn-chart-data">
        <summary>Lihat data grafik dan asalnya</summary>
        <div className="pn-chart-table-scroll">
          <table>
            <thead>
              <tr>
                <th>Tanggal</th>
                <th>Harga sumber / isi kalender</th>
                <th>Prakiraan</th>
                <th>Keterangan</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.date}>
                  <th scope="row">{shortDate(row.date)}</th>
                  <td>
                    {row.sourcePrice === undefined &&
                    row.filledPrice === undefined
                      ? '—'
                      : rupiah(row.sourcePrice ?? row.filledPrice ?? 0) + '/kg'}
                  </td>
                  <td>
                    {row.forecastPrice === undefined
                      ? '—'
                      : rupiah(row.forecastPrice) + '/kg'}
                  </td>
                  <td>{row.provenance || 'Origin prakiraan'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

export function ForecastEvaluationSeriesChart({
  evaluation,
  horizon,
}: {
  evaluation: ForecastEvaluation;
  horizon: 1 | 3 | 7;
}) {
  const rows = evaluation.test_predictions_vs_actual
    .filter((row) => row.horizon_days === horizon)
    .map((row) => ({
      date: day(row.target_date),
      actual: row.actual_price,
      xgboost: row.xgboost_predicted_price,
      naive: row.naive_predicted_price,
    }));
  if (!rows.length)
    return (
      <div className="pn-chart pn-chart-empty">
        <p>Data pembandingan H+{horizon} belum tersedia.</p>
      </div>
    );
  return (
    <section
      className="pn-chart"
      aria-label={`Grafik evaluasi harga aktual dan model H+${horizon}`}
    >
      <div className="pn-chart-heading">
        <div>
          <span className="pn-chart-eyebrow">Periode uji historis</span>
          <h3>Observasi dan prediksi H+{horizon}</h3>
        </div>
        <span className="pn-chart-unit">Rp/kg</span>
      </div>
      <div className="pn-chart-legend">
        <span>
          <i style={{background: palette.forest}} />
          Aktual sumber
        </span>
        <span>
          <i style={{background: palette.gold}} />
          XGBoost
        </span>
        <span>
          <i style={{background: palette.slate}} />
          Naive
        </span>
      </div>
      <div className="pn-chart-canvas evaluation-series-canvas">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <ComposedChart
            data={rows}
            margin={{left: 0, top: 12, right: 16, bottom: 4}}
            accessibilityLayer
          >
            <CartesianGrid
              vertical={false}
              stroke="#E7ECE7"
              strokeDasharray="3 5"
            />
            <XAxis
              dataKey="date"
              type="number"
              scale="time"
              domain={['dataMin', 'dataMax']}
              tickFormatter={shortDate}
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
              minTickGap={38}
            />
            <YAxis
              domain={['auto', 'auto']}
              width={58}
              tickFormatter={shortRupiah}
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(value) => longDate(Number(value))}
              formatter={(value, name) => [rupiah(Number(value)) + '/kg', name]}
            />
            <Line
              name="Aktual sumber"
              dataKey="actual"
              stroke={palette.forest}
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              name="XGBoost"
              dataKey="xgboost"
              stroke={palette.gold}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              name="Naive"
              dataKey="naive"
              stroke={palette.slate}
              strokeDasharray="5 4"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="pn-chart-note">
        Pembandingan pada tanggal evaluasi terdokumentasi. Hasil historis tidak
        menjamin akurasi pada tanggal prakiraan baru.
      </p>
      <details className="pn-chart-data">
        <summary>Lihat data evaluasi H+{horizon}</summary>
        <div className="pn-chart-table-scroll">
          <table>
            <thead>
              <tr>
                <th>Tanggal</th>
                <th>Aktual</th>
                <th>XGBoost</th>
                <th>Naive</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={row.date + '-' + i}>
                  <th scope="row">{shortDate(row.date)}</th>
                  <td>{rupiah(row.actual)}/kg</td>
                  <td>{rupiah(row.xgboost)}/kg</td>
                  <td>{rupiah(row.naive)}/kg</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
