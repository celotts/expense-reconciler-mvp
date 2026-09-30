/**
 * Traduccion de los estados y los checks del gate a lenguaje humano.
 *
 * El backend guarda los motivos como codigos: `subtotal_plus_tax_mismatch
 * (leido=1160.00,esperado=660.00)`. Eso sirve para agrupar error en un
 * reporte, y no sirve para nada en una pantalla donde hay que corregir el
 * ticket. Quien revisa no puede arreglar `tax_exceeds_total`: hay que
 * decirle "el IVA (200.00) es mayor que el total (100.00)".
 *
 * Regla de este archivo: un codigo que no conozco se muestra, no se esconde.
 * Si el backend agrega un check nuevo y esta tabla no lo tiene, el revisor
 * ve "motivo no reconocido: <codigo>" y puede reportarlo. Un motivo que se
 * come en silencio es un ticket que nadie puede corregir.
 */

import type { ExtractionStatus } from '../types/api';
import { UNKNOWN_PROVIDER } from './validation';

export type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info';

export interface StatusMeta {
  label: string;
  variant: BadgeVariant;
  /** Que significa el estado, en una linea. Para el title del badge. */
  help: string;
}

export const STATUS_META: Record<ExtractionStatus, StatusMeta> = {
  AUTO_APROBADO: {
    label: 'Auto-aprobado',
    variant: 'success',
    help: 'La IA lo leyo con confianza alta y los checks pasaron. Entra directo a conciliacion.',
  },
  APROBADO: {
    label: 'Aprobado',
    variant: 'success',
    help: 'Una persona reviso y confirmo estos datos.',
  },
  REQUIERE_REVISION: {
    label: 'Requiere revision',
    variant: 'warning',
    help: 'Se leyo algo, pero la confianza fue baja o un check fallo. Necesita una persona.',
  },
  PENDIENTE: {
    label: 'Pendiente',
    variant: 'danger',
    help: 'No se pudo extraer nada confiable del documento. Casi siempre es una foto ilegible.',
  },
  RECHAZADO: {
    label: 'Rechazado',
    variant: 'default',
    help: 'Una persona lo descarto. No entra a conciliacion.',
  },
};

export const STATUS_ORDEN: ExtractionStatus[] = [
  'PENDIENTE',
  'REQUIERE_REVISION',
  'AUTO_APROBADO',
  'APROBADO',
  'RECHAZADO',
];

/**
 * Estado desconocido, con la forma que espera la pantalla pero sin mentir.
 *
 * Por que hace falta: `STATUS_META[estado]` sobre un estado que la pantalla no
 * conoce es `undefined`, y al leer `.help` revienta el render entero. La cola
 * queda en blanco. Y una cola en blanco no muestra un error: muestra "no hay
 * nada pendiente", que es justo la mentira que esta pantalla existe para no
 * contar. Si el backend agrega un estado y el frontend no lo conoce, lo que
 * tiene que verse es "hay algo que no entiendo", no una pantalla vacia.
 */
const ESTADO_DESCONOCIDO: StatusMeta = {
  label: 'Estado no reconocido',
  variant: 'warning',
  help: 'El backend devolvio un estado que esta version de la pantalla no conoce. '
    + 'No se puede revisar hasta actualizar el frontend.',
};

export function statusMeta(status: string | null | undefined): StatusMeta {
  if (!status) {
    return {
      label: 'Sin estado',
      variant: 'danger',
      help: 'El registro no tiene estado de extraccion. No se puede revisar.',
    };
  }
  return STATUS_META[status as ExtractionStatus] ?? {
    ...ESTADO_DESCONOCIDO,
    label: `No reconocido: ${status}`,
  };
}

/**
 * El nombre del proveedor, o `null` si de verdad no se pudo leer.
 *
 * El parser emite la cadena literal `Unknown Provider` cuando no logra
 * identificar al emisor. Eso no es un proveedor: es la ausencia de uno. Si se
 * muestra tal cual, el revisor lee "Unknown Provider" como si fuera el nombre
 * del comercio y aprueba un ticket sin proveedor real.
 *
 * `UNKNOWN_PROVIDER` vive en validation.ts porque es la misma cadena que
 * emite el backend. Que esten en un solo lado es lo que impide que la cola y el
 * formulario de captura se comporten distinto para el mismo dato.
 */
