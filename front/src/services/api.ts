// API Service Layer
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

async function fetchApi<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Error desconocido' }));
    throw new Error(error.detail || `Error ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

// Companies API
export const companiesApi = {
  list: () => fetchApi<Company[]>('/companies/'),
  get: (id: string) => fetchApi<Company>(`/companies/${id}`),
  create: (data: CompanyCreate) => fetchApi<Company>('/companies/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  update: (id: string, data: CompanyUpdate) => fetchApi<Company>(`/companies/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),
  delete: (id: string) => fetchApi<void>(`/companies/${id}`, { method: 'DELETE' }),
};

// Tickets API
export const ticketsApi = {
  list: (companyId?: string) => {
    const params = companyId ? `?company_id=${companyId}` : '';
    return fetchApi<Ticket[]>(`/tickets/${params}`);
  },
  get: (id: string) => fetchApi<Ticket>(`/tickets/${id}`),
  create: (data: TicketCreate) => fetchApi<Ticket>('/tickets/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  update: (id: string, data: TicketUpdate) => fetchApi<Ticket>(`/tickets/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),
  delete: (id: string) => fetchApi<void>(`/tickets/${id}`, { method: 'DELETE' }),
  
  // Extract data from file (preview)
  extract: (file: File, fileType: 'pdf' | 'image' | 'text' = 'pdf'): Promise<TicketExtractionResult> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('file_type', fileType);
    return fetchApi<TicketExtractionResult>('/tickets/extract', {
      method: 'POST',
      body: formData,
      headers: {}, // Let browser set Content-Type with boundary
    });
  },

  // Extract and create in one step
  extractAndCreate: (file: File, companyId: string, fileType: 'pdf' | 'image' | 'text' = 'pdf'): Promise<Ticket> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('file_type', fileType);
    formData.append('company_id', companyId);
    return fetchApi<Ticket>('/tickets/extract-and-create', {
      method: 'POST',
      body: formData,
      headers: {},
    });
  },
};

// Bank Transactions API
export const bankTransactionsApi = {
  list: (companyId?: string) => {
    const params = companyId ? `?company_id=${companyId}` : '';
    return fetchApi<BankTransaction[]>(`/bank-transactions/${params}`);
  },
  get: (id: string) => fetchApi<BankTransaction>(`/bank-transactions/${id}`),
  create: (data: BankTransactionCreate) => fetchApi<BankTransaction>('/bank-transactions/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  update: (id: string, data: BankTransactionUpdate) => fetchApi<BankTransaction>(`/bank-transactions/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),
  delete: (id: string) => fetchApi<void>(`/bank-transactions/${id}`, { method: 'DELETE' }),

  // Preview CSV import
  importCsvPreview: (file: File, options: {
    date_column?: string;
    amount_column?: string;
    description_column?: string;
    reference_column?: string;
    date_format?: string;
    decimal_separator?: string;
    thousands_separator?: string;
    encoding?: string;
    separator?: string;
  } = {}): Promise<BankTransactionRow[]> => {
    const formData = new FormData();
    formData.append('file', file);
    Object.entries(options).forEach(([key, value]) => {
      if (value) formData.append(key, value);
    });
    return fetchApi<BankTransactionRow[]>('/bank-transactions/import-csv', {
      method: 'POST',
      body: formData,
      headers: {},
    });
  },

  // Import CSV and create
  importCsvAndCreate: (file: File, companyId: string, options: {
    date_column?: string;
    amount_column?: string;
    description_column?: string;
    reference_column?: string;
    date_format?: string;
    decimal_separator?: string;
    thousands_separator?: string;
    encoding?: string;
    separator?: string;
  } = {}): Promise<BankTransaction[]> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('company_id', companyId);
    Object.entries(options).forEach(([key, value]) => {
      if (value) formData.append(key, value);
    });
    return fetchApi<BankTransaction[]>('/bank-transactions/import-csv-and-create', {
      method: 'POST',
      body: formData,
      headers: {},
    });
  },
};

