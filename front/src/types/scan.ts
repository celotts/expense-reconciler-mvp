/** Las formas del escaner de carpeta.
 *
 *  Viven aqui y no en `types/api.ts` por la misma razon que las del dashboard:
 *  un tipo que describe una respuesta vieja hace que un cambio en el servidor
 *  parezca un bug en el cliente. Si el backend cambia una forma, este archivo
 *  se actualiza en el mismo commit.
 *
 *  Cada tipo lleva el comentario de POR QUE el campo existe cuando no es
 *  obvio. El caso claro es `status`: dice como esta el ARCHIVO, no como esta
 *  el ticket, y confundirlos hace que una pantalla prometa mas de lo que sabe.
 */

/** El estado del ARCHIVO, que no es el del ticket.
 *
 *  Los nombres se parecen a los de `ExtractionStatus` y no tienen nada que ver:
 *  uno juzga COMO SE LEYO EL PAPEL y el otro SI CUADRA CON EL BANCO. Aqui esta
 *  el tercero, que juzga QUE SE HIZO CON EL ARCHIVO DE LA CARPETA.
 *
 *  Vive aqui y no en `api.ts` porque los dos son de dominios distintos, y
 *  mezclarlos en una sola lista es lo que hace que alguien escriba
 *  `status === 'REQUIERE_REVISION` sobre un archivo y espere una cola de
 *  revision que no existe. */
export type ScanStatus =
  | 'PENDIENTE'
  | 'PROCESADO'
  | 'DUPLICADO'
  | 'ERROR'
  | 'NO_SOPORTADO';

/** Un archivo de la carpeta, tal como esta en el registro.
 *
 *  `relative_path` y no la ruta absoluta: la absoluta es `TICKETS_INPUT_DIR` mas
 *  esta, y quien mira ya sabe cual es la carpeta. La API no la manda a proposito
 *  (ver `docs/known-issues.md`), asi que el front no debe esperar de mas. */
export interface ScanFile {
  id: string;
  relative_path: string;
  content_hash: string;
  file_size: number | null;
  file_mtime: string | null;
  /** Lo que dicen los BYTES, no la extension. Un `ticket.pdf` puede ser `image`. */
  detected_format: string | null;
  declared_extension: string | null;
  status: ScanStatus;
  attempts: number;
  /** Que escalon lo leyo: `ocr`, `llm`, `pdf_text`, `rules`. */
  read_by: string | null;
  /** Por que no se pudo LEER el archivo. No es lo mismo que los checks del gate. */
  last_error: string | null;
  ticket_id: string | null;
  company_id: string | null;
  first_seen_at: string;
  last_scanned_at: string | null;
  processed_at: string | null;
}

/** Un cambio de estado en el historico del archivo.
 *
 *  Es la linea de tiempo: `attempts` dice cuantas veces se intento, no en que
 *  orden. La pantalla de detalle muestra esto porque "por que este ticket se
 *  leyo ayer con 0.93 y hoy tiene tres intentos" no lo contesta un contador. */
export interface ScanEvent {
  id: string;
  action: string;
  detail: string | null;
  /** Quien lo disparo: el correo del token, o `sistema` para el automatico. */
  actor: string | null;
  confidence: string | null;
  created_at: string;
}

export interface ScanFileDetail extends ScanFile {
  events: ScanEvent[];
}

export interface ScanFilePage {
  total: number;
  limit: number;
  offset: number;
  archivos: ScanFile[];
}

/** Que le paso a UN archivo en la corrida.
 *
 *  `accion` es lo que OCURRIO y `status` en que QUEDO. No son lo mismo: un
 *  archivo queda `PROCESADO` y en la corrida no se hizo nada porque su
 *  contenido no cambio. Por eso la pantalla muestra los dos. */
export interface ScanItem {
  relative_path: string;
  scan_file_id: string | null;
  status: ScanStatus;
  accion: string;
  ticket_id: string | null;
  confianza: number | null;
  origen: string | null;
  detalle: string | null;

