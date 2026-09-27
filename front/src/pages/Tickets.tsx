import { useState, useEffect } from 'react';
import { ticketsApi, type Ticket, type TicketCreate, type TicketExtractionResult } from '../services/api';
import { companiesApi, type Company } from '../services/api';
import { 
  Button, Input, Modal, Table, Card, Badge, Loading, EmptyState, FileUpload, CameraCapture 
} from '../components/ui';
import {
  validateTicketForm, hasErrors, normalizeTicketForm,
  type TicketFormErrors, UNKNOWN_PROVIDER,
} from '../utils/validation';

export function Tickets() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<string>('');
  const [showModal, setShowModal] = useState(false);
  const [editingTicket, setEditingTicket] = useState<Ticket | null>(null);
  const [formData, setFormData] = useState<TicketCreate>({ 
    company_id: '', provider_name: '', total_amount: '', expense_date: '' 
  });
  const [submitting, setSubmitting] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractionResult, setExtractionResult] = useState<TicketExtractionResult | null>(null);
  // Aviso visible en el modal de extraccion cuando la IA no pudo leer un campo.
  const [extractionWarnings, setExtractionWarnings] = useState<string[]>([]);
  const [showExtractModal, setShowExtractModal] = useState(false);
  const [showCamera, setShowCamera] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Ticket | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [formErrors, setFormErrors] = useState<TicketFormErrors>({});

  const loadData = async () => {
    try {
      setLoading(true);
      const [companiesData, ticketsData] = await Promise.all([
        companiesApi.list(),
        selectedCompany ? ticketsApi.list(selectedCompany) : Promise.resolve([])
      ]);
      setCompanies(companiesData);
      setTickets(ticketsData);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar datos');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [selectedCompany]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const normalized = normalizeTicketForm(formData);
    const errors = validateTicketForm(normalized);
    setFormErrors(errors);
    if (hasErrors(errors)) return;  // no golpear la API con datos invalidos

    try {
      setSubmitting(true);
      setError(null);
      if (editingTicket) {
        await ticketsApi.update(editingTicket.id, normalized);
      } else {
        await ticketsApi.create(normalized);
      }
      setShowModal(false);
      resetForm();
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al guardar');
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setFormData({ 
      company_id: selectedCompany || '', 
      provider_name: '', 
      total_amount: '', 
      tax_amount: '0', 
      expense_date: new Date().toISOString().split('T')[0],
      category: '',
      raw_text: '' 
    });
    setEditingTicket(null);
    setFormErrors({});
  };

  const openCreateModal = () => {
    resetForm();
    setShowModal(true);
  };

  const openEditModal = (ticket: Ticket) => {
    setFormErrors({});
    setFormData({
      company_id: ticket.company_id,
      provider_name: ticket.provider_name,
      provider_tax_id: ticket.provider_tax_id || '',
      total_amount: ticket.total_amount,
      tax_amount: ticket.tax_amount,
      expense_date: ticket.expense_date,
      category: ticket.category || '',
      raw_text: ticket.raw_text || '',
    });
    setEditingTicket(ticket);
    setShowModal(true);
  };

  const requestDelete = (ticket: Ticket) => setDeleteTarget(ticket);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      setDeleting(true);
      await ticketsApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al eliminar');
    } finally {
      setDeleting(false);
    }
  };

  const handleExtract = async (file: File, fileType: 'pdf' | 'image' = 'pdf') => {
    if (!selectedCompany) { setError('Selecciona una empresa primero'); return; }
    try {
      setExtracting(true);
      setError(null);
      const result = await ticketsApi.extract(file, fileType);
      setExtractionResult(result);
      setExtractionWarnings(collectExtractionWarnings(result));
      setShowExtractModal(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al extraer datos');
    } finally {
      setExtracting(false);
    }
  };

  const handleCameraCapture = (file: File) => {
    setShowCamera(false);
    handleExtract(file, 'image');
  };

  /**
   * La IA no siempre logra leer todos los campos. Convertimos los fallos en
   * avisos visibles en vez de dejar que el usuario descubra el problema al
   * confirmar el ticket.
   */
  const collectExtractionWarnings = (r: TicketExtractionResult): string[] => {
    const warnings: string[] = [];
    const name = (r.provider_name ?? '').trim();
    if (!name || name === UNKNOWN_PROVIDER) {
      warnings.push('No se pudo identificar el proveedor. Escríbelo manualmente.');
    }
    const total = Number(r.total_amount);
    if (!Number.isFinite(total) || total === 0) {
      warnings.push('El total viene en 0. Revisa el monto antes de crear el ticket.');
    }
    if (!r.expense_date) {
      warnings.push('No se pudo detectar la fecha. Revísala antes de crear el ticket.');
    }
    return warnings;
  };

  const useExtractedData = () => {
    if (!extractionResult) return;
    setFormErrors({});
    setFormData({
      company_id: selectedCompany,
      provider_name: extractionResult.provider_name,
      provider_tax_id: extractionResult.provider_tax_id || '',
      total_amount: extractionResult.total_amount,
      tax_amount: extractionResult.tax_amount,
      expense_date: extractionResult.expense_date,
      category: extractionResult.category || '',
      raw_text: extractionResult.raw_text,
    });
    setShowExtractModal(false);
    setShowModal(true);
  };

  const columns = [
    { key: 'provider_name', header: 'Proveedor', render: (t: Ticket) => <span className="font-medium">{t.provider_name}</span> },
    { key: 'provider_tax_id', header: 'RFC Proveedor', render: (t: Ticket) => t.provider_tax_id ? <code className="text-sm">{t.provider_tax_id}</code> : <span className="text-gray-400">-</span> },
    { key: 'total_amount', header: 'Total', render: (t: Ticket) => <span className="font-medium text-green-700">${Number(t.total_amount).toLocaleString('es-MX', {minimumFractionDigits: 2})}</span> },
    { key: 'tax_amount', header: 'IVA', render: (t: Ticket) => <span className="text-gray-600">${Number(t.tax_amount).toLocaleString('es-MX', {minimumFractionDigits: 2})}</span> },
    { key: 'expense_date', header: 'Fecha', render: (t: Ticket) => new Date(t.expense_date).toLocaleDateString('es-MX') },
    { key: 'category', header: 'Categoría', render: (t: Ticket) => t.category ? <Badge>{t.category}</Badge> : <span className="text-gray-400">-</span> },
    { key: 'actions', header: 'Acciones', render: (t: Ticket) => (
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); openEditModal(t); }}>Editar</Button>
        <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); requestDelete(t); }}>Eliminar</Button>
      </div>
    )},
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Tickets / Facturas</h1>
          <p className="text-gray-500">Registra y gestiona tus comprobantes fiscales</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={() => { setExtractionResult(null); setExtractionWarnings([]); setShowCamera(false); setShowExtractModal(true); }}
            disabled={!selectedCompany || extracting}
          >
            Extraer de PDF/Imagen
          </Button>
          <Button onClick={openCreateModal} disabled={!selectedCompany}>
            Nuevo Ticket
          </Button>
        </div>
      </div>

      <Card className="p-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-sm font-medium text-gray-700 mb-1">Empresa</label>
            <select
              value={selectedCompany}
              onChange={e => setSelectedCompany(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              <option value="">Seleccionar empresa...</option>
              {companies.map(c => <option key={c.id} value={c.id}>{c.name} ({c.tax_id})</option>)}
            </select>
          </div>
        </div>
      </Card>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)}>×</Button>
        </div>
      )}

      {selectedCompany ? (
        <Card>
          {loading ? (
            <Loading message="Cargando tickets..." />
          ) : tickets.length === 0 ? (
            <EmptyState 
              message="No hay tickets. Crea uno manualmente o extrae datos de un PDF."
              icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>}
            />
          ) : (
            <Table
              columns={columns}
              data={tickets}
              keyField="id"
            />
          )}
        </Card>
      ) : (
        <Card>
          <EmptyState 
            message="Selecciona una empresa para ver y gestionar sus tickets"
            icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" /></svg>}
          />
        </Card>
      )}

      {/* Modal Crear/Editar Ticket */}
      <Modal isOpen={showModal} onClose={() => { setShowModal(false); resetForm(); }} title={editingTicket ? 'Editar Ticket' : 'Nuevo Ticket'}>
        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Empresa" disabled
              value={companies.find(c => c.id === formData.company_id)?.name || formData.company_id}
              error={formErrors.company_id}
            />
            <Input
              label="Fecha" type="date" value={formData.expense_date}
              onChange={e => setFormData({...formData, expense_date: e.target.value})}
              error={formErrors.expense_date} required
            />
          </div>
          <Input
            label="Proveedor" value={formData.provider_name}
            onChange={e => setFormData({...formData, provider_name: e.target.value})}
            error={formErrors.provider_name} required maxLength={150}
          />
          <Input
            label="RFC Proveedor" value={formData.provider_tax_id || ''}
            onChange={e => setFormData({...formData, provider_tax_id: e.target.value.toUpperCase()})}
            error={formErrors.provider_tax_id} placeholder="Opcional. Ej: WAL910101XXX" maxLength={13}
          />
          <div className="grid grid-cols-3 gap-4">
            <Input
              label="Total" type="number" step="0.01" min="0.01" value={formData.total_amount}
              onChange={e => setFormData({...formData, total_amount: e.target.value})}
              error={formErrors.total_amount} required
            />
            <Input
              label="IVA" type="number" step="0.01" min="0" value={formData.tax_amount || ''}
              onChange={e => setFormData({...formData, tax_amount: e.target.value})}
              error={formErrors.tax_amount}
            />
            <Input
              label="Categoría" value={formData.category || ''}
              onChange={e => setFormData({...formData, category: e.target.value})}
              error={formErrors.category} placeholder="Ej: SUPERMERCADO" maxLength={100}
            />
          </div>
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" type="button" onClick={() => { setShowModal(false); resetForm(); }}>Cancelar</Button>
            <Button type="submit" disabled={submitting}>{submitting ? 'Guardando...' : (editingTicket ? 'Actualizar' : 'Crear')}</Button>
          </div>
        </form>
      </Modal>

      {/* Modal Extracción PDF */}
      <Modal isOpen={showExtractModal} onClose={() => { setShowExtractModal(false); setExtractionResult(null); setExtractionWarnings([]); setShowCamera(false); }} title="Extraer datos de PDF/Imagen" className="max-w-xl">
        <div className="space-y-4">
          {!extractionResult && !showCamera ? (
            <>
              <div className="flex gap-4 mb-4">
                <Button variant="secondary" className="flex-1" onClick={() => setShowCamera(true)}>
                  📷 Capturar con Cámara
                </Button>
                <Button variant="ghost" className="flex-1" onClick={() => document.getElementById('ticket-file-input')?.click()}>
                  📁 Subir Archivo
                </Button>
              </div>
              <FileUpload 
                inputId="ticket-file-input"
                accept=".pdf,.png,.jpg,.jpeg" 
                onChange={files => {
                  const file = files[0];
                  if (!file) return;
                  const isImage = file.type.startsWith('image/') || /\.(png|jpe?g)$/i.test(file.name);
                  handleExtract(file, isImage ? 'image' : 'pdf');
                }}
                label="Arrastrar PDF o imagen de factura"
              />
              <p className="text-sm text-gray-500 text-center">Formatos soportados: PDF, PNG, JPG (máx 10MB)</p>
            </>
          ) : showCamera ? (
            <CameraCapture 
              onCapture={(file: File) => handleCameraCapture(file)}
              onCancel={() => setShowCamera(false)}
            />
          ) : (
            <div className="space-y-4">
              {extractionResult && (
                <>
                  {extractionWarnings.length > 0 && (
                    <div className="bg-amber-50 border border-amber-200 text-amber-800 px-4 py-3 rounded-lg">
                      <p className="font-medium text-sm mb-1">Revisa antes de crear:</p>
                      <ul className="list-disc list-inside text-sm space-y-0.5">
                        {extractionWarnings.map(w => <li key={w}>{w}</li>)}
                      </ul>
                    </div>
                  )}
                  <div className="bg-gray-50 p-4 rounded-lg">
                    <h4 className="font-medium mb-2">Datos extraídos:</h4>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <div>
                        <span className="text-gray-500">Proveedor:</span>{' '}
                        <span className={extractionResult.provider_name === UNKNOWN_PROVIDER ? 'text-amber-700 font-medium' : 'font-medium'}>
                          {extractionResult.provider_name}
                        </span>
                      </div>
                      <div><span className="text-gray-500">RFC:</span> <span>{extractionResult.provider_tax_id || '-'}</span></div>
                      <div><span className="text-gray-500">Total:</span> <span className="font-medium text-green-700">${Number(extractionResult.total_amount).toLocaleString('es-MX')}</span></div>
                      <div><span className="text-gray-500">IVA:</span> <span>${Number(extractionResult.tax_amount).toLocaleString('es-MX')}</span></div>
                      <div><span className="text-gray-500">Fecha:</span> <span>{extractionResult.expense_date}</span></div>
                      <div><span className="text-gray-500">Categoría:</span> <span>{extractionResult.category || '-'}</span></div>
                    </div>
                  </div>
                  <div className="flex gap-3 justify-end">
                    <Button variant="secondary" onClick={() => { setExtractionResult(null); setExtractionWarnings([]); }}>Nueva extracción</Button>
                    <Button onClick={useExtractedData}>Continuar con estos datos</Button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </Modal>

      {/* Modal Confirmar Eliminación */}
      <Modal isOpen={!!deleteTarget} onClose={() => !deleting && setDeleteTarget(null)} title="Eliminar ticket">
        <div className="space-y-4">
          <p className="text-gray-700">
            ¿Eliminar el ticket de <span className="font-medium">{deleteTarget?.provider_name}</span>
            {deleteTarget?.total_amount && (
              <> por <span className="font-medium text-green-700">${Number(deleteTarget.total_amount).toLocaleString('es-MX', { minimumFractionDigits: 2 })}</span></>
            )}?
          </p>
          <p className="text-sm text-gray-500">Esta acción no se puede deshacer.</p>
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>Cancelar</Button>
            <Button variant="danger" onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Eliminando...' : 'Eliminar'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}