import { useState, useEffect, useMemo } from 'react';
import { ticketsApi, type Ticket, type TicketCreate, type TicketExtractionResult } from '../services/api';
import { companiesApi, type Company } from '../services/api';
import type { Categoria } from '../types/dashboard';
import { 
  Button, Input, Modal, Table, Card, Badge, Loading, EmptyState, FileUpload, CameraCapture 
} from '../components/ui';
import {
  validateTicketForm, hasErrors, normalizeTicketForm,
  type TicketFormErrors, UNKNOWN_PROVIDER,
} from '../utils/validation';
import { dinero } from '../utils/format';
import { VeredictoTicket } from '../components/VeredictoTicket';

/** Como se filtra esta pantalla.
 *
 *  `sinClasificar` existe por el camino que llega desde el tablero: la barra
 *  gris a la que se le da clic. Es un filtro mas y no una pantalla aparte, y a
 *  proposito: clasificar un ticket es editarlo, y tener dos pantallas para lo
 *  mismo obliga a mantener dos listas, dos tablas y dos caminos para el mismo
 *  PATCH. */
type Filtro = 'todos' | 'sinClasificar' | 'clasificados';

export function Tickets({ filtroInicial }: { filtroInicial?: Filtro }) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [categorias, setCategorias] = useState<Categoria[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<string>('');
  const [filtro, setFiltro] = useState<Filtro>(filtroInicial ?? 'todos');
  // Que se abre en la pantalla que se acaba de llegar. Se sincroniza con
  // `filtroInicial` para que el clic en la barra gris del tablero abra aqui en
  // modo "sin clasificar" y no en la lista completa, que es lo que pasaria si
  // solo se usara el estado inicial: seLee una vez y nunca mas.
  useEffect(() => {
    if (filtroInicial) setFiltro(filtroInicial);
  }, [filtroInicial]);

  // Seleccion multiple para clasificar en lote.
  const [seleccionados, setSeleccionados] = useState<Set<string>>(new Set());
  const [clasificando, setClasificando] = useState(false);
  const [categoriaLote, setCategoriaLote] = useState('');

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
  // El archivo original del que salio la extraccion.
  //
  // Antes se perdia: handleExtract hacia la peticion de preview y luego soltaba
  // el File. El ticket se creaba despues por POST /tickets/, que es captura
  // manual y no guarda comprobante. Resultado: por la UI ningun ticket tenia el
  // original, y el muestreo de exactitud no tenia contra que contrastar.
  //
  // Se retiene aqui y se adjunta despues de crear, para no quitar el paso de
  // revision: el usuario confirma y corrige lo que leyó la IA antes de que exista
  // como registro contable.
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [avisoDocumento, setAvisoDocumento] = useState<string | null>(null);
  // Aviso de que editar cambia el significado del ticket. Sin el, elegir
  // "revisar y editar" parece la version menos completa de la misma accion.
  const [avisoEdicionManual, setAvisoEdicionManual] = useState<string | null>(null);
  // El veredicto del gate tras aceptar una lectura, para enseñarlo de inmediato.
  // Antes de esto el usuario creaba un ticket automatico y no se enteraba de que
  // habia pasado por el gate, ni de si habiaauto-aprobado o no.
  const [ultimoVeredicto, setUltimoVeredicto] = useState<Ticket | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      // El filtro se pide al servidor y no se filtra en el navegador. Filtrar en
      // el cliente significaria traer los 500 tickets para descartar 400, y con
      // el limite del endpoint (100 por pagina) la cuenta seria falsa: "no hay
      // sin clasificar" cuando en realidad no salio en la primera pagina.
      const [empresasData, ticketsData, categoriasData] = await Promise.all([
        companiesApi.list(),
        selectedCompany
          ? ticketsApi.list(selectedCompany, {
              sinCategoria: filtro === 'sinClasificar',
              conCategoria: filtro === 'clasificados',
              limit: 500,
            })
          : Promise.resolve([] as Ticket[]),
        ticketsApi.categorias(),
      ]);
      setCompanies(empresasData);
      setTickets(ticketsData);
      setCategorias(categoriasData);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar datos');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [selectedCompany, filtro]);

  // Al cambiar de filtro o de empresa, la seleccion se vacia. Sin esto, se
  // seleccionan cinco tickets en "sin clasificar", se cambia a "clasificados" y
  // la barra dice "5 seleccionados" sobre tickets que ya no estan en pantalla:
  // se clasifican cinco cosas que la persona no vio, y el resultado no se puede
  // explicar.
  useEffect(() => {
    setSeleccionados(new Set());
  }, [selectedCompany, filtro]);

  const alternarSeleccion = (id: string) => {
    setSeleccionados((prev) => {
      const siguiente = new Set(prev);
      if (siguiente.has(id)) siguiente.delete(id);
      else siguiente.add(id);
      return siguiente;
    });
  };

  const alternarTodos = () => {
    setSeleccionados((prev) =>
      prev.size === tickets.length ? new Set() : new Set(tickets.map((t) => t.id)),
    );
  };

  /** Clasifica lo seleccionado de golpe.
   *
   *  Se manda la categoria y nada mas: el endpoint no toca el estado de
   *  extraccion ni mete nada en la cola. Clasificar es un dato del gasto, no una
   *  aprobacion, y si se mezclaran las dos cosas "ya lo clasifique" pareceria
   *  una entrada mas de trabajo pendiente. */
  const clasificarSeleccion = async () => {
    if (seleccionados.size === 0) return;
    const ids = Array.from(seleccionados);
    // Cadena vacia = desclasificar. Se permite a proposito: corregir una
    // clasificacion equivocada tiene que ser tan facil como poner la correcta,
    // y si no hay salida, la gente deja de clasificar por miedo a equivocarse.
    const destino = categoriaLote.trim() === '' ? null : categoriaLote.trim();

    try {
      setClasificando(true);
      setError(null);
      const r = await ticketsApi.clasificarLote(ids, destino);
      // Si el servidor toco menos de los pedidos, se dice. Puede pasar sin que
      // haya un fallo: alguien borro un ticket entre que se abrio la pantalla
      // y se envio el PATCH. Decir "clasificados 200" cuando fueron 198 deja a
      // la persona creyendo que hay dos que se le quedaron, y no hay forma de
      // saber cuales.
      if (r.actualizados < r.pedidos) {
        setError(
          `Se clasificaron ${r.actualizados} de ${r.pedidos}: ` +
          `${r.pedidos - r.actualizados} ya no existían.`,
        );
      }
      setSeleccionados(new Set());
      setCategoriaLote('');
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo clasificar');
    } finally {
      setClasificando(false);
    }
  };


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
        const creado = await ticketsApi.create(normalized);

        // El comprobante original se sube DESPUES de crear, y su fallo no
        // deshace el ticket. El backend guarda la fila y los bytes con un
        // SAVEPOINT separado justamente para esto: perder el archivo es
        // recuperable (se vuelve a subir con PUT /tickets/{id}/documento),
        // perder el gasto no lo es.
        if (pendingFile) {
          try {
            await ticketsApi.subirDocumento(creado.id, pendingFile);
            setAvisoDocumento(null);
          } catch (e) {
            setAvisoDocumento(
              'El ticket se guardó, pero el comprobante original no se pudo adjuntar. ' +
              'Vuelve a subirlo desde la ficha del ticket.'
            );
          }
        }
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
    // Sin esto, el comprobante del ticket anterior se adjuntaria al siguiente.
    setPendingFile(null);
    setExtractionResult(null);
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
      setAvisoDocumento(null);
      const result = await ticketsApi.extract(file, fileType);
      setExtractionResult(result);
      setExtractionWarnings(collectExtractionWarnings(result));
      // Se retiene el archivo para adjuntarlo al ticket cuando el usuario
      // confirme. El endpoint de preview no persiste nada.
      setPendingFile(file);
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

  const abrirFormularioConExtraccion = () => {
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

  /** Cierra el modal de extraccion dejando la pantalla como estaba. */
  const cerrarExtraccion = () => {
    setShowExtractModal(false);
    setShowCamera(false);
    setExtractionResult(null);
    setExtractionWarnings([]);
  };

  /**
   * Via 1: el usuario ACEPTA la lectura tal cual.
   *
   * Pasa por `POST /tickets/extract-and-create`, que corre el confidence gate de
   * verdad: el ticket nace con `confidence`, `confidence_source` y el veredicto
   * que la evidencia sostiene, y si sale AUTO_APROBADO entra al muestreo del 5%.
   * Eso es lo que hace medible el SLO de exactitud por el camino que la gente
   * usa, que antes no pasaba por aqui.
   *
   * Se elige con un boton explicito, no comparando el formulario contra la
   * extraccion. Un diff de strings se rompe por cosas que no son correccion:
   * "4094.80" contra "4094.8", una fecha con otro formato, un rfc con espacios.
   * Ademas el usuario tiene que saber que esta eligiendo, porque las dos vias
   * ended en tickets con significado distinto.
   *
   * Si esta via falla (4xx), se degrada a la manual en vez de dejar al usuario
   * sin ticket: un ticket guardado con su comprobante vale mas que una lectura
   * perfecta que se perdio.
   */
  const aceptarLecturaDeIA = async () => {
    if (!pendingFile || !selectedCompany) return;
    const file = pendingFile;
    const fileType: 'pdf' | 'image' = file.type.startsWith('image/') || /\.(png|jpe?g)$/i.test(file.name)
      ? 'image' : 'pdf';
    try {
      setSubmitting(true);
      setError(null);
      setAvisoDocumento(null);
      const creado = await ticketsApi.extractAndCreate(file, selectedCompany, fileType);
      setUltimoVeredicto(creado);
      cerrarExtraccion();
      setShowModal(false);
      resetForm();
      await loadData();
    } catch (e) {
      // No se pierde la lectura: se vuelve a la via manual con el formulario
      // ya lleno, que es exactamente lo que el usuario iba a hacer.
      setError(
        'No se pudo guardar la lectura automática (' +
        (e instanceof Error ? e.message : 'error desconocido') +
        '). Se.abre el formulario para que la revises y la guardes a mano.'
      );
      abrirFormularioConExtraccion();
    } finally {
      setSubmitting(false);
    }
  };

  /**
   * Via 2: el usuario quiere EDITAR antes de guardar.
   *
   * Se registra como captura manual a proposito. Si una persona corrige a la IA,
   * el resultado ya no es lectura automatica, y contarlo como si lo fuera
   * inflaria la exactitud medida con datos que el sistema no leyo solo. El
   * formulario lo dice, para que no parezca un fallo.
   */
  const editarAntesDeGuardar = () => {
    setAvisoEdicionManual(
      'Vas a corregir estos datos, así que el ticket se guardará como captura manual ' +
      'y no contará para la medición de exactitud de la IA.'
    );
    abrirFormularioConExtraccion();
  };

  const columns = [
    {
      key: 'seleccion',
      header: '',
      render: (t: Ticket) => (
        <input
          type="checkbox"
          checked={seleccionados.has(t.id)}
          onChange={() => alternarSeleccion(t.id)}
          onClick={(e) => e.stopPropagation()}
          className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
          aria-label={`Seleccionar ${t.provider_name}`}
        />
      ),
    },
    { key: 'provider_name', header: 'Proveedor', render: (t: Ticket) => <span className="font-medium">{t.provider_name}</span> },
    { key: 'provider_tax_id', header: 'RFC Proveedor', render: (t: Ticket) => t.provider_tax_id ? <code className="text-sm">{t.provider_tax_id}</code> : <span className="text-gray-400">-</span> },
    { key: 'total_amount', header: 'Total', render: (t: Ticket) => <span className="font-medium text-green-700">{dinero(t.total_amount)}</span> },
    { key: 'tax_amount', header: 'IVA', render: (t: Ticket) => <span className="text-gray-600">{dinero(t.tax_amount)}</span> },
    { key: 'expense_date', header: 'Fecha', render: (t: Ticket) => new Date(t.expense_date).toLocaleDateString('es-MX') },
    { key: 'category', header: 'Categoría', render: (t: Ticket) => t.category ? <Badge>{t.category}</Badge> : <span className="text-amber-500 text-xs">sin clasificar</span> },
    { key: 'actions', header: 'Acciones', render: (t: Ticket) => (
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); openEditModal(t); }}>Editar</Button>
        <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); requestDelete(t); }}>Eliminar</Button>
      </div>
    )},
  ];

  // Cuanto dinero esta seleccionado. Con solo el conteo, alguien puede clasificar
  // veinte comprobantes de 50 pesos sin saber que son 1,000. El monto va
  // porque la pregunta "que estoy a punto de cambiar" es de dinero.
  const montoSeleccionado = useMemo(
    () => tickets
      .filter((t) => seleccionados.has(t.id))
      .reduce((a, t) => a + Number(t.total_amount || 0), 0),
    [tickets, seleccionados],
  );

  const sinClasificarCount = tickets.filter((t) => !t.category || t.category.trim() === '').length;

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

      <Card>
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

          {/* El filtro de clasificacion. Es lo que hace que la barra gris del
              tablero sea accionable en vez de decorativa. */}
          <div className="min-w-[240px]">
            <label className="block text-sm font-medium text-gray-700 mb-1">Mostrar</label>
            <div className="flex rounded-lg overflow-hidden border border-gray-300">
              {([
                { k: 'todos', l: 'Todos' },
                { k: 'sinClasificar', l: 'Sin clasificar' },
                { k: 'clasificados', l: 'Clasificados' },
              ] as const).map((o, i) => (
                <button
                  key={o.k}
                  onClick={() => setFiltro(o.k)}
                  className={`px-3 py-2 text-sm transition-colors ${
                    i > 0 ? 'border-l border-gray-300' : ''
                  } ${
                    filtro === o.k
                      ? 'bg-blue-600 text-white'
                      : 'bg-white text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {o.l}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)}>×</Button>
        </div>
      )}

      {/* Distinto del error: el ticket SI se guardo. Por eso va en ambar y no en
          rojo, y por eso no se limpia con el error. */}
      {avisoDocumento && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{avisoDocumento}</span>
          <Button variant="ghost" size="sm" onClick={() => setAvisoDocumento(null)}>×</Button>
        </div>
      )}

      {/* Que va a pasar con la medicion, dicho antes de que pase y no despues. */}
      {avisoEdicionManual && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{avisoEdicionManual}</span>
          <Button variant="ghost" size="sm" onClick={() => setAvisoEdicionManual(null)}>×</Button>
        </div>
      )}

      {/* El veredicto del gate, apenas se acepta una lectura. Antes de esto se
          creaba un ticket automatico sin que nadie se enterara de si habia
          pasado la verificacion o no. */}
      {ultimoVeredicto && (
        <div className="space-y-2">
          <VeredictoTicket ticket={ultimoVeredicto} />
          <button
            type="button"
            onClick={() => setUltimoVeredicto(null)}
            className="text-sm text-gray-500 hover:text-gray-700 underline"
          >
            Entendido
          </button>
        </div>
      )}

      {/* La barra de lote. Solo aparece con algo seleccionado, porque una barra
          vacia con un desplegable de categorias es ruido en la pantalla de todos
          los dias. */}
      {seleccionados.size > 0 && (
        <div className="sticky top-0 z-20 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-3 justify-between">
          <div>
            <p className="text-sm font-medium text-blue-900">
              {seleccionados.size} {seleccionados.size === 1 ? 'ticket seleccionado' : 'tickets seleccionados'}
            </p>
            <p className="text-xs text-blue-700">
              {dinero(montoSeleccionado)} en total
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={categoriaLote}
              onChange={e => setCategoriaLote(e.target.value)}
              className="px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 bg-white"
            >
              <option value="">Sin clasificar (quitar categoría)</option>
              {categorias.map(c => (
                <option key={c.clave} value={c.clave}>{c.etiqueta}</option>
              ))}
            </select>
            <Button onClick={clasificarSeleccion} disabled={clasificando}>
              {clasificando ? 'Clasificando...' : 'Aplicar a seleccionados'}
            </Button>
            <Button variant="ghost" onClick={() => setSeleccionados(new Set())}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      {selectedCompany ? (
        <Card>
          {loading ? (
            <Loading message="Cargando tickets..." />
          ) : tickets.length === 0 ? (
            <EmptyState
              message={
                filtro === 'sinClasificar'
                  ? 'No queda nada sin clasificar. Todo el gasto de esta empresa está en una categoría.'
                  : filtro === 'clasificados'
                    ? 'Ninguno de los tickets de esta empresa está clasificado todavía.'
                    : 'No hay tickets. Crea uno manualmente o extrae datos de un PDF.'
              }
              action={
                filtro !== 'todos' ? { label: 'Ver todos', onClick: () => setFiltro('todos') } : undefined
              }
            />
          ) : (
            <>
              <div className="flex items-center justify-between mb-3">
                <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={seleccionados.size > 0 && seleccionados.size === tickets.length}
                    onChange={alternarTodos}
                    className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
                  />
                  Seleccionar todos ({tickets.length})
                </label>
                {filtro === 'sinClasificar' && sinClasificarCount > 0 && (
                  <span className="text-xs text-amber-600">
                    {sinClasificarCount} sin categoría
                  </span>
                )}
              </div>
              <Table
                columns={columns}
                data={tickets}
                keyField="id"
              />
            </>
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
          {/* El veredicto de como se leyo este ticket. Solo al editar uno que ya
              existe: al crear, todavia no hay nada que veredicto. */}
          {editingTicket && <VeredictoTicket ticket={editingTicket} />}
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
                accept=".pdf,.png,.jpg,.jpeg,.heic,.heif,.tif,.tiff"
                onChange={files => {
                  const file = files[0];
                  if (!file) return;
                  // El `file_type` ya no decide nada: el backend deduce el
                  // formato de los bytes (`app/core/archivo_real.py`). Se sigue
                  // mandando por compatibilidad con la API, asi que se manda la
                  // extension del archivo y no una heuristica al vuelo.
                  //
                  // La heuristica anterior era `file.type.startsWith('image/') ||
                  // /\.(png|jpe?g)$/i`, y ese regex no cubria ni `.heic` ni
                  // `.tiff`: un TIFF que el selector de ARRIBA ya aceptaba
                  // salia etiquetado como `pdf`. Inofensivo con el backend de
                  // hoy, y exactamente el residuo del bug que cerro el commit
                  // del sniffing. Si alguien "limpia" el backend y vuelve a
                  // usar lo declarado, el front reintroduce el problema sin que
                  // nada lo delate.
                  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
                  const esImagen = file.type.startsWith('image/')
                    || ['heic', 'heif', 'tif', 'tiff', 'png', 'jpg', 'jpeg'].includes(ext);
                  handleExtract(file, esImagen ? 'image' : 'pdf');
                }}
                label="Arrastrar PDF o imagen de factura"
              />
              <p className="text-sm text-gray-500 text-center">
                PDF, PNG, JPG, HEIC y TIFF (máx 10MB)
              </p>
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
                  {/* Las dos vias. La separacion no es estetica: guardar por
                      `extract-and-create` hace que el ticket pase por el gate y
                      entre al muestreo, y guardar por el formulario lo deja como
                      captura manual. Elegir sin entender la diferencia seria elegir
                      al azar, asi que se explica antes de elegir. */}
                  <div className="bg-blue-50 border border-blue-200 text-blue-900 px-4 py-3 rounded-lg text-sm">
                    <p className="font-medium mb-1">¿Los datos están correctos?</p>
                    <p>
                      Si sí, se guardan con la verificación de la IA activada: el sistema
                      comprueba que la aritmética del comprobante cuadre y, si la lectura
                      es segura, la aprueba sin que nadie la revise (y entra al muestreo
                      para poder medirla). Si corriges algo, se guarda como captura manual.
                    </p>
                  </div>
                  <div className="flex gap-3 justify-end">
                    <Button variant="ghost" onClick={() => { setExtractionResult(null); setExtractionWarnings([]); }}>Nueva extracción</Button>
                    <Button variant="secondary" onClick={editarAntesDeGuardar}>
                      Revisar y corregir
                    </Button>
                    <Button onClick={aceptarLecturaDeIA} disabled={submitting || !pendingFile}>
                      {submitting ? 'Verificando...' : 'Aceptar y verificar con IA'}
                    </Button>
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