// API Types matching backend schemas

// ---------------------------------------------------------------------------
// Sesion
// ---------------------------------------------------------------------------
//
// El `nombre` y el `is_active` vienen del servidor y no se adivinan: el nombre
// que se pinta arriba sale de aqui, y una cuenta dada de baja tiene que dejar de
// poder entrar aunque su token siga siendo criptograficamente valido.

export interface Usuario {
  id: string;
  email: string;
  nombre: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

/** Lo que devuelve `POST /auth/login`.
 *
 *  `expires_in` son SEGUNDOS, no una fecha. El servidor lo manda asi a proposito
 *  para que el cliente sume y sepa cuando va a pasar, en vez de interpretar una
 *  fecha absoluta con la zona horaria del navegador. */
export interface RespuestaToken {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: Usuario;
}

export interface Company {
  id: string;
  name: string;
  tax_id: string;
  created_at: string;
}

export interface CompanyCreate {
  name: string;
  tax_id: string;
}

export interface CompanyUpdate {
  name?: string;
  tax_id?: string;
}

// Estados del ciclo de vida de un ticket. Deben coincidir con
// app/core/enums.py::ExtractionStatus. Si divergen, la UI muestra estados que
// el backend nunca produce y la cola aparece vacia sin error visible.
export type ExtractionStatus =
  | 'AUTO_APROBADO'
  | 'REQUIERE_REVISION'
  | 'PENDIENTE'
  | 'APROBADO'
  | 'RECHAZADO';

export type ConfidenceSource = 'manual' | 'llm' | 'rules' | 'llm_validated' | 'pdf_text';

export type SourceType = 'manual' | 'pdf' | 'image' | 'directory' | 'bulk' | 'camera';

export interface Ticket {
  id: string;
  company_id: string;
  provider_name: string;
  provider_tax_id: string | null;
  total_amount: string;
  tax_amount: string;
  /** Antes no se guardaba. Va en la respuesta porque el muestreo lo necesita:
   *  sin el, la pregunta "¿el subtotal se leyó bien?" no tiene con qué
   *  responderse. `null` cuando el comprobante no lo trae, nunca "0.00": un
   *  cero haría que la cuenta `subtotal + IVA == total` pareciera cuadrar. */
  subtotal: string | null;
  /** El IEPS, SEPARADO del IVA. Hace falta porque `tax_amount` se exporta bajo el
   *  encabezado "IVA" del archivo que ve el contador: meter los dos impuestos ahi
   *  le entregaria un IVA que el comprobante no tiene. Sin este campo, un ticket
   *  con IVA e IEPS se ve como `Subtotal 217.27 / IVA 8.14 / Total 234.00` y no
   *  hay donde poner los 8.59 que sobran. `null` nunca "0.00": un cero dira que
   *  se leyo el IEPS y era cero. */
  ieps_amount: string | null;
  expense_date: string;
  category: string | null;
  raw_text: string | null;
  /** Las lineas del comprobante, crudas. `null` NO es lo mismo que `[]`: `null`
   *  es "el lector no produjo lineas" (la ruta OCR no las extrae) y `[]` es
   *  "produjo lineas y no eran ninguna". Sin la distincion no se puede saber si
   *  hay que volver a mirar el papel o no. */
  items: LineaTicketUpdate[] | null;
  /** `null` es real, no un descuido: la columna admite NULL y un ticket sin
   *  fecha de creacion tiene que poder existir, porque es justamente el que
   *  mas importa ver (no se puede calcular su antiguedad). Pintar `null` como
   *  fecha da "Invalid Date", que se lee como un dato y no como una falta, asi
   *  que quien lo use tiene que decidir que mostrar, no confiar en el tipo. */
  created_at: string | null;
  // Trazabilidad de la extraccion. `confidence` es null en captura manual: no
  // hay confianza que medir, y un 0.000 contaminaria el promedio de la IA.
  confidence: string | null;
  confidence_source: ConfidenceSource | string | null;
  extraction_status: ExtractionStatus;
  source_type: SourceType | string | null;
  source_file: string | null;
  /** SHA-256 de los bytes del comprobante, o null en captura manual.
   *
   *  Es lo que hace idempotente la carga (el mismo archivo dos veces devuelve el
   *  mismo ticket) y lo que permite verificar que el documento guardado es
   *  exactamente ese archivo.
   *
   *  La UI lo usa para una cosa concreta: explicar por que un archivo salio de
   *  la carpeta de entrada. El escaner borra un comprobante solo cuando sus
   *  bytes estan guardados con el mismo hash, y sin este campo el operador ve
   *  "se borro" sin poder ver que no se perdio nada.
   */
  source_hash: string | null;
  /** Si el comprobante original quedo guardado, y donde ir a verlo.
   *
   *  Sin esto, la cola de revision y el muestreo dicen "contrasta contra el
   *  documento original" sin que haya documento: el sistema lo leia y lo
   *  tiraba. Con esto, la pregunta del muestreo ("¿coincidio con el papel?")
   *  tiene con que responderse.
   *
   *  `tiene_documento: false` NO es un fallo del sistema. Es lo normal en
   *  captura manual, que no viene de un archivo, y tambien lo que queda de una
   *  captura anterior a que existiera el almacenamiento. Son dos causas que
   *  piden cosas distintas y ninguna es "el sistema esta roto", asi que la
   *  pantalla debe poder distinguirlas en vez de tratar `false` como error. */
  tiene_documento: boolean;
  /** Relativo y sin host a proposito: el front y la API pueden no estar en el
   *  mismo origen, y un host aqui seria una URL que funciona hoy y se rompe
   *  al primer proxy. `null` cuando `tiene_documento` es `false`. */
  documento_url: string | null;
  /** Bytes. Permite avisar el peso antes de descargar, que en un celular con
   *  datos es la diferencia entre abrir el comprobante y no abrirlo. */
  documento_tamano: number | null;
  validation_errors: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
}

export interface TicketReviewQueue {
  company_id: string | null;
  total_open: number;
  /** Conteos globales por estado. No cambian al filtrar, para que el badge no salte. */
  por_estado: Partial<Record<ExtractionStatus, number>>;
  antiguedad_promedio_dias: number | null;
  tickets: Ticket[];
}

export interface TicketReviewRequest {
  action: 'approve' | 'reject';
  provider_name?: string;
  provider_tax_id?: string;
  total_amount?: string;
  tax_amount?: string;
  /** El subtotal y el IEPS son lo que hace aprobable un comprobante con IVA+IEPS:
   *  sin ellos, el gate corre `subtotal + IVA + IEPS == total` sobre numeros que
   *  la persona no puede escribir y el approve falla siempre. */
  subtotal?: string;
  ieps_amount?: string;
  expense_date?: string;
  notes?: string;
}

// ---------------------------------------------------------------------------
// Muestreo de exactitud
// ---------------------------------------------------------------------------
//
// El veredicto de una revision de muestreo NO corrige el ticket. Solo dice si
// la extraccion coincidia con el papel. Si la muestra pudiera cambiar un total,
// la exactitud medida dependeria de quien reviso, y dejaria de medir el
// automatismo: estariamos midiendo a la persona.

export type SpotCheckStatus = 'PENDIENTE' | 'CORRECTO' | 'INCORRECTO';

/** Los unicos campos que se pueden marcar como mal leidos.
 *
 *  `category` NO esta, a proposito. La categoria la pone el usuario cuando
 *  clasifica el gasto, no la lee la IA del papel: un papel no dice "esto es
 *  alimento". Marcarla aqui seria medir algo que el sistema no hizo. */
export type SpotCheckField =
  | 'provider_name'
  | 'provider_tax_id'
  | 'total_amount'
  | 'tax_amount'
  | 'expense_date'
  | 'subtotal';

export interface SpotCheckItem {
  ticket: Ticket;
  spot_check_status: SpotCheckStatus;
  spot_checked_at: string | null;
  spot_check_notes: string | null;
  spot_check_wrong_fields: SpotCheckField[];
}

export interface SpotCheckQueue {
  company_id: string | null;
  /** Conteo global, no recortado por el limite de la lista. Una cola de 200
   *  con `limit=50` tiene que decir 200, no 50. */
  total_pendientes: number;
  total_revisados: number;
  aciertos: number;
  incorrectos: number;
  antiguedad_promedio_dias: number | null;
  tickets: SpotCheckItem[];
}

export interface SpotCheckRequest {
  correct: boolean;
  campos_incorrectos?: SpotCheckField[];
  notes?: string;
}

/** Que se puede concluir con la evidencia reunida. Los cuatro se muestran con
 *  texto y color distintos: "sin evidencia" y "no cumple" no pueden verse
 *  igual, porque significan cosas opuestas. */
export type Veredicto = 'SIN_EVIDENCIA' | 'CUMPLE' | 'NO_CUMPLE' | 'INCONCLUYENTE';

export interface ExactitudPorOrigen {
  origen: string;
  revisados: number;
  aciertos: number;
  incorrectos: number;
  pendientes: number;
  /** `null` cuando nadie miró. No es un cero: no mirar no es fallar. */
  exactitud: number | null;
  intervalo_inferior: number | null;
  intervalo_superior: number | null;
  veredicto: Veredicto;
  /** Por qué no se puede afirmar aún, o qué falta para poder hacerlo. Viene
   *  como texto porque la acción depende de la razón. */
  motivo_faltante: string;
  total_revisiones_necesarias: number | null;
  campo_mas_fallido: string | null;
  conteo_por_campo: Record<string, number>;
}

export interface ReporteExactitud {
  company_id: string | null;
  objetivo: number;
  nivel_confianza: number;
  /** El PEOR veredicto por origen, no el promedio. Un sistema que falla en una
   *  vía de captura no cumple el objetivo aunque la otra sea perfecta. */
  veredicto_global: Veredicto;
  explicacion: string;
  por_origen: ExactitudPorOrigen[];
}

// ---------------------------------------------------------------------------
// Informe de cierre mensual — `GET /reports/cierre-mensual`
// ---------------------------------------------------------------------------
//
// Estos tipos son COPIA de `app/schemas/reporte.py`, no una interpretacion. La
// razon es que el preview y el PDF salen del MISMO objeto serializado
// (contrato §5.2): si el front reescribiera los campos a su manera, el contador
// veria dos numeros distintos en la misma pantalla.
//
// Cada importe es `string`, no `number`, y no es descuido: el backend los
// serializa desde `Decimal` y un `number` en JavaScript no representa 234.00 de
// forma exacta. `0.1 + 0.2 !== 0.3` en binario, y aqui la aritmetica decide si
// un periodo cuadra. Para sumar se usa `parsearMonto`.

/** El mes anterior, para comparar. */
export interface ComparativoMes {
  nombre_anterior: string;
  monto_anterior: string;
  tickets_anterior: number;
  diferencia: string | null;
  /** `number` y no `string` porque NO es un importe: es un porcentaje que el
   *  backend ya calculo como float. Solo se muestra. */
  diferencia_pct: number | null;
  /** `false` cuando el mes anterior esta en curso: comparar 6 dias contra 31
   *  produce una caida que no existe, y el backend lo dice con esto. */
  conclusivo: boolean;
  nota: string | null;
}

/** Una categoria del gasto.
 *
 *  Las claves son las que emite `analitica.por_categoria`: `clave`, `etiqueta`,
 *  `monto`, `tickets`, `porcentaje`, `variable`, `sin_clasificar`.
 *
 *  ## AVISO QUE ESTA EN EL CODIGO Y NO EN ESTE ARCHIVO
 *  El PDF lee `cat.get("categoria")` (reporte_cierre.py:720) y esta clave NO
 *  existe: por eso el PDF imprime "sin clasificar" en todas las categorias.
 *  Ver `docs/known-issues.md`. Aqui se lee `etiqueta`, que es la que si viene. */
export interface CategoriaGastoInforme {
  clave: string;
  etiqueta: string;
  monto: string;
  tickets: number;
  porcentaje: number;
  variable: boolean;
  sin_clasificar: boolean;
}

/** Un hallazgo del informe. `tono` es libre en el backend: `neutro` en el caso
 *  real. Se pinta tal cual y sin inventar una paleta que el backend no emite. */
export interface Hallazgo {
  tipo: string;
  titulo: string;
  detalle: string;
  tono: string;
}

export interface ResumenGasto {
  total: string;
  tickets: number;
  por_categoria: CategoriaGastoInforme[];
  comparativo: ComparativoMes | null;
}

export interface SeccionConciliacion {
  por_estado: Record<string, number>;
  conciliados: number;
  sin_conciliar: number;
  monto_discrepancias: string;
  /** Gastos de este periodo cuyo pago cae en otro mes. Son las partidas
   *  conciliatorias: la contabilidad (devengo) y el banco (pago) dicen meses
   *  distintos y las dos fechas son ciertas. No es un error de captura. */
  pagados_en_otro_periodo_tickets: number;
  pagados_en_otro_periodo_monto: string;
}

/** R5: los dos numeros van separados porque son afirmaciones de distinta fuerza.
 *  `leido_por_el_sistema` y `verificado_por_una_persona` jamas se suman ni se
 *  presentan como un solo "revisados". */
export interface SeccionLectura {
  leido_por_el_sistema: number;
  verificado_por_una_persona: number;
  pendiente_de_revision: number;
}

/** Conteos y NO porcentajes: un "% firmado" sin denominador es R1 incumplido. */
export interface SeccionVerificacionHumana {
  en_muestra: number;
  revisadas: number;
  firmadas: number;
  pendientes_de_firmar: number;
  revisores: string[];
  ultima_verificacion: string | null;
}

export interface SeccionPendientes {
  sin_categoria_tickets: number;
  sin_categoria_monto: string;
  sin_conciliar_tickets: number;
  sin_conciliar_monto: string;
  discrepancias_abiertas: number;
  discrepancias_monto: string;
  /** Gastos cuya `expense_date` NO vino del papel: es el dia de la captura, no el
   *  de la compra. No se suman al total del periodo ni a ninguna categoria, y por
   *  eso se reportan aparte. Hasta que alguien escriba la fecha mirando el
   *  comprobante no se sabe de que mes es este gasto. */
  sin_fecha_tickets: number;
  sin_fecha_monto: string;
  /** `@computed_field` en el backend: derivado de los cuatro de arriba. */
  hay_pendientes: boolean;
  /** Los motivos nombrados, no "hay pendientes". `null` cuando no hay ninguno. */
  motivo: string | null;
}

/** Los cuatro estados del periodo.
 *
 *  `CIERRE_DISPONIBLE` y `CERRADO_CON_PENDIENTES` son los que hacen que
 *  `cerrado` (el hecho) y `puebe_cerrarse` (lo que R3 permite afirmar) puedan
 *  discrepar: alguien cerro enero y despues metio un ticket de enero. Con tres
 *  estados eso se reportaba como "no cierra", que es falso —enero SI esta
 *  cerrado en el registro— y tapaba el hallazgo mas util del informe. */
export type EstadoPeriodo =
  | 'CIERRE_DISPONIBLE'
  | 'CERRADO'
  | 'CERRADO_CON_PENDIENTES'
  | 'NO_CIERRA';

export interface ReporteCierreMensual {
  empresa_id: string;
  empresa_nombre: string;
  empresa_tax_id: string;
  periodo: string;
  periodo_inicio: string;
  periodo_fin: string;
  /** No es `emitido_en`: es la fecha derivada del periodo. Un informe cuya fecha
   *  fuera la hora de reloj no valdria como evidencia (R4). */
  fecha_referencia: string;
  firmado_por: string;