export function nombreProvisorLegible(nombre: string | null | undefined): string | null {
  const n = (nombre ?? '').trim();
  if (!n || n === UNKNOWN_PROVIDER) return null;
  return n;
}

export interface ValidationProblem {
  /** Codigo tal cual lo escribio el backend, para poder reportarlo. */
  code: string;
  /** Que esta mal y que hay que hacer, en espanol. */
  message: string;
}

/** Extrae los valores entre parentesis de un codigo, si los trae. */
function argsDe(code: string): Record<string, string> {
  const m = code.match(/\(([^)]*)\)/);
  if (!m) return {};
  const out: Record<string, string> = {};
  for (const parte of m[1].split(',')) {
    const [k, ...resto] = parte.split('=');
    if (resto.length) out[k.trim()] = resto.join('=').trim();
  }
  return out;
}

/**
 * El contenido crudo entre parentesis, para los checks que pasan un valor
 * posicional y no con clave: `date_in_future(2026-01-01)`, no
 * `date_in_future(fecha=2026-01-01)`.
 */
function valorDe(code: string): string | undefined {
  const m = code.match(/\(([^)]*)\)/);
  return m ? m[1].trim() : undefined;
}

/** Quita el "(...)" final y deja solo el nombre del check. */
function nombreDe(code: string): string {
  return code.replace(/\([^)]*\)$/, '').trim();
}

const sinConocer = (code: string): ValidationProblem => ({
  code,
  message: `Motivo no reconocido por esta version de la pantalla: ${code}. Revisa el documento completo.`,
});

/**
 * Los checks del gate, en el mismo orden en que los revisa
 * app/services/confidence_gate.py. El orden importa: se muestra primero lo que
 * impide hacer el ticket (no hay proveedor) y al final lo fino (el RFC).
 */
function explicar(codigo: string): ValidationProblem {
  const nombre = nombreDe(codigo);
  const a = argsDe(codigo);

  switch (nombre) {
    case 'provider_missing':
      return {
        code: codigo,
        message: 'No se identifico el proveedor. El documento esta ilegible o la IA no lo leyo. '
          + 'Escribe el nombre tal como aparece en el papel.',
      };

    case 'total_not_positive':
      return {
        code: codigo,
        message: 'El total no es un numero positivo. Si el documento esta roto, descartalo; '
          + 'si el total si se ve, escribelo a mano.',
      };

    case 'tax_negative':
      return { code: codigo, message: 'El IVA es negativo, lo cual es imposible. Corrigelo o pon 0.00.' };

    case 'tax_exceeds_total':
      return {
        code: codigo,
        message: 'El IVA no puede ser mayor que el total. Revisa los dos numeros: casi siempre '
          + 'uno de los dos se leyo de mas.',
      };

    case 'subtotal_plus_tax_mismatch': {
      const leido = a.leido;
      const esperado = a.esperado;
      if (!leido || !esperado) return sinConocer(codigo);
      return {
        code: codigo,
        message: `La aritmetica del documento no cuadra: subtotal + IVA da ${esperado}, `
          + `pero el total dice ${leido}. Compara contra el papel para ver cual de los tres se leyo mal.`,
      };
    }

    case 'malformed_rfc':
      return {
        code: codigo,
        message: 'El RFC esta mal formado. Son 3 o 4 letras, 6 digitos de fecha y 3 letras o '
          + 'digitos (la homoclave). Si no hay RFC en el papel, dejalo vacio: no es un error.',
      };

    case 'date_missing':
      return { code: codigo, message: 'No se pudo leer la fecha. Escribe la del documento.' };

    case 'date_in_future': {
      const f = valorDe(codigo) ?? 'que no se ve';
      return {
        code: codigo,
        message: `La fecha (${f}) esta en el futuro. Casi siempre la IA corto el mes o el anio `
          + 'al leer. Corrige a la fecha real.',
      };
    }

    case 'date_too_old': {
      const f = valorDe(codigo) ?? 'que no se ve';
      return {
        code: codigo,
        message: `La fecha (${f}) es de mas de 3 anios. Revisa que sea la fecha del ticket y no `
          + 'otra del papel.',
      };
    }

    default:
      // El backfill de la migracion escribe texto libre, no codigos.
      if (nombre.startsWith('backfill')) {
        return {
          code: codigo,
          message: 'Este ticket es anterior al gate de confianza: nunca se valido de forma '
            + 'automatica. Revisalo desde cero contra el documento.',
        };
      }
      /* Los fallos del modelo viajan como FRASE en espanol, no como codigo.
       *
       * `capture.motivo_de_fallo_del_modelo` devuelve `FALLOS_DEL_MODELO[clave]`,
       * o sea el TEXTO, no la clave. Escrito a proposito: "el proveedor no se
       * pudo leer" y "el extractor esta apagado" producen el mismo ticket, sin
       * proveedor y en la cola, pero piden cosas opuestas -al primero se le busca
       * otro escalon de lectura, al segundo se le enciende el extractor. Si los
       * dos se reportan igual, el segundo se manifiesta como "muchos tickets
       * raros" y nadie lo reconoce como una configuracion apagada.
       *
       * El coste es que esta pantalla traduce DOS vocabularios: codigos para los
       * checks del gate y frases para los fallos del modelo. Los tres de abajo
       * caian en `sinConocer`, que le decia al contador "Motivo no reconocido por
       * esta version de la pantalla" sobre cosas que el backend si emite y que el
       * contador si puede arreglar.
       *
       * Se comparan por FRAGMENTO y no contra la frase entera a proposito: la
       * redaccion del backend puede cambiar sin que esta pantalla se entere, y
       * un texto que no casa es un "no reconocido" que no ayuda a nadie.
       *
       * El `default` de mas abajo NO se quita. Sigue siendo lo correcto para lo
       * desconocido de verdad, y por eso estos tres se nombran en vez de dejar
       * que el default los cubra. */
      if (codigo.includes('no se pudo decodificar')) {
        return {
          code: codigo,
          message: 'El archivo no se pudo abrir como imagen. Puede ser un HEIC que necesita '
            + 'convertirse, un escaneo danado, o un formato que el sistema no sabe leer. '
            + 'Abre el comprobante y vuelve a subirlo como PDF o como foto.',
        };
      }
      if (codigo.includes('no se pudo interpretar')) {
        return {
          code: codigo,
          message: 'La IA leyo el documento pero su respuesta no se pudo interpretar. '
            + 'Suele ser el modelo apagado o sin descargar. Revisa el documento y, si '
            + 'tienes los datos, escribelos a mano.',
        };
      }
      if (codigo.includes('apagado') || codigo.includes('no respondio')) {
        return {
          code: codigo,
          message: 'El extractor de IA esta apagado o no respondio, asi que el documento '
            + 'no se leyo. Es una configuracion, no un problema del comprobante: enciendela '
            + 'y vuelve a subirlo.',
        };
      }

      return sinConocer(codigo);
  }
}

