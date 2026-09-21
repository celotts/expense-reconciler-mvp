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

export interface Ticket {
  id: string;
  company_id: string;
  provider_name: string;
  provider_tax_id: string | null;
  total_amount: string;
  tax_amount: string;
  expense_date: string;
  category: string | null;
  raw_text: string | null;
  created_at: string;
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