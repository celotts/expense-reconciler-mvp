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

/** Que clase de archivo DEBERIA ser, por su extension.
 *
 *  MEDIDO el 2026-10-12: el aviso de "el archivo se llama X pero sus bytes son
 *  Y" salia en los **8 de 8** archivos. La comparacion era
 *  `'.jpeg'.includes('image')`, que es `false` SIEMPRE: una extension y una
 *  categoria de formato no son comparables, asi que el aviso no detectaba
 *  discrepancias: las notificaba todas.
 *
 *  Lo que si es una discrepancia: un archivo llamado `.pdf` cuyos bytes son una
 *  imagen, o uno llamado `.heic` que en realidad es un PDF. Ahi el nombre y el
 *  contenido dicen cosas distintas, y el sistema leyo bien: por los bytes.
 *  Eso es lo que este mapa permite distinguir.
 *
 *  Devuelve `null` para una extension que no está en la lista, y quien llama lo
 *  trata como "no hay opinion": es preferible no avisar a avisar de todo. */
const CATEGORIA_POR_EXTENSION: Record<string, string> = {
  // Imagenes. HEIC y HEIF entran porque el producto se define por la foto del
  // telefono; `.jpg2` y `.tif` por el mismo motivo.
  jpg: 'image', jpeg: 'image', png: 'image', heic: 'image', heif: 'image',
  webp: 'image', gif: 'image', bmp: 'image', tif: 'image', tiff: 'image',
  jpg2: 'image',
  pdf: 'pdf',
  txt: 'text', csv: 'text', text: 'text', log: 'text',
};

/** ¿El nombre del archivo contradice lo que dicen sus bytes?
 *
 *  `true` SOLO cuando hay una discrepancia real: la extension pertenece a una
 *  categoria y los bytes son de OTRA. Cuando la extension no esta en el mapa se
 *  devuelve `false`, porque ahi no hay nada que comparar y un aviso sin
 *  fundamento entrena a ignorar los avisos.
 *
 *  Ojo con la asimetria que se acepto a proposito: `IMG_4253 2.HEIC` esta
 *  guardado y **por sus bytes es un JPEG** — la camara del telefono lo nombro
 *  mal. El sistema lo lee como lo que es y eso es lo correcto, pero el nombre
 *  sigue diciendo HEIC y el usuario lo ve en su carpeta. Por eso el aviso
 *  compara CATEGORIAS y no formatos exactos: un HEIC que es JPEG sigue siendo
 *  una imagen, y ahi no hay nada que avisar. Lo que se avisa es un PDF
 *  disfrazado de foto, que es lo que si confunde a un operador. */
export function hayDiscrepanciaDeFormato(
  declaredExtension: string | null | undefined,
  detectedFormat: string | null | undefined,
): boolean {
  if (!declaredExtension || !detectedFormat) return false;

  const ext = declaredExtension.replace(/^\./, '').trim().toLowerCase();
  if (!ext) return false;

  const declarada = CATEGORIA_POR_EXTENSION[ext];
  if (!declarada) return false;

  return declarada !== detectedFormat;
}

/** La categoria que el nombre sugiere, en palabras. Para el mensaje. */
export function categoriaDeExtension(extension: string | null | undefined): string | null {
  if (!extension) return null;
  const ext = extension.replace(/^\./, '').trim().toLowerCase();
  return CATEGORIA_POR_EXTENSION[ext] ?? null;
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
