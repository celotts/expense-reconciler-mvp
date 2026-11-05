export interface ResumenPeriodo {
  desde: string;
  hasta: string;
  monto: string;
  monto_anterior: string;
  tickets: number;
  tickets_anterior: number;
  /** `null` cuando el mes anterior no tiene gasto: no hay contra que comparar. */
  variacion_pct: number | null;
  delta_absoluto: string;
  etiqueta_anterior: string;
  /** El nombre COMPLETO del mes anterior, para el texto. "ago" en un eje no da
   *  problema, pero "de ago" en una frase se lee "de hace". */
  nombre_anterior: string;
  /** El mes va a medias. Cambia como se lee el porcentaje de arriba. */
  dias_transcurridos: number;
  dias_del_mes: number;
  mes_en_curso: boolean;
  monto_anterior_a_la_fecha: string;
  variacion_pct_a_la_fecha: number | null;
}

export interface Categoria {
  clave: string;
  etiqueta: string;
  /** El gasto se mueve con la actividad, o es un gasto fijo. */
  variable: boolean;
}

export interface CategoriaGasto {
  clave: string;
  etiqueta: string;
  monto: string;
  tickets: number;
  porcentaje: number;
  /** El gasto se mueve con la actividad, o es un gasto fijo. */
  variable: boolean;
  sin_clasificar: boolean;
}

export interface Hallazgo {
  tipo: string;
  titulo: string;
  detalle: string;
  tono: string;
}

export interface MesGasto {
  anio: number;
  mes: number;
  etiqueta: string;
  nombre: string;
  monto: string;
  tickets: number;
}

export interface ProveedorGasto {
  proveedor: string;
  monto: string;
  tickets: number;
}

export interface EstadoBanco {
  total: number;
  conciliados: number;
  sin_conciliar: number;
  monto_total: string;
  porcentaje: number;
}

/** Reexportado de `api.ts` para que este archivo se pueda leer solo. La fuente
 *  de la verdad es ahi, junto al resto del muestreo; duplicar el tipo seria
 *  tener dos definiciones que pueden divergir sin que nada lo note. */
export type { Veredicto } from './api';
import type { Veredicto } from './api';

export interface ExactitudPorOrigenDashboard {
  origen: string;
  revisados: number;
  aciertos: number;
  incorrectos: number;
  pendientes: number;
  exactitud: number | null;
  intervalo_inferior: number | null;
  intervalo_superior: number | null;
  veredicto: Veredicto;
  motivo_faltante: string;
  total_revisiones_necesarias: number | null;
  campo_mas_fallido: string | null;
}

export interface SinClasificar {
  /** En TODO el historico, no solo el mes. Es el trabajo pendiente real. */
  tickets: number;
  monto: string;
}

export interface MesConCategorias {
  anio: number;
  mes: number;
  etiqueta: string;
  nombre: string;
  monto: string;
  tickets: number;
  por_categoria: CategoriaGasto[];
}

export interface DashboardResponse {
  company_id: string | null;
  company_name: string | null;
  mes: ResumenPeriodo;
  por_categoria: CategoriaGasto[];
  comparativo: MesConCategorias[];
  tendencia: MesGasto[];
  /** Lo que el servidor concludes de la serie. Vacio o con
   *  `datos_insuficientes` significa "no hay nada que decir", y es una
   *  respuesta valida: una conclusion inventada es peor que ninguna. */
  hallazgos: Hallazgo[];
  top_proveedores: ProveedorGasto[];
  por_estado: Record<string, number>;
  por_estado_conciliacion: Record<string, number>;
  banco: EstadoBanco;
  cola_revision: Record<string, number>;
  sin_clasificar: SinClasificar;
  /** Hay al menos un ticket o un movimiento. Si no, la pantalla va vacia de verdad. */
  hay_datos: boolean;
  exactitud: {
    objetivo: number;
    veredicto_global: Veredicto;
    explicacion: string;
    por_origen: ExactitudPorOrigenDashboard[];
  } | null;
  total_historico: string;
  tickets_historico: number;
}
