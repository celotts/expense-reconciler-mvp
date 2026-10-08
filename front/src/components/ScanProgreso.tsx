/**
 * El progreso del escaneo, en vivo.
 *
 * `POST /scan` es SINCRONO y con fotos tarda minutos. Sin esto, la pantalla es
 * un "Escaneando..." de cuatro minutos del que no se sabe si va bien o se trabo
 * en el archivo 3 de 200. El backend ya expone el canal
 * (`GET /scan/runs` y `GET /scan/runs/{id}`), asi que lo que faltaba era
 * pintarlo.
 *
 * TRES COSAS QUE ESTE PANEL DICE, Y LAS TRAS IMPORTAN
 * ===================================================
 *
 * 1. **Que archivo va.** `corrida.actual`. Un contador que sube solo dice "algo
 *    pasa"; el nombre del archivo dice si se trabo en uno concreto.
 *
 * 2. **Que salio de ahi, con su importe.** `corrida.procesados`. Sin esto, cuatro
 *    minutos de "cargando" y luego un resultado: el operador no puede empezar a
 *    corregir mientras corre, que es justo cuando tiene el contexto fresco.
 *
 * 3. **Lo leido contra lo afirmado.** `procesados[].confiable` NO es "el monto
 *    parece bien": es si el GATE se atrevio a afirmarlo. Con OCR al 33% casi nunca
 *    coinciden, y pintar solo el monto hace creer que la lectura fue buena. Por
 *    eso los dos importes van juntos y el que no se afirma se ve gris.
 *
 * Y una cuarta cosa que este panel NO hace, y es deliberada: **no muestra el
 * comprobante original mientras corre.** Seria lo ideal y no se puede: el
 * archivo se borra de la carpeta de entrada cuando ya esta respaldado, asi que
 * durante el escaneo esta disponible solo para los que ya se leyeron. Ver el
 * documento requires `GET /tickets/{id}/documento` por cada uno, y son peticiones
 * que compiten con el OCR por el ancho de banda. Se deja para la cola de
 * revision, donde el operador ya esta mirando una sola cosa.
 */

import type { ScanCorrida, ScanProcesado } from '../types/scan';

