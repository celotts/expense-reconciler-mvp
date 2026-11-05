import { reconciliationsApi, type ReconciliationRunRequest, type ReconciliationRunResponse, type AccountingMapping, type AccountingMappingCreate, type Reconciliation } from '../services/api';
import { companiesApi } from '../services/api';
import { useState, useEffect } from 'react';
import {
  Button, Input, Modal, Table, Card, Badge, Loading, EmptyState
} from '../components/ui';

/**
 * Un importe, o un guion si no hay dato.
 *
 * ESTE ES EL ARREGLO DE FONDO, mas que las columnas. Antes cada celda hacia
 * `Number(x).toLocaleString()` sobre un campo que no existia, y `Number(undefined)`
 * es `NaN`: por eso se veia `$NaN` en vez de "no hay dato". UnHelpers asi no
 * puede decir "$NaN", porque el unico camino que produce un guion es `null`.
 *
 * La regla que sigue es la de R7 y la del informe de cierre: **un dato que no se
 * conoce se dice con guion y jamas con cero**. `null` es "no disponible"; `$0.00`
 * seria "se midio y dio cero", que es una afirmacion que nadie hizo.
 */
function dinero(valor: string | number | null | undefined): string {
  if (valor === null || valor === undefined || valor === '') return '—';
  const n = Number(valor);
  return Number.isNaN(n) ? '—' : `$${n.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// La fecha de esta pantalla NO es decorativa: es la que cruza el gasto con el
// movimiento del banco, y la que el revisor mira para decidir si concilian.
// MEDIDO el 2026-10-12: `new Date('2026-10-01')` es medianoche UTC y Mexico es
// UTC-6, asi que un gasto del 1 de octubre se mostraba como 30 de septiembre
// —al lado de un movimiento que si es del 1— y la fila parecia no cuadrar cuando
// el problema era la pantalla. Se delega en `utils/format.ts`, que parte el texto
// en vez de pasar la cadena por `Date`.
import { fecha } from '../utils/format';

export function Reconciliations() {
  // ## POR QUE ESTOS DOS ESTADOS YA NO SON `any[]`
  //
  // MEDIDO el 2026-10-10: eran `useState<any[]>`, y el `any` borro la
  // comprobacion de tipos que habria detectado que las columnas leian campos
  // que la API no manda. Resultado: `tsc` en verde y la pantalla llena de
  // `$NaN`. Un `any` en un estado que viene de la red no es atajo, es borrar el
  // unico sitio donde se podia ver el error antes de que llegara a un humano.
  const [reconciliations, setReconciliations] = useState<Reconciliation[]>([]);
  const [companies, setCompanies] = useState<{ id: string; name: string; tax_id: string }[]>([]);
  const [mappings, setMappings] = useState<AccountingMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<string>('');
  
  const [runLoading, setRunLoading] = useState(false);
  const [runResult, setRunResult] = useState<ReconciliationRunResponse | null>(null);
  const [showRunModal, setShowRunModal] = useState(false);
  const [runParams, setRunParams] = useState<ReconciliationRunRequest>({
    company_id: '', amount_tolerance: '0.01', date_tolerance_days: 3
  });

  const [exportLoading, setExportLoading] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportFormat, setExportFormat] = useState<'excel' | 'contpaqi' | 'generic'>('excel');
  const [exportParams, setExportParams] = useState({
    date_from: '', date_to: '', only_reconciled: true, columns: ''
  });

  const [showMappingModal, setShowMappingModal] = useState(false);
  const [mappingForm, setMappingForm] = useState<AccountingMappingCreate>({
    company_id: '', software_name: '', column_mappings: {}
  });
  const [mappingSubmitting, setMappingSubmitting] = useState(false);

  const loadData = async () => {
    try {
      setLoading(true);
      const [companiesData, mappingsData] = await Promise.all([
        companiesApi.list(),
        selectedCompany ? reconciliationsApi.listMappings(selectedCompany) : Promise.resolve([])
      ]);
      setCompanies(companiesData);
      setMappings(mappingsData);
      
      if (selectedCompany) {
        const reconData = await reconciliationsApi.list(selectedCompany);
        setReconciliations(reconData);
      }
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

  // ## POR QUE EL RESULTADO SE MUESTRA DENTRO DEL MODAL Y NO DETRAS
  //
  // MEDIDO el 2026-10-12: al pulsar "Ejecutar" el motor corria, devolvia 200, las
  // cifras se actualizaban en la pagina... y el modal SE QUEDABA ABIERTO con el
  // mismo formulario de siempre. El `runResult` se pintaba en una tarjeta de la
  // pagina, DEBAJO del modal, asi que el usuario veia el formulario otra vez y
  // ningun aviso de que algo habia pasado. Sin la fila de resultado a la vista,
  // el unico sintoma de que la conciliacion corrio es que las cifras de atras
  // cambiaron, y solo si se cierra el modal.
  //
  // Por eso el boton de Ejecutar se cambia por el resultado cuando ya hay
  // resultado: el modal pasa de "configurar" a "esto es lo que salio". Cerrar y
  // volver a abrir vuelve al formulario, porque `runResult` se limpia al cerrar
  // (ver el `onClose` del modal mas abajo).
  const handleRun = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setRunLoading(true);
      const result = await reconciliationsApi.run(runParams);
      setRunResult(result);
      setShowRunModal(true);
      loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al conciliar');
    } finally {
      setRunLoading(false);
    }
  };

  const handleExport = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setExportLoading(true);
      let blob: Blob;
      
      if (exportFormat === 'excel') {
        blob = await reconciliationsApi.exportExcel(selectedCompany, {
          date_from: exportParams.date_from || undefined,
          date_to: exportParams.date_to || undefined,
          only_reconciled: exportParams.only_reconciled,
        });
      } else if (exportFormat === 'contpaqi') {
        blob = await reconciliationsApi.exportContpaqi(selectedCompany, {
          date_from: exportParams.date_from || undefined,
          date_to: exportParams.date_to || undefined,
          only_reconciled: exportParams.only_reconciled,
        });
      } else {
        blob = await reconciliationsApi.exportGeneric(selectedCompany, {
          date_from: exportParams.date_from || undefined,
          date_to: exportParams.date_to || undefined,
          only_reconciled: exportParams.only_reconciled,
          columns: exportParams.columns || undefined,
        });
      }

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `export_${exportFormat}_${new Date().toISOString().split('T')[0]}.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      
      setShowExportModal(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al exportar');
    } finally {
      setExportLoading(false);
    }
  };

  const handleCreateMapping = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setMappingSubmitting(true);
      await reconciliationsApi.createMapping({
        ...mappingForm,
        company_id: selectedCompany,
      });
      setShowMappingModal(false);
      setMappingForm({ company_id: '', software_name: '', column_mappings: {} });
      loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al crear plantilla');
    } finally {
      setMappingSubmitting(false);
    }
  };

  const matchBadges: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    'PERFECT': 'success',
    'MANUAL': 'warning',
    'DISCREPANCY': 'danger',
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Conciliación</h1>
          <p className="text-gray-500">Ejecuta el motor automático y exporta reportes contables</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => { setShowExportModal(true); setExportFormat('excel'); }}>Exportar</Button>
          <Button onClick={() => { setRunParams({...runParams, company_id: selectedCompany}); setShowRunModal(true); }} disabled={!selectedCompany || runLoading}>
            Conciliar
          </Button>
        </div>
      </div>

      <Card>
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-sm font-medium text-gray-700 mb-1">Empresa</label>
            <select
              value={selectedCompany}
              onChange={e => setSelectedCompany(e.target.value)}
              className="w-full px-3 py-3 sm:py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
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
        <>
          {runResult && (
            <Card className="bg-green-50 border-green-200">
              <div className="grid grid-cols-2 md:grid-cols-7 gap-4 p-4">
                <div><p className="text-sm text-gray-500">Tickets</p><p className="text-2xl font-bold">{runResult.total_tickets}</p></div>
                <div><p className="text-sm text-gray-500">Mov. Banco</p><p className="text-2xl font-bold">{runResult.total_bank_transactions}</p></div>
                <div className="text-green-700"><p className="text-sm text-gray-500">PERFECT</p><p className="text-2xl font-bold">{runResult.perfect_matches}</p></div>
                <div className="text-yellow-700"><p className="text-sm text-gray-500">MANUAL</p><p className="text-2xl font-bold">{runResult.manual_review}</p></div>
                <div className="text-red-700"><p className="text-sm text-gray-500">DISCREPANCY</p><p className="text-2xl font-bold">{runResult.discrepancies}</p></div>
                <div><p className="text-sm text-gray-500">Tickets sin match</p><p className="text-2xl font-bold">{runResult.unmatched_tickets}</p></div>
                <div><p className="text-sm text-gray-500">Bancos sin match</p><p className="text-2xl font-bold">{runResult.unmatched_bank_transactions}</p></div>
              </div>
            </Card>
          )}

          <Card>
            {loading ? (
              <Loading message="Cargando conciliaciones..." />
            ) : reconciliations.length === 0 ? (
              <EmptyState 
                message="No hay conciliaciones. Ejecuta el motor de conciliación para emparejar tickets con movimientos bancarios."
                icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>}
              />
            ) : (
              <Table
                columns={[
                  { key: 'match_status', header: 'Estatus', render: (r: Reconciliation) => (
                    <Badge variant={matchBadges[r.match_status] || 'default'}>{r.match_status}</Badge>
                  )},
                  // ## POR QUE ESTAS COLUMNAS LEEN DE ABAJO Y NO DE ARRIBA
                  //
                  // MEDIDO el 2026-10-10: estas nueve columnas leian campos
                  // PLANOS (`r.ticket_amount`, `r.bank_amount`, `r.date_diff_days`)
                  // contra una respuesta que los trae ANIDADOS en `ticket` y
                  // `bank_transaction`. Cinco celdas salian `$NaN` y
                  // `Invalid Date` en pantalla.
                  //
                  // Y no lo cazaba `tsc` porque el estado era `useState<any[]>`:
                  // el `any` borraba justamente la comprobacion que lo habria
                  // detectado. Por eso el tipo es `Reconciliation` y no `any`, y
                  // por eso un campo mal escrito ahora se ve al compilar.
                  { key: 'ticket_provider', header: 'Proveedor (Ticket)', render: (r: Reconciliation) => (
                    // `truncate` y no solo `max-w-xs`: con el OCR de una foto el
                    // proveedor sale largo y Media texto lo partia en tres lineas,
                    // lo que estiraba la fila y empujaba las dos ultimas columnas
                    // fuera de la vista. Un nombre que no cabe entero se recorta
                    // con puntos y el tooltip lo deja legible.
                    <span className="block max-w-[12rem] truncate font-medium" title={r.ticket?.provider_name ?? ''}>
                      {r.ticket?.provider_name ?? '—'}
                    </span>
                  )},
                  { key: 'ticket_amount', header: 'Monto Ticket', render: (r: Reconciliation) => (
                    <span className="font-medium">{dinero(r.ticket?.total_amount)}</span>
                  )},
                  { key: 'ticket_date', header: 'Fecha Ticket', render: (r: Reconciliation) => (
                    <span>{fecha(r.ticket?.expense_date)}</span>
                  )},
                  { key: 'bank_description', header: 'Desc. Banco', render: (r: Reconciliation) => (
                    <span className="block max-w-[14rem] truncate" title={r.bank_transaction?.description ?? ''}>
                      {r.bank_transaction?.description ?? '—'}
                    </span>
                  )},
                  { key: 'bank_amount', header: 'Monto Banco', render: (r: Reconciliation) => (
                    <span className={Number(r.bank_transaction?.amount) >= 0 ? 'text-green-700' : 'text-red-700'}>
                      {dinero(r.bank_transaction?.amount)}
                    </span>
                  )},
                  { key: 'bank_date', header: 'Fecha Banco', render: (r: Reconciliation) => (
                    <span>{fecha(r.bank_transaction?.transaction_date)}</span>
                  )},
                  // `null` aqui es "no se pudo comprobar", y se dice con guion:
                  // un `$0.00` afirmaría que se midió y dio cero, que es lo que
                  // R7 prohíbe. Mismo trato que `exactitud` en el informe de cierre.
                  { key: 'amount_diff', header: 'Diff Monto', render: (r: Reconciliation) => (
                    r.amount_diff == null
                      ? <span className="text-gray-400">—</span>
                      : <span className={Number(r.amount_diff) === 0 ? 'text-green-700' : 'text-red-700'}>{dinero(r.amount_diff)}</span>
                  )},
                  { key: 'date_diff_days', header: 'Diff Días', render: (r: Reconciliation) => (
                    r.date_diff_days == null
                      ? <span className="text-gray-400">—</span>
                      : <span>{r.date_diff_days}</span>
                  )},
                ]}
                data={reconciliations}
                keyField="id"
              />
            )}
          </Card>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card>
              <h3 className="text-lg font-semibold mb-4">Exportar Reportes</h3>
              <Button variant="secondary" className="w-full" onClick={() => { setShowExportModal(true); setExportFormat('excel'); }}>
                Excel Estándar
              </Button>
              <Button variant="secondary" className="w-full mt-2" onClick={() => { setShowExportModal(true); setExportFormat('contpaqi'); }}>
                CONTPAQI (México)
              </Button>
              <Button variant="secondary" className="w-full mt-2" onClick={() => { setShowExportModal(true); setExportFormat('generic'); }}>
                Genérico Personalizado
              </Button>
            </Card>

            <Card>
              <h3 className="text-lg font-semibold mb-4">Plantillas de Mapeo Contable</h3>
              <Button variant="secondary" className="w-full mb-4" onClick={() => { setMappingForm({...mappingForm, company_id: selectedCompany}); setShowMappingModal(true); }}>
                Nueva Plantilla
              </Button>
              {mappings.length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-4">No hay plantillas. Crea una para exportar a CONTPAQI con tu configuración.</p>
              ) : (
                <div className="space-y-2">
                  {mappings.map(m => (
                    <div key={m.id} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between">
                      <div>
                        <p className="font-medium">{m.software_name}</p>
                        <p className="text-sm text-gray-500">Creada: {fecha(m.created_at)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      ) : (
        <Card>
          {companies.length === 0 ? (
            <EmptyState 
              message="No hay empresas registradas. Crea una para empezar a conciliar."
              icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4v16m8-8H4" /></svg>}
            />
          ) : (
            <EmptyState 
              message="Selecciona una empresa para acceder a la conciliación y exportaciones"
              icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>}
            />
          )}
        </Card>
      )}

      {/* El modal tiene DOS estados, y el segundo es el que faltaba. Antes de correr
          el motor es un formulario; despues, el resultado. Y el resultado va
          AQUI DENTRO, no en la tarjeta de la pagina: MEDIDO el 2026-10-12, el
          `runResult` se renderizaba en el cuerpo, que queda detras de este
          overlay `z-50`. El usuario pulsaba "Ejecutar", el motor corria, y lo
          unico que veia era el mismo formulario otra vez.

          El titulo cambia con el estado porque el titulo es lo primero que se
          lee: si dice "Ejecutar" cuando ya se ejecuto, el usuario pulses otra
          vez. */}
      <Modal
        isOpen={showRunModal}
        onClose={() => { setShowRunModal(false); setRunResult(null); }}
        title={runResult ? 'Resultado de la conciliación' : 'Ejecutar Conciliación Automática'}
      >
        {runResult ? (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              El motor comparó {runResult.total_tickets} tickets contra{' '}
              {runResult.total_bank_transactions} movimientos bancarios.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div><p className="text-sm text-gray-500">PERFECT</p><p className="text-2xl font-bold text-green-700">{runResult.perfect_matches}</p></div>
              <div><p className="text-sm text-gray-500">MANUAL</p><p className="text-2xl font-bold text-yellow-700">{runResult.manual_review}</p></div>
              <div><p className="text-sm text-gray-500">DISCREPANCY</p><p className="text-2xl font-bold text-red-700">{runResult.discrepancies}</p></div>
              <div><p className="text-sm text-gray-500">Sin cruzar</p><p className="text-2xl font-bold">{runResult.unmatched_tickets}</p></div>
            </div>
            {runResult.discrepancies > 0 && (
              <p className="text-sm text-amber-800">
                Hay {runResult.discrepancies} {runResult.discrepancies === 1 ? 'discrepancia' : 'discrepancias'}: hay un movimiento en la fecha correcta con otro monto, y eso lo tiene que ver una persona.
              </p>
            )}
            {runResult.perfect_matches + runResult.manual_review === 0 && runResult.unmatched_tickets > 0 && (
              <p className="text-sm text-gray-600">
                No se cruzó nada. Si no hay movimientos bancarios importados, el motor no tiene contra qué comparar.
              </p>
            )}
            <div className="flex justify-end gap-3 pt-4 border-t">
              <Button variant="secondary" type="button" onClick={() => { setShowRunModal(false); setRunResult(null); }}>
                Cerrar
              </Button>
              <Button
                type="button"
                onClick={() => { setRunResult(null); }}
              >
                Ejecutar otra vez
              </Button>
            </div>
          </div>
        ) : (
        <form onSubmit={handleRun} className="space-y-4">
          <p className="text-sm text-gray-600">El motor comparará tickets vs movimientos bancarios por monto y fecha.</p>
          <div className="grid grid-cols-2 gap-4">
            <Input label="Tolerancia Monto" type="number" step="0.01" value={runParams.amount_tolerance} onChange={e => setRunParams({...runParams, amount_tolerance: e.target.value})} />
            <Input label="Tolerancia Días" type="number" value={runParams.date_tolerance_days} onChange={e => setRunParams({...runParams, date_tolerance_days: parseInt(e.target.value)})} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Input label="Fecha Desde" type="date" value={runParams.date_from || ''} onChange={e => setRunParams({...runParams, date_from: e.target.value})} />
            <Input label="Fecha Hasta" type="date" value={runParams.date_to || ''} onChange={e => setRunParams({...runParams, date_to: e.target.value})} />
          </div>
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" type="button" onClick={() => setShowRunModal(false)}>Cancelar</Button>
            <Button type="submit" disabled={runLoading}>{runLoading ? 'Conciliando...' : 'Ejecutar'}</Button>
          </div>
        </form>
        )}
      </Modal>

      <Modal isOpen={showExportModal} onClose={() => { setShowExportModal(false); setExportLoading(false); }} title={`Exportar ${exportFormat.toUpperCase()}`}>
        <form onSubmit={handleExport} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Input label="Fecha Desde" type="date" value={exportParams.date_from} onChange={e => setExportParams({...exportParams, date_from: e.target.value})} />
            <Input label="Fecha Hasta" type="date" value={exportParams.date_to} onChange={e => setExportParams({...exportParams, date_to: e.target.value})} />
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="only_reconciled" checked={exportParams.only_reconciled} onChange={e => setExportParams({...exportParams, only_reconciled: e.target.checked})} className="w-4 h-4 text-blue-600 rounded" />
            <label htmlFor="only_reconciled" className="text-sm">Solo conciliados (PERFECT + MANUAL)</label>
          </div>
          {exportFormat === 'generic' && (
            <Input label="Columnas (separadas por coma)" value={exportParams.columns} onChange={e => setExportParams({...exportParams, columns: e.target.value})} placeholder="Fecha,Proveedor,Total,Estatus Conciliacion" />
          )}
          {exportFormat === 'contpaqi' && (
            /* Esto no es un aviso decorativo: es la diferencia entre un archivo
             * utilizable y uno que el contador no puede subir. Sin plantilla, las
             * ocho columnas que CONTPAQI necesita (TipoComprobante, Serie, Folio,
             * Moneda, TipoCambio, MetodoPago, UsoCFDI, Cuenta) salen VACIAS.
             *
             * Antes salian con literales fijos del backend — Moneda="MXN",
             * TipoCambio="1.0000" — que un contador decia al verlos y leia como
             * "el sistema lo determino". Para un comprobante en USD eso es falso,
             * y sin marca de error. Ahora la celda vacia dice "no lo se", que es
             * verdad y se corrige a mano. El archivo no se importa igual sin
             * plantilla, y por eso se dice ANTES de descargar y no despues. */
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-sm text-amber-900">
                <strong>Sin plantilla, el archivo sale incompleto.</strong> Las columnas de
                tipo de comprobante, serie, folio, moneda, tipo de cambio, metodo de pago, uso
                CFDI y cuenta van vacias: el sistema no las inventa, porque un valor inventado
                en una celda se ve igual que uno real.
              </p>
              <button
                type="button"
                onClick={() => { setShowExportModal(false); setExportLoading(false); setShowMappingModal(true); }}
                className="mt-2 text-sm text-amber-900 underline"
              >
                Crear una plantilla de mapeo
              </button>
            </div>
          )}
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" type="button" onClick={() => { setShowExportModal(false); setExportLoading(false); }}>Cancelar</Button>
            <Button type="submit" disabled={exportLoading}>{exportLoading ? 'Exportando...' : 'Descargar'}</Button>
          </div>
        </form>
      </Modal>

      <Modal isOpen={showMappingModal} onClose={() => { setShowMappingModal(false); setMappingForm({ company_id: '', software_name: '', column_mappings: {} }); }} title="Nueva Plantilla de Mapeo">
        <form onSubmit={handleCreateMapping} className="space-y-4">
          <Input label="Nombre Software" value={mappingForm.software_name} onChange={e => setMappingForm({...mappingForm, software_name: e.target.value})} required placeholder="CONTPAQI, SICOSS, Custom..." />
          <div className="bg-gray-50 p-4 rounded-lg">
            <p className="text-sm text-gray-600 mb-3">Mapeo de columnas (JSON). Ejemplo:</p>
            <pre className="text-xs bg-white p-2 rounded overflow-auto">
{`{"Fecha": "Fecha", "Concepto": "Concepto", "RFC": "RFC", "Nombre": "Proveedor", "Total": "Total", "Cuenta": "6000", "Moneda": "MXN"}`}
            </pre>
          </div>
          <textarea
            value={JSON.stringify(mappingForm.column_mappings, null, 2)}
            onChange={e => setMappingForm({...mappingForm, column_mappings: JSON.parse(e.target.value || '{}')})}
            className="w-full p-3 border border-gray-300 rounded-lg font-mono text-sm min-h-[150px]"
            placeholder='{"Fecha": "Fecha", "Concepto": "Concepto", "Total": "Total"}'
          />
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" type="button" onClick={() => { setShowMappingModal(false); setMappingForm({ company_id: '', software_name: '', column_mappings: {} }); }}>Cancelar</Button>
            <Button type="submit" disabled={mappingSubmitting}>{mappingSubmitting ? 'Creando...' : 'Crear Plantilla'}</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}