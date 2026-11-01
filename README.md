# PanganNadi Adaptive

Sistem Pendukung Keputusan (Decision Support System / DSS) redistribusi pangan cabai rawit merah (*Capsicum frutescens*) di Jawa Timur berbasis sinyal risiko disparitas harga deret waktu dan optimasi alokasi adaptif multi-gudang dengan kendala stok aman dan kuota armada harian kumulatif.

Dikembangkan untuk **Seno Hackathon ITS 2026** oleh Tim PanganNadi (Hanif, Fauzan, ARU, Akbar).

---

## 📌 Hakikat Produk & Filosofi Sistem

PanganNadi Adaptive dirancang dengan prinsip **Human-in-the-Loop Decision Support**:
1. **Prediksi Harga Bukan Pemicu Alokasi Otomatis**: Model prediksi deret waktu berfungsi menghasilkan indikator peringatan dini (*early warning risk signal*), bukan instruksi dispatch otomatis. Kebutuhan fisik di wilayah tujuan wajib melalui proses verifikasi resmi.
2. **Data Readiness Gate**: Verifikasi formal atas kemutakhiran data (<24 jam), kelengkapan kuantitas, integritas stok aman, dan konfirmasi kebutuhan resmi sebelum solver alokasi dijalankan.
3. **Optimasi Berbasis Kendala Nyata**: Formulasi Google OR-Tools CP-SAT meminimalkan biaya transportasi variabel terhadap kendala stok aman minimum (*safety stock*) daerah sumber dan kuota kapasitas pengiriman harian kumulatif per gudang.
4. **Single-Level Human Approval**: Otorisasi tunggal oleh pejabat logistik berwenang sebelum reservasi stok dan kuota dikunci ke dalam sistem. Dilarang melakukan dispatch otomatis tanpa persetujuan manusia.
5. **Closed-Loop Adaptive Replanning**: Pencatatan penerimaan independen di tujuan (Berita Acara Penerimaan / BAP) mendeteksi selisih transit timbangan dan sortasi mutu. Jika terjadi defisit, sistem menghitung rencana pemulihan (*Plan v2*, status `PENDING_APPROVAL`) pada hari operasional yang sama dengan memperhitungkan sisa kuota armada harian dan stok aman yang belum terpakai.
6. **Buku Besar Audit Mutlak**: Seluruh pembuatan kasus, parameter solver, keputusan pejabat, reservasi stok, manifest keberangkatan, dan penerimaan tercatat secara persisten dan tidak dapat diubah (*immutable audit trail*).

---

## 🏗️ Arsitektur & Tumpukan Teknologi

- **Frontend**: React 19, TypeScript, Vite 7, GSAP (animasi performan dengan dukungan penuh *reduced motion*), Recharts (visualisasi harga, horizon evaluasi, dan kapasitas gudang), Lucide React (ikonografi SVG native, bebas emoji), dan Custom CSS Civic Agri-Logistics design system.
- **Backend**: FastAPI (Modular Monolith), SQLAlchemy 2, SQLite untuk pengembangan dan pengujian lokal terisolasi, PostgreSQL + Psycopg untuk deployment cloud.
- **Mesin Optimasi**: Google OR-Tools CP-SAT (Integer Programming lexicographic penalty formulation).
- **Intelijen Harga**: Model XGBoost Regressor (horizon H+1, H+3) dan Naive Persistence baseline (H+7, sesuai kebijakan evaluasi empiris eksploratori ADR-021).
- **Keamanan & Perimeter**: Operator Perimeter dengan HTTP Basic Auth berbasis PBKDF2-HMAC-SHA256 (600.000 iterasi), *actor binding* di sisi server (`request.state.principal`), validasi header `Origin` terpercaya untuk mencegah CSRF, pembatasan laju (*rate limiting*) atomik pada tabel database, header proteksi `Cache-Control: no-store`, dan Vercel Deployment Protection (SSO).

---

## 🔒 Pemberitahuan Keterbukaan Sumber (Source-Only Release)