/** Formatea un importe que llega como texto desde la API. */
function dinero(valor: string | null | undefined): string {
  if (!valor) return '—';
  const n = Number(valor);
  if (Number.isNaN(n)) return valor;
  return `$${n.toLocaleString('es-MX', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function segundosLegibles(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const resto = Math.round(s % 60);
  return resto ? `${m}m ${resto}s` : `${m}m`;
}

/**
 * El estado del motor por su veredicto.
 *
 * `AUTO_APROBADO` y `APROBADO` son los unicos dos donde el sistema se atreve a
 * afirmar el importe (`SETTLED_STATUSES`). Lo demas esta a revision de una
 * persona, y por eso va en ambar y no en gris: no es un fallo, es trabajo
 * pendiente de hacer.
 */
function estadoVisual(p: ScanProcesado): { tono: string; etiqueta: string } {
  if (p.es_duplicado) return { tono: 'bg-gray-100 text-gray-500', etiqueta: 'duplicado' };
  if (p.extraction_status === 'AUTO_APROBADO') return { tono: 'bg-green-100 text-green-800', etiqueta: 'auto-aprobado' };
  if (p.extraction_status === 'APROBADO') return { tono: 'bg-green-100 text-green-800', etiqueta: 'aprobado' };
  if (p.extraction_status === 'REQUIERE_REVISION') return { tono: 'bg-amber-100 text-amber-800', etiqueta: 'revisar' };
  if (p.extraction_status === 'RECHAZADO') return { tono: 'bg-red-100 text-red-800', etiqueta: 'rechazado' };
  return { tono: 'bg-amber-100 text-amber-800', etiqueta: 'pendiente' };
}

/** El progreso de una corrida, con el feed de lo que ya se leyo. */
export function PanelProgreso({ corrida }: { corrida: ScanCorrida }) {
  const procesados = [...(corrida.procesados ?? [])].reverse();

  // El total no se conoce hasta el final: el resumen se rellena al cerrar. Se
  // muestra el conteo real y NO un porcentaje inventado — con 200 archivos a
  // medio leer, un "45%" calculado sobre lo que ya se vio seria mentira.
  const hechos = corrida.procesados?.length ?? 0;
  const leidos = corrida.con_ticket ?? 0;

  return (
    <section
      className="mt-4 rounded-xl border-2 border-blue-200 bg-blue-50/50 overflow-hidden"
      aria-live="polite"
      aria-busy={!corrida.terminada}
    >
      {/* La cabecera: el "va bien" o "se trabo" del operador */}
      <header className="px-4 py-3 border-b border-blue-200 bg-white/60">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <span
              className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${
                corrida.terminada ? 'bg-green-500' : 'bg-blue-500 animate-pulse'
              }`}
              aria-hidden="true"
            />
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-gray-900">
                {corrida.terminada ? 'Escaneo terminado' : 'Escaneando...'}
              </h2>
              <p className="text-xs text-gray-600 truncate">
                {corrida.actual ? (
                  <>
                    Leyendo <span className="font-mono text-gray-800">{corrida.actual}</span>
                  </>
                ) : corrida.terminada ? (
                  `${corrida.archivos_vistos} archivos vistos en ${segundosLegibles(corrida.segundos)}`
                ) : (
                  'Preparando la carpeta...'
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4 text-xs text-gray-700">
            <span>
              <span className="font-semibold text-gray-900">{hechos}</span> leídos
            </span>
            <span>
              <span className="font-semibold text-gray-900">{leidos}</span> con ticket
            </span>
            {corrida.con_error > 0 && (
              <span className="text-red-700">
                <span className="font-semibold">{corrida.con_error}</span> con error
              </span>
            )}
            <span className="tabular-nums">{segundosLegibles(corrida.segundos)}</span>
          </div>
        </div>

        {/* La barra no tiene porcentaje: ver el comentario de arriba. Es una
            barra de "va avanzando", no una fraccion del total, y por eso el
            relleno es un ancho relativo a los archivos que YA pasaron, con un
            minimo visible para que nunca parezca arrestedo. */}
        {!corrida.terminada && (
          <div className="mt-3 h-1.5 bg-blue-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-500"
              style={{ width: `${Math.min(100, 12 + hechos * 4)}%` }}
            />
          </div>
        )}
      </header>

      {/* El feed: que salio de cada archivo, con su importe */}
      {procesados.length > 0 ? (
        <ul className="divide-y divide-blue-100 max-h-80 overflow-y-auto">
          {procesados.map((p, i) => {
            const ev = estadoVisual(p);
            return (
              <li
                key={`${p.relative_path}-${i}`}
                className="px-4 py-2.5 flex items-center gap-3 bg-white/70 hover:bg-white transition-colors"
              >
                <span className={`shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded ${ev.tono}`}>
                  {ev.etiqueta}
                </span>

                <span className="font-mono text-xs text-gray-700 truncate flex-1 min-w-0" title={p.relative_path}>
                  {p.relative_path}
                </span>

                {p.motor && (
                  <span className="hidden sm:inline text-[10px] text-gray-500 uppercase tracking-wide shrink-0">
                    {p.motor}
                  </span>
                )}

                {/* El importe, y la distincion que hay que ensenar: lo que se
                    LEYO y lo que se AFIRMA. Cuando no coinciden, el monto va
                    atenuado y con el aviso al lado, porque un $4,093.80 sin
                    marca gray se lee como un dato bueno. */}
                <span className="text-right shrink-0 tabular-nums">
                  <span
                    className={`text-sm font-semibold ${
                      p.confiable ? 'text-gray-900' : 'text-gray-400'
                    }`}
                  >
                    {dinero(p.monto)}
                  </span>
                  {!p.confiable && p.monto && (
                    <span className="block text-[10px] text-amber-700 leading-tight">
                      sin afirmar
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-4 py-6 text-center text-sm text-gray-500">
          {corrida.terminada
            ? 'Ningún archivo se leyó en esta corrida.'
            : 'Todavía no se ha leído ningún archivo. Con fotos esto tarda unos segundos por comprobante.'}
        </p>
      )}

      {/* El pie: el resumen honesto, solo si ya termino */}
      {corrida.terminada && corrida.resumen && (
        <footer className="px-4 py-3 bg-white/80 border-t border-blue-200">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            <span className="text-gray-600">
              Total leído:{' '}
              <span className="font-semibold text-gray-900 tabular-nums">
                {dinero(corrida.resumen.importe_total_leido as string)}
              </span>
            </span>
            <span className="text-gray-600">
              Afirmado con evidencia:{' '}
              <span
                className={`font-semibold tabular-nums ${
                  Number(corrida.resumen.importe_total_confiable) > 0
                    ? 'text-green-700'
                    : 'text-gray-500'
                }`}
              >
                {dinero(corrida.resumen.importe_total_confiable as string)}
              </span>
            </span>
            {Number(corrida.resumen.importe_requiere_revision) > 0 && (
              <span className="text-amber-700">
                Requiere revisión:{' '}
                <span className="font-semibold tabular-nums">
                  {dinero(corrida.resumen.importe_requiere_revision as string)}
                </span>
              </span>
            )}
          </div>
          {Number(corrida.resumen.importe_total_confiable) === 0 &&
            Number(corrida.resumen.importe_total_leido) > 0 && (
              <p className="mt-2 text-xs text-amber-800">
                El sistema se leyó{' '}
                {dinero(corrida.resumen.importe_total_leido as string)} pero{' '}
                <strong>no se atreve a afirmar nada</strong>: ninguna lectura pasó los
                checks. Los comprobantes están en la cola de revisión.
              </p>
            )}
        </footer>
      )}
    </section>
  );
}