/**
 * Convierte el texto crudo de `validation_errors` en problemas entendibles.
 *
 * `null` o vacio significan "sin problemas registrados", que es un estado
 * legitimo: un ticket AUTO_APROBADO no guarda fallos.
 */
export function explainValidationErrors(raw: string | null | undefined): ValidationProblem[] {
  if (!raw) return [];
  return raw
    .split(';')
    .map(p => p.trim())
    .filter(Boolean)
    .map(explicar);
}

/**
 * Como se muestra la confianza.
 *
 * La captura manual devuelve `null` a proposito: no hay confianza que medir y
 * un numero inventado falsea las metricas de exactitud. Mostrar "0%" seria
 * mentir en la direccion opuesta: sugeriria que el sistema midi y fallo.
 */
export function confidenceLabel(
  confidence: string | null,
  source: string | null,
): { texto: string; help: string } {
  if (confidence === null || confidence === '') {
    if (source === 'manual') {
      return { texto: 'Manual', help: 'Datos tecleados por una persona. No hay confianza que medir.' };
    }
    return {
      texto: 'Sin medir',
      help: 'El extractor no informo confianza. Se trato como revision, no como aprobacion.',
    };
  }
  const n = Number(confidence);
  if (!Number.isFinite(n)) {
    return { texto: '—', help: 'La confianza guardada no es un numero legible.' };
  }
  const pct = (n * 100).toFixed(1);
  return {
    texto: `${pct}%`,
    help: `Confianza informada por el extractor (${source ?? 'sin fuente'}).`,
  };
}

const FUENTES: Record<string, string> = {
  manual: 'Teclado',
  llm: 'IA (vision)',
  rules: 'Reglas',
  llm_validated: 'IA + checks',
  pdf_text: 'Texto del PDF',
};

export function sourceLabel(source: string | null): string {
  if (!source) return 'Desconocido';
  return FUENTES[source] ?? source;
}
