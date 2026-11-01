import {useEffect, useRef, useState} from 'react';
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  ClipboardCheck,
  Database,
  Leaf,
  Menu,
  PackageCheck,
  ShieldCheck,
  Sprout,
  Warehouse,
  X,
} from 'lucide-react';
import {Logo} from './Logo';
import './landing.css';

interface LandingPageProps {
  apiHealth: string;
  onOpenWorkspace: () => void;
  onOpenGovView: () => void;
  onOpenDriverView: () => void;
}
const journey = [
  {
    icon: Database,
    title: 'Baca sinyal harga',
    text: 'Pahami kondisi pasar dan konteks risikonya.',
  },
  {
    icon: ShieldCheck,
    title: 'Verifikasi kebutuhan',
    text: 'Pastikan data tujuan, stok, dan kapasitas siap.',
  },
  {
    icon: Warehouse,
    title: 'Rencanakan alokasi',
    text: 'Cari alokasi yang efisien dalam batas aman.',
  },
  {
    icon: ClipboardCheck,
    title: 'Setujui & kirim',
    text: 'Keputusan manusia, pelaksanaan tercatat.',
  },
  {
    icon: PackageCheck,
    title: 'Terima & sesuaikan',
    text: 'Gunakan hasil aktual untuk rencana pemulihan.',
  },
];
function Brand() {
  return (
    <span className="pn-brand">
      <Logo />
      <span>
        PanganNadi<small>ADAPTIVE OPERATIONS</small>
      </span>
    </span>
  );
}
function SectionTop({
  number,
  text,
  note,
}: {
  number: string;
  text: string;
  note?: string;
}) {
  return (
    <div className="pn-section-top" data-reveal>
      <span className="pn-kicker">
        {number} / {text}
      </span>
      {note && <span className="pn-section-note">{note}</span>}
    </div>
  );
}

export function LandingPage({onOpenWorkspace}: LandingPageProps) {
  const scope = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showcase, setShowcase] = useState<'intelligence' | 'operations'>(
    'intelligence',
  );
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [menuOpen]);
  useEffect(() => {
    let disposed = false;
    let revert = () => {};
    // Motion is progressive enhancement: content is visible before import and on failure.
    Promise.all([import('gsap'), import('gsap/ScrollTrigger')])
      .then(([core, plugin]) => {
        if (disposed || !scope.current) return;
        const gsap = core.default;
        gsap.registerPlugin(plugin.ScrollTrigger);
        const media = gsap.matchMedia();
        const context = gsap.context(() => {
          media.add('(prefers-reduced-motion: no-preference)', () => {
            gsap.from('.pn-hero-copy > *', {
              y: 24,
              opacity: 0,
              duration: 0.75,
              stagger: 0.1,
              ease: 'power2.out',
              clearProps: 'opacity,transform',
            });
            gsap.to('.pn-hero-image', {
              yPercent: 6,
              ease: 'none',
              scrollTrigger: {
                trigger: '.pn-hero',
                start: 'top top',
                end: 'bottom top',
                scrub: 0.7,
              },
            });
            gsap.utils
              .toArray<HTMLElement>('[data-reveal]')
              .forEach((element) =>
                gsap.from(element, {
                  y: 24,
                  opacity: 0,
                  duration: 0.65,
                  ease: 'power2.out',
                  clearProps: 'opacity,transform',
                  scrollTrigger: {
                    trigger: element,
                    start: 'top 94%',
                    once: true,
                  },
                }),
              );
            gsap.from('.pn-process-step', {
              y: 18,
              opacity: 0,
              stagger: 0.12,
              duration: 0.6,
              clearProps: 'opacity,transform',
              scrollTrigger: {
                trigger: '.pn-process',
                start: 'top 88%',
                once: true,
              },
            });
          });
        }, scope.current);
        revert = () => {
          media.revert();
          context.revert();
        };
      })
      .catch(() => {
        scope.current
          ?.querySelectorAll<HTMLElement>(
            '[data-reveal], .pn-hero-copy > *, .pn-process-step',
          )
          .forEach((element) => {
            element.style.removeProperty('opacity');
            element.style.removeProperty('transform');
          });
      });
    return () => {
      disposed = true;
      revert();
    };
  }, []);
  function scrollTo(id: string) {
    setMenuOpen(false);
    document
      .getElementById(id)
      ?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 'instant'
          : 'smooth',
        block: 'start',
      });
  }
  const nav = [
    ['pn-context', 'Tentang'],
    ['pn-solution', 'Solusi'],
    ['pn-workflow', 'Cara kerja'],
    ['pn-product', 'Platform'],
    ['pn-evidence', 'Bukti konsep'],
  ];
  const navLink = (id: string, title: string) => (
    <a
      key={id}
      href={'#' + id}
      onClick={(event) => {
        event.preventDefault();
        scrollTo(id);
      }}
    >
      {title}
    </a>
  );
  return (
    <div className="pn-landing" ref={scope}>
      <a className="pn-skip" href="#pn-main">
        Lewati navigasi
      </a>
      <header className="pn-nav">
        <a
          href="#pn-hero"
          className="pn-brand-home"
          onClick={(event) => {
            event.preventDefault();
            scrollTo('pn-hero');
          }}
        >
          <Brand />
        </a>
        <nav className="pn-desktop-nav" aria-label="Navigasi utama">
          {nav.map(([id, title]) => navLink(id, title))}
        </nav>
        <div className="pn-nav-actions">
          <button className="pn-login" onClick={onOpenWorkspace}>
            Masuk platform <ArrowUpRight size={16} />
          </button>
          <button
            ref={menuButton}
            className="pn-menu-button"
            aria-label={menuOpen ? 'Tutup navigasi' : 'Buka navigasi'}
            aria-expanded={menuOpen}
            aria-controls="pn-mobile-menu"
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>
      </header>
      {menuOpen && (
        <nav
          id="pn-mobile-menu"
          className="pn-mobile-nav"
          aria-label="Navigasi ponsel"
        >
          {nav.map(([id, title]) => navLink(id, title))}
          <button
            className="pn-button pn-button-forest"
            onClick={() => {
              setMenuOpen(false);
              onOpenWorkspace();
            }}
          >
            Masuk platform <ArrowRight size={18} />
          </button>
        </nav>
      )}
      <main id="pn-main">
        <section
          className="pn-hero"
          id="pn-hero"
          aria-labelledby="pn-hero-title"
        >
          <img
            className="pn-hero-image"
            src="/brand/pangannadi-ricefields-hero.webp"
            alt="Visual orisinal berbantuan AI: lanskap terasering sawah Indonesia dengan pegunungan dan cahaya pagi."
            width={1672}
            height={941}
            fetchPriority="high"
          />
          <div className="pn-hero-shade" />
          <div className="pn-hero-inner">
            <div className="pn-hero-copy">
              <span className="pn-eyebrow">
                <i />
                TEKNOLOGI UNTUK KETAHANAN PANGAN
              </span>
              <h1 id="pn-hero-title">
                Dari data harga
                <br />
                ke distribusi pangan
                <br />
                <em>yang lebih tepat.</em>
              </h1>
              <p>
                Menghubungkan intelijen pasar, perencanaan pasokan, dan
                pengawasan distribusi dalam satu alur keputusan yang adaptif.
              </p>
              <div className="pn-hero-actions">
                <button
                  className="pn-button pn-button-ivory"
                  onClick={onOpenWorkspace}
                >
                  Jelajahi platform <ArrowUpRight size={19} />
                </button>
                <a
                  className="pn-hero-secondary"
                  href="#pn-workflow"
                  onClick={(event) => {
                    event.preventDefault();
                    scrollTo('pn-workflow');
                  }}
                >
                  Pelajari cara kerja <ArrowDown size={17} />
                </a>
              </div>
            </div>
            <div className="pn-hero-caption">
              <span />
              <p>
                Menjaga aliran pasokan.
                <br />
                <strong>Melindungi cadangan wilayah.</strong>
              </p>
              <small>VISUAL BRAND ORISINAL · BERBANTUAN AI</small>
            </div>
          </div>
          <div className="pn-hero-bottom">
            <span>INTELIJEN HARGA</span>
            <span>PERENCANAAN DISTRIBUSI</span>
            <span>PEMULIHAN ADAPTIF</span>
            <button
              onClick={() => scrollTo('pn-context')}
              aria-label="Gulir ke konteks distribusi"
            >
              <ArrowDown size={20} />
            </button>
          </div>
        </section>

        <section
          className="pn-context pn-section"
          id="pn-context"
          aria-labelledby="pn-context-title"
        >
          <div className="pn-container">
            <SectionTop
              number="01"
              text="MEMAHAMI TANTANGAN"
              note="Dari sinyal pasar ke kebutuhan nyata."
            />
            <div className="pn-context-grid">
              <div data-reveal>
                <h2 id="pn-context-title">
                  Pangan tersedia.
                  <br />
                  Distribusinya perlu
                  <br />
                  <span>lebih terarah.</span>
                </h2>
                <p className="pn-body-large">
                  Perbedaan harga hanyalah awal cerita. Keputusan distribusi
                  juga perlu melihat kebutuhan fisik, cadangan sumber, dan
                  kondisi barang saat diterima.
                </p>
              </div>
              <div className="pn-challenges">
                {[
                  [
                    'Harga tinggi belum tentu stok kurang.',
                    'Intervensi memerlukan kebutuhan yang terverifikasi, bukan hanya membaca lonjakan harga.',
                  ],
                  [
                    'Daerah sumber tetap harus terlindungi.',
                    'Pengiriman memperhitungkan komitmen lokal, cadangan minimum, dan kuota harian yang tersisa.',
                  ],
                  [
                    'Rencana perlu mengikuti hasil aktual.',
                    'Jumlah layak yang diterima dapat berbeda. Kekurangannya perlu ditangani dengan rencana pemulihan.',
                  ],
                ].map(([title, text], i) => (
                  <article data-reveal key={title}>
                    <span className="pn-item-index">0{i + 1}</span>
                    <div>
                      <h3>{title}</h3>
                      <p>{text}</p>
                    </div>
                    <ArrowUpRight size={23} />
                  </article>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section
          className="pn-solution pn-section"
          id="pn-solution"
          aria-labelledby="pn-solution-title"
        >
          <div className="pn-container">
            <SectionTop number="02" text="SATU ALUR, KEPUTUSAN TERHUBUNG" />
            <div className="pn-solution-heading" data-reveal>
              <h2 id="pn-solution-title">
                Dari kewaspadaan dini
                <br />
                hingga <span>pemulihan pasokan.</span>
              </h2>
              <p>
                PanganNadi membantu tim merencanakan, meninjau, dan memperbarui
                distribusi pangan dengan data yang bisa ditelusuri.
              </p>
            </div>
            <div className="pn-pillars">
              {[
                {
                  icon: CircleDot,
                  title: 'Intelijen harga',
                  text: 'Pahami tren harga dan prediksi multi-horizon sebagai konteks kewaspadaan. Kebutuhan fisik tetap diverifikasi.',
                  tag: 'Membaca kondisi',
                },
                {
                  icon: Warehouse,
                  title: 'Alokasi yang terukur',
                  text: 'Hitung alokasi dengan batas stok aman, reservasi, dan kuota harian. Tinjau biaya sebelum mengambil keputusan.',
                  tag: 'Menjaga keseimbangan',
                },
                {
                  icon: Leaf,
                  title: 'Operasi yang adaptif',
                  text: 'Rekonsiliasi hasil penerimaan, lalu susun pemulihan dari kapasitas yang masih tersedia. Persetujuan tetap di tangan manusia.',
                  tag: 'Menutup siklus keputusan',
                },
              ].map(({icon: Icon, title, text, tag}, i) => (
                <article data-reveal key={title}>
                  <div className="pn-pillar-icon">
                    <Icon size={27} strokeWidth={1.5} />
                  </div>
                  <span className="pn-pillar-no">0{i + 1}</span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                  <span className="pn-pillar-tag">{tag}</span>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section
          className="pn-workflow pn-section"
          id="pn-workflow"
          aria-labelledby="pn-workflow-title"
        >
          <div className="pn-container">
            <SectionTop
              number="03"
              text="CARA KERJA"
              note="Keputusan manusia. Jejak proses yang jelas."
            />
            <div className="pn-workflow-heading" data-reveal>
              <h2 id="pn-workflow-title">
                Setiap tahap memiliki
                <br />
                dasar yang bisa diperiksa.
              </h2>
              <p>
                Dari membaca pasar hingga barang diterima, setiap langkah
                memberi konteks untuk keputusan berikutnya.
              </p>
            </div>
            <div className="pn-process">
              {journey.map(({icon: Icon, title, text}, i) => (
                <article className="pn-process-step" key={title}>
                  <div className="pn-process-node">
                    <Icon size={25} strokeWidth={1.5} />
                    <span>0{i + 1}</span>
                  </div>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </article>
              ))}
            </div>
            <div className="pn-process-loop" data-reveal>
              <ArrowRight size={18} />
              <p>
                <strong>Hasil penerimaan kembali menjadi masukan.</strong>{' '}
                Kekurangan aktual membuka rencana pemulihan, tanpa mengulang
                kuota yang sudah terpakai.
              </p>
            </div>
          </div>
        </section>

        <section
          className="pn-product pn-section"
          id="pn-product"
          aria-labelledby="pn-product-title"
        >
          <div className="pn-container">
            <SectionTop
              number="04"
              text="KENALI PLATFORM"
              note="Informasi yang membantu tindakan."
            />
            <div className="pn-product-heading" data-reveal>
              <h2 id="pn-product-title">
                Satu pandangan utuh.
                <br />
                <span>Keputusan lebih terarah.</span>
              </h2>
              <div
                className="pn-product-tabs"
                role="tablist"
                aria-label="Pratinjau fitur platform"
              >
                {(['intelligence', 'operations'] as const).map((value, i) => (
                  <button
                    key={value}
                    id={'pn-tab-' + value}
                    role="tab"
                    aria-selected={showcase === value}
                    aria-controls="pn-product-panel"
                    tabIndex={showcase === value ? 0 : -1}
                    onClick={() => setShowcase(value)}
                    onKeyDown={(event) => {
                      if (
                        ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(
                          event.key,
                        )
                      ) {
                        event.preventDefault();
                        const next =
                          event.key === 'Home'
                            ? 'intelligence'
                            : event.key === 'End'
                              ? 'operations'
                              : value === 'intelligence'
                                ? 'operations'
                                : 'intelligence';
                        setShowcase(next);
                        document.getElementById('pn-tab-' + next)?.focus();
                      }
                    }}
                  >
                    {i === 0 ? 'Intelijen harga' : 'Control tower'}
                  </button>
                ))}
              </div>
            </div>
            <div
              className="pn-showcase"
              id="pn-product-panel"
              role="tabpanel"
              aria-labelledby={'pn-tab-' + showcase}
              tabIndex={0}
            >
              <div className="pn-showcase-copy">
                <span className="pn-mini-label">
                  {showcase === 'intelligence'
                    ? 'INTELIJEN HARGA'
                    : 'CONTROL TOWER DISTRIBUSI'}
                </span>
                <h3>
                  {showcase === 'intelligence'
                    ? 'Baca tren. Kenali sinyal. Siapkan respons.'
                    : 'Lihat rencana dan realisasi dalam konteks yang sama.'}
                </h3>
                <p>
                  {showcase === 'intelligence'
                    ? 'Harga historis, prediksi 1, 3, dan 7 hari, serta evaluasi model disajikan dengan sumber dan batas penggunaannya.'
                    : 'Pantau kebutuhan, manifest pengiriman, hasil sortasi, dan sisa kekurangan tanpa menyamakan reservasi dengan barang yang sudah diterima.'}
                </p>
                <ul>
                  {(showcase === 'intelligence'
                    ? [
                        'Seri harga historis dan prediksi terpisah',
                        'Atribusi model pada setiap horizon',
                        'Evaluasi XGBoost bersama baseline Naive',
                      ]
                    : [
                        'Kapasitas gudang dan kuota harian',
                        'Manifest dan penerimaan aktual',
                        'Pemulihan adaptif dengan persetujuan',
                      ]
                  ).map((item) => (
                    <li key={item}>
                      <Check size={16} />
                      {item}
                    </li>
                  ))}
                </ul>
                <button className="pn-text-button" onClick={onOpenWorkspace}>
                  Jelajahi di platform <ArrowUpRight size={18} />
                </button>
              </div>
              <div className="pn-product-window">
                <div className="pn-window-bar">
                  <span />
                  <span />
                  <span />
                  <small>
                    PanganNadi /{' '}
                    {showcase === 'intelligence'
                      ? 'Intelijen Harga'
                      : 'Beranda'}
                  </small>
                </div>
                {showcase === 'intelligence' ? (
                  <div className="pn-price-preview">
                    <div className="pn-preview-heading">
                      <div>
                        <span className="pn-mini-label">CABAI RAWIT MERAH</span>
                        <h4>Memahami pergerakan pasar</h4>
                        <p>Jawa Timur · PIHPS Nasional — Bank Indonesia</p>
                      </div>
                      <span className="pn-preview-tag">
                        Pratinjau antarmuka
                      </span>
                    </div>
                    <div className="pn-preview-legend">
                      <span>
                        <i />
                        Harga historis
                      </span>
                      <span>
                        <i />
                        Prediksi
                      </span>
                    </div>
                    <svg
                      viewBox="0 0 600 230"
                      role="img"
                      aria-label="Ilustrasi struktur grafik: garis historis solid dipisahkan dari titik prediksi. Bukan data harga aktual."
                    >
                      <defs>
                        <linearGradient
                          id="pn-chart-fill"
                          x1="0"
                          y1="0"
                          x2="0"
                          y2="1"
                        >
                          <stop
                            offset="0"
                            stopColor="#a5b99d"
                            stopOpacity=".4"
                          />
                          <stop
                            offset="1"
                            stopColor="#a5b99d"
                            stopOpacity="0"
                          />
                        </linearGradient>
                      </defs>
                      {[35, 85, 135, 185].map((y) => (
                        <line
                          key={y}
                          x1="24"
                          y1={y}
                          x2="580"
                          y2={y}
                          stroke="#e6eae3"
                          strokeDasharray="3 5"
                        />
                      ))}
                      <path
                        d="M24 162 L62 173 L100 140 L138 146 L176 115 L214 124 L252 77 L290 90 L328 62 L366 81 L404 52 L442 69 L442 205 L24 205 Z"
                        fill="url(#pn-chart-fill)"
                      />
                      <path
                        d="M24 162 L62 173 L100 140 L138 146 L176 115 L214 124 L252 77 L290 90 L328 62 L366 81 L404 52 L442 69"
                        fill="none"
                        stroke="#1b5544"
                        strokeWidth="3"
                        strokeLinejoin="round"
                      />
                      <line
                        x1="454"
                        y1="20"
                        x2="454"
                        y2="205"
                        stroke="#a5b99d"
                        strokeDasharray="5 5"
                      />
                      <path
                        d="M442 69 L480 61 L525 74 L574 55"
                        fill="none"
                        stroke="#c99a51"
                        strokeWidth="3"
                        strokeDasharray="5 5"
                      />
                      {[
                        [480, 61],
                        [525, 74],
                        [574, 55],
                      ].map(([x, y]) => (
                        <circle
                          key={x}
                          cx={x}
                          cy={y}
                          r="5"
                          fill="#c99a51"
                          stroke="#fff"
                          strokeWidth="3"
                        />
                      ))}
                      <text x="480" y="225" textAnchor="middle">
                        H+1
                      </text>
                      <text x="525" y="225" textAnchor="middle">
                        H+3
                      </text>
                      <text x="574" y="225" textAnchor="middle">
                        H+7
                      </text>
                    </svg>
                    <div className="pn-preview-footnote">
                      Ilustrasi struktur antarmuka, bukan data pasar. Data dan
                      evaluasi aktual tersedia setelah masuk.
                    </div>
                  </div>
                ) : (
                  <div className="pn-distribution-preview">
                    <div className="pn-preview-heading">
                      <div>
                        <span className="pn-mini-label">
                          SIMULASI / KABUPATEN MALANG
                        </span>
                        <h4>Ringkasan pemenuhan distribusi</h4>
                      </div>
                      <span className="pn-preview-tag">Proof of concept</span>
                    </div>
                    <div className="pn-mini-metrics">
                      {[
                        ['Target kebutuhan', '8.000'],
                        ['Diterima layak', '7.710'],
                        ['Kekurangan', '290'],
                      ].map(([label, value]) => (
                        <div key={label}>
                          <span>{label}</span>
                          <strong>
                            {value} <small>kg</small>
                          </strong>
                        </div>
                      ))}
                    </div>
                    <div className="pn-fulfillment-track">
                      <span />
                    </div>
                    <div className="pn-preview-row">
                      <span>
                        <i className="pn-dot" />
                        Diterima layak
                      </span>
                      <strong>96,4% dari target</strong>
                    </div>
                    {[
                      ['Gudang A', '6.000'],
                      ['Gudang B', '1.950'],
                    ].map(([name, value]) => (
                      <div className="pn-shipment-row" key={name}>
                        <Warehouse size={21} />
                        <div>
                          <strong>{name}</strong>
                          <small>{value} kg diberangkatkan</small>
                        </div>
                        <CheckCircle2 size={18} />
                      </div>
                    ))}
                    <div className="pn-recovery-callout">
                      <CircleDot size={18} />
                      <span>
                        Recovery v2 · 290 kg disetujui dan direservasi.
                        <br />
                        <strong>Belum diberangkatkan.</strong>
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        <section
          className="pn-evidence pn-section"
          id="pn-evidence"
          aria-labelledby="pn-evidence-title"
        >
          <div className="pn-container">
            <div className="pn-section-top" data-reveal>
              <span className="pn-kicker">05 / DARI RENCANA KE PEMULIHAN</span>
              <span className="pn-simulation-label">
                SIMULASI · PROOF OF CONCEPT
              </span>
            </div>
            <div className="pn-evidence-grid">
              <div data-reveal>
                <h2 id="pn-evidence-title">
                  Ketika realisasi berbeda,
                  <br />
                  <span>rencana ikut beradaptasi.</span>
                </h2>
                <p className="pn-body-large">
                  Satu komoditas. Dua gudang sumber. Satu tujuan di Kabupaten
                  Malang. Skenario terukur untuk menguji alur distribusi dan
                  pemulihannya.
                </p>
                <div className="pn-evidence-reference">
                  <span>REFERENSI KASUS DEMO</span>
                  <strong>CASE-EFD6A1CC</strong>
                </div>
                <p className="pn-evidence-disclaimer">
                  Seluruh angka pada bagian ini adalah simulasi pengujian. Tidak
                  mewakili pengiriman fisik atau dampak operasional nyata.
                </p>
              </div>
              <div className="pn-evidence-flow" data-reveal>
                <div className="pn-evidence-big">
                  <span>Target kebutuhan</span>
                  <strong>
                    8.000 <small>kg</small>
                  </strong>
                </div>
                <div className="pn-evidence-chain">
                  {[
                    ['Diberangkatkan', '7.950'],
                    ['Tiba di tujuan', '7.820'],
                    ['Diterima layak', '7.710'],
                  ].map(([title, value], i) => (
                    <div className="pn-chain-part" key={title}>
                      {i > 0 && <ChevronRight size={18} />}
                      <div>
                        <span>{title}</span>
                        <strong>
                          {value} <small>kg</small>
                        </strong>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="pn-evidence-recovery">
                  <div>
                    <span>Kekurangan aktual</span>
                    <strong>
                      290 <small>kg</small>
                    </strong>
                  </div>
                  <ArrowRight size={28} />
                  <div>
                    <span>Recovery v2 disetujui</span>
                    <strong>
                      290 <small>kg</small>
                    </strong>
                  </div>
                </div>
                <p className="pn-evidence-status">
                  <ShieldCheck size={18} />
                  Reservasi aktif · 0 kg recovery diberangkatkan
                </p>
                <div className="pn-evidence-foot">
                  <span>50 kg batal muat</span>
                  <span>130 kg selisih transit</span>
                  <span>110 kg ditolak sortasi</span>
                </div>
              </div>
            </div>
            <div className="pn-evidence-bottom" data-reveal>
              <span>
                <CheckCircle2 size={20} />
                11 peristiwa audit pada kasus demo
              </span>
              <p>
                Setiap keputusan ditelusuri. Stok aman dan sisa kuota tetap
                menjadi batas rencana pemulihan.
              </p>
            </div>
          </div>
        </section>

        <section
          className="pn-trust pn-section"
          id="pn-research"
          aria-labelledby="pn-trust-title"
        >
          <div className="pn-container">
            <SectionTop
              number="06"
              text="DIBANGUN UNTUK DIPERIKSA"
              note="Transparan dalam metode dan batas klaim."
            />
            <div className="pn-trust-grid">
              <div data-reveal>
                <h2 id="pn-trust-title">
                  Teknologi membantu.
                  <br />
                  <span>Manusia memutuskan.</span>
                </h2>
                <p className="pn-body-large">
                  Setiap rekomendasi memiliki konteks, setiap tindakan
                  memerlukan kewenangan, dan setiap hasil dapat ditelusuri
                  kembali.
                </p>
              </div>
              <div className="pn-trust-list">
                {[
                  {
                    icon: ShieldCheck,
                    title: 'Persetujuan manusia',
                    text: 'Alokasi dan pemulihan ditinjau sebelum dijalankan. Prediksi tidak mengirimkan barang secara otomatis.',
                  },
                  {
                    icon: Database,
                    title: 'Sumber dan model yang bisa ditinjau',
                    text: 'Harga bersumber dari PIHPS Nasional. Evaluasi XGBoost ditampilkan bersama baseline Naive; model tidak diklaim unggul di setiap horizon.',
                  },
                  {
                    icon: ClipboardCheck,
                    title: 'Pembuktian konsep yang jujur',
                    text: 'Uji lokal memeriksa alur dan kendala distribusi. Validasi independen, penerimaan lapangan, dan penilaian TKT 3 masih memerlukan bukti lanjutan.',
                  },
                ].map(({icon: Icon, title, text}) => (
                  <article key={title} data-reveal>
                    <Icon size={24} />
                    <div>
                      <h3>{title}</h3>
                      <p>{text}</p>
                    </div>
                  </article>
                ))}
              </div>
            </div>
            <div className="pn-technology-strip" data-reveal>
              <span>FONDASI TEKNOLOGI</span>
              {[
                'React + TypeScript',
                'FastAPI',
                'OR-Tools CP-SAT',
                'XGBoost / Naive',
                'PostgreSQL',
              ].map((item) => (
                <strong key={item}>{item}</strong>
              ))}
            </div>
          </div>
        </section>

        <section className="pn-closing">
          <div className="pn-container" data-reveal>
            <div>
              <span className="pn-eyebrow">
                KEPUTUSAN YANG LEBIH TERARAH DIMULAI DI SINI
              </span>
              <h2>
                Lihat pangan dari
                <br />
                <em>perspektif yang lebih utuh.</em>
              </h2>
            </div>
            <button
              className="pn-button pn-button-ivory"
              onClick={onOpenWorkspace}
            >
              Jelajahi platform <ArrowUpRight size={20} />
            </button>
          </div>
        </section>
      </main>
      <footer className="pn-footer">
        <div className="pn-container">
          <div className="pn-footer-top">
            <div>
              <Brand />
              <p>
                Intelijen pangan. Alokasi terukur.
                <br />
                Operasi yang adaptif.
              </p>
            </div>
            <div className="pn-footer-links">
              <span>PLATFORM</span>
              {navLink('pn-solution', 'Solusi PanganNadi')}
              {navLink('pn-workflow', 'Cara kerja')}
              <button onClick={onOpenWorkspace}>
                Akses platform <ArrowUpRight size={15} />
              </button>
            </div>
            <div className="pn-footer-links">
              <span>PROYEK</span>
              {navLink('pn-evidence', 'Bukti konsep simulasi')}
              {navLink('pn-research', 'Metode & validasi')}
              <p>
                Seno Hackathon ITS 2026
                <br />
                Purwarupa untuk evaluasi TKT 3
              </p>
            </div>
          </div>
          <div className="pn-footer-bottom">
            <span>2026 PanganNadi Adaptive.</span>
            <span>
              Proof of concept · Data operasional demo merupakan simulasi.
            </span>
            <span>
              Dirancang untuk Indonesia <Sprout size={15} />
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
