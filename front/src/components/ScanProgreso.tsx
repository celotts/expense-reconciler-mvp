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
 * documento requiere `GET /tickets/{id}/documento` por cada uno, y son peticiones
 * que compiten con el OCR por el ancho de banda. Se deja para la cola de
 * revision, donde el operador ya esta mirando una sola cosa.
 *
 * LO QUE SE ENSENA DE CADA TICKET, Y POR QUE NO BASTA EL IMPORTE
 * --------------------------------------------------------------
 *
 * El feed **trae el encabezado completo del ticket** —proveedor, RFC, fecha,
 * subtotal, IVA, IEPS, lineas y `validation_errors`— y no solo el total. La razon
 * es que el total solo no distingue dos situaciones que piden acciones opuestas:
 *
 *   - `total 0.00` + `total_not_positive` → el sistema **no supo leer** el papel.
 *     Corregir exige volver a la foto, no cambiar un numero.
 *   - `total 119.97` + `subtotal_plus_tax_mismatch` → el sistema **si leyo** algo,
 *     pero el gate vio que no cuadra. Ahi el trabajo es comparar dos numeros.
 *
 * Con solo el total, las dos se ven como "$X, pendiente" y el operador no sabe
 * cual es. Con el encabezado, el `validation_errors` dice cual, y el proveedor y
 * la fecha dicen si el papel es siquiera el que se cree.
 *
 * **Y los numeros van al lado del veredicto, nunca solos.** Es lo que evita el
 * malentendido de que "se ve un importe" quiere decir "el importe es bueno". Un
 * `279.33` junto a `subtotal_plus_tax_mismatch` es una lectura en disputa, y se
 * muestra como tal.
 *
 * **El `validation_errors` va siempre visible cuando hay**, incluso con el
 * panel plegado, porque es el unico texto que convierte una fila en una tarea. Y
 * va en texto crudo y no como "3 errores": el operador tiene que saber *cual*.
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

/** Divide `validation_errors` en la lista de motivos.
 *
 * El backend los une con `; ` (`confidence_gate` los separa asi), y llegan como
 * un solo texto: `subtotal_plus_tax_mismatch(leido=234.00,esperado=217.27);
 * date_missing`. Se parten para poder ponerlos en `<li>` en vez de en un parrafo
 * largo que hay que leer entero para saber si hay uno o tres.
 *
 * Se separa por `;` y no por `,` a proposito: los motivos llevan comas DENTRO
 * (`leido=234.00,esperado=217.27`), y partir por coma parte un motivo en dos que
 * no significan nada por separado. */
function motivos(validationErrors: string | null): string[] {
  if (!validationErrors) return [];
  return validationErrors
    .split(';')
    .map((m) => m.trim())
    .filter(Boolean);
}

/** Un motivo del gate, traducido.
 *
 * Los codigos van en ingles porque son los que se ven en `validation_errors`, en
 * los logs y en la base: traducirlos aqui los haria imposibles de buscar. Lo que
 * se traduce es la EXPLICACION, que es lo que no se deduce del codigo.
 *
 * Un codigo sin entrada aqui se muestra tal cual, y es lo correcto: es mejor ver
 * `algo_nuevo_del_gate` que no ver nada y creer que no hubo error. Un switch
 * silencioso es un switch que miente cuando el backend crece. */
const GLOSARIO: Record<string, string> = {
  total_not_positive: 'no se leyó un total',
  provider_missing: 'no se leyó el comercio',
  date_missing: 'no se leyó la fecha',
  subtotal_plus_tax_mismatch: 'subtotal + IVA no da el total',
  rfc_missing: 'no se leyó el RFC',
  rfc_invalid: 'el RFC no tiene forma válida',
  date_in_future: 'la fecha está en el futuro',
  date_too_old: 'la fecha es demasiado vieja',
  total_mismatch: 'el total no coincide',
  items_missing: 'el comprobante no trae líneas',
  ieps_plus_tax_mismatch: 'subtotal + IVA + IEPS no da el total',
};