  gasto: ResumenGasto;
  hallazgos: Hallazgo[];
  conciliacion: SeccionConciliacion;
  /** `null` cuando no hay evidencia. NO es `0%`: R7 dice "no disponible", y un
   *  cero se lee como "se midio y fallo". */
  exactitud: ReporteExactitud | null;
  exactitud_disponible: boolean;
  /** R2: la advertencia va en la PRIMERA pagina, no como nota al pie. */
  advertencia_principal: string | null;

  lectura: SeccionLectura;
  verificacion: SeccionVerificacionHumana;
  pendientes: SeccionPendientes;

  /** HECHOS de `cierres_periodo`. */
  cerrado: boolean;
  cerrado_por: string | null;
  /** `@computed_field`: derivado de `pendientes.hay_pendientes`. */
  puede_cerrarse: boolean;
  estado_periodo: EstadoPeriodo;
}

export interface TicketCreate {
  company_id: string;
  provider_name: string;
  provider_tax_id?: string;
  total_amount: string;
  tax_amount?: string;
  expense_date: string;
  category?: string;
  raw_text?: string;
}

export interface LineaTicketUpdate {
  description?: string;
  quantity?: string;
  unit_price?: string;
  total?: string;
}

export interface TicketUpdate {
  provider_name?: string;
  provider_tax_id?: string;
  total_amount?: string;
  tax_amount?: string;
  expense_date?: string;
  category?: string;
  raw_text?: string;
  /** El subtotal entra en la correccion: sin el, `subtotal + IVA == total` no
   *  se puede comprobar despues de corregir el total a mano. */
  subtotal?: string;
  /** El IEPS entra en la correccion por lo mismo que el subtotal, y porque es el
   *  dato que hace que un ticket con IVA e IEPS pueda aprobarse sin que alguien
   *  tenga que inventar un IVA para que la cuenta cuadre. */
  ieps_amount?: string;
  /** Las lineas del comprobante. Sin esto el inventario por foto no tiene
   *  entrada: la ruta OCR llega con `items` en null y no habia por donde cargarlas. */
  items?: LineaTicketUpdate[];
}

export interface BankTransaction {
  id: string;
  company_id: string;
  transaction_date: string;
  amount: string;
  description: string;
  reference: string | null;
  is_reconciled: boolean;
  created_at: string;
}

export interface BankTransactionCreate {
  company_id: string;
  transaction_date: string;
  amount: string;
  description: string;
  reference?: string;
}

export interface BankTransactionUpdate {
  transaction_date?: string;
  amount?: string;
  description?: string;
  reference?: string;
  is_reconciled?: boolean;
}

export interface BankTransactionRow {
  transaction_date: string;
  amount: string;
  description: string;
  reference: string | null;
}

export interface TicketExtractionResult {
  provider_name: string;
  provider_tax_id: string | null;
  total_amount: string;
  tax_amount: string;
  expense_date: string;
  category: string | null;
  raw_text: string;
}

export interface Reconciliation {
  id: string;
  ticket_id: string | null;
  bank_transaction_id: string | null;
  match_status: string;
  matched_at: string;
  revisado_por?: string | null;
  revisado_at?: string | null;
  ticket?: Ticket;
  bank_transaction?: BankTransaction;
  /**
   * `null` = no se pudo comprobar (falta un lado del cruce), NUNCA `0`.
   * Es la misma distincion que usa el informe de cierre: `exactitud: null` es
   * "no disponible", no "se midio y dio cero".
   *
   * Los calcula el BACKEND (`computed_field` en `ReconciliationResponse`).
   * Antes no existian en esta respuesta y la tabla los pedia igual: salia
   * `$NaN`. La pantalla no los calcula, porque es aritmetica de negocio y
   * belongs a un solo lugar.
   */
  amount_diff?: string | null;
  date_diff_days?: number | null;
}

export interface ReconciliationRunRequest {
  company_id: string;
  date_from?: string;
  date_to?: string;
  amount_tolerance?: string;
  date_tolerance_days?: number;
}

export interface ReconciliationMatchDetail {
  ticket_id: string;
  ticket_provider: string;
  ticket_amount: string;
  ticket_date: string;
  bank_transaction_id: string;
  bank_description: string;
  bank_amount: string;
  bank_date: string;
  match_status: string;
  amount_diff: string;
  date_diff_days: number;
}

export interface ReconciliationRunResponse {
  total_tickets: number;
  total_bank_transactions: number;
  perfect_matches: number;
  manual_review: number;
  discrepancies: number;
  unmatched_tickets: number;
  unmatched_bank_transactions: number;
  matches: ReconciliationMatchDetail[];
}

export interface AccountingMapping {
  id: string;
  company_id: string;
  software_name: string;
  column_mappings: Record<string, string>;
  created_at: string;
}

export interface AccountingMappingCreate {
  company_id: string;
  software_name: string;
  column_mappings: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
//
// Las formas del dashboard viven en `types/dashboard.ts`, no aqui. Se movieron
// cuando se reescribio la pantalla: la version anterior metia en este archivo un
// `PeriodStats` con doce campos que la pantalla nueva ya no pide, y dejarlo
// описaba un API que no existe. Un tipo que describe una respuesta vieja hace
// que un cambio en el servidor parezca un bug en el cliente.