import { useState, useEffect, useCallback } from 'react';
import { ticketsApi, companiesApi, type Company, type Ticket, type ExtractionStatus, type LineaTicketUpdate } from '../services/api';
import { Button, Card, Badge, Loading, EmptyState, Select, Input } from '../components/ui';
import {
  STATUS_ORDEN, explainValidationErrors, confidenceLabel, sourceLabel,
  statusMeta, nombreProvisorLegible,
} from '../utils/extraction';
import { VerDocumento } from '../components/TicketDocumento';

/**
 * Cola de revision: todo lo que el sistema NO se atrevio a dar por bueno.
 *
 * Esta pantalla existe por una razon puntual. Los tickets que la IA no pudo
 * leer se guardan con estado PENDIENTE y motivo. Si no hay una pantalla que
 * los muestre, esos documentos existen en la base y no existen para nadie: el
 * error es invisible y no se corrige nunca. Un ticket ilegible que nadie ve es
 * peor que un ticket que falla, porque al menos del fallo te enteras.
 *
 * La regla de la pantalla: nunca se muestra un ticket en la cola sin decir
 * POR QUE esta ahi. Si el motivo no se reconoce, se muestra el codigo crudo,
 * porque un motivo escondido no se puede corregir.
 */
export function ReviewQueue() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<string>('');
  const [filtro, setFiltro] = useState<ExtractionStatus | ''>('');
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [porEstado, setPorEstado] = useState<Partial<Record<ExtractionStatus, number>>>({});
  const [totalAbiertos, setTotalAbiertos] = useState(0);
  const [antiguedad, setAntiguedad] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [descartando, setDescartando] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    try {
      setLoading(true);
      const cola = await ticketsApi.reviewQueue(companyId || undefined, filtro || undefined);
      setTickets(cola.tickets);
      setPorEstado(cola.por_estado);
      setTotalAbiertos(cola.total_open);
      setAntiguedad(cola.antiguedad_promedio_dias);
      setError(null);
    } catch (e) {
      // El error y la lista vacia son cosas distintas. Si falla la carga y
      // dejamos la lista vacia, la pantalla dice "todo bien" cuando en
      // realidad no sabemos nada: el peor mensaje posible.
      setError(e instanceof Error ? e.message : 'No se pudo cargar la cola de revisión');
    } finally {
      setLoading(false);
    }
  }, [companyId, filtro]);

  useEffect(() => { cargar(); }, [cargar]);

  useEffect(() => {
    companiesApi.list().then(setCompanies).catch(() => setCompanies([]));
  }, []);

  return (
    <div className="p-4 lg:p-8 max-w-7xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Cola de revisión</h1>
          <p className="text-sm text-gray-500 mt-1">
            Documentos que el sistema no se atrevió a aprobar solo.
          </p>
        </div>
        <div className="flex gap-2">
          <div className="w-56">
            <Select
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              options={[
                { value: '', label: 'Todas las empresas' },
                ...companies.map(c => ({ value: c.id, label: c.name })),
              ]}
            />
          </div>
          <div className="w-48">
            <Select
              value={filtro}
              onChange={(e) => setFiltro(e.target.value as ExtractionStatus | '')}
              options={[
                { value: '', label: 'Todos los estados' },
                ...STATUS_ORDEN.map(s => ({ value: s, label: statusMeta(s).label })),
              ]}
            />
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm flex items-start gap-2">
          <span className="font-medium">No se pudo cargar la cola:</span>
          <span className="flex-1">{error}</span>
          <Button variant="secondary" onClick={cargar} className="!py-1 !px-2 !text-xs">Reintentar</Button>
        </div>
      )}

      <Resumen
        totalAbiertos={totalAbiertos}
        porEstado={porEstado}
        antiguedad={antiguedad}
        onFiltrar={(s) => setFiltro(s === filtro ? '' : s)}
        filtroActivo={filtro}
      />

      <div className="mt-4">
        {loading ? (
          <Loading message="Cargando cola de revisión..." />
        ) : tickets.length === 0 ? (
          <Card>
            <EmptyState
              message={error
                ? 'No se pudo leer la cola. Revisa el mensaje arriba.'
                : filtro
                  ? `No hay tickets en estado "${statusMeta(filtro).label}".`
                  : 'La cola está vacía. Todo lo que se leyó pasó los checks.'}
            />
          </Card>
        ) : (
          <div className="space-y-3">
            {tickets.map(t => (
              <TicketEnCola
                key={t.id}
                ticket={t}
                onRefresh={cargar}
                onEditar={() => setEditando(t.id)}
                onDescartar={() => setDescartando(t.id)}
                editando={editando === t.id}
                onCancelarEdicion={() => setEditando(null)}
              />
            ))}
          </div>
        )}
      </div>

      {descartando && (
        <DialogoDescartar
          ticket={tickets.find(t => t.id === descartando)!}
          onCerrar={() => setDescartando(null)}
          onHecho={async () => { setDescartando(null); await cargar(); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------


// Los subcomponentes se exportan para poder renderizarlos con datos reales de la
// API sin navegador (scripts/verify_review_queue_ui.mjs). El render es donde
// se rompe lo que TypeScript no ve: un estado sin etiqueta, un campo en null
// inesperado, un motivo que no se traduce.

export function Resumen({
  totalAbiertos, porEstado, antiguedad, onFiltrar, filtroActivo,
}: {
  totalAbiertos: number;
  porEstado: Partial<Record<ExtractionStatus, number>>;
  antiguedad: number | null;
  onFiltrar: (s: ExtractionStatus) => void;
  filtroActivo: ExtractionStatus | '';
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      <Card>
        <p className="text-xs text-gray-500 uppercase tracking-wider">En cola</p>
        <p className={`text-3xl font-bold mt-1 ${totalAbiertos > 0 ? 'text-red-600' : 'text-green-600'}`}>
          {totalAbiertos}
        </p>
        {antiguedad !== null && (
          <p className="text-xs text-gray-500 mt-1">
            {antiguedad === 0
              ? 'Entraron hoy'
              : `Entrada hace ${antiguedad} días en promedio`}
          </p>
        )}
      </Card>

      {STATUS_ORDEN.map(s => {
        const n = porEstado[s] ?? 0;
        const activo = filtroActivo === s;
        return (
          <button
            key={s}
            onClick={() => onFiltrar(s)}
            title={statusMeta(s).help}
            className={`text-left rounded-xl shadow-sm border p-4 transition-colors ${
              activo ? 'border-blue-400 bg-blue-50 ring-1 ring-blue-300' : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
          >
            <p className="text-xs text-gray-500 uppercase tracking-wider">{statusMeta(s).label}</p>
            <p className="text-3xl font-bold mt-1 text-gray-900">{n}</p>
            <p className="text-xs text-gray-500 mt-1">
              {activo ? 'Filtro activo — clic para quitarlo' : 'Clic para filtrar'}
            </p>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------

export function TicketEnCola({
  ticket, editando, onEditar, onCancelarEdicion, onDescartar, onRefresh,
}: {
  ticket: Ticket;
  editando: boolean;
  onEditar: () => void;
  onCancelarEdicion: () => void;
  onDescartar: () => void;
  onRefresh: () => void | Promise<void>;
}) {
  const problemas = explainValidationErrors(ticket.validation_errors);
  const meta = statusMeta(ticket.extraction_status);
  const conf = confidenceLabel(ticket.confidence, ticket.confidence_source);

  const [ocupado, setOcupado] = useState(false);
  const [errorAccion, setErrorAccion] = useState<string | null>(null);

  const aprobar = async (correcciones?: Partial<{
    provider_name: string; provider_tax_id: string;
    total_amount: string; tax_amount: string; expense_date: string;
  }>) => {
    setOcupado(true);
    setErrorAccion(null);
    try {
      await ticketsApi.review(ticket.id, { action: 'approve', ...correcciones });
      await onRefresh();
    } catch (e) {
      setErrorAccion(e instanceof Error ? e.message : 'No se pudo aprobar');
      // Si no se pudo aprobar, el usuario va a tener que corregir. Dejarlo en
      // la vista de solo lectura lo obliga a adivinar que hay que abrir el
      // editor, asi que se abre solo.
      onEditar();
    } finally {
      setOcupado(false);
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span title={meta.help}>
              <Badge variant={meta.variant}>{meta.label}</Badge>
            </span>
            <span title={conf.help} className="text-xs text-gray-500">
              Confianza: <span className="font-medium text-gray-700">{conf.texto}</span>
            </span>
            <span className="text-xs text-gray-500">
              Origen: <span className="font-medium text-gray-700">{sourceLabel(ticket.source_type)}</span>
            </span>
            {ticket.source_file && (
              <span className="text-xs text-gray-400 font-mono truncate" title={ticket.source_file}>
                {ticket.source_file}
              </span>
            )}
          </div>

          {editando ? (
            <EditorTicket
              ticket={ticket}
              ocupado={ocupado}
              onCancelar={onCancelarEdicion}
              onAprobar={aprobar}
            />
          ) : (
            <>
              <p className="mt-2 text-lg font-semibold text-gray-900 truncate">
                {nombreProvisorLegible(ticket.provider_name)
                  ?? <span className="text-red-600">Sin proveedor identificado</span>}
              </p>
              <p className="text-sm text-gray-600">
                Total <span className="font-semibold">{ticket.total_amount}</span>
                {' · '}
                IVA <span className="font-semibold">{ticket.tax_amount}</span>
                {' · '}
                {ticket.expense_date}
                {ticket.provider_tax_id && ` · RFC ${ticket.provider_tax_id}`}
              </p>

              {problemas.length > 0 ? (
                <ul className="mt-3 space-y-1.5">
                  {problemas.map((p, i) => (
                    <li key={`${p.code}-${i}`} className="flex gap-2 text-sm text-red-700">
                      <span aria-hidden>•</span>
                      <span>{p.message}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-sm text-gray-500 italic">
                  Sin problemas registrados, pero está en la cola: la confianza no llegó al umbral
                  de auto-aprobación. Revísalo igual.
                </p>
              )}

              <VerDocumento ticket={ticket} />
            </>
          )}

          {errorAccion && (
            <p className="mt-3 p-2 bg-red-50 border border-red-200 rounded text-sm text-red-800">
              {errorAccion}
            </p>
          )}
        </div>

        {!editando && (
          <div className="flex gap-2 shrink-0">
            <Button onClick={onEditar} variant="secondary">Corregir</Button>
            <Button onClick={() => aprobar()} disabled={ocupado}>
              Aprobar
            </Button>
            <Button onClick={onDescartar} variant="secondary" className="!text-red-700">
              Descartar
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------

export function EditorTicket({
  ticket, ocupado, onCancelar, onAprobar,
}: {
  ticket: Ticket;
  ocupado: boolean;
  onCancelar: () => void;
  onAprobar: (c: {
    provider_name: string;
    provider_tax_id?: string;
    total_amount: string;
    tax_amount?: string;
    expense_date: string;
    subtotal?: string;
    items?: LineaTicketUpdate[];
  }) => void | Promise<void>;
}) {
  const [form, setForm] = useState({
    provider_name: nombreProvisorLegible(ticket.provider_name) ?? '',
    provider_tax_id: ticket.provider_tax_id ?? '',
    total_amount: ticket.total_amount,
    tax_amount: ticket.tax_amount,
    expense_date: ticket.expense_date,
    subtotal: ticket.subtotal ?? '',
  });

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }));

  // Las lineas viven en su propio estado y NO en `form`, porque son una lista:
  // meterlas en el objeto del formulario las mezclaria con los campos de texto y
  // el `set` de arriba (`e.target.value`) no aplicaria.
  //
  // Se siembran con las del ticket si las hay. Si `items` es `null` se arranca
  // con UNA fila vacia, no con cero: un ticket sin lineas es el caso normal de la
  // ruta OCR, y una tabla vacia no dice "agrega las tuyas" — parece que no hay
  // nada que capturar.
  const [lineas, setLineas] = useState<LineaTicketUpdate[]>(
    ticket.items && ticket.items.length > 0
      ? ticket.items.map(l => ({
          description: l.description ?? '',
          quantity: l.quantity ?? '',
          unit_price: l.unit_price ?? '',
          total: l.total ?? '',
        }))
      : [{ description: '', quantity: '', unit_price: '', total: '' }]
  );

  const setLinea = (i: number, k: keyof LineaTicketUpdate) => (
    e: React.ChangeEvent<HTMLInputElement>
  ) => setLineas(ls => ls.map((l, j) => (j === i ? { ...l, [k]: e.target.value } : l)));

  const agregarLinea = () =>
    setLineas(ls => [...ls, { description: '', quantity: '', unit_price: '', total: '' }]);

  const quitarLinea = (i: number) => setLineas(ls => ls.filter((_, j) => j !== i));

  return (
    <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded-lg space-y-3">
      {/* El papel va ANTES que el formulario, no despues. "Corrige contra el
          documento original" es la instruccion de esta pantalla, y si el
          documento se abre despues de que alguien ya escribio el total, la
          correccion se hizo sin mirarlo. Ver el papel es la parte que cuesta;
          corregir despues es lo rapido. */}
      <VerDocumento ticket={ticket} />

      <p className="text-xs text-gray-600">
        Corrige contra el documento original y pulsa <strong>Aprobar y guardar</strong>. El sistema
        vuelve a validar: si los números siguen sin cuadrar, no deja pasar el ticket.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Input label="Proveedor" value={form.provider_name} onChange={set('provider_name')} required />
        <Input label="RFC" value={form.provider_tax_id} onChange={set('provider_tax_id')} />
        <Input label="Total" value={form.total_amount} onChange={set('total_amount')} required />
        {/* El subtotal no estaba, y sin el `subtotal + IVA == total` no se puede
            comprobar despues de corregir el total. Es el campo que hace falta
            para que el sistema pueda volver a decirte si los numeros cuadran. */}
        <Input
          label="Subtotal"
          value={form.subtotal}
          onChange={set('subtotal')}
          placeholder="opcional"
        />
        <Input label="IVA" value={form.tax_amount} onChange={set('tax_amount')} />
        <Input label="Fecha" value={form.expense_date} onChange={set('expense_date')} required type="date" />
      </div>

      {/* Las partidas del comprobante.
          Es la parte que hace que el inventario tenga entrada: sin lineas, la
          compra no se abre y el stock no se puede contar. Y hay una razon para
          que sea opcional y no obligatoria: muchos tickets de gasto NO son de
          inventario (una nota, un casero, una tarifa), y exigir detalle ahi
          obligaria a inventar lineas para poder guardar el gasto. */}
      <fieldset className="border-t border-gray-200 pt-3">
        <legend className="text-xs font-semibold text-gray-700">
          Partidas del comprobante
          <span className="font-normal text-gray-500">
            {' '}(opcional — solo si el comprobante lista artículos)
          </span>
        </legend>

        {lineas.length === 0 ? (
          <p className="text-xs text-gray-500 my-2">
            Sin partidas. Si el comprobante no lista artículos, déjalo así: el gasto se guarda
            igual y solo no entra al inventario.
          </p>
        ) : (
          <div className="space-y-2">
            {lineas.map((l, i) => (
              <div key={i} className="grid grid-cols-12 gap-2 items-end">
                <div className="col-span-5">
                  <Input
                    label="Descripción"
                    value={l.description ?? ''}
                    onChange={setLinea(i, 'description')}
                  />
                </div>
                <div className="col-span-2">
                  <Input label="Cant." value={l.quantity ?? ''} onChange={setLinea(i, 'quantity')} />
                </div>
                <div className="col-span-2">
                  <Input
                    label="P. unit"
                    value={l.unit_price ?? ''}
                    onChange={setLinea(i, 'unit_price')}
                  />
                </div>
                <div className="col-span-2">
                  <Input label="Importe" value={l.total ?? ''} onChange={setLinea(i, 'total')} />
                </div>
                <div className="col-span-1">
                  <button
                    type="button"
                    onClick={() => quitarLinea(i)}
                    disabled={ocupado}
                    className="text-xs text-red-600 hover:text-red-800 disabled:opacity-40 py-2"
                    aria-label={`Quitar la partida ${i + 1}`}
                  >
                    Quitar
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          onClick={agregarLinea}
          disabled={ocupado}
          className="mt-2 text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
        >
          + Agregar partida
        </button>
      </fieldset>

      <div className="flex gap-2 justify-end">
        <Button onClick={onCancelar} variant="secondary" disabled={ocupado}>Cancelar</Button>
        <Button
          onClick={() => onAprobar({
            provider_name: form.provider_name.trim(),
            // Un RFC vacio se manda como ausente, no como cadena vacia: el
            // backend distingue "no hay RFC" de "el RFC esta en blanco".
            provider_tax_id: form.provider_tax_id.trim() || undefined,
            tax_amount: form.tax_amount.trim() || undefined,
            subtotal: form.subtotal.trim() || undefined,
            total_amount: form.total_amount.trim(),
            expense_date: form.expense_date,
            // Solo se mandan las lineas que tienen ALGO. La fila vacia que se
            // siembra para invites a escribir no puede acabar en la base como una
            // partida sin descripcion: `interpretar_items` la descartaria con
            // motivo, y el ruido en la cola de productos es peor que no tener la
            // fila.
            items: lineas
              .filter(l => (l.description ?? '').trim() || (l.total ?? '').trim())
              .map(l => ({
                description: (l.description ?? '').trim() || undefined,
                quantity: (l.quantity ?? '').trim() || undefined,
                unit_price: (l.unit_price ?? '').trim() || undefined,
                total: (l.total ?? '').trim() || undefined,
              })),
          })}
          disabled={ocupado}
        >
          {ocupado ? 'Guardando...' : 'Aprobar y guardar'}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

export function DialogoDescartar({
  ticket, onCerrar, onHecho,
}: {
  ticket: Ticket;
  onCerrar: () => void;
  onHecho: () => void | Promise<void>;
}) {
  const [motivo, setMotivo] = useState('');
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirmar = async () => {
    if (!motivo.trim()) {
      setError('Escribe por qué se descarta. Sin motivo, nadie puede revisar la decisión después.');
      return;
    }
    setOcupado(true);
    setError(null);
    try {
      await ticketsApi.review(ticket.id, { action: 'reject', notes: motivo.trim() });
      await onHecho();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo descartar');
    } finally {
      setOcupado(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <Card className="w-full max-w-md">
        <h2 className="text-lg font-bold text-gray-900">Descartar documento</h2>
        <p className="mt-1 text-sm text-gray-600">
          Sale de la cola y no entra a conciliación. No se borra: queda registrado como rechazado.
        </p>
        <p className="mt-2 text-sm text-gray-500">
          {ticket.source_file && <span className="font-mono">{ticket.source_file}</span>}
          {ticket.source_file && ' · '}
          {nombreProvisorLegible(ticket.provider_name) ?? 'sin proveedor'}
        </p>

        <div className="mt-4">
          <Input
            label="Motivo"
            value={motivo}
            onChange={(e) => { setMotivo(e.target.value); setError(null); }}
            placeholder="Foto borrosa, papel ilegible, duplicado..."
            error={error ?? undefined}
          />
        </div>

        <div className="mt-5 flex gap-2 justify-end">
          <Button onClick={onCerrar} variant="secondary" disabled={ocupado}>Cancelar</Button>
          <Button onClick={confirmar} disabled={ocupado} className="!bg-red-600 hover:!bg-red-700">
            {ocupado ? 'Descartando...' : 'Descartar'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