Repositori publik GitHub ini merupakan rilis **kode sumber terbuka (*source-only*)**. Demi perlindungan keamanan, kepatuhan hak kekayaan intelektual, dan integritas data operasional:
1. **Aset AI Privat Tidak Disertakan**: Berkas bobot model machine learning terlatih, dataset historis deret waktu PIHPS/Bapanas, dan berkas evaluasi historis sengaja **dikecualikan** dari repositori publik.
2. **Ketiadaan Aset Citra Biner**: Repositori kode sumber publik tidak menyertakan berkas citra biner (foto lanskap, logo biner, favicon). Seluruh antarmuka web dibangun menggunakan komponen SVG terprogram (Lucide React) dan kode sumber TypeScript/CSS murni; aplikasi web terbukti dapat dikompilasi secara lengkap (`npm run build`) tanpa dependensi build pada berkas citra biner tersebut.
3. **Database Operasional Tidak Disertakan**: Repositori tidak memuat berkas database SQLite lokal, kredensial koneksi cloud, file lingkungan `.env`, atau log operasional privat.
4. **Perilaku Fail-Closed**: Tanpa aset model lokal, endpoint liveness `/health` tetap mengembalikan HTTP 200 OK, sedangkan endpoint kesiapan `/ready` dan prediksi `/forecasting/*` mengembalikan HTTP 503 (`DATASET_UNAVAILABLE` atau `MODEL_NOT_TRAINED`) secara aman tanpa mengganggu fungsionalitas operasional lainnya.

---

## 📁 Struktur Repositori

```text
├── backend/
│   ├── app/
│   │   ├── api/routes.py              # Endpoint REST API operasional & analitik
│   │   ├── core/                      # Keamanan PBKDF2, rate limiting, koneksi database & CA bundle
│   │   ├── models/                    # Skema SQLAlchemy & Pydantic
│   │   ├── services/                  # Gate, optimizer OR-Tools, replanning, receiving, forecasting
│   │   ├── main.py                    # Aplikasi FastAPI & konfigurasi middleware
│   │   └── vercel.py                  # Entrypoint serverless Vercel Services
│   ├── tests/                         # Suite pengujian otomatis backend
│   ├── pyproject.toml                 # Deklarasi dependensi Python 3.12 (UV/pip)
│   ├── requirements.txt               # Dependensi inti backend
│   └── requirements-vercel.txt        # Dependensi terpin untuk runtime Vercel
├── frontend/
│   ├── src/                           # Komponen React, CSS tokens, typed API client & dashboard
│   ├── e2e/                           # Pengujian alur kerja Playwright E2E
│   ├── index.html                     # Dokumen HTML utama
│   ├── package.json                   # Dependensi frontend (React 19, Vite 7, GSAP, Recharts)
│   ├── tsconfig.json                  # Konfigurasi TypeScript ketat
│   └── vite.config.ts                 # Konfigurasi build Vite
├── tools/
│   ├── package_preview.py             # Utilitas staging deployment Vercel Preview privat
│   ├── test_package_preview.py        # Pengujian regresi packaging allowlist
│   └── audit_push_history.py          # Auditor keamanan blob Git outgoing
├── .gitignore                         # Pengabaian berkas sensitif, DB, env & aset privat
├── .vercelignore                      # Pencegah upload langsung dari direktori pengembangan
├── vercel.json                        # Konfigurasi Vercel Services & pencegah autodeploy main
└── README.md                          # Dokumentasi resmi repositori publik
```

---

## 🚀 Panduan Menjalankan Secara Lokal

### Prasyarat
- **Python**: Versi 3.12 (disarankan menggunakan lingkungan virtual venv).
- **Node.js**: Versi 20.19+ atau 22.12+ (kompatibel dengan Vite 7).
- **npm**: Versi 10+.

### 1. Menjalankan Backend (FastAPI)

Buka terminal dan arahkan ke direktori `backend`:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Atur variabel lingkungan untuk database SQLite lokal baru (disposable) dan jalankan server:

```powershell
$env:PANGANNADI_DATABASE_URL = "sqlite:///" + (Join-Path (Get-Location) "pangannadi-dev.sqlite3").Replace("\","/")
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend akan berjalan pada `http://127.0.0.1:8000`.

