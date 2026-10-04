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
  ticket?: Ticket;
  bank_transaction?: BankTransaction;
}

export interface ReconciliationCreate {
  ticket_id?: string;
  bank_transaction_id?: string;
  match_status: string;
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