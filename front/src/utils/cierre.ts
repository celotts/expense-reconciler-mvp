/** Lo que el front necesita saber del informe de cierre, y que NO recalcula.
 *
 * ## LA REGLA DE ESTE ARCHIVO
 *
 * **Aqui no se calcula nada del informe.** No hay suma, ni porcentaje, ni
 * veredicto. Todo lo que se muestra sale del objeto que devuelve
 * `GET /reports/cierre-mensual`, y ese objeto es el MISMO del que sale el PDF
 * (contrato §5.2).
 *
 * Eso no es una preferencia de estilo: el backend garantiza que dos llamadas
 * del mismo periodo sobre la misma base producen el mismo objeto y que el PDF
 * sale de el sin recalcular (R4). Si el front hiciera su propia aritmetica
 * —aunque fuera "solo para pintar"— el contador veria DOS numeros distintos en
 * la misma pantalla, uno del PDF y otro del preview. Es el unico punto donde
 * este trabajo puede romper la promesa entera del producto, y se rompe aqui.
 *
 * Lo que SI hace este archivo:
 *
 * 1. Traducir a palabras lo que el backend ya decidio (`veredictoMeta`, que se
 *    reusa de `spotcheck.ts` y no se reescribe).
 * 2. Formatear importes y fechas para que se vean (`format.ts`).
 * 3. Decir si el periodo se puede declarar cerrado, leyendo `puede_cerrarse` y
 *    `estado_periodo` — que son DERIVADOS en el backend y llegan calculados.
 *
 * Y en el punto 3 hay una trampa que conviene dejar escrita: `cerrado` y
 * `puede_cerrarse` son preguntas distintas y las dos se necesitan. `cerrado` es
 * un HECHO (esta en `cierres_periodo`); `puede_cerrarse` es lo que R3 permite
 * AFIRMAR hoy. Pueden discrepar —alguien cerro enero y despues metio un ticket
 * de enero— y ese caso tiene su propio estado, `CERRADO_CON_PENDIENTES`. Un
 * front que solo mirara uno de los dos mostraria un "no cierra" donde enero si
 * esta cerrado, que es falso, y taparia el hallazgo mas util del informe.
 */

import type { EstadoPeriodo, ReporteCierreMensual } from '../types/api';
import { dinero, porcentaje, variacion } from './format';
import { veredictoMeta } from './spotcheck';

/** `2026-01` a partir de una fecha.
 *
 *  Se usa `getFullYear()`/`getMonth()` y no `toISOString()` a proposito:
 *  `toISOString()` convierte a UTC, y en Mexico (UTC-6) el dia 1 de mes a las
 *  20:00 sale con el mes ANTERIOR. El selector abriria en el periodo equivocado
 *  y el contador miraria el informe del mes pasado sin que nada lo diga.
 *
 *  ## POR QUE ACEPTA UNA FECHA Y NO USA `new Date()` DENTRO
 *
 *  MEDIDO: el verificador de pantalla daba VERDE con la version de
 *  `toISOString()`, porque el defecto solo se reproduce cerca de medianoche y el
 *  test se corria a otra hora. Un defecto que aparece una vez cada treinta dias
 *  no lo caza un test que compara "hoy" contra "hoy".
 *
 *  Con el parametro, el test pasa una fecha FIJA de la franja que falla —el dia
 *  1 a las 20:30 hora local— y el fallo aparece siempre. Es la misma razon por la
 *  que `known-issues.md` §19 dice que un test que depende del reloj no verifica
 *  nada: aqui no se fia del reloj, se le pasa la fecha que se quiere probar.
 *
 *  Sin argumento devuelve el mes en curso, que es como lo usa la pantalla. */
export function periodoActual(fecha: Date = new Date()): string {
  return `${fecha.getFullYear()}-${String(fecha.getMonth() + 1).padStart(2, '0')}`;
}

/** Los meses que se pueden pedir, del mas reciente al mas antiguo.
 *
 *  Se ofrecen 24 y no 12 porque el cierre de enero se consulta en febrero: si el
 *  selector solo llegara al mes en curso, el trabajo del mes anterior —que es
 *  justo cuando se hace— seria inaccessible. */
export function periodosDisponibles(cuantos = 24): string[] {
  const hoy = new Date();
  const salida: string[] = [];
  for (let i = 0; i < cuantos; i++) {
    const d = new Date(hoy.getFullYear(), hoy.getMonth() - i, 1);
    salida.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  }
  return salida;
}

