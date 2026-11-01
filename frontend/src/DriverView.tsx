import {
  ArrowLeft,
  Check,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  LockKeyhole,
  MapPin,
  ShieldCheck,
  Truck,
} from 'lucide-react';
import {Logo} from './Logo';
import type {Shipment} from './types';
import './portals.css';

interface DriverViewProps {
  shipments: Shipment[];
  onSaveCheckpoint?: (
    shipmentId: string,
    eventType: string,
    note: string,
  ) => Promise<void>;
  onBackToMain: () => void;
}

/** The backend has no shipment-scoped driver principal yet. Never expose a write action here. */
export function DriverView({onBackToMain}: DriverViewProps) {
  return (
    <div className="portal-page driver-page">
      <div className="driver-shell">
        <div className="portal-topbar">
          <div className="portal-brand">
            <Logo />
            <span>
              PanganNadi<small>ADAPTIVE OPERATIONS</small>
            </span>
          </div>
          <button
            type="button"
            className="driver-back"
            aria-label="Kembali ke platform"
            onClick={onBackToMain}
          >
            <ArrowLeft size={20} />
          </button>
        </div>
        <div className="driver-title">
          <span className="portal-eyebrow">RUANG PENGEMUDI</span>
          <h1>
            Setiap kabar perjalanan
            <br />
            membantu pasokan tiba.
          </h1>
          <p>
            Laporkan kondisi perjalanan dengan ringkas, terarah, dan dapat
            ditelusuri.
          </p>
        </div>
        <div className="driver-access">
          <div className="driver-lock">
            <LockKeyhole size={24} />
          </div>
          <div>
            <strong>Akses pengemudi belum diaktifkan</strong>
            <p>
              Pratinjau desain. Identitas dan penugasan khusus pengemudi belum
              didukung server. Tidak ada data pengiriman privat atau fungsi
              kirim laporan di halaman ini.
            </p>
          </div>
        </div>
        <section className="driver-assignment">
          <div>
            <Truck size={24} />
            <span>PENUGASAN PENGIRIMAN</span>
          </div>
          <h2>
            Menunggu penugasan
            <br />
            yang terverifikasi.
          </h2>
          <p>
            Nomor pengiriman saja tidak memberikan hak akses. Rincian tugas
            tersedia setelah identitas dan penugasan dapat diverifikasi.
          </p>
          <div className="driver-assignment-footer">
            <ShieldCheck size={16} />
            <span>Belum ada penugasan terverifikasi</span>
          </div>
        </section>
        <div className="driver-progress" aria-label="Alur laporan pengemudi">
          <span>
            <b>01</b> Periksa tugas
          </span>
          <ChevronRight size={15} />
          <span>
            <b>02</b> Catat kondisi
          </span>
          <ChevronRight size={15} />
          <span>
            <b>03</b> Kirim laporan
          </span>
        </div>
        <section className="portal-surface driver-form-preview">
          <div className="portal-section-heading">
            <div>
              <span className="portal-eyebrow">PRATINJAU FORMULIR</span>
              <h2>Kabar dari perjalanan</h2>
            </div>
            <ClipboardCheck size={23} />
          </div>
          <p className="portal-footnote">
            Contoh susunan formulir. Seluruh isian dinonaktifkan dan tidak
            tersimpan.
          </p>
          <fieldset disabled>
            <label htmlFor="driver-preview-status">Status perjalanan</label>
            <select id="driver-preview-status" defaultValue="">
              <option value="">Pilih kondisi perjalanan</option>
              <option value="IN_TRANSIT">Dalam perjalanan</option>
              <option value="AT_CHECKPOINT">Tiba di titik pemeriksaan</option>
              <option value="DELAY_REPORTED">Melaporkan keterlambatan</option>
            </select>
            <label htmlFor="driver-preview-location">
              <MapPin size={15} /> Lokasi dilaporkan pengemudi
            </label>
            <input
              id="driver-preview-location"
              placeholder="Nama lokasi atau titik pemeriksaan"
            />
            <label htmlFor="driver-preview-notes">Catatan kondisi</label>
            <textarea
              id="driver-preview-notes"
              rows={3}
              placeholder="Kondisi jalan, muatan, atau kendala perjalanan"
            />
            <button type="button" className="driver-disabled-submit" disabled>
              <LockKeyhole size={16} /> Pengiriman laporan belum tersedia
            </button>
          </fieldset>
          <div className="driver-assurance">
            <Clock3 size={17} />
            <p>
              Waktu laporan akan dicatat server. Lokasi manual tidak menunjukkan
              pelacakan GPS langsung.
            </p>
          </div>
        </section>
        <div className="driver-next">
          <Check size={18} />
          <p>
            <strong>Informasi yang jelas, perjalanan yang terlacak.</strong>
            <br />
            Antarmuka ini disiapkan untuk akses terbatas berdasarkan penugasan.
          </p>
        </div>
        <footer className="portal-bottom">
          <span>PanganNadi Adaptive Operations</span>
          <span>Pratinjau purwarupa</span>
        </footer>
      </div>
    </div>
  );
}
