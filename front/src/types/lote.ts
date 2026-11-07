/** Las formas de los lotes de digitalización.
 *
 *  Viven aquí y no en `types/api.ts` por la misma razón que las del escáner:
 *  un tipo que describe una respuesta vieja hace que un cambio en el servidor
 *  parezca un bug en el cliente. Si el backend cambia una forma, este archivo
 *  se actualiza en el mismo commit. Espejo de `app/schemas/lote.py`.
 *
 *  Los importes son `string` y no `number`, como en el resto del front: el
 *  backend los serializa desde `Decimal` y un `number` de JS ya perdió los
 *  centavos antes de llegar aquí.
 */

/** Los tres estados de un lote. Un lote CERRADO no acepta más archivos: el
 *  servidor lo comprueba antes de asignar (`app/api/lotes.py`) y responde 409.
 *  Los faltantes de un lote cerrado van en un lote complementario, no
 *  reabriendo a ciegas. */
export type LoteEstado = 'ABIERTO' | 'EN_REVISION' | 'CERRADO';

/** El periodo que cubre el lote. Sin `fecha_inicio`/`fecha_fin`, el servidor
 *  lo deduce del día 1 de (`ejercicio`, `mes`): el mes entero en mensual. */
export type TipoPeriodo = 'mensual' | 'quincenal' | 'semanal';

/** Un lote, como lo ve la pantalla. Espejo de `LoteResponse`. */
export interface Lote {
  id: string;
  company_id: string;
  /** Lo genera el servidor, secuencial por empresa y mes: `LOT-2026-10-001`.
   *  Nunca se manda en la creación: aceptar uno del cliente dejaría que dos
   *  lotes nacieran con el mismo. */
  folio: string;
  nombre: string;
  ejercicio: number;
  mes: number;
  tipo_periodo: TipoPeriodo;
  /** Fechas ISO (`2026-10-01`). */
  fecha_inicio: string;
  fecha_fin: string;
  estado: LoteEstado;
  /** Cuántos comprobantes se esperan. `null` si la empresa no lo exige. */
  total_esperado: number | null;
  /** Cuántos lleva escaneados. Es un CACHÉ que mantiene el servicio de
   *  escaneo; la cuenta en vivo está en `LoteDetalle`. */
  total_escaneado: number;
  importe_total: string;
  /** El lote del que este es complemento, o `null` si no lo es. */
  lote_padre_id: string | null;
  /** Por qué se reabrió (o de qué es complemento), o `null`. */
  motivo: string | null;
  creado_por: string | null;
  created_at: string;
  cerrado_at: string | null;
}

/** Un lote con sus conteos en vivo. Espejo de `LoteDetalleResponse`.
 *
 *  Se calculan desde `tickets` y `scan_files` en cada lectura, no desde
 *  `total_escaneado` / `importe_total` del lote. Si discrepan, manda esto. */
export interface LoteDetalle extends Lote {
  total_archivos: number;
  total_tickets: number;
  importe_total_vivo: string;
  /** Tickets de más por grupo (proveedor + total + fecha): dos iguales son 1
   *  duplicado, tres son 2. La misma regla que `duplicados_por_contenido`. */
  duplicados: number;
}

/** Alta de un lote. Espejo de `LoteCreate`.
 *
 *  `fecha_inicio` y `fecha_fin` van juntas o no van: una sola fecha es un
 *  periodo a medias y el servidor responde 422. */
export interface LoteCreate {
  company_id: string;
  nombre: string;
  ejercicio: number;
  mes: number;
  tipo_periodo?: TipoPeriodo;
  fecha_inicio?: string;
  fecha_fin?: string;
  total_esperado?: number;
}