// Reconciliations API
export const reconciliationsApi = {
  list: (companyId?: string, matchStatus?: string) => {
    const params = new URLSearchParams();
    if (companyId) params.append('company_id', companyId);
    if (matchStatus) params.append('match_status', matchStatus);
    return fetchApi<Reconciliation[]>(`/reconciliations/?${params.toString()}`);
  },
  get: (id: string) => fetchApi<Reconciliation>(`/reconciliations/${id}`),
  create: (data: ReconciliationCreate) => fetchApi<Reconciliation>('/reconciliations/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  delete: (id: string) => fetchApi<void>(`/reconciliations/${id}`, { method: 'DELETE' }),

  // Run reconciliation engine
  run: (data: ReconciliationRunRequest): Promise<ReconciliationRunResponse> =>
    fetchApi<ReconciliationRunResponse>('/reconciliations/run', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Exports
  exportExcel: (companyId: string, options?: { date_from?: string; date_to?: string; only_reconciled?: boolean }): Promise<Blob> => {
    const params = new URLSearchParams({ company_id: companyId });
    if (options?.date_from) params.append('date_from', options.date_from);
    if (options?.date_to) params.append('date_to', options.date_to);
    if (options?.only_reconciled !== undefined) params.append('only_reconciled', String(options.only_reconciled));
    
    return fetch(`${API_BASE}/reconciliations/export/excel?${params.toString()}`, {
      headers: { 'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    }).then(res => {
      if (!res.ok) throw new Error('Error al exportar Excel');
      return res.blob();
    });
  },

  exportContpaqi: (companyId: string, options?: { 
    date_from?: string; 
    date_to?: string; 
    only_reconciled?: boolean; 
    mapping_id?: string 
  }): Promise<Blob> => {
    const params = new URLSearchParams({ company_id: companyId });
    if (options?.date_from) params.append('date_from', options.date_from);
    if (options?.date_to) params.append('date_to', options.date_to);
    if (options?.only_reconciled !== undefined) params.append('only_reconciled', String(options.only_reconciled));
    if (options?.mapping_id) params.append('mapping_id', options.mapping_id);
    
    return fetch(`${API_BASE}/reconciliations/export/contpaqi?${params.toString()}`, {
      headers: { 'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    }).then(res => {
      if (!res.ok) throw new Error('Error al exportar CONTPAQI');
      return res.blob();
    });
  },

  exportGeneric: (companyId: string, options?: { 
    date_from?: string; 
    date_to?: string; 
    only_reconciled?: boolean; 
    columns?: string 
  }): Promise<Blob> => {
    const params = new URLSearchParams({ company_id: companyId });
    if (options?.date_from) params.append('date_from', options.date_from);
    if (options?.date_to) params.append('date_to', options.date_to);
    if (options?.only_reconciled !== undefined) params.append('only_reconciled', String(options.only_reconciled));
    if (options?.columns) params.append('columns', options.columns);
    
    return fetch(`${API_BASE}/reconciliations/export/generic?${params.toString()}`, {
      headers: { 'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    }).then(res => {
      if (!res.ok) throw new Error('Error al exportar genérico');
      return res.blob();
    });
  },

  // Accounting Mappings
  createMapping: (data: AccountingMappingCreate) => fetchApi<AccountingMapping>('/reconciliations/mappings', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  listMappings: (companyId?: string) => {
    const params = companyId ? `?company_id=${companyId}` : '';
    return fetchApi<AccountingMapping[]>(`/reconciliations/mappings${params}`);
  },
  getMapping: (id: string) => fetchApi<AccountingMapping>(`/reconciliations/mappings/${id}`),
};

// Re-export types
export type { 
  Company, CompanyCreate, CompanyUpdate,
  Ticket, TicketCreate, TicketUpdate,
  BankTransaction, BankTransactionCreate, BankTransactionUpdate, BankTransactionRow,
  TicketExtractionResult,
  Reconciliation, ReconciliationCreate, ReconciliationRunRequest, ReconciliationRunResponse, ReconciliationMatchDetail,
  AccountingMapping, AccountingMappingCreate
} from '../types/api';