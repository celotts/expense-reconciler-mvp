import { useState, useEffect, useCallback } from 'react';
import {
  bankTransactionsApi, type BankTransaction, type BankTransactionCreate,
  type BankTransactionRow, type ConciliacionCajaResponse,
} from '../services/api';
import {
  Button, Input, Modal, Table, Card, Badge, Loading, EmptyState, FileUpload
} from '../components/ui';
import { fecha } from '../utils/format';
import { AvisoSinEmpresa } from '../contextos/Empresa';
import { useEmpresa } from '../contextos/EmpresaContexto';
import { useNavegacion } from '../contextos/NavegacionContexto';
import { cajaChicaApi, type CajaChica } from '../services/cajaChica';

export function BankTransactions() {
  const [transactions, setTransactions] = useState<BankTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Una sola empresa activa, la del contexto. Un selector aqui permitiria ver
  // cargos de una empresa mientras el header autoriza otra.
  const { empresaActiva } = useEmpresa();
  const { irA } = useNavegacion();
  const selectedCompany = empresaActiva?.company_id ?? '';
  const [cajasAbiertas, setCajasAbiertas] = useState<CajaChica[]>([]);
  const [conciliacionCaja, setConciliacionCaja] = useState<ConciliacionCajaResponse | null>(null);
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

  // `useCallback` no es adorno: el efecto de abajo solo debe recargar cuando
  // cambia la empresa, no en cada render. Sin el, poner `loadData` en las
  // dependencias (como exige eslint) disparaba una peticion nueva por cada
  // tecla del formulario.
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [txData, cajas] = await Promise.all([
        selectedCompany ? bankTransactionsApi.list(selectedCompany) : Promise.resolve([]),
        selectedCompany ? cajaChicaApi.list() : Promise.resolve([]),
      ]);
      setTransactions(txData);
      const abiertas = cajas.filter((c) => c.estado !== 'cerrada');
      setCajasAbiertas(abiertas);
      const primera = abiertas[0];
      setConciliacionCaja(
        primera ? await cajaChicaApi.conciliacionBancaria(primera.id) : null
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar datos');
    } finally {
      setLoading(false);
    }
  }, [selectedCompany]);

  useEffect(() => {
    loadData();
  }, [loadData]);

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

  /**
   * MEDIDO el 2026-10-10: el boton "Importar y Guardar" hacia
   * `handleImportCsv(previewData[0] as any)`.
   *
   * `previewData[0]` es una fila YA parseada (`{transaction_date, amount, ...}`),
   * y `handleImportCsv` espera un `File`. `FormData.append` con un objeto plano
   * lo convierte en la cadena `"[object Object]"`, el backend recibe texto donde
   * espera `UploadFile` y contesta 422. **El boton no podia funcionar nunca.**
   *
   * Y no era un `as any` de una linea: `handlePreviewCsv` recibia el `File`, lo
   * usaba para el preview y lo dejaba ir. No habia nada que reenviar. Por eso
   * aqui se retiene el archivo en el estado en vez de intentar adivinarlo desde
   * la previsualizacion.
   */
  const [csvFile, setCsvFile] = useState<File | null>(null);

  // ## POR QUE NO SE MANDAN NOMBRES DE COLUMNA NI FORMATO DE FECHA
  //
  // MEDIDO el 2026-10-12: este bloque mandaba `fecha`/`importe`/`concepto` y
  // `%d/%m/%Y` FIJOS, y con eso tres de cuatro CSVs reales de banco mexicano
  // daban 400. Un banco exporta `Fecha`, `Importe`, `Descripcion`; otro
  // `05/10/2026`; otro `2026-10-05` con punto y coma. Los cuatro son el mismo
  // archivo con distinta forma, y el unico que no podia leerlos era el que
  // exigia una forma exacta.
  //
  // Ahora NO se mandan: el backend detecta el separador y el formato de la
  // fecha sobre el contenido real, y dice cuales columnas encontró si falta
  // alguna. Mandarlos desde aqui fijaba el problema en el cliente, que es donde
  // no se puede ver el archivo.
  //
  // Los separadores de decimal y de miles SI se quedan, porque dependen de COMO
  // esta escrito el numero en ese banco y no se deducen del encabezado:
  //
  //   1,234.56  -> decimal '.', miles ','   (estilo中美)
  //   1.234,56  -> decimal ',', miles '.'   (estilo europeo)
  //
  // Mandar solo uno de los dos es peor que no mandar ninguno, porque convierte
  // 1.234,56 en 1.234 — cien veces el importe real, sin error ni aviso.
  const handlePreviewCsv = async (file: File) => {
    try {
      setPreviewing(true);
      const data = await bankTransactionsApi.importCsvPreview(file, {});
      setPreviewData(data);
      // El archivo se retiene para el paso de guardar. Sin esto, el unico
      // "archivo" disponible es una fila de la previsualizacion, y eso no es
      // un archivo.
      setCsvFile(file);
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
      // Sin columnas ni formato: los detecta el backend. Ver la razon en
      // `handlePreviewCsv`, que es donde se explica por que NO se mandan.
      await bankTransactionsApi.importCsvAndCreate(file, selectedCompany, {});
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

      <AvisoSinEmpresa mensaje="Elige una empresa activa en la barra lateral. El extracto no se puede importar sin saber a qué empresa pertenece." />

      {(cajasAbiertas.length > 0 || conciliacionCaja) && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-medium text-gray-900">Caja chica y reposiciones</p>
              <p className="mt-1 text-sm text-gray-600">
                {cajasAbiertas.length === 0
                  ? 'No hay cajas abiertas en esta empresa.'
                  : `Abiertas: ${cajasAbiertas.map((c) => c.nombre).join(' · ')}`}
              </p>
              {conciliacionCaja && (
                <p className="mt-1 text-sm text-gray-600">
                  {conciliacionCaja.sin_cruzar} reposición(es) sin cargo que cuadre.{' '}
                  {conciliacionCaja.advertencia ?? ''}
                </p>
              )}
              <p className="mt-1 text-xs text-gray-500">
                El cruce es solo lectura: no marca el cargo como reconciliado.
              </p>
            </div>
            <Button variant="secondary" onClick={() => irA('cajachica')}>Ver caja chica</Button>
          </div>
        </Card>
      )}

      <Card>
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[200px]">
            <p className="text-sm font-medium text-gray-700 mb-1">Empresa activa</p>
            <p className="text-sm text-gray-900">{empresaActiva?.name ?? 'Sin empresa activa'}</p>
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
                // `fecha()`, no `new Date(...)`. MEDIDO el 2026-10-12: un movimiento con
                // `transaction_date = "2026-10-01"` se mostraba `30/9/2026`, porque
                // `new Date('2026-10-01')` es medianoche UTC y Mexico es UTC-6.
                // Y en una conciliación esa fecha es la que cruza el gasto con el
                // banco. Ver `utils/format.ts`.
                { key: 'transaction_date', header: 'Fecha', render: (t: BankTransaction) => fecha(t.transaction_date) },
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
                    <Button variant="edit" size="sm" onClick={(e) => { e.stopPropagation(); openEditModal(t); }}>Editar</Button>
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
                <Button variant="secondary" onClick={() => { setShowPreviewModal(false); setPreviewData([]); setCsvFile(null); }}>Cancelar</Button>
                <Button
                  onClick={() => csvFile && handleImportCsv(csvFile)}
                  disabled={importing || !selectedCompany || !csvFile}
                >{importing ? 'Importando...' : 'Importar y Guardar'}</Button>
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