/** Formas de numeros y percentages.
 *
 *  Que vivan aqui y no en cada pantalla es por una causa concreta: un importe
 *  llega del servidor como texto (`Decimal` en JSON), y hay dos formas de
 *  convertirlo a numero. `parseFloat` funciona; `Number` tambien. La tentacion es
 *  escribirlo en el lugar donde sale el dato, y entonces las dos pantallas
 *  nuevas empiezan a hacerlo de una manera y las dos viejas de otra. En un
 *  tablero, dos importes formateados distinto se leen como dos monedas.
 */

/** El importe de la API (`Decimal` -> texto) a numero.
 *
 *  `Number('')` es 0 y `Number('N/D')` es `NaN`, y un `NaN` en el tablero se
 *  pinta como "$NaN". Se devuelve 0 antes que un numero roto: un cero se
 *  reconoce como "no hay dato" y un `NaN` no. */
export function monto(valor: string | number | null | undefined): number {
  if (valor === null || valor === undefined || valor === '') return 0;
  const n = typeof valor === 'number' ? valor : Number(valor);
  return Number.isFinite(n) ? n : 0;
}

/** `$1,234.56` en es-MX. Cifras grandes si se ponen, porque un gasto de
 * Materials de construccion de 1,521,166.00 sin separador es un bloque de
 *  numeros que nadie lee. */
export function dinero(valor: string | number | null | undefined, conCifras = false): string {
  const n = monto(valor);
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
    minimumFractionDigits: conCifras ? 0 : 2,
    maximumFractionDigits: conCifras ? 0 : 2,
  }).format(n);
}

/** El `$` suelto, para ejes y barras donde el simbolo en cada etiqueta hace
 *  ruido. */
export function numero(valor: string | number | null | undefined): string {
  return new Intl.NumberFormat('es-MX').format(monto(valor));
}

export function porcentaje(valor: number | null | undefined, decimales = 1): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return '—';
  return `${valor.toFixed(decimales)}%`;
}

/** Una fecha de negocio, en español, SIN corrimiento de zona horaria.
 *
 *  ## POR QUE NO SE USA `new Date(...)`
 *
 *  `new Date('2026-10-01')` se interpreta como **medianoche UTC**, y al
 *  renderizarlo con `toLocaleDateString` se pasa a la zona horaria **local**.
 *  En cualquier zona detras de UTC —Mexico es UTC-6— eso convierte el 1 de
 *  octubre en **30 de septiembre**.
 *
 *  MEDIDO el 2026-10-12: un movimiento bancario creado con
 *  `transaction_date = "2026-10-01"` se mostraba como `30/9/2026`. En un
 *  sistema contable eso no es un detalle de formato: quien revisa busca el papel
 *  del dia 1 en una pantalla que le dice que es del dia 30, y el gasto se
 *  busca en el mes equivocado.
 *
 *  Este error ya estaba escrito y corregido en `components/ScanProgreso.tsx`
 *  (`fechaLegible`) pero se dejo en tres sitios mas. Una defensa aplicada en un
 *  archivo no protege el proyecto: la siguiente pantalla escribe la version
 *  vieja porque `new Date(...).toLocaleDateString()` es lo que todo el mundo
 *  escribe.
 *
 *  ## POR QUE SE PARTE EL TEXTO Y NO SE USA `Date`
 *
 *  Una fecha de negocio no tiene hora ni zona: es un dia del calendario. El
 *  parseo correcto es "el dia que dice el texto", y eso es una operacion de
 *  texto. `Date` le anade un instante y una zona que el dato no tiene, y de ahi
 *  sale el corrimiento.
 *
 *  ## Y CUANDO SI ES UNA FECHA-HORA
 *
 *  `created_at` y `matched_at` SI son instantes, y para esos la conversion a
 *  hora local es lo correcto: uno quiere saber "a que hora se escaneo esto" en
 *  su propio reloj. Por eso hay dos funciones y no una: mezclarlas seria volver
 *  a la confusion en un sentido.
 */
import { getConfig } from '../configuracion';

export function fecha(iso: string | null | undefined): string {
  if (!iso) return '—';

  const cfg = getConfig();
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return '—';

  const [, anio, mes, dia] = m;
  return new Intl.DateTimeFormat(cfg.locale, {
    timeZone: 'UTC',
    ...(cfg.date as any),
  }).format(new Date(`${anio}-${mes}-${dia}T00:00:00Z`));
}

/** Un instante con hora: `2026-10-01T13:45:00Z` -> `01/10/2026, 13:45`.
 *
 *  Distinto de `fecha` a proposito. Para un `created_at` la zona local es lo
 *  que se quiere: el instante es el mismo en todas partes, y lo que cambia es
 *  como se ve. Para una fecha de gasto no hay instante, y por eso no se
 *  convierte de ninguna manera.
 */
export function fechaHora(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat('es-MX', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

/** `+12.4%` o `−7.6%`, con el signo explicito.
 *
 *  El signo menos es el que cuesta: una variacion del `-7.6%` escrita como
 *  `7.6%` en gris se lee como una subida. El `−` es el caracter tipografico y no
 *  el guion, para que no parezca un guion suelto pegado al numero. */
export function variacion(valor: number | null | undefined, decimales = 1): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return '—';
  const signo = valor > 0 ? '+' : valor < 0 ? '−' : '';
  return `${signo}${Math.abs(valor).toFixed(decimales)}%`;
}
