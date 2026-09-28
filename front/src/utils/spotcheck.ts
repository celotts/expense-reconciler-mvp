/**
 * Traduccion de la evidencia del muestreo a lenguaje humano.
 *
 * El muestreo responde a una pregunta que casi nunca tiene respuesta limpia:
 * "que tan exacto es esto?". Y la respuesta honesta casi nunca es un numero.
 * Con 30 revisiones al 96% el intervalo es [83%, 99%]: se puede decir que es
 * malo Y que probablemente este bien. Publicar el 96% sin el 83% seria
 * afirmar una certeza que nadie midio.
 *
 * Reglas de este archivo, en orden de prioridad:
 *
 * 1. Un veredicto que no conozco se muestra, no se esconde. Un codigo
 *    desconocidos aqui se ve como texto crudo. Si el backend agrega un
 *    veredicto y esta tabla no lo tiene, la pantalla tiene que decir "no lo
 *    entiendo", no mentir con un color.
 *
 * 2. `null` no es `0`. Un origen sin revisiones tiene `exactitud: null`, que
 *    significa "nadie miró". Si se pintara como 0% se leeria como "falló
 *    todo", y son cosas opuestas: 0% es un resultado, `null` es una ausencia.
 *
 * 3. El peor veredicto manda. No se promedian ni se elige el mayor conteo.
 */

import type { SpotCheckField, SpotCheckStatus, Veredicto } from '../types/api';
import type { BadgeVariant } from './extraction';

export interface VeredictoMeta {
  label: string;
  variant: BadgeVariant;
  /** Que significa, y que se puede hacer. Para el title del badge. */
  help: string;
}

export const VEREDICTO_META: Record<Veredicto, VeredictoMeta> = {
  CUMPLE: {
    label: 'Cumple el objetivo',
    variant: 'success',
    help: 'El limite inferior del intervalo esta por encima del objetivo. '
      + 'Se puede afirmar la exactitud con la confianza declarada.',
  },
  NO_CUMPLE: {
    label: 'No cumple el objetivo',
    variant: 'danger',
    help: 'El limite superior esta por debajo del objetivo. No hace falta mas '
      + 'muestra: hay que arreglar el extractor. Medir mas no lo va a cambiar.',
  },
  INCONCLUYENTE: {
    label: 'Inconcluyente',
    variant: 'warning',
    help: 'La evidencia no alcanza ni para afirmar ni para descartar. Hay que '
      + 'seguir revisando, oar el campo que mas falla.',
  },
  SIN_EVIDENCIA: {
    label: 'Sin evidencia',
    variant: 'default',
    help: 'Nadie reviso todavia ningun ticket de esta via. No se puede decir '
      + 'nada: ni que funciona ni que no.',
  },
};

/** Lo que hay que hacer cuando el veredicto no es CUMPLE.
 *
 *  Sin esto, "INCONCLUYENTE" es una palabra sin accion, y la gente deja de
 *  revisar. La accion depende de la RAZON, que es lo que el backend manda
 *  separada del veredicto: falta muestra se resuelve revisando, acierto por
 *  debajo del objetivo se resuelve arreglando. */
export function accionSegunVeredicto(v: Veredicto | string): string {
  switch (v) {
    case 'CUMPLE':
      return 'Nada que hacer. Se sigue muestreando para vigilar que no se caiga.';
    case 'NO_CUMPLE':
      return 'Hay que arreglar la lectura. Medir mas no lo cambia.';
    case 'INCONCLUYENTE':
      return 'Seguir revisando la muestra, y arreglar el campo que mas falla.';
    case 'SIN_EVIDENCIA':
      return 'Revisar muestra de esta via. Sin veredictos no se puede afirmar nada.';
    default:
      return 'Veredicto no reconocido por esta version de la pantalla. '
        + `El backend devolvio "${v}".`;
  }
}

export function veredictoMeta(v: Veredicto | string | null | undefined): VeredictoMeta {
  if (!v) {
    return {
      label: 'Sin veredicto',
      variant: 'default',
      help: 'El backend no devolvio veredicto para esta via.',
    };
  }
  return VEREDICTO_META[v as Veredicto] ?? {
    label: `No reconocido: ${v}`,
    variant: 'warning',
    help: 'El backend devolvio un veredicto que esta version de la pantalla no '
      + 'conoce. El numero de abajo puede estar mal interpretado.',
  };
}

export const SPOT_CHECK_META: Record<SpotCheckStatus, { label: string; variant: BadgeVariant }> = {
  PENDIENTE: { label: 'Sin revisar', variant: 'info' },
  CORRECTO: { label: 'Coincidía con el papel', variant: 'success' },
  INCORRECTO: { label: 'No coincidía', variant: 'danger' },
};

/**
 * Los campos que se pueden marcar como mal leidos, en el orden en que se
 * muestra un comprobante, no en el que estan en la base.
 *
 * `category` NO esta. La categoria la elige la persona que clasifica el gasto:
 * un papel no dice "esto es alimento", asi que no hay con que compararla. Si
 * apareciera aqui, el muestreo mediria una decision humana con la metrica del
 * automatismo, y el numero de exactitud dejaria de significar lo que dice.
 */
export const CAMPOS_MUESTRABLES: ReadonlyArray<{
  campo: SpotCheckField;
  label: string;
}> = [
  { campo: 'provider_name', label: 'Proveedor' },
  { campo: 'provider_tax_id', label: 'RFC' },
  { campo: 'expense_date', label: 'Fecha' },
  { campo: 'subtotal', label: 'Subtotal' },
  { campo: 'tax_amount', label: 'IVA' },
  { campo: 'total_amount', label: 'Total' },
];

export function esCampoMuestrable(c: string): c is SpotCheckField {
  return CAMPOS_MUESTRABLES.some(x => x.campo === c);
}

/** La etiqueta de un campo, o el texto crudo si no lo conozco.
 *
 *  Mismo motivo que en el resto del archivo: un campo nuevo que el backend
 *  acepte y esta tabla no sepa se muestra como esta, para poder reportarlo. */
export function etiquetaCampo(campo: string): string {
  return CAMPOS_MUESTRABLES.find(x => x.campo === campo)?.label
    ?? `campo no reconocido: ${campo}`;
}

/**
 * Un porcentaje o un intervalo, listo para pintar, o `null` si no hay dato.
 *
 * Se devuelve `null` y no un string vacio a proposito: la pantalla tiene que
 * poder distinguir "no hay dato" de "0%", y un string vacio en JSX pinta una
 * celda en blanco que se lee como un dato que se perdio al pintar.
 */
export function porcentaje(v: number | null | undefined, decimales = 1): string | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  return `${(v * 100).toFixed(decimales)}%`;
}

/** `96.0% [92.4% - 98.1%]`, o `null` si el intervalo no vino.
 *
 *  El intervalo va JUNTO al punto, no aparte. Es la unica forma de que el
 *  numero signifique algo: "96% de 25 y 96% de 5000" son el mismo numero con
 *  consecuencias opuestas, y sin el intervalo no hay forma de saber cual de
 *  las dos se esta mirando. */
export function exactitudConIntervalo(
  exacta: number | null | undefined,
  bajo: number | null | undefined,
  alto: number | null | undefined,
): string | null {
  const p = porcentaje(exacta);
  if (p === null) return null;
  const b = porcentaje(bajo);
  const a = porcentaje(alto);
  if (b === null || a === null) return p;
  return `${p} [${b} - ${a}]`;
}