/** `2026-01` -> `enero de 2026`.
 *
 *  El array viene de `Date` con mes fijo en UTC para no caer en el corrimiento
 *  del que habla `periodoActual`: `new Date('2026-01')` es el 1 de enero en UTC y
 *  en Mexico seria el 31 de diciembre del ano anterior. El dia se fija en 15, a
 *  mitad de mes, para que ningun corrimiento de un dia lo mueva de mes. */
const MESES = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

export function nombrePeriodo(periodo: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(periodo);
  if (!m) return periodo;
  const mes = Number(m[2]);
  if (mes < 1 || mes > 12) return periodo;
  return `${MESES[mes - 1]} de ${m[1]}`;
}

export type TonoEstado = 'verde' | 'ambar' | 'rojo' | 'neutro';

export interface LineaEstado {
  titulo: string;
  detalle: string;
  tono: TonoEstado;
}

/** El banner de arriba del todo: si el periodo se puede declarar cerrado.
 *
 *  Sale SIEMPRE, no solo cuando hay problema. Un periodo que SI cierra necesita
 *  su afirmacion en pantalla, porque "no hay nada que decir" y "todo bien" se
 *  leen igual y no son lo mismo. */
/** El aviso del backend, SI lo hay.
 *
 *  `advertencia_principal` lo compone el servidor, no el front. Se muestra tal
 *  cual y por eso va aparte de `lineaEstado`: son dos preguntas distintas —"¿se
 *  puede cerrar?" y "¿la evidencia alcanza?"— y meterlas en un solo texto
 *  haría que quien lee suppose que el periodo no cierra porque no hay muestra,
 *  que no es lo que pasa.
 *
 *  ## POR QUE `tono: 'ambar'` SIEMPRE, Y NO SEGUN EL VEREDICTO
 *
 *  Tienta hacerlo: pintar en rojo cuando `NO_CUMPLE` y en verde cuando
 *  `CUMPLE`. Seria una traduccion del veredicto a color, que es justo el trabajo
 *  que hace `veredictoMeta` en el bloque 5 y que aqui seria DUPLICADO. Si los
 *  dos sitios tuvieran su propio color, un `CUMPLE` podria verse rojo en el
 *  banner y verde en la tabla, y el contador leeria dos veredictos del mismo
 *  dato.
 *
 *  Este bloque es SIEMPRE un aviso —existe solo cuando hay algo que avisar—,
 *  asi que su color es el de "atencion", y el veredicto se pinta donde toca:
 *  en el bloque 5. */
export function lineaAdvertencia(r: ReporteCierreMensual): LineaEstado | null {
  if (!r.advertencia_principal) return null;
  return {
    titulo: 'Antes de usar este informe',
    detalle: r.advertencia_principal,
    tono: 'ambar',
  };
}

export function lineaEstado(r: ReporteCierreMensual): LineaEstado {
  const estado: EstadoPeriodo = r.estado_periodo;

  switch (estado) {
    case 'CERRADO':
      return {
        titulo: 'Periodo cerrado',
        detalle: r.cerrado_por
          ? `Registrado por ${r.cerrado_por} el ${r.fecha_referencia}.`
          : `Registrado el ${r.fecha_referencia}.`,
        tono: 'verde',
      };

    case 'CERRADO_CON_PENDIENTES':
      // El caso que hace falta el cuarto estado. NO es "no cierra": el periodo SI
      // esta cerrado en el registro, y ademas se escribieron cosas despues.
      return {
        titulo: 'Cerrado, pero con pendientes dentro',
        detalle:
          `El periodo esta cerrado (${r.cerrado_por ?? 'sin responsable'}) y despues se `
          + `registraron pendientes: ${r.pendientes.motivo}. El cierre es un hecho; `
          + `esto es lo que hay que revisar.`,
        tono: 'ambar',
      };

    case 'NO_CIERRA':
      return {
        titulo: 'El periodo NO se puede declarar cerrado',
        detalle: r.pendientes.motivo
          ? `Queda pendiente: ${r.pendientes.motivo}.`
          : 'Queda trabajo pendiente en el periodo.',
        tono: 'rojo',
      };

    case 'CIERRE_DISPONIBLE':
      return {
        titulo: 'El periodo se puede declarar cerrado',
        detalle:
          'No hay tickets sin categoria, sin conciliar ni con discrepancias abiertas. '
          + 'Marcarlo como cerrado sigue siendo una decision de una persona.',
        tono: 'verde',
      };

    default:
      // Un estado que el backend emite y esta pantalla no conoce. Se muestra
      // crudo en vez de inventar uno: tapar un estado desconocido hace que
      // nadie sepa que hay algo que no se esta explicando. Mismo criterio que
      // `lineaVeredicto` en `veredicto.ts`.
      return {
        titulo: `Estado no reconocido: ${String(estado)}`,
        detalle: 'El backend devolvio un estado que esta version de la pantalla no traduce.',
        tono: 'neutro',
      };
  }
}

