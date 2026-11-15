/**
 * Aviso de gastos guardados sin conexión.
 *
 * Se pinta FIJO en la esquina: el gasto pendiente es lo único que la app
 * le debe al operador cuando vuelve la red, y merece verse desde
 * cualquier pantalla. Sin pendientes no se pinta nada — un cartel
 * permanente que dice "todo bien" entrena al operador a no mirarlo.
 */

import { useColaOffline } from '../hooks/useColaOffline';
import { dinero } from '../utils/format';
import type { GastoPendiente } from '../services/colaOffline';

export function AvisoGastosPendientes({
  pendientesIniciales,
}: { pendientesIniciales?: GastoPendiente[] } = {}) {
  const { pendientes, cantidad, sincronizando, sincronizar, descartar } =
    useColaOffline(pendientesIniciales);

  if (cantidad === 0) return null;

  const enLinea = typeof navigator === 'undefined' || navigator.onLine !== false;

  return (
    <div
      role="status"
      className="fixed bottom-4 right-4 z-50 w-80 rounded-lg border border-amber-300 bg-amber-50 p-3 shadow-lg"
    >
      <p className="text-sm font-semibold text-amber-900">
        {cantidad === 1 ? '1 gasto sin enviar' : `${cantidad} gastos sin enviar`}
      </p>
      <p className="mt-0.5 text-xs text-amber-800">
        Se guardaron en este equipo porque no había conexión. Se envían solos cuando vuelve la red.
      </p>

      <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-xs text-amber-900">
        {pendientes.map((pendiente) => (
          <li
            key={pendiente.id}
            className="flex items-center justify-between gap-2 rounded border border-amber-200 bg-white/60 px-2 py-1"
          >
            <span className="truncate" title={pendiente.ultimoError ?? undefined}>
              {pendiente.payload.provider_name} · {dinero(pendiente.payload.total_amount)}
            </span>
            <button
              type="button"
              onClick={() => descartar(pendiente.id)}
              className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
              title="Descartar este gasto (no se enviará)"
            >
              Descartar
            </button>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={sincronizar}
        disabled={sincronizando || !enLinea}
        className="mt-2 w-full rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {sincronizando ? 'Enviando…' : enLinea ? 'Reintentar ahora' : 'Sin conexión'}
      </button>
    </div>
  );
}
