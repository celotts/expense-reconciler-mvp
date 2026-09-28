// API Types matching backend schemas

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

export interface TicketUpdate {
  provider_name?: string;
  provider_tax_id?: string;
  total_amount?: string;
  tax_amount?: string;
  expense_date?: string;
  category?: string;
  raw_text?: string;
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