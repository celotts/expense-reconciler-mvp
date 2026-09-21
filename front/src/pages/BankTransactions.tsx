import { useState, useEffect } from 'react';
import { bankTransactionsApi, type BankTransaction, type BankTransactionCreate, type BankTransactionRow } from '../services/api';
import { companiesApi, type Company } from '../services/api';
import { 
  Button, Input, Modal, Table, Card, Badge, Loading, EmptyState, FileUpload 
} from '../components/ui';

export function BankTransactions() {
  const [transactions, setTransactions] = useState<BankTransaction[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<string>('');
  const [showModal, setShowModal] = useState(false);
  const [editingTx, setEditingTx] = useState<BankTransaction | null>(null);
  const [formData, setFormData] = useState<BankTransactionCreate>({ 
    company_id: '', transaction_date: '', amount: '', description: '' 
  });
  const [submitting, setSubmitting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewData, setPreviewData] = useState<BankTransactionRow[]>([]);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [importing, setImporting] = useState(false);

  const loadData = async () => {
    try {
      setLoading(true);
      const [companiesData, txData] = await Promise.all([
        companiesApi.list(),
        selectedCompany ? bankTransactionsApi.list(selectedCompany) : Promise.resolve([])
      ]);
      setCompanies(companiesData);
      setTransactions(txData);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar datos');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { 
    loadData(); 
    companiesApi.list().then(setCompanies).catch(() => {});
  }, [selectedCompany]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      if (editingTx) {
        await bankTransactionsApi.update(editingTx.id, formData);
      } else {
        await bankTransactionsApi.create(formData);
      }
      setShowModal(false);
      resetForm();
      loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al guardar');
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setFormData({ 
      company_id: selectedCompany || '', 
      transaction_date: new Date().toISOString().split('T')[0],
      amount: '', 
      description: '', 
      reference: '' 
    });
    setEditingTx(null);
  };

  const openCreateModal = () => {
    resetForm();
    setShowModal(true);
  };

  const openEditModal = (tx: BankTransaction) => {
    setFormData({
      company_id: tx.company_id,
      transaction_date: tx.transaction_date,
      amount: tx.amount,
      description: tx.description,
      reference: tx.reference || '',
    });
    setEditingTx(tx);
    setShowModal(true);
  };

  const handleDelete = async (id: string) => {
    if (!confirm('¿Eliminar este movimiento?')) return;
    try {
      await bankTransactionsApi.delete(id);
      loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al eliminar');
    }
  };

  const handlePreviewCsv = async (file: File) => {
    try {
      setPreviewing(true);
      const data = await bankTransactionsApi.importCsvPreview(file, {
        date_column: 'fecha',
        amount_column: 'importe',
        description_column: 'concepto',
        reference_column: 'referencia',
        date_format: '%d/%m/%Y',
        decimal_separator: '.',
        thousands_separator: ',',
      });
      setPreviewData(data);
      setShowPreviewModal(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al previsualizar CSV');
    } finally {
      setPreviewing(false);
    }
  };

  const handleImportCsv = async (file: File) => {
    if (!selectedCompany) { setError('Selecciona una empresa primero'); return; }
    try {
      setImporting(true);
      await bankTransactionsApi.importCsvAndCreate(file, selectedCompany, {
        date_column: 'fecha',
        amount_column: 'importe',
        description_column: 'concepto',
        reference_column: 'referencia',
        date_format: '%d/%m/%Y',
        decimal_separator: '.',
        thousands_separator: ',',
      });
      setShowPreviewModal(false);
      loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al importar CSV');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Movimientos Bancarios</h1>
          <p className="text-gray-500">Importa y gestiona tus extractos bancarios</p>
        </div>
        <Button onClick={openCreateModal} disabled={!selectedCompany}>
          Nuevo Movimiento
        </Button>
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
          {selectedCompany && (
            <FileUpload
              accept=".csv"
              onChange={files => files[0] && handlePreviewCsv(files[0])}
              label="Previsualizar CSV"
              disabled={previewing}
            />
          )}
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
            <Loading message="Cargando movimientos..." />
          ) : transactions.length === 0 ? (
            <EmptyState 
              message="No hay movimientos. Crea uno manualmente o importa un CSV."
              icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>}
            />
          ) : (
            <Table
              columns={[
                { key: 'transaction_date', header: 'Fecha', render: (t: BankTransaction) => new Date(t.transaction_date).toLocaleDateString('es-MX') },
                { key: 'amount', header: 'Monto', render: (t: BankTransaction) => (
                  <span className={`font-medium ${Number(t.amount) >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                    ${Number(t.amount).toLocaleString('es-MX', {minimumFractionDigits: 2})}
                  </span>
                )},
                { key: 'description', header: 'Descripción', render: (t: BankTransaction) => <span className="max-w-xs truncate block">{t.description}</span> },
                { key: 'reference', header: 'Referencia', render: (t: BankTransaction) => t.reference ? <code className="text-sm">{t.reference}</code> : <span className="text-gray-400">-</span> },
                { key: 'is_reconciled', header: 'Conciliado', render: (t: BankTransaction) => 
                  t.is_reconciled 
                    ? <Badge variant="success">Sí</Badge> 
                    : <Badge variant="warning">No</Badge>
                },
                { key: 'actions', header: 'Acciones', render: (t: BankTransaction) => (
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); openEditModal(t); }}>Editar</Button>
                    <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); handleDelete(t.id); }}>Eliminar</Button>
                  </div>
                )},
              ]}
              data={transactions}
              keyField="id"
            />
          )}
        </Card>
      ) : (
        <Card>
          <EmptyState 
            message="Selecciona una empresa para ver y gestionar sus movimientos bancarios"
            icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" /></svg>}
          />
        </Card>
      )}

      {/* Modal Crear/Editar Movimiento */}
      <Modal isOpen={showModal} onClose={() => { setShowModal(false); resetForm(); }} title={editingTx ? 'Editar Movimiento' : 'Nuevo Movimiento'}>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Input label="Empresa" disabled value={formData.company_id} />
            <Input label="Fecha" type="date" value={formData.transaction_date} onChange={e => setFormData({...formData, transaction_date: e.target.value})} required />
          </div>
          <Input label="Monto (negativo = gasto)" type="number" step="0.01" value={formData.amount} onChange={e => setFormData({...formData, amount: e.target.value})} required placeholder="-125.50" />
          <Input label="Descripción" value={formData.description} onChange={e => setFormData({...formData, description: e.target.value})} required />
          <Input label="Referencia" value={formData.reference} onChange={e => setFormData({...formData, reference: e.target.value})} placeholder="REF001" />
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" type="button" onClick={() => { setShowModal(false); resetForm(); }}>Cancelar</Button>
            <Button type="submit" disabled={submitting}>{submitting ? 'Guardando...' : (editingTx ? 'Actualizar' : 'Crear')}</Button>
          </div>
        </form>
      </Modal>

      {/* Modal Preview CSV */}
      <Modal isOpen={showPreviewModal} onClose={() => { setShowPreviewModal(false); setPreviewData([]); }} title="Previsualizar CSV" className="max-w-4xl">
        <div className="space-y-4">
          {previewData.length > 0 ? (
            <>
              <p className="text-sm text-gray-600">{previewData.length} transacciones detectadas</p>
              <div className="max-h-96 overflow-auto">
                <Table
                  columns={[
                    { key: 'transaction_date', header: 'Fecha', render: (t: BankTransactionRow) => t.transaction_date },
                    { key: 'amount', header: 'Monto', render: (t: BankTransactionRow) => <span className={Number(t.amount) >= 0 ? 'text-green-700' : 'text-red-700'}>${Number(t.amount).toLocaleString('es-MX', {minimumFractionDigits: 2})}</span> },
                    { key: 'description', header: 'Descripción', render: (t: BankTransactionRow) => t.description },
                    { key: 'reference', header: 'Referencia', render: (t: BankTransactionRow) => t.reference || '-' },
                  ]}
                  data={previewData.slice(0, 20)}
                  keyField="transaction_date"
                />
              </div>
              {previewData.length > 20 && <p className="text-sm text-gray-500 text-center">Mostrando 20 de {previewData.length} transacciones</p>}
              <div className="flex justify-end gap-3 pt-4 border-t">
                <Button variant="secondary" onClick={() => { setShowPreviewModal(false); setPreviewData([]); }}>Cancelar</Button>
                <Button onClick={() => handleImportCsv(previewData[0] as any)} disabled={importing || !selectedCompany}>{importing ? 'Importando...' : 'Importar y Guardar'}</Button>
              </div>
            </>
          ) : (
            <p className="text-gray-500 text-center py-8">No se pudieron detectar transacciones válidas en el archivo.</p>
          )}
        </div>
      </Modal>
    </div>
  );
}