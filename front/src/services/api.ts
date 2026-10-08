// API Service Layer
import type {
  Company, CompanyCreate, CompanyUpdate,
  Ticket, TicketCreate, TicketUpdate, TicketReviewQueue, TicketReviewRequest,
  SpotCheckQueue, SpotCheckItem, SpotCheckRequest, SpotCheckStatus,
  ReporteExactitud,
  BankTransaction, BankTransactionCreate, BankTransactionUpdate, BankTransactionRow,
  TicketExtractionResult,
  Reconciliation, ReconciliationCreate, ReconciliationRunRequest, ReconciliationRunResponse,
  AccountingMapping, AccountingMappingCreate,
} from '../types/api';
import type { Categoria, DashboardResponse } from '../types/dashboard';
import type {
  ScanConfig, ScanCorrida, ScanFileDetail, ScanFilePage, ScanItem, ScanOcrEstado,
  ScanRequest, ScanResultado, ScanStats, ScanStatus,
} from '../types/scan';
import { leerToken, notificarCaducidad, SesionVencida } from './sesion';

const API_BASE = import.meta.env.VITE_API_URL || '/api/v1';

/** Lo que hay que aadirle a una peticion que no es solo metodo y cuerpo. */
interface OpcionesPeticion extends RequestInit {
  /** Esta peticion NO espera token.
   *
   *  La usa el login, y la unica razon de que exista es el 401: un 401 de
   *  "correos y contrasena incorrectos" y un 401 de "la sesion termino" son el
   *  mismo numero y la misma pantalla, pero significan cosas opuestas. Si el
   *  login usara el camino normal, un contrasena equivocado expulsaria al
   *  usuario de la pantalla de entrar y lo mandaria a una pantalla de
   *  "vuelve a entrar" que no tiene sentido porque nunca entro. */
  sinToken?: boolean;
}

/** La cabecera de autorizacion, o `null` si no hay token que mandar.
 *
 *  `null` y no `Bearer ` a secas: mandar "Bearer" sin token hace que el
 *  servidor responda "el token no es valido" en vez de "falta el token", que
 *  son diagnosticos distintos y el segundo es el que dice la verdad. */
function cabeceraDeAutorizacion(): Record<string, string> {
  const token = leerToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** La peticion cruda, con token y con manejo de 401.
 *
 *  Se exporta para que `auth.ts` la use, y es la unica via de salida: el login
 *  tiene que pasar por aqui en vez de por un `fetch` suelto, porque es aqui
 *  donde se decide que es un 401 de "sesion caduco" y donde se tira el token. Un
 *  `fetch` suelto en el login se saltaria las dos cosas, y un 401 ahi se
 *  leeria como credenciales malas, que es la unica mentira que este flujo no
 *  debe contar. */
export async function fetchApi<T>(endpoint: string, options: OpcionesPeticion = {}): Promise<T> {
  const { sinToken, ...resto } = options;

  // Un `FormData` NO lleva `Content-Type`. El navegador tiene que ponerlo
  // porque es el unico que sabe donde va el boundary que separa las partes del
  // multipart; si se lo pone uno a mano, sin ese boundary, el servidor no
  // encuentra el archivo y responde 422 a una subida que el navegador si
  // mando. Por eso la decision se toma con `instanceof` y no "poniendo un
  // header vacio": un `Content-Type: ''` tambien lo rompe.
  const esFormulario = typeof FormData !== 'undefined' && resto.body instanceof FormData;

  const cabeceras: Record<string, string> = {
    ...(esFormulario ? {} : { 'Content-Type': 'application/json' }),
    ...(sinToken ? {} : cabeceraDeAutorizacion()),
    ...(resto.headers as Record<string, string> | undefined),
  };

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...resto,
    headers: cabeceras,
  });

  if (response.status === 401 && !sinToken) {
    // Se avisa a la puerta de sesion y ADEMAS se lanza, porque hacen falta las
    // dos cosas y para cosas distintas: el aviso saca el login, y el error le
    // dice a la pantalla que esta fallando que no fue un fallo suyo y que su
    // `catch` no ofrezca "Reintentar", que es la instruccion que hace repetir la
    // transaccion y terminar con dos tickets.
    //
    // Si la peticion no era un GET, ademas se marca como transaccion intentada:
    // el archivo ya se consumio del selector y el trabajo no quedo guardado, y
    // el aviso tiene que decirlo o el usuario solo ve "vuelve a entrar" y
    // asume que si se guardo.
    //
    // El token no se borra aqui. Lo borra la puerta, en un solo sitio: si cada
    // peticion lo hiciera por su cuenta, cada una tendria su propio criterio
    // sobre cuando hacerlo, y dos peticiones que fallan a la vez dejarian un
    // estado que ninguna sabe explicar.
    const transaccionIntentada = resto.method !== undefined && resto.method !== 'GET';
    notificarCaducidad(transaccionIntentada);
    throw new SesionVencida(transaccionIntentada);
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Error desconocido' }));
    throw new Error(error.detail || `Error ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

/** El comprobante original de un ticket, como Blob.
 *
 *  Va aparte de `fetchApi` por la misma razon que `fetchArchivo`: el resultado
 *  son bytes, no JSON, y `response.json()` sobre un PDF revienta.
 *
 *  Devuelve un Blob y no una URL, a proposito. La URL que da el backend es
 *  relativa y sin token, asi que no se puede poner en un `<img src>` ni en un
 *  `iframe`: el endpoint exige cabecera `Authorization` y un `src` no la manda.
 *  Con un `<img src={documento_url}>` el revisor ve un recuadro vacio y no sabe
 *  si el documento no esta o si la peticion fallo, que son dos cosas que se
 *  comprueban de forma distinta.
 *
 *  El backend decide el `Content-Type` que devuelve, y siempre es de una lista
 *  cerrada de tipos que el navegador no puede ejecutar. Por eso el Blob se puede
 *  mostrar sin miedo: lo que el backend decided servir no es codigo. Ver
 *  `app/models/ticket_document.py`. */
export async function fetchDocumento(ticketId: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}/tickets/${ticketId}/documento`, {
    headers: { ...cabeceraDeAutorizacion() },
  });

  if (response.status === 401) {
    notificarCaducidad(false);
    throw new SesionVencida(false);
  }

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const detalle = error && typeof error.detail === 'string' ? error.detail : null;
    throw new Error(detalle || `Error ${response.status}`);
  }

  return response.blob();
}

