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