function explica(motivo: string): string | null {
  // El motivo trae el detalle entre parentesis: `subtotal_plus_tax_mismatch
  // (leido=234.00,esperado=217.27)`. El codigo va hasta el primer `(`.
  const corte = motivo.indexOf('(');
  if (corte === -1) return GLOSARIO[motivo] ?? null;
  const codigo = motivo.slice(0, corte).trim();
  return GLOSARIO[codigo] ?? null;
}

/** El codigo del motivo, sin su detalle.
 *
 * Va en `<code>` pequeno porque es el termino que se busca en la base y en los
 * logs, y esconderlo detras de la traduccion hace imposible responder "por que
 * salio `date_missing`". */
function codigoDe(motivo: string): string {
  const corte = motivo.indexOf('(');
  return corte === -1 ? motivo : motivo.slice(0, corte).trim();
}

/** El detalle entre parentesis, o `null`. */
function detalleDe(motivo: string): string | null {
  const abre = motivo.indexOf('(');
  if (abre === -1) return null;
  const cierra = motivo.lastIndexOf(')');
  if (cierra < abre) return null;
  const dentro = motivo.slice(abre + 1, cierra).trim();
  return dentro || null;
}

/** `2026-10-01` -> `01 oct 2026`.
 *
 * Se parte el texto en vez de pasar la cadena por `Date`: `new Date('2026-10-01')`
 * lo interpreta en UTC y en una zona horaria detras de UTC se renderiza como el
 * dia ANTERIOR. Un comprobante fechado el dia 1 aparece el dia 30 y el operador
 * busca el papel en el mes equivocado. */
function fechaLegible(iso: string | null): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const mes = meses[Number(m[2]) - 1] ?? m[2];
  return `${m[3]} ${mes} ${m[1]}`;
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
                className="px-4 py-3 bg-white/70 hover:bg-white transition-colors"
              >
                {/* La primera linea: el estado, el archivo y el importe.
                    Es el resumen de un vistazo, y lo que se lee de reojo. */}
                <div className="flex items-center gap-3">
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
                </div>

                {/* La segunda linea: QUE se leyo del papel.
                    Es lo que hace que la fila sea una tarea y no un contador. */}
                {(p.proveedor || p.fecha || p.subtotal || p.iva) && (
                  <div className="mt-1.5 pl-1 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-gray-600">
                    {p.proveedor && (
                      <span className="font-medium text-gray-800" title={p.proveedor}>
                        {p.proveedor}
                      </span>
                    )}
                    {p.rfc && (
                      <span className="font-mono text-[11px] text-gray-500">{p.rfc}</span>
                    )}
                    {fechaLegible(p.fecha) && (
                      <span title={p.fecha ?? undefined}>{fechaLegible(p.fecha)}</span>
                    )}
                    {p.subtotal && <span>subtotal {dinero(p.subtotal)}</span>}
                    {p.iva && <span>IVA {dinero(p.iva)}</span>}
                    {p.ieps && <span>IEPS {dinero(p.ieps)}</span>}
                    {/* El conteo de lineas, y `null` NO es lo mismo que `0`: la
                        ruta OCR no trae detalle de partidas, y no decir nada es
                        mas honesto que decir "0 lineas". */}
                    {p.lineas !== null && p.lineas !== undefined && (
                      <span className="text-gray-500">
                        {p.lineas === 1 ? '1 línea' : `${p.lineas} líneas`}
                      </span>
                    )}
                  </div>
                )}

                {/* Los motivos del gate. SIEMPRE visibles, sin plegar.
                    Es lo unico que convierte "pendiente" en algo accionable, y
                    va en el codigo real para que se pueda buscar en la base. */}
                {motivos(p.validation_errors).length > 0 && (
                  <ul className="mt-1.5 pl-1 space-y-0.5">
                    {motivos(p.validation_errors).map((m, j) => {
                      const explicacion = explica(m);
                      const detalle = detalleDe(m);
                      return (
                        <li
                          key={`${m}-${j}`}
                          className="text-[11px] text-amber-800 leading-snug"
                        >
                          <code className="font-mono">{codigoDe(m)}</code>
                          {explicacion && <span> — {explicacion}</span>}
                          {detalle && (
                            <span className="text-amber-700"> ({detalle})</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
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