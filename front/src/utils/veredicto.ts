// Como se le explica al usuario el veredicto del confidence gate.
//
// El dato crudo (`extraction_status`, `confidence`, `confidence_source`) no se
// enseña nunca solo. "AUTO_APROBADO" no le dice a un contador nada; lo que le
// dice es por que se le creo sin que nadie lo revisara, y eso es exactamente
// lo que el sistema puede o no respaldar.
//
// Dos reglas que este archivo no rompe:
//
// 1. Nunca se muestra una confianza sin decir de donde viene. Un 0.97 "de la IA"
//    y un 0.97 "de las reglas del documento" no son la misma evidencia: el
//    primero es un modelo opinando, el segundo es aritmetica verificada.
//
// 2. Los motivos del gate se muestran con sus numeros. `validation_errors` trae
//    `subtotal_plus_tax_mismatch(leido=..., esperado=...)` a proposito, para que
//    revisar 500 tickets no sea comparar a ciegas. Pillar ese texto es
//    reescribirlo para un humano.
//
// El enum y sus valores vienen de app/core/enums.py. Si divergen, el backend
// manda y hay que corregir esto: una etiqueta que el backend nunca emite es una
// etiqueta que nunca se ve, y peor, una que hides un estado desconocido.

import type { ConfidenceSource, ExtractionStatus } from '../types/api';

export interface Veredicto {
  estado: ExtractionStatus | string;
  confianza: number | null;
  origen: string | null;
  /** `validation_errors` partido por `"; "`, como lo arma el gate. */
  motivos: string[];
  /** Si el ticket tiene veredicto humano (muestreo o revisión de cola). */
  revisado: boolean;
  revisadoPor: string | null;
}

export interface LineaVeredicto {
  titulo: string;
  detalle: string;
  tono: 'verde' | 'ambar' | 'rojo' | 'neutro';
}

/** Convierte `"0.970"` en `0.97`. `null` si no es utilizable. */
function parseConfianza(valor: string | null | undefined): number | null {
  if (valor === null || valor === undefined || valor === '') return null;
  const n = Number(valor);
  if (!Number.isFinite(n)) return null;
  return n >= 0 && n <= 1 ? n : null;
}

/** De donde salio la lectura, en palabras y sin vender de mas. */
export function etiquetaOrigen(origen: string | null | undefined): string {
  switch (origen) {
    case 'pdf_text':
      return 'leído del texto del PDF';
    case 'rules':
      return 'leído por reglas del documento';
    case 'llm':
    case 'llm_validated':
      return 'leído por el modelo';
    case 'manual':
      return 'capturado a mano';
    default:
      return 'origen no identificado';
  }
}

/** Que puede afirmar el sistema, y hasta donde. */
export function lineaVeredicto(v: Veredicto): LineaVeredicto {
  const conf = v.confianza;
  const origen = etiquetaOrigen(v.origen);
  const motivos = v.motivos.length > 0 ? ` ${v.motivos.join('. ')}.` : '';

  switch (v.estado) {
    case 'AUTO_APROBADO':
      return {
        titulo: 'Aprobado sin revisión',
        detalle:
          `La aritmética del comprobante cuadra y la lectura es suficientemente segura ` +
          `(${conf !== null ? conf.toFixed(2) : 'sin confianza medida'}, ${origen}). ` +
          `No hace falta que alguien lo revise, pero entra al muestreo: si el sistema ` +
          `se equivoca, se va a notar y se va a poder medir.${motivos}`,
        tono: 'verde',
      };

    case 'APROBADO':
      return {
        titulo: 'Aprobado a mano',
        detalle:
          `Una persona revisó y aprobó estos datos${v.revisadoPor ? ` (${v.revisadoPor})` : ''}. ` +
          `No es una lectura automática, así que no cuenta para la medición de exactitud.${motivos}`,
        tono: 'verde',
      };

    case 'REQUIERE_REVISION':
      return {
        titulo: 'Va a revisión',
        detalle:
          `Se leyó, pero no se(auto-aprobó${motivos} ` +
          `Revísalo: si lo corriges, deja de contar como lectura automática.`,
        tono: 'ambar',
      };

    case 'PENDIENTE':
      return {
        titulo: 'Falta algo para aprobarlo',
        detalle:
          `La lectura tiene un dato que no se puede dar por bueno${motivos} ` +
          `Complétalo a mano y se guarda.`,
        tono: 'rojo',
      };

    case 'RECHAZADO':
      return {
        titulo: 'Rechazado',
        detalle: `Una persona descartó este ticket${motivos}`,
        tono: 'rojo',
      };

    default:
      // Un estado que el backend emite y aqui no se conoce. Se muestra crudo en
      // vez de inventar una etiqueta: tapar un estado desconocido hace que el
      // usuario no sepa que hay algo que no se esta explicando.
      return {
        titulo: `Estado no reconocido: ${v.estado}`,
        detalle: 'El sistema emitió un estado que esta version de la pantalla no traduce.',
        tono: 'neutro',
      };
  }
}

/** El texto de `validation_errors` tal cual viene, para no perder los numeros. */
export function motivosDelGate(validationErrors: string | null | undefined): string[] {
  if (!validationErrors) return [];
  return validationErrors
    .split(';')
    .map(s => s.trim())
    .filter(Boolean);
}

/** Compone el veredicto desde lo que devuelve la API. */
export function veredictoDeTicket(t: {
  extraction_status: ExtractionStatus | string;
  confidence: string | null;
  confidence_source: ConfidenceSource | string | null;
  validation_errors: string | null;
  reviewed_by: string | null;
}): Veredicto {
  return {
    estado: t.extraction_status,
    confianza: parseConfianza(t.confidence),
    origen: t.confidence_source,
    motivos: motivosDelGate(t.validation_errors),
    revisado: !!t.reviewed_by,
    revisadoPor: t.reviewed_by,
  };
}

export const TONOS: Record<LineaVeredicto['tono'], string> = {
  verde: 'bg-green-50 border-green-200 text-green-800',
  ambar: 'bg-amber-50 border-amber-200 text-amber-800',
  rojo: 'bg-red-50 border-red-200 text-red-800',
  neutro: 'bg-gray-50 border-gray-200 text-gray-700',
};