  /** Que le paso AL ARCHIVO en disco, que es distinto de lo que le paso al ticket. */
  /** `MOVIDO` = esta en otra carpeta. `RETIRADO` = salio de la entrada porque sus
   *  bytes ya estaban guardados con el mismo sha256. `NADA` = sigue en la bandeja. */
  retiro?: string;
  /** El por que. Sin esto, un `RETIRADO` parece una perdida de datos. */
  motivo_retiro?: string | null;
  /** Los bytes estan en `ticket_documents` y el sha256 coincide.
   *  `false` = se resolvio pero NO habia respaldo, y el archivo se quedo. */
  respaldo_verificado?: boolean | null;
  /** Donde se recupera el comprobante original. */
  recuperable_desde?: string | null;
}

/** Un archivo que el escaner ya termino de leer, con lo que leyo de el. */
export interface ScanProcesado {
  relative_path: string;
  ticket_id: string | null;
  /** Texto y no numero: es un `Decimal` de la API, y un `number` de JS ya perdio
   *  los centavos antes de llegar aqui. */
  monto: string | null;
  extraction_status: string | null;
  motor: string | null;
  accion: string | null;
  /** Si el GATE se atrevio a afirmar el importe. NO es "el monto parece bien":
   *  con OCR al 33% casi nunca se afirma nada, y esa diferencia hay que
   *  ensenarla en la pantalla en vez de dejar que se suponga. */
  confiable: boolean;
  es_duplicado: boolean;
}

/** El estado de una corrida mientras corre, y despues de terminar. */
export interface ScanCorrida {
  id: string;
  carpeta: string;
  actor: string | null;
  iniciada_at: string;
  terminada_at: string | null;
  terminada: boolean;
  simulado: boolean;
  archivos_vistos: number;
  con_ticket: number;
  con_error: number;
  /** El archivo que se esta leyendo AHORA. */
  actual: string | null;
  /** Los ultimos archivos leidos, con su importe. Acotado en el servidor. */
  procesados?: ScanProcesado[];
  segundos: number;
  /** El total. `null` mientras corre: un resumen a medias parece el de una
   *  corrida corta. */
  resumen?: Record<string, unknown> | null;
}

export interface ScanResultado {
  carpeta: string;
  archivos_vistos: number;
  nuevos: number;
  actualizados: number;
  sin_cambios: number;
  duplicados: number;
  con_error: number;
  no_soportados: number;
  /** Lo que no se alcanzo a procesar por `TICKETS_SCAN_MAX_ARCHIVOS`. */
  omitidos_por_tope: number;
  leidos: number;
  lecturas_por_motor: Record<string, number>;
  detalles: ScanItem[];
}

export interface ScanRequest {
  /** Omitirlo inventaria los archivos y NO crea tickets. */
  company_id?: string | null;
  reprocesar?: boolean;
  solo_pendientes?: boolean;
}

export interface ScanStats {
  total_archivos: number;
  por_estado: Record<string, number>;
  /** Por motor de lectura. Separa "lo leyo el modelo" de "lo leyo Tesseract". */
  por_motor: Record<string, number>;
  intentos_totales: number;
  archivos_con_error: number;
  /** PROCESADO pero con el archivo cambiado y un ticket ya corregido a mano. */
  archivos_desactualizados: number;
  tickets_vinculados: number;
  archivos_sin_ticket: number;
  processed_at_mas_reciente: string | null;
}

export interface ScanConfig {
  carpeta: string;
  carpeta_existe: boolean;
  recursivo: boolean;
  max_por_corrida: number;
  max_bytes_por_archivo: number;
}

/** Como esta el OCR en ESTA maquina.
 *
 *  Existe porque "OCR no funciona" y "OCR esta apagado" piden acciones
 *  opuestas, y un booleano las hace la misma. El backend lo dice con el motivo
 *  exacto, incluido como instalarlo. */
export interface ScanOcrEstado {
  ocr_habilitado: boolean;
  idioma: string;
  motores: Record<string, { disponible: boolean; motivo: string | null }>;
}
