import {
  FileCheck2,
  FileText,
  Printer,
  Scale,
  ShieldCheck,
  Truck,
} from 'lucide-react';
import type {CaseDetail, Receipt, Shipment} from './types';
import {kg, wib} from './lib/api';
import {Status} from './components';
import {Logo} from './Logo';
import './reports.css';

function text(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : 'Tidak tercatat';
}
function timestamp(value: unknown): string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? wib(value)
    : 'Tidak tercatat';
}
function printDocument(kind: 'sender' | 'receiver') {
  document.body.dataset.pnPrint = kind;
  window.addEventListener(
    'afterprint',
    () => {
      delete document.body.dataset.pnPrint;
    },
    {once: true},
  );
  window.print();
}
function ReportBrand() {
  return (
    <div className="report-brand">
      <Logo />
      <div>
        <strong>PanganNadi</strong>
        <span>ADAPTIVE OPERATIONS</span>
      </div>
    </div>
  );
}

export function ShipperReportView({
  caseData,
  shipments,
}: {
  caseData: CaseDetail | null;
  shipments: Shipment[];
}) {
  if (!caseData || !shipments.length)
    return (
      <section className="report-empty">
        <FileText size={30} aria-hidden="true" />
        <h2>Laporan pengirim belum tersedia</h2>
        <p>
          Pilih kasus dengan manifest pengiriman untuk meninjau rencana,
          keberangkatan, dan pembatalan reservasi.
        </p>
      </section>
    );
  const planned = shipments.reduce(
    (sum, row) => sum + row.planned_quantity_kg,
    0,
  );
  const dispatched = shipments.reduce(
    (sum, row) => sum + row.actual_dispatched_quantity_kg,
    0,
  );
  const cancelled = shipments.reduce(
    (sum, row) => sum + row.unshipped_cancelled_quantity_kg,
    0,
  );
  const reserved = shipments.reduce(
    (sum, row) => sum + row.remaining_reserved_stock_kg,
    0,
  );
  return (
    <article
      className="report-sheet"
      data-report-kind="sender"
      aria-labelledby="sender-report-title"
    >
      <div className="report-masthead">
        <ReportBrand />
        <span className="report-simulation">SIMULASI / PROOF OF CONCEPT</span>
      </div>
      <div className="report-title-row">
        <div>
          <span className="report-eyebrow">Dokumen distribusi · 01</span>
          <h2 id="sender-report-title">Laporan pengirim</h2>
          <p>
            Rekap manifest, realisasi keberangkatan, dan reservasi gudang
            sumber.
          </p>
        </div>
        <button
          type="button"
          className="secondary report-print-button"
          onClick={() => printDocument('sender')}
        >
          <Printer size={16} aria-hidden="true" />
          Cetak laporan
        </button>
      </div>
      <dl className="report-context">
        <div>
          <dt>Referensi kasus</dt>
          <dd>{caseData.id}</dd>
        </div>
        <div>
          <dt>Tujuan distribusi</dt>
          <dd>{caseData.destination_region}</dd>
        </div>
        <div>
          <dt>Komoditas</dt>
          <dd>{caseData.commodity_name}</dd>
        </div>
        <div>
          <dt>Perencana kasus</dt>
          <dd>{text(caseData.created_by)}</dd>
        </div>
      </dl>
      <div className="report-kpis">
        <div>
          <span>Rencana seluruh manifest</span>
          <strong>{kg(planned)}</strong>
        </div>
        <div>
          <span>Aktual diberangkatkan</span>
          <strong>{kg(dispatched)}</strong>
        </div>
        <div>
          <span>Reservasi dibatalkan</span>
          <strong>{kg(cancelled)}</strong>
        </div>
        <div>
          <span>Masih direservasi</span>
          <strong>{kg(reserved)}</strong>
        </div>
      </div>
      <div className="report-section-title">
        <Truck size={18} aria-hidden="true" />
        <h3>Rincian manifest pengiriman</h3>
        <span>{shipments.length} manifest</span>
      </div>
      <div className="report-table-scroll">
        <table className="report-table">
          <caption>
            Rencana dan realisasi per manifest. Rencana pemulihan dicatat
            terpisah dari barang yang telah diberangkatkan.
          </caption>
          <thead>
            <tr>
              <th>Referensi & rencana</th>
              <th>Gudang asal</th>
              <th>Rencana</th>
              <th>Berangkat</th>
              <th>Dibatalkan</th>
              <th>Reservasi tersisa</th>
              <th>Keberangkatan (WIB)</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {shipments.map((shipment) => {
              const plan = caseData.plans.find(
                (candidate) => candidate.plan_id === shipment.plan_id,
              );
              return (
                <tr key={shipment.shipment_id}>
                  <th scope="row">
                    <span className="report-id">{shipment.shipment_id}</span>
                    <small>
                      {plan
                        ? `Rencana v${plan.version}`
                        : 'Versi rencana tidak tersedia'}
                      {plan?.version === 2 ? ' · pemulihan' : ''}
                    </small>
                  </th>
                  <td data-label="Gudang asal">{shipment.warehouse_id}</td>
                  <td data-label="Rencana">
                    {kg(shipment.planned_quantity_kg)}
                  </td>
                  <td data-label="Berangkat" className="report-quantity-actual">
                    {kg(shipment.actual_dispatched_quantity_kg)}
                  </td>
                  <td data-label="Dibatalkan">
                    {kg(shipment.unshipped_cancelled_quantity_kg)}
                  </td>
                  <td data-label="Reservasi tersisa">
                    {kg(shipment.remaining_reserved_stock_kg)}
                  </td>
                  <td data-label="Keberangkatan">
                    {shipment.dispatch_timestamp
                      ? timestamp(shipment.dispatch_timestamp)
                      : 'Belum diberangkatkan'}
                  </td>
                  <td data-label="Status">
                    <Status value={shipment.manifest_status} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="report-footnotes">
        <div>
          <ShieldCheck size={18} aria-hidden="true" />
          <p>
            <strong>Alokasi dan keberangkatan dibedakan.</strong> Rencana
            seluruh manifest dapat mencakup pemulihan. Hanya kolom aktual
            berangkat yang menunjukkan barang diberangkatkan; reservasi aktif
            bukan bukti pengiriman atau penerimaan.
          </p>
        </div>
        <p>
          Identitas operator pelaksana dispatch tidak tersedia dalam kontrak
          manifest ini. Perencana kasus tidak ditampilkan sebagai pengganti
          operator pelaksana. Estimasi biaya rencana dapat ditinjau pada detail
          rencana; bukan tagihan pembayaran.
        </p>
      </div>
      <footer className="report-footer">
        <span>PanganNadi Adaptive · Artefak simulasi</span>
        <span>Bukan dokumen resmi pemerintah yang diterbitkan.</span>
      </footer>
    </article>
  );
}

export function ReceiverReportView({
  caseData,
  receipt,
}: {
  caseData: CaseDetail | null;
  receipt: Receipt | null;
}) {
  if (!caseData || !receipt)
    return (
      <section className="report-empty">
        <FileCheck2 size={30} aria-hidden="true" />
        <h2>Laporan penerimaan belum tersedia</h2>
        <p>
          Catatan akan tersedia setelah petugas mencatat pemeriksaan penerimaan
          pada kasus yang dipilih.
        </p>
      </section>
    );
  const finalized = receipt.receipt_status === 'FINALIZED';
  const arithmeticSound =
    receipt.arrived_weight_kg - receipt.rejected_weight_kg;
  const arithmeticDiscrepancy =
    receipt.total_dispatched_kg - receipt.arrived_weight_kg;
  const arithmeticMatches =
    arithmeticSound === receipt.sound_weight_kg &&
    arithmeticDiscrepancy === receipt.transit_discrepancy_kg;
  return (
    <article
      className="report-sheet"
      data-report-kind="receiver"
      aria-labelledby="receiver-report-title"
    >
      <div className="report-masthead">
        <ReportBrand />
        <span className="report-simulation">SIMULASI / PROOF OF CONCEPT</span>
      </div>
      <div className="report-title-row">
        <div>
          <span className="report-eyebrow">Dokumen distribusi · 02</span>
          <h2 id="receiver-report-title">Laporan penerimaan & pemeriksaan</h2>
          <p>
            Catatan bobot tiba dan hasil sortasi gabungan di tujuan distribusi.
          </p>
        </div>
        <button
          type="button"
          className="secondary report-print-button"
          onClick={() => printDocument('receiver')}
        >
          <Printer size={16} aria-hidden="true" />
          Cetak laporan
        </button>
      </div>
      <dl className="report-context">
        <div>
          <dt>Referensi penerimaan</dt>
          <dd>{receipt.receipt_id}</dd>
        </div>
        <div>
          <dt>Referensi kasus</dt>
          <dd>{caseData.id}</dd>
        </div>
        <div>
          <dt>Tujuan distribusi</dt>
          <dd>{caseData.destination_region}</dd>
        </div>
        <div>
          <dt>Komoditas</dt>
          <dd>{caseData.commodity_name}</dd>
        </div>
      </dl>
      <div className="report-status-line">
        <Status value={receipt.receipt_status} />
        <span>
          {finalized
            ? 'Penerimaan telah difinalisasi.'
            : 'Penerimaan tercatat; belum dinyatakan final.'}
        </span>
      </div>
      <div className="report-kpis receiver-report-kpis">
        <div>
          <span>Aktual berangkat</span>
          <strong>{kg(receipt.total_dispatched_kg)}</strong>
        </div>
        <div>
          <span>Bobot tiba</span>
          <strong>{kg(receipt.arrived_weight_kg)}</strong>
        </div>
        <div>
          <span>Ditolak sortasi</span>
          <strong>{kg(receipt.rejected_weight_kg)}</strong>
        </div>
        <div className="report-kpi-sound">
          <span>Diterima layak</span>
          <strong>{kg(receipt.sound_weight_kg)}</strong>
        </div>
      </div>
      <div className="report-inspection-grid">
        <section className="report-inspection">
          <div className="report-section-title">
            <Scale size={18} aria-hidden="true" />
            <h3>Rekonsiliasi timbangan</h3>
          </div>
          <dl className="report-calculation">
            <div>
              <dt>Bobot tiba − ditolak sortasi</dt>
              <dd>
                {kg(receipt.arrived_weight_kg)} −{' '}
                {kg(receipt.rejected_weight_kg)}
              </dd>
            </div>
            <div className="report-calculation-result">
              <dt>Layak diterima</dt>
              <dd>{kg(receipt.sound_weight_kg)}</dd>
            </div>
            <div>
              <dt>Berangkat − bobot tiba</dt>
              <dd>
                {kg(receipt.total_dispatched_kg)} −{' '}
                {kg(receipt.arrived_weight_kg)}
              </dd>
            </div>
            <div className="report-calculation-result">
              <dt>Selisih transit timbangan</dt>
              <dd>{kg(receipt.transit_discrepancy_kg)}</dd>
            </div>
          </dl>
          {!arithmeticMatches && (
            <p className="report-warning" role="alert">
              Angka penerimaan perlu ditinjau: hasil perhitungan bobot tidak
              sama dengan ringkasan yang diterima. Data ditampilkan tanpa
              diubah.
            </p>
          )}
          <p className="report-small-note">
            Selisih timbangan dicatat objektif. Penyebab fisik, kehilangan, atau
            tanggung jawab pihak tertentu tidak disimpulkan dari angka ini.
          </p>
        </section>
        <section className="report-inspection">
          <div className="report-section-title">
            <FileCheck2 size={18} aria-hidden="true" />
            <h3>Catatan pemeriksaan</h3>
          </div>
          <dl className="report-inspector">
            <div>
              <dt>Petugas pemeriksa</dt>
              <dd>{text(receipt.inspector_id)}</dd>
            </div>
            <div>
              <dt>Waktu pemeriksaan (WIB)</dt>
              <dd>{timestamp(receipt.inspected_at)}</dd>
            </div>
            <div>
              <dt>Catatan hasil sortasi</dt>
              <dd className="report-inspection-notes">
                {text(receipt.inspection_notes)}
              </dd>
            </div>
          </dl>
          <p className="report-small-note">
            Bobot bruto dan tara tidak ditampilkan karena tidak tersedia dalam
            kontrak penerimaan ini.
          </p>
        </section>
      </div>
      <div className="report-footnotes">
        <div>
          <ShieldCheck size={18} aria-hidden="true" />
          <p>
            <strong>
              Penerimaan bersumber dari pemeriksaan aktual yang dicatat.
            </strong>{' '}
            Alokasi atau reservasi pemulihan tidak ditambahkan ke jumlah layak
            diterima. Laporan ini mengikuti status penerimaan tersimpan.
          </p>
        </div>
      </div>
      <footer className="report-footer">
        <span>PanganNadi Adaptive · Artefak simulasi</span>
        <span>Bukan dokumen resmi pemerintah yang diterbitkan.</span>
      </footer>
    </article>
  );
}