### 2. Menjalankan Frontend (React + Vite)

Buka terminal terpisah dan arahkan ke direktori `frontend`:

```powershell
cd frontend
npm ci
npm run dev
```

Frontend pengembangan akan berjalan pada `http://127.0.0.1:5173`. Masuk ke aplikasi melalui antarmuka login yang disediakan.

### 3. Menjalankan Pengujian Otomatis

**Pengujian Frontend:**
```powershell
cd frontend
npm test
npm run build
```

**Pengujian Packaging Tools:**
```powershell
python -B tools/test_package_preview.py -v
```

---

## 🧠 Konfigurasi Aset AI Privat (Khusus Lingkungan Tertutup)

Bagi pengembang berwenang yang memiliki izin akses berkas model dan data historis, arahkan variabel lingkungan `PANGANNADI_FORECAST_ROOT` ke direktori absolut di luar repositori publik yang memuat 6 berkas runtime terverifikasi:
- `cabai_rawit_merah_jatim_daily_processed.csv` (SHA-256: `151ac36c1e05791598bc93388b8ac21ee33faf5bb15c66620f2ca66b70d31ba8`)
- `model_metadata_m7b1.json` (SHA-256: `0c2d8d41dc1d2fdb1f2421c13f10744569303060c1499b7652b151accd85fe21`)
- `evaluation_predictions.json` (SHA-256: `f90fa52e0cdfbda76bb579db0cafc1d99e33df7ceeada425dc703a6576e02c3a`)
- `xgb_model_h1.json` (SHA-256: `9bef42d2d6c54d3471b432fe62ac6491437b91d9bf53005967d6a63088704cbb`)
- `xgb_model_h3.json` (SHA-256: `51ced7fc2c3ecba52aef6ab292e0c5b6527db5438e211dd2b447f7c82341904a`)
- `xgb_model_h7.json` (SHA-256: `a5525e0fefd12123f1d2bef226273cb22314fd3270cbd26e7821fccde633b6f2`)

---

## ⚠️ Batasan Ilmiah & Ruang Lingkup MVP (Seno Hackathon ITS 2026)

1. **Komoditas Tunggal**: Dibatasi secara ketat pada Cabai Rawit Merah (*Capsicum frutescens*) di Jawa Timur.
2. **Topologi Model**: Topologi 2 gudang pengirim (Gudang A kapasitas harian 6.000 kg, Gudang B kapasitas harian 4.000 kg) menuju 1 wilayah penerima (Kabupaten Malang).
3. **Simulasi Operasional**: Angka studi kasus simulasi (kebutuhan 8.000 kg, tiba layak 7.710 kg, defisit 290 kg, Plan v2 290 kg disetujui dan direservasi dari Gudang B) merupakan kasus pengujian simulasi terverifikasi, bukan bukti kegiatan fisik lapangan nyata.
4. **Status Peran Pemerintah & Pengemudi**: Portal Pengawasan Pemerintah dan Portal Checkpoint Pengemudi disajikan sebagai **Pratinjau Desain / Prototype Antarmuka (Read-Only)**. Sistem otorisasi server dan penugasan armada bertingkat belum diimplementasikan pada tingkat produksi.
5. **Fitur yang Sengaja Dikecualikan (*Blacklist MVP*)**: Tidak menggunakan penambahan armada otomatis, live GPS telemetry tracking pada armada, multi-commodity solver, vehicle routing problem (VRP), e-commerce/marketplace, dan payment gateway.

---

## 📄 Lisensi & Hak Penggunaan

Hak cipta kode sumber aplikasi dimiliki oleh Tim Pengembang PanganNadi Adaptive untuk ajang Seno Hackathon ITS 2026. Repositori publik ini disediakan semata-mata untuk keperluan penilaian kompetisi, verifikasi akademik, dan pembuktian prinsip (*proof of concept*). Tidak ada izin redistribusi komersial atau publikasi ulang aset model tanpa persetujuan tertulis dari pemilik proyek.
