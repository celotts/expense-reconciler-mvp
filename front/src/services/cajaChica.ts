/** Cliente de caja chica y duplicados fiscales.
 *
 *  El `company_id` NO se elige aqui. `fetchApi` manda la empresa activa en la
 *  cabecera `X-Company-ID` y el servidor la comprueba contra las membresias de
 *  la BD. Dejar un parametro `companyId` en cada llamada permite que una
 *  pantalla pida una empresa y el header diga otra: el servidor responde 409 y
 *  el operador ve un error que no entiende.
 *
 *  Los importes son texto: son `Decimal` en el servidor y el navegador solo los
 *  formatea. Sumarlos aqui introduciria centavos de `float` en un saldo que el
 *  backend ya calculo con `ROUND_HALF_UP`.
 */
import { fetchApi } from './api';
import type {
  BuscarDuplicadoRequest,
  CajaChicaConMovimientos,
  CajaChicaCreate,
  CajaChicaResponse,
  CajaChicaUpdate,
  CierreCajaRequest,
  DuplicadoFiscalResponse,
  MovimientoCajaCreate,
  MovimientoCajaResponse,
  ResumenDuplicados,
  OrigenFondo,
} from './api';

/** La caja que devuelve el backend. Alias con el nombre del dominio: el sufijo
 *  `Response` sigue siendo el tipo del contrato HTTP. */
export type CajaChica = CajaChicaResponse;
export type MovimientoCaja = MovimientoCajaResponse;

/** Una reposicion solo viene de efectivo o de banco, y las dos cosas se
 *  preguntan en el formulario. No es el `OrigenFondo` de cualquier movimiento:
 *  un gasto con ticket no trae origen. */
export type OrigenFondoReposicion = Extract<OrigenFondo, 'efectivo' | 'banco'>;

/** `banco_nombre` existe solo en la variante de banco. Con `efectivo` el tipo
 *  prohíbe escribirlo: un nombre de banco en una reposicion en efectivo es un
 *  dato inventado que luego aparece en el kardex. */
export type ReposicionCajaPayload = {
  company_id: string;
  tipo: 'reposicion';
  monto: string;
  notas?: string;
} & (
  | { origen_fondo: 'banco'; banco_nombre: string; referencia_bancaria?: string }
  | { origen_fondo: 'efectivo'; banco_nombre?: never; referencia_bancaria?: never }
);

/** Lo que el servidor afirma sobre el dinero ya conciliado. El front no suma
 *  `grupos`: un reenvio saldria dos veces y un ticket pendiente contaria como
 *  gasto confirmado. */
export interface ConciliacionResumen {
  company_id: string;
  grupos: ResumenDuplicados['grupos'];
  total_conciliado_sin_duplicados: string;
  advertencia: string | null;
}

export const cajaChicaApi = {
  list: () => fetchApi<CajaChica[]>('/caja-chica/'),

  get: (cajaId: string) => fetchApi<CajaChicaConMovimientos>(`/caja-chica/${cajaId}`),

  create: (data: CajaChicaCreate) => fetchApi<CajaChica>('/caja-chica/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  update: (cajaId: string, data: CajaChicaUpdate) => fetchApi<CajaChica>(`/caja-chica/${cajaId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),

  movimientos: (cajaId: string) => fetchApi<MovimientoCaja[]>(`/caja-chica/${cajaId}/movimientos`),

  registrarMovimiento: (cajaId: string, data: MovimientoCajaCreate | ReposicionCajaPayload) =>
    fetchApi<MovimientoCaja>(`/caja-chica/${cajaId}/movimientos`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  enRevision: (cajaId: string) => fetchApi<CajaChica>(`/caja-chica/${cajaId}/en-revision`, {
    method: 'POST',
  }),

  reabrir: (cajaId: string) => fetchApi<CajaChica>(`/caja-chica/${cajaId}/reabrir`, {
    method: 'POST',
  }),

  /** Sin `closed_at` el backend estampa la hora; no la inventa el navegador. */
  cerrar: (cajaId: string, data?: CierreCajaRequest) => fetchApi<CajaChica>(`/caja-chica/${cajaId}/cerrar`, {
    method: 'POST',
    body: data ? JSON.stringify(data) : undefined,
  }),
};

export const duplicadosApi = {
  buscar: (data: BuscarDuplicadoRequest) => fetchApi<DuplicadoFiscalResponse>('/duplicados/buscar', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  resumen: () => fetchApi<ResumenDuplicados>('/duplicados/resumen'),
};