/** El nombre del archivo del PDF: `cierre_<tax_id>_<periodo>.pdf`.
 *
 *  Se arma aqui y no con el `Content-Disposition` del servidor a proposito: el
 *  header no se puede leer desde `fetch` (esta en la lista de headers
 *  prohibidos), asi que si el nombre lo pusiera el backend, esta pantalla
 *  tendria que inventarlo — y un nombre inventado es un nombre que no coincide
 *  con el archivo que el backend genero. */
export function nombreArchivo(r: ReporteCierreMensual): string {
  return `cierre_${r.empresa_tax_id}_${r.periodo}.pdf`;
}

/** La linea del comparativo contra el mes anterior.
 *
 *  `conclusivo=false` NO se maquilla: el backend lo dice porque el mes anterior
 *  esta en curso y comparar 6 dias contra 31 produce una caida que no existe.
 *  Poner el porcentaje sin ese matiz seria affirmar una caida que el dato no
 *  soporta, que es justo lo que R1 prohibe. */
export function lineaComparativo(r: ReporteCierreMensual): LineaEstado | null {
  const c = r.gasto.comparativo;
  if (!c) return null;

  const monto = dinero(c.monto_anterior, true);
  const tickets = String(c.tickets_anterior);

  if (!c.conclusivo) {
    return {
      titulo: `Comparacion con ${c.nombre_anterior}: no concluyente`,
      detalle: c.nota ?? 'El mes anterior no ha terminado, asi que la comparacion no dice nada.',
      tono: 'ambar',
    };
  }

  if (c.diferencia_pct === null || c.diferencia === null) {
    return {
      titulo: `Comparacion con ${c.nombre_anterior}`,
      detalle: `Sin diferencia calculable. El mes anterior registro ${monto} en ${tickets} tickets.`,
      tono: 'neutro',
    };
  }

  return {
    titulo: `Comparacion con ${c.nombre_anterior}`,
    detalle:
      `${variacion(c.diferencia_pct)} contra ${monto} en ${tickets} tickets. `
      + (c.nota ?? ''),
    tono: c.diferencia_pct < 0 ? 'ambar' : 'neutro',
  };
}

/** El bloque 5: que se puede afirmar de la exactitud.
 *
 *  R1 y R7 se respetan aqui y no por buena voluntad:
 *
 *  - `exactitud === null` es `no disponible`, NUNCA `0%`. Un cero se lee como
 *    "se midio y fallo", que es una afirmacion que el dato no sostiene.
 *  - Cuando hay numero, el intervalo va JUNTO. Un `96%` sin el intervalo al lado
 *    es un numero que no significa nada: `96%` de 25 y `96%` de 5000 son el
 *    mismo numero con consecuencias opuestas.
 *
 *  La etiqueta y el color salen de `veredictoMeta`, que es el MISMO que usa la
 *  pantalla de muestreo. Reutilizarlo es lo que garantiza que el contador no
 *  lea "cumple" en un sitio y "no cumple" en otro por dos textos distintos. */
export function lineaExactitud(r: ReporteCierreMensual): LineaEstado | null {
  if (!r.exactitud) {
    // R7: se dice que no se pudo calcular. No se omite la seccion y no se pone 0.
    return {
      titulo: 'Exactitud: no disponible',
      detalle:
        'No hay evidencia de muestreo para este periodo. Sin ella no se puede afirmar '
        + 'ni que funciona ni que no: revisar la muestra es lo que la produce.',
      tono: 'neutro',
    };
  }

  const meta = veredictoMeta(r.exactitud.veredicto_global);
  const objetivo = porcentaje(r.exactitud.objetivo * 100, 0);

  return {
    titulo: `Exactitud: ${meta.label}`,
    detalle:
      `Objetivo ${objetivo}. ${r.exactitud.explicacion}\n`
      + `Origen: ${r.exactitud.por_origen.map(o => o.origen).join(', ') || 'sin origenes'}.`,
    tono: meta.variant === 'success' ? 'verde'
      : meta.variant === 'danger' ? 'rojo'
      : meta.variant === 'warning' ? 'ambar' : 'neutro',
  };
}

/** El nombre del archivo de un `Blob`, para la descarga. */
export function descargarBlob(blob: Blob, nombre: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // El `revokeObjectURL` va DESPUES del `click` y no antes: si se revoca antes,
  // la descarga arranca contra una URL que ya no existe y no sale nada. En
  // Safari el error es silencioso, que es peor.
  URL.revokeObjectURL(url);
}