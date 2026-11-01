import {useState} from 'react';
import {Panel} from './components';
type Props = {
  principal?: string;
  busy: boolean;
  create: (body: unknown) => Promise<void>;
};
const warehouseFields = [
  ['physical_stock', 'Stok fisik (kg)'],
  ['local_demand', 'Kebutuhan lokal (kg)'],
  ['safety_stock', 'Cadangan minimum (kg)'],
  ['daily_dispatch_capacity', 'Kuota harian (kg)'],
  ['actual_dispatched_today', 'Berangkat hari ini (kg)'],
  ['active_undispatched_reservations', 'Komitmen eksternal (kg)'],
  ['cost_per_kg', 'Tarif (Rp / kg)'],
] as const;
export function CaseForm({busy, create, principal}: Props) {
  const [count, setCount] = useState(2);
  return (
    <Panel title="Kebutuhan & sumber pasokan">
      <p>
        Catat kebutuhan tujuan yang telah diverifikasi dan kondisi sumber
        pasokan. Seluruh pengujian ini merupakan simulasi. Komitmen eksternal
        tidak mencakup reservasi internal aplikasi.
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          await create({
            commodity_name: String(f.get('commodity')).trim(),
            destination_region: String(f.get('destination')).trim(),
            verified_demand: Number(f.get('demand')),
            deadline_hours: Number(f.get('deadline')),
            created_by: String(f.get('creator')).trim(),
            is_demand_verified: f.has('verified'),
            warehouses: Array.from({length: count}, (_, i) =>
              Object.fromEntries([
                ['warehouse_id', String(f.get(`warehouse-${i}`)).trim()],
                ...warehouseFields.map(([key]) => [
                  key,
                  Number(f.get(`${key}-${i}`)),
                ]),
              ]),
            ),
          });
        }}
      >
        <fieldset disabled={busy}>
          <div className="form-grid">
            <label>
              Komoditas
              <input
                name="commodity"
                required
                defaultValue="Cabai Rawit Merah"
                pattern=".*\S.*"
              />
            </label>
            <label>
              Tujuan
              <input name="destination" required pattern=".*\S.*" />
            </label>
            <label>
              Kebutuhan terverifikasi (kg)
              <input
                name="demand"
                type="number"
                min="0"
                max="1000000000"
                step="1"
                required
              />
            </label>
            <label>
              Batas waktu (jam)
              <input
                name="deadline"
                type="number"
                min="1"
                step="1"
                defaultValue="48"
                required
              />
            </label>
            <label>
              Pembuat kasus
              <input
                name="creator"
                defaultValue={principal ?? ''}
                readOnly={!!principal}
                required
                maxLength={100}
                pattern=".*\S.*"
              />
            </label>
          </div>
          <label className="check">
            <input name="verified" type="checkbox" /> Kebutuhan fisik telah
            diverifikasi (simulasi)
          </label>
          {Array.from({length: count}, (_, i) => (
            <fieldset className="warehouse-form" key={i}>
              <legend>Gudang kandidat {i + 1}</legend>
              <div className="form-grid">
                <label>
                  ID gudang
                  <input
                    name={`warehouse-${i}`}
                    required
                    pattern=".*\S.*"
                    maxLength={50}
                  />
                </label>
                {warehouseFields.map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      name={`${key}-${i}`}
                      type="number"
                      min={key === 'cost_per_kg' ? '0.01' : '0'}
                      max="1000000000"
                      step={key === 'cost_per_kg' ? '0.01' : '1'}
                      required
                      defaultValue={
                        [
                          'actual_dispatched_today',
                          'active_undispatched_reservations',
                        ].includes(key)
                          ? '0'
                          : undefined
                      }
                    />
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
          <div className="actions">
            <button
              type="button"
              className="secondary"
              onClick={() => setCount(count + 1)}
            >
              Tambah gudang
            </button>
            {count > 1 && (
              <button
                type="button"
                className="secondary"
                onClick={() => setCount(count - 1)}
              >
                Hapus kandidat terakhir
              </button>
            )}
            <button>Buat kasus</button>
          </div>
        </fieldset>
      </form>
    </Panel>
  );
}
