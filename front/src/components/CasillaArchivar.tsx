/**
 * La casilla de archivar, y el aviso de lo que implica.
 *
 * POR QUÉ ESTA EN UN ARCHIVO PROPIO Y NO DENTRO DE `Scan.tsx`
 * ============================================================
 *
 * Porque la casilla sola es fácil y **el aviso es lo que la hace honesta**. Una
 * casilla que dice "Archivar los comprobantes leídos" sin más hace dos cosas malas
 * a la vez:
 *
 * 1. **No dice que mueve.** Quien la marca espera un efecto local; lo que pasa es
 *    que el comprobante desaparece de la bandeja de entrada. Un comprobante que se
 *    va de la carpeta es el tipo de cosa que hace que alguien piense que se perdio.
 * 2. **No avisa de la consecuencia en la prueba.** Con el archivado apagado, los
 *    archivos se quedan y se puede repetir el escaneo. Encendido, hay que
 *    devolverlos a mano para la segunda pasada. Es exactamente el detalle que hace
 *    que probar dos veces sea incómodo.
 *
 * Y POR QUÉ AVISA DEL VALOR REAL, NO DE UNO SUPONIDO
 * ====================================================
 *
 * El texto del aviso cambia segun el estado, y el estado inicial lo trae
 * `GET /scan/config`. Si la casilla arrancara marcada por un `true` de aqui,
 * mentiria en cuanto alguien apague `TICKETS_SCAN_ARCHIVAR` en el `.env` — que es
 * justo lo que hay que hacer para poder repetir la prueba—. Una casilla de
 * archivado que miente es peor que no tenerla: mueve comprobantes de carpeta sin
 * que nadie lo haya pedido.
 *
 * LO QUE ESTE COMPONENTE NO HACE
 * ==============================
 *
 * **No inventa el valor.** Si la configuracion todavia no ha llegado, sale
 * deshabilitado con un texto que lo dice. Un control deshabilitado con su motivo
 * a la vista es mejor que un control habilitado que puede estar equivocado.
 */

import type { ScanConfig } from '../types/scan';

interface Props {
  /** El valor actual. `null` = todavia no se sabe (la config no ha llegado). */
  valor: boolean | null;
  onCambio: (v: boolean) => void;
  /** Deshabilitado mientras corre un escaneo. */
  ocupado: boolean;
  config: ScanConfig | null;
}

export function CasillaArchivar({ valor, onCambio, ocupado, config }: Props) {
  const marcada = valor === true;

  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={marcada}
          disabled={ocupado || valor === null}
          onChange={e => onCambio(e.target.checked)}
          className="mt-0.5 w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
        />
        <span className="min-w-0">
          <span className="block text-sm font-medium text-gray-900">
            Mover los comprobantes a la carpeta de escaneados
          </span>
          <span className="block text-xs text-gray-600 mt-0.5">
            {valor === null
              ? 'Esperando la configuración del escáner...'
              : marcada
                ? `Al terminar, los comprobantes salen de ${config?.carpeta ?? 'la carpeta de entrada'} y quedan en la carpeta de escaneados. Los archivos NO se borran.`
                : `Los comprobantes se quedan en ${config?.carpeta ?? 'la carpeta de entrada'}, así que puedes repetir el escaneo las veces que quieras.`}
          </span>
        </span>
      </label>

      {/* El aviso de la consecuencia en la prueba. */}
      {marcada && !ocupado && (
        <p className="mt-2 pl-7 text-xs text-amber-700 leading-relaxed">
          Para escanearlos otra vez tendrás que devolverlos a la carpeta de entrada.
          Si solo estás probando, conviene dejarlo sin marcar.
        </p>
      )}

      {!marcada && valor === false && !ocupado && (
        <p className="mt-2 pl-7 text-xs text-gray-500 leading-relaxed">
          Es el valor configurado en el servidor
          {config && !config.archivar_por_defecto ? ' (archivado apagado)' : ''}.
        </p>
      )}
    </div>
  );
}