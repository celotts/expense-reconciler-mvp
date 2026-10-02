/**
 * Traduccion del escaner de carpeta a lenguaje humano.
 *
 * Reglas de este archivo, las mismas del muestreo y por las mismas razones:
 *
 * 1. Un estado que no conozco se muestra crudo, no se esconde. Si el backend
 *    agrega un estado a `ScanStatus` y esta tabla no lo tiene, la pantalla tiene
 *    que decir el texto, no mentir con un color.
 *
 * 2. `null` no es `0`. Un archivo sin `confidence` no tiene confianza, que es
 *    distinto de tener cero.
 *
 * 3. Un estado NO dice si el TICKET esta bien. `PROCESADO` quiere decir que el
 *    archivo se leyo; el ticket que salio puede estar en la cola de revision
 *    porque el papel no daba para cerrarlo. Son dos maquinas de estado y la
 *    pantalla no debe mezclarlas.
 */

import type { ScanStatus } from '../types/scan';
import type { BadgeVariant } from './extraction';

export interface ScanStatusMeta {
  label: string;
  variant: BadgeVariant;
  /** Que significa y que se puede hacer. Para el title del badge. */
  help: string;
}

export const SCAN_STATUS_META: Record<ScanStatus, ScanStatusMeta> = {
  PENDIENTE: {
    label: 'Pendiente',
    variant: 'default',
    help: 'Visto en la carpeta y todavia sin leer.',
  },
  PROCESADO: {
    label: 'Procesado',
    variant: 'success',
    help: 'Se leyo y hay un ticket ligado. No significa que el ticket este '
      + 'cerrado: puede seguir en la cola de revision si el papel no daba para '
      + 'auto-aprobarlo.',
  },
  DUPLICADO: {
    label: 'Duplicado',
    variant: 'info',
    help: 'Los mismos bytes que otro archivo ya procesado. Se conserva el ticket '
      + 'original y este no se vuelve a leer.',
  },
  ERROR: {
    label: 'Error',
    variant: 'danger',
    help: 'Se intento leer y no se pudo. El motivo esta en el detalle del archivo.',
  },
  NO_SOPORTADO: {
    label: 'No soportado',
    variant: 'warning',
    help: 'El archivo no es un PDF ni una imagen con firma conocida. No es un '
      + 'comprobante que se haya leido mal: es algo que nunca fue candidato.',
  },
};

/** Que escalon leyo el archivo, en palabras.
 *
 *  El nombre del motor importa aqui mas que en ninguna otra pantalla, porque
 *  es lo que separa "el modelo leyo esto" de "Tesseract leyo esto y la foto
 *  estaba bien". Un ticket de OCR que se guarda como `llm` hace que el reporte
 *  de exactitud atribuya al modelo algo que el modelo no leyo. */
export function motorLabel(motor: string | null): string {
  if (!motor) return 'sin leer';
  switch (motor) {
    case 'ocr': return 'OCR local';
    case 'llm': return 'modelo de vision';
    case 'pdf_text': return 'texto del PDF';
    case 'rules': return 'reglas';
    case 'llm_validated': return 'modelo validado';
    case 'manual': return 'a mano';
    default: return motor;
  }
}

/** Que paso en la corrida, en palabras.
 *
 *  `accion` es lo que OCURRIO (creado, sin cambios, omitido) y `status` es en
 *  que QUEDO el archivo. No son lo mismo: un archivo puede quedar `PROCESADO`
 *  y que en la corrida no se haya hecho nada porque su contenido no cambio. */
export function accionLabel(accion: string): string {
  switch (accion) {
    case 'CREADO': return 'creado';
    case 'ACTUALIZADO': return 'actualizado';
    case 'SIN_CAMBIOS': return 'sin cambios';
    case 'OMITIDO': return 'omitido';
    case 'ERROR': return 'error';
    default: return accion;
  }
}

/** Los colores del resumen, por estado del archivo.
 *
 *  `DESACTUALIZADO` no es un estado del backend: son los archivos `PROCESADO`
 *  con un aviso en `last_error`, que el backend cuenta aparte. Se pinta aparte
 *  porque `PROCESADO` a secas se leeria como "todo al dia" y estos no lo estan:
 *  su archivo cambio y el ticket ya tiene correcciones de una persona. */
export const COLOR_ESTADO: Record<ScanStatus, string> = {
  PENDIENTE: 'bg-gray-100 text-gray-700',
  PROCESADO: 'bg-green-100 text-green-700',
  DUPLICADO: 'bg-blue-100 text-blue-700',
  ERROR: 'bg-red-100 text-red-700',
  NO_SOPORTADO: 'bg-yellow-100 text-yellow-700',
};