/** La descarga de un archivo, que no es JSON.
 *
 *  Va aparte de `fetchApi` porque el resultado es un `Blob` y no un objeto, y
 *  porque necesita la MISMA cabecera de autorizacion: una descarga sin token es
 *  un 401 que se ve como "el Excel sale vacio", no como "hay que entrar". */
async function fetchArchivo(endpoint: string, params: URLSearchParams): Promise<Blob> {
  const response = await fetch(`${API_BASE}${endpoint}?${params.toString()}`, {
    headers: {
      Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      ...cabeceraDeAutorizacion(),
    },
  });

  if (response.status === 401) {
    notificarCaducidad(true);
    throw new SesionVencida(true);
  }

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const detalle = error && typeof error.detail === 'string' ? error.detail : null;
    throw new Error(detalle || `Error ${response.status}`);
  }

  return response.blob();
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
  list: (
    companyId?: string,
    filters?: {
      onlyOpen?: boolean;
      extractionStatus?: string;
      /** Solo los que no tienen categoria. La barra gris del tablero. */
      sinCategoria?: boolean;
      conCategoria?: boolean;
      categoria?: string;
      limit?: number;
    },
  ) => {
    const params = new URLSearchParams();
    if (companyId) params.append('company_id', companyId);
    if (filters?.onlyOpen) params.append('only_open', 'true');
    if (filters?.extractionStatus) params.append('extraction_status', filters.extractionStatus);
    if (filters?.sinCategoria) params.append('sin_categoria', 'true');
    if (filters?.conCategoria) params.append('con_categoria', 'true');
    if (filters?.categoria) params.append('categoria', filters.categoria);
    if (filters?.limit) params.append('limit', String(filters.limit));
    const qs = params.toString();
    return fetchApi<Ticket[]>(`/tickets/${qs ? `?${qs}` : ''}`);
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

  // Clasifica varios de golpe. Va en un solo PATCH y no en uno por ticket: la
  // barra gris puede ser de 200 y a uno por uno son 200 viajes de ida y vuelta.
  clasificarLote: (ticketIds: string[], category: string | null): Promise<{ actualizados: number; pedidos: number }> =>
    fetchApi('/tickets/categoria', {
      method: 'PATCH',
      body: JSON.stringify({ ticket_ids: ticketIds, category }),
    }),

  // La taxonomia la pide al servidor y no la trae escrita. La lista de este
  // archivo seria una segunda copia, y clasificar con una categoria que el
  // servidor no reconoce no daria error: el ticket se guardaria y no apareceria
  // en ninguna barra del reparto.
  categorias: () => fetchApi<Categoria[]>('/categorias'),

  // Cola de revision. Los conteos que devuelve son globales aunque se filtre,
  // para que el encabezado no salte de 40 a 3 al cambiar el filtro.
  reviewQueue: (companyId?: string, status?: string): Promise<TicketReviewQueue> => {
    const params = new URLSearchParams();
    if (companyId) params.append('company_id', companyId);
    if (status) params.append('status', status);
    const qs = params.toString();
    return fetchApi<TicketReviewQueue>(`/tickets/review-queue${qs ? `?${qs}` : ''}`);
  },

  // `approve` con datos que siguen rotos devuelve 422: la opinion de una
  // persona no puede saltarse los checks. Hay que corregir y volver a intentar.
  review: (id: string, data: TicketReviewRequest) => fetchApi<Ticket>(`/tickets/${id}/review`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),

  // -------------------------------------------------------------------------
  // Muestreo de exactitud
  // -------------------------------------------------------------------------

  // Sin `status` devuelve solo lo PENDIENTE, que es el trabajo. Los conteos
  // son globales: no bajan al filtrar, para que el encabezado no salte de 200
  // a 3 y deje de informar.
  spotCheckQueue: (
    companyId?: string,
    status?: SpotCheckStatus,
    limit?: number,
  ): Promise<SpotCheckQueue> => {
    const params = new URLSearchParams();
    if (companyId) params.append('company_id', companyId);
    if (status) params.append('status', status);
    if (limit) params.append('limit', String(limit));
    const qs = params.toString();
    return fetchApi<SpotCheckQueue>(`/tickets/spot-check${qs ? `?${qs}` : ''}`);
  },

  // Registra el veredicto de la muestra. NO corrige el ticket: solo deja
  // constancia de si la lectura coincidia con el papel.
  registrarVeredicto: (id: string, data: SpotCheckRequest) =>
    fetchApi<SpotCheckItem>(`/tickets/${id}/spot-check`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  // El reporte se pide aparte de la cola. No es un dato derivado que se pueda
  // calcular en el navegador: el intervalo de confianza y el veredicto los
  // decide el servidor, con la misma aritmetica que los tests.
  reporteExactitud: (companyId?: string): Promise<ReporteExactitud> => {
    const qs = companyId ? `?company_id=${companyId}` : '';
    return fetchApi<ReporteExactitud>(`/tickets/accuracy${qs}`);
  },

  // Extract data from file (preview)
  extract: (file: File, fileType: 'pdf' | 'image' | 'text' = 'pdf'): Promise<TicketExtractionResult> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('file_type', fileType);
    // Sin `headers`: el Content-Type del multipart lo pone `fetchApi` segun
    // ver si el cuerpo es un FormData. Ponerlo aqui a mano lo rompe.
    return fetchApi<TicketExtractionResult>('/tickets/extract', {
      method: 'POST',
      body: formData,
    });
  },

  // -------------------------------------------------------------------------
  // Escaner de carpeta
  // -------------------------------------------------------------------------

  /** Escanea la carpeta.
   *
   *  Sin `company_id` el escaneo SOLO inventaria los archivos y no crea
   *  tickets, y por eso los tres campos son opcionales aqui y no requeridos:
   *  la pantalla tiene dos botones distintos para las dos intenciones, y el
   *  body los distingue. */
  escanear: (data: ScanRequest = {}): Promise<ScanResultado> =>
    fetchApi<ScanResultado>('/scan/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Las corridas recientes, y cuales siguen en marcha.
   *
   *  Es el canal de progreso: `POST /scan` es sincrono y con fotos tarda
   *  minutos, asi que el cliente necesita algo que consultar mientras espera. */
  scanRuns: (limite = 10): Promise<{ corridas: ScanCorrida[]; en_curso: string[] }> =>
    fetchApi<{ corridas: ScanCorrida[]; en_curso: string[] }>(`/scan/runs?limite=${limite}`),

  /** El progreso de UNA corrida. El mismo endpoint sirve para el progreso y
   *  para el resultado: cuando `terminada` es true, `resumen` ya esta lleno. */
  scanRun: (id: string): Promise<ScanCorrida> => fetchApi<ScanCorrida>(`/scan/runs/${id}`),

  /** El registro de archivos, con `limit` y `offset` explicitos.
   *
   *  El backend tiene un tope de 200 por pagina y `limit` por omision es 50.
   *  Aqui se mandan siempre, para que la paginacion sea la misma sin importar
   *  desde donde se entro a la pantalla. */
  scanFiles: (params: { estado?: ScanStatus; companyId?: string; limit?: number; offset?: number } = {}): Promise<ScanFilePage> => {
    const q = new URLSearchParams();
    if (params.estado) q.append('estado', params.estado);
    if (params.companyId) q.append('company_id', params.companyId);
    if (params.limit !== undefined) q.append('limit', String(params.limit));
    if (params.offset !== undefined) q.append('offset', String(params.offset));
    const qs = q.toString();
    return fetchApi<ScanFilePage>(`/scan/files${qs ? `?${qs}` : ''}`);
  },

  scanFile: (id: string): Promise<ScanFileDetail> =>
    fetchApi<ScanFileDetail>(`/scan/files/${id}`),

  /** Relee UN archivo.
   *
   *  Es el UNICO camino que sobreescribe un ticket con correcciones humanas, y
   *  por eso lleva su propia funcion y no es un parametro del escaneo: quien lo
   *  pide quiere decir "relee ESTE", no "relee lo que haya cambiado". Si el
   *  archivo ya no esta en el disco el backend responde 409, no un exito. */
  reprocesarScanFile: (id: string, companyId?: string): Promise<{ archivo: ScanItem; forzado: boolean }> => {
    const qs = companyId ? `?company_id=${companyId}` : '';
    return fetchApi<{ archivo: ScanItem; forzado: boolean }>(
      `/scan/files/${id}/reprocess${qs}`,
      { method: 'POST' },
    );
  },

  scanStats: (): Promise<ScanStats> => fetchApi<ScanStats>('/scan/stats'),

  scanConfig: (): Promise<ScanConfig> => fetchApi<ScanConfig>('/scan/config'),

  scanOcr: (): Promise<ScanOcrEstado> => fetchApi<ScanOcrEstado>('/scan/ocr'),

  /** El comprobante original, como Blob.
   *
   *  Devuelve bytes y no una URL porque el endpoint pide token: un `<img src>`
   *  contra la URL del backend se veria vacio, sin error visible, y el revisor
   *  no distinguiria "el documento no esta" de "la peticion fallo". */
  documento: (ticketId: string): Promise<Blob> => fetchDocumento(ticketId),

  /** Vuelve a subir el comprobante de un ticket que ya existe.
   *
   *  Es lo que hace recuperable un documento que no se pudo leer: los bytes se
   *  pueden volver a subir sobre el ticket que ya esta, y no se crea un gasto
   *  nuevo. */
  subirDocumento: (ticketId: string, file: File): Promise<Ticket> => {
    const formData = new FormData();
    formData.append('file', file);
    // Sin `headers`: el `Content-Type` del multipart lo pone `fetchApi` segun
    // vea que el cuerpo es un FormData. Ponerlo aqui a mano rompe el boundary.
    return fetchApi<Ticket>(`/tickets/${ticketId}/documento`, {
      method: 'PUT',
      body: formData,
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

    return fetchArchivo('/reconciliations/export/excel', params);
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

    return fetchArchivo('/reconciliations/export/contpaqi', params);
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

    return fetchArchivo('/reconciliations/export/generic', params);
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

// Dashboard API
export const dashboardApi = {
  get: (companyId?: string, meses = 12) => {
    const params = new URLSearchParams({ meses: String(meses) });
    if (companyId) params.append('company_id', companyId);
    return fetchApi<DashboardResponse>(`/dashboard/?${params.toString()}`);
  },
};

// Re-export types
export type { 
  Company, CompanyCreate, CompanyUpdate,
  Ticket, TicketCreate, TicketUpdate, TicketReviewQueue, TicketReviewRequest,
  LineaTicketUpdate,
  BankTransaction, BankTransactionCreate, BankTransactionUpdate, BankTransactionRow,
  TicketExtractionResult,
  SpotCheckQueue, SpotCheckItem, SpotCheckRequest, SpotCheckStatus,
  SpotCheckField, ExactitudPorOrigen, ReporteExactitud, Veredicto,
  Reconciliation, ReconciliationCreate, ReconciliationRunRequest, ReconciliationRunResponse, ReconciliationMatchDetail,
  AccountingMapping, AccountingMappingCreate,
} from '../types/api';

// El dashboard tiene sus propias formas en `types/dashboard.ts` y se reexporta
// aqui para que quien lo consume no tenga que saber de donde vino. La pantalla
// lo importa de los dos lados segun le convenga; lo que no se hace es tener dos
// copias del mismo tipo, que es como una empieza a mentir sobre la otra.
export type {
  DashboardResponse, ResumenPeriodo, CategoriaGasto, MesGasto, ProveedorGasto, EstadoBanco,
} from '../types/dashboard';

export type { ExtractionStatus, ConfidenceSource, SourceType } from '../types/api';