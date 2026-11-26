/** Caja chica, movimientos y huella fiscal.
 *
 *  Dos reglas gobiernan esta pantalla y no se negocian aqui:
 *
 *  1. El saldo y el aviso de umbral los afirma el BACKEND. El front no suma
 *     movimientos: un `saldo_actual` calculado dos veces es dos verdades.
 *  2. Un ticket no se vincula a la caja sin antes comprobar su huella fiscal.
 *     El aviso no es decorativo: si el servidor nombra un comprobante previo,
 *     el boton de registrar queda deshabilitado.
 *
 *  Las props `*`Iniciales existen para el verificador de HTML. `renderToStaticMarkup`
 *  no corre `useEffect`, y sin datos iniciales la pantalla se quedaria en el
 *  spinner; un test que solo ve un spinner no prueba nada.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  ticketsApi,
  type Company,
  type CajaChicaConMovimientos,
  type CajaChicaCreate,
  type ConciliacionCajaResponse,
  type MovimientoCajaCreate,
  type DuplicadoFiscalResponse,
  type EstadoCaja,
  type TipoMovimientoCaja,
  type OrigenFondo,
} from '../services/api';
import {
  cajaChicaApi, duplicadosApi,
  type CajaChica as CajaChicaItem,
  type MovimientoCaja,
  type ConciliacionResumen,
} from '../services/cajaChica';
import { registrarEventoFrontend } from '../services/auditoria';
import { useSesion } from '../contextos/SesionContexto';
import { AvisoSinEmpresa } from '../contextos/Empresa';
import { useEmpresa } from '../contextos/EmpresaContexto';
import {
  Badge, Button, Card, EmptyState, Input, Loading, Modal, Select, Table,
} from '../components/ui';
import { dinero, fecha, fechaHora } from '../utils/format';

interface CajaChicaProps {
  cajasIniciales?: CajaChicaItem[];
  cajaInicial?: CajaChicaConMovimientos | null;
  resumenInicial?: ConciliacionResumen | null;
  companiesIniciales?: Company[];
  /** `renderToStaticMarkup` no corre clics: el verificador necesita entrar a
   *  la pestaña de duplicados sin simular un usuario. En la app nunca se pasa. */
  pestanaInicial?: 'cajas' | 'duplicados';
}

interface FormaMovimiento {
  tipo: TipoMovimientoCaja;
  monto: string;
  origen_fondo: OrigenFondo | '';
  banco_nombre: string;
  referencia_bancaria: string;
  ticket_id: string;
  notas: string;
  closed_at: string;
  resta: boolean;
}

interface FormaBusqueda {
  provider_tax_id: string;
  folio: string;
  expense_date: string;
  total_amount: string;
}

const ESTADO_LABEL: Record<EstadoCaja, string> = {
  abierta: 'Abierta',
  en_revision: 'En revisión',
  cerrada: 'Cerrada',
};

const TIPO_LABEL: Record<TipoMovimientoCaja, string> = {
  apertura: 'Apertura',
  reposicion: 'Reposición',
  gasto_ticket: 'Gasto con ticket',
  ajuste: 'Ajuste',
  cierre: 'Cierre',
};

const MONTO_RE = /^\d+(?:\.\d{1,2})?$/;

function montoValido(valor: string): boolean {
  return MONTO_RE.test(valor.trim()) && Number(valor) > 0;
}

function textoOVacio(valor: string): string | undefined {
  const limpio = valor.trim();
  return limpio || undefined;
}

/** Un `datetime-local` es un instante LOCAL; el backend quiere ISO con zona. */
function aIso(valor: string): string | undefined {
  if (!valor.trim()) return undefined;
  const fechaHoraLocal = new Date(valor);
  return Number.isNaN(fechaHoraLocal.getTime()) ? undefined : fechaHoraLocal.toISOString();
}

function mensajeDeError(error: unknown, porOmision: string): string {
  return error instanceof Error ? error.message : porOmision;
}

function Dato({ titulo, valor }: { titulo: string; valor: string }) {
  return (
    <div className="rounded-lg bg-gray-50 p-3">
      <p className="text-xs text-gray-500">{titulo}</p>
      <p className="text-sm font-semibold text-gray-900 break-words">{valor}</p>
    </div>
  );
}

function Candidatos({ candidatos }: { candidatos: DuplicadoFiscalResponse['candidatos'] }) {
  if (candidatos.length === 0) return null;
  return (
    <ul className="mt-3 space-y-2">
      {candidatos.map((candidato) => (
        <li key={candidato.ticket_id} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <p className="font-medium text-amber-900">{candidato.provider_name}</p>
          <p className="text-amber-800">
            {fecha(candidato.expense_date)} · {dinero(candidato.total_amount)}
          </p>
          <p className="text-xs text-amber-700 break-words">
            {candidato.folio ? `Folio ${candidato.folio} · ` : ''}
            {candidato.source_file || 'sin archivo'}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function CajaChica({
  cajasIniciales,
  cajaInicial = null,
  resumenInicial = null,
  companiesIniciales,
  pestanaInicial = 'cajas',
}: CajaChicaProps) {
  const { usuario } = useSesion();
  const { empresaActiva, empresas } = useEmpresa();

  // La empresa NO se elige aqui. Viene del contexto y viaja en la cabecera
  // `X-Company-ID` que inyecta `api.ts`. Los datos iniciales solo existen para
  // que el verificador de HTML pueda montar la pantalla sin navegador.
  const companies: Array<Pick<Company, 'id' | 'name'>> = empresas.length
    ? empresas.map((m) => ({ id: m.company_id, name: m.name }))
    : companiesIniciales ?? [];
  const companyId = empresaActiva?.company_id
    ?? (companiesIniciales
      ? cajaInicial?.company_id ?? cajasIniciales?.[0]?.company_id ?? ''
      : '');

  const [cajas, setCajas] = useState<CajaChicaItem[]>(cajasIniciales ?? []);
  const [seleccionada, setSeleccionada] = useState<CajaChicaConMovimientos | null>(cajaInicial);
  const [conciliacion, setConciliacion] = useState<ConciliacionCajaResponse | null>(null);
  const [efectivoContado, setEfectivoContado] = useState('');
  const [resumen, setResumen] = useState<ConciliacionResumen | null>(resumenInicial);
  const [pestana, setPestana] = useState<'cajas' | 'duplicados'>(pestanaInicial);

  const [cargando, setCargando] = useState(!cajasIniciales);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [procesando, setProcesando] = useState(false);

  const [modalCaja, setModalCaja] = useState(false);
  const [nuevaCaja, setNuevaCaja] = useState({
    nombre: '',
    monto_inicial: '0.00',
    umbral_alerta: '0.00',
  });

  const [modalMovimiento, setModalMovimiento] = useState(false);
  const [movimiento, setMovimiento] = useState<FormaMovimiento>({
    tipo: 'reposicion',
    monto: '',
    origen_fondo: 'efectivo',
    banco_nombre: '',
    referencia_bancaria: '',
    ticket_id: '',
    notas: '',
    closed_at: '',
    resta: false,
  });
  const [duplicadoTicket, setDuplicadoTicket] = useState<DuplicadoFiscalResponse | null>(null);
  const [verificandoTicket, setVerificandoTicket] = useState(false);

  const [formaBusqueda, setFormaBusqueda] = useState<FormaBusqueda>({
    provider_tax_id: '',
    folio: '',
    expense_date: '',
    total_amount: '',
  });
  const [resultadoBusqueda, setResultadoBusqueda] = useState<DuplicadoFiscalResponse | null>(null);
  const [buscando, setBuscando] = useState(false);

  const seleccionarCaja = useCallback(async (id: string) => {
    setProcesando(true);
    setError(null);
    try {
      const detalle = await cajaChicaApi.get(id);
      setSeleccionada(detalle);
      setEfectivoContado(detalle.efectivo_contado ?? '');
      setConciliacion(await cajaChicaApi.conciliacionBancaria(id));
      setPestana('cajas');
    } catch (e) {
      setError(mensajeDeError(e, 'No se pudo abrir la caja chica'));
    } finally {
      setProcesando(false);
    }
  }, []);

  useEffect(() => {
    if (cajasIniciales) return;
    if (!companyId) {
      setCajas([]);
      setCargando(false);
      return;
    }
    let vivo = true;
    setCargando(true);
    setError(null);
    cajaChicaApi.list()
      .then((datos) => {
        if (!vivo) return;
        setCajas(datos);
        if (!seleccionada && datos[0]) void seleccionarCaja(datos[0].id);
      })
      .catch((e) => setError(mensajeDeError(e, 'No se pudieron cargar las cajas chicas')))
      .finally(() => { if (vivo) setCargando(false); });
    return () => { vivo = false; };
    // `seleccionada` se lee solo para evitar abrir otra caja cuando ya hay una.
  }, [companyId, cajasIniciales, seleccionarCaja]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (resumenInicial || !companyId) return;
    let vivo = true;
    duplicadosApi.resumen()
      .then((datos) => { if (vivo) setResumen(datos); })
      .catch((e) => setError(mensajeDeError(e, 'No se pudo cargar el resumen de duplicados')));
    return () => { vivo = false; };
  }, [companyId, resumenInicial]);

  /** Despues de escribir se vuelve a LEER. El resultado del POST no sustituye
   *  al saldo calculado por el servidor ni al kardex completo. */
  const recargar = useCallback(async (cajaId = seleccionada?.id) => {
    // Se vuelve a LEER con la empresa activa del header, no con un parametro
    // que la pantalla pueda cambiar por su cuenta.
    setCajas(await cajaChicaApi.list());
    setResumen(await duplicadosApi.resumen());
    if (cajaId) setSeleccionada(await cajaChicaApi.get(cajaId));
  }, [seleccionada?.id]);

  const ejecutar = useCallback(async <T,>(
    accion: () => Promise<T>,
    mensaje: string,
    cajaId?: string,
  ): Promise<T | undefined> => {
    setProcesando(true);
    setError(null);
    setAviso(null);
    try {
      const resultado = await accion();
      try {
        await recargar(cajaId ?? seleccionada?.id);
      } catch {
        // La escritura si ocurrió. Decir "falló" aqui haria que el operador
        // repitiera la operacion y contara dos veces el mismo movimiento.
        setAviso(`${mensaje} La lista podria estar desactualizada; recarga la pantalla.`);
      }
      setAviso((actual) => actual || mensaje);
      registrarEventoFrontend({
        accion: 'caja_chica',
        modulo: 'caja_chica',
        registro_afectado: cajaId ?? seleccionada?.id,
        detalles: { mensaje },
      });
      return resultado;
    } catch (e) {
      setError(mensajeDeError(e, 'No se pudo completar la operación'));
      return undefined;
    } finally {
      setProcesando(false);
    }
  }, [recargar, seleccionada?.id]);

  const abrirMovimiento = () => {
    setError(null);
    setAviso(null);
    setDuplicadoTicket(null);
    setMovimiento({
      tipo: seleccionada?.estado === 'en_revision' ? 'cierre' : 'reposicion',
      monto: '',
      origen_fondo: 'efectivo',
      banco_nombre: '',
      referencia_bancaria: '',
      ticket_id: '',
      notas: '',
      closed_at: '',
      resta: false,
    });
    setModalMovimiento(true);
  };

  const guardarCaja = async (evento: React.FormEvent) => {
    evento.preventDefault();
    if (!companyId) {
      setError('Selecciona una empresa antes de abrir la caja.');
      return;
    }
    if (!usuario?.id) {
      setError('No se pudo identificar al responsable de la sesión.');
      return;
    }
    if (!nuevaCaja.nombre.trim() || !montoValido(nuevaCaja.monto_inicial)) {
      setError('Escribe un nombre y un monto inicial mayor que cero.');
      return;
    }
    const creada = await ejecutar(async () => {
      const datos: CajaChicaCreate = {
        company_id: companyId,
        responsable_id: usuario.id,
        nombre: nuevaCaja.nombre.trim(),
        monto_inicial: nuevaCaja.monto_inicial.trim(),
        umbral_alerta: nuevaCaja.umbral_alerta.trim() || '0.00',
      };
      return cajaChicaApi.create(datos);
    }, 'Caja chica abierta.');
    if (creada) {
      setModalCaja(false);
      setNuevaCaja({ nombre: '', monto_inicial: '0.00', umbral_alerta: '0.00' });
      await seleccionarCaja(creada.id);
    }
  };

  const comprobarTicket = async () => {
    if (!seleccionada || !movimiento.ticket_id.trim()) {
      setError('Escribe el id del ticket que se paga con esta caja.');
      return;
    }
    setVerificandoTicket(true);
    setDuplicadoTicket(null);
    setError(null);
    try {
      const ticket = await ticketsApi.get(movimiento.ticket_id.trim());
      if (ticket.company_id !== seleccionada.company_id) {
        throw new Error('El ticket pertenece a otra empresa');
      }
      const respuesta = await duplicadosApi.buscar({
        company_id: seleccionada.company_id,
        provider_tax_id: ticket.identificacion_fiscal ?? undefined,
        folio: ticket.folio ?? undefined,
        expense_date: ticket.expense_date,
        total_amount: ticket.total_amount,
        // El ticket que se esta vinculando no es su propio duplicado.
        excluir_ticket_id: ticket.id,
      });
      setDuplicadoTicket(respuesta);
    } catch (e) {
      setError(mensajeDeError(e, 'No se pudo comprobar la huella fiscal del ticket'));
    } finally {
      setVerificandoTicket(false);
    }
  };

  const guardarMovimiento = async (evento: React.FormEvent) => {
    evento.preventDefault();
    if (!seleccionada) return;
    if (!montoValido(movimiento.monto)) {
      setError('El monto debe ser un número positivo con hasta dos decimales.');
      return;
    }
    if (movimiento.tipo === 'reposicion' && !movimiento.origen_fondo) {
      setError('La reposición debe decir si el fondo viene de efectivo o de banco.');
      return;
    }
    if (movimiento.origen_fondo === 'banco' && !movimiento.banco_nombre.trim()) {
      setError('Si el fondo viene de banco, escribe el nombre del banco.');
      return;
    }
    if (movimiento.tipo === 'gasto_ticket') {
      if (!movimiento.ticket_id.trim()) {
        setError('Un gasto con ticket debe llevar el id del ticket.');
        return;
      }
      if (!duplicadoTicket) {
        setError('Comprueba la huella fiscal del ticket antes de contarlo.');
        return;
      }
      if (duplicadoTicket.es_duplicado) {
        setError('Hay un comprobante previo con la misma huella fiscal. No se puede contar dos veces.');
        return;
      }
    }
    if (movimiento.tipo === 'cierre' && !aIso(movimiento.closed_at)) {
      setError('El cierre necesita una fecha y hora válidas.');
      return;
    }

    const datos: MovimientoCajaCreate = {
      company_id: companyId || undefined,
      tipo: movimiento.tipo,
      monto: movimiento.monto.trim(),
      notas: textoOVacio(movimiento.notas),
    };
    // El origen del fondo no es una etiqueta general: solo existe en una
    // reposicion. Un gasto con ticket que arrastrara `origen_fondo=banco` del
    // formulario anterior llegaria al kardex con un dato que el papel no dice.
    if (movimiento.tipo === 'reposicion' && movimiento.origen_fondo) {
      datos.origen_fondo = movimiento.origen_fondo;
      if (movimiento.origen_fondo === 'banco') {
        datos.banco_nombre = movimiento.banco_nombre.trim();
        datos.referencia_bancaria = textoOVacio(movimiento.referencia_bancaria);
      }
    }
    if (movimiento.tipo === 'gasto_ticket') datos.ticket_id = movimiento.ticket_id.trim();
    if (movimiento.tipo === 'ajuste') datos.resta = movimiento.resta;
    if (movimiento.tipo === 'cierre') datos.closed_at = aIso(movimiento.closed_at);

    const guardado = await ejecutar(
      () => cajaChicaApi.registrarMovimiento(seleccionada.id, datos),
      'Movimiento registrado.',
      seleccionada.id,
    );
    if (guardado) {
      setModalMovimiento(false);
      setDuplicadoTicket(null);
    }
  };

  const cambiarEstado = async (accion: 'enRevision' | 'reabrir' | 'cerrar', mensaje: string) => {
    if (!seleccionada) return;
    if (accion === 'cerrar' && efectivoContado.trim() && !montoValido(efectivoContado)) {
      setError('El efectivo contado debe ser un monto válido.');
      return;
    }
    const cuerpo = accion === 'cerrar' && efectivoContado.trim()
      ? { efectivo_contado: efectivoContado.trim() }
      : undefined;
    await ejecutar(() => cajaChicaApi[accion](seleccionada.id, cuerpo), mensaje, seleccionada.id);
    if (accion === 'cerrar') {
      const actualizada = await cajaChicaApi.get(seleccionada.id);
      setSeleccionada(actualizada);
      setConciliacion(await cajaChicaApi.conciliacionBancaria(seleccionada.id));
    }
  };

  const buscarDuplicado = async (evento: React.FormEvent) => {
    evento.preventDefault();
    if (!companyId) {
      setError('Selecciona una empresa para buscar duplicados.');
      return;
    }
    if (!formaBusqueda.expense_date || !montoValido(formaBusqueda.total_amount)) {
      setError('La búsqueda necesita una fecha y un total válidos.');
      return;
    }
    setBuscando(true);
    setResultadoBusqueda(null);
    setError(null);
    try {
      const respuesta = await duplicadosApi.buscar({
        company_id: companyId,
        provider_tax_id: textoOVacio(formaBusqueda.provider_tax_id),
        folio: textoOVacio(formaBusqueda.folio),
        expense_date: formaBusqueda.expense_date,
        total_amount: formaBusqueda.total_amount.trim(),
      });
      setResultadoBusqueda(respuesta);
    } catch (e) {
      setError(mensajeDeError(e, 'No se pudo buscar el duplicado'));
    } finally {
      setBuscando(false);
    }
  };

  const estado = seleccionada?.estado;
  const puedeMoverse = !!seleccionada && estado !== 'cerrada';
  const tiposPermitidos: { value: TipoMovimientoCaja; label: string }[] =
    estado === 'en_revision'
      ? [{ value: 'cierre', label: TIPO_LABEL.cierre }]
      : (Object.keys(TIPO_LABEL) as TipoMovimientoCaja[])
        .filter((tipo) => tipo !== 'cierre')
        .map((tipo) => ({ value: tipo, label: TIPO_LABEL[tipo] }));

  return (
    <div className="p-4 lg:p-6 max-w-7xl mx-auto space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-gray-900">Caja chica</h1>
        <p className="text-sm text-gray-600 mt-1">
          Saldo, movimientos y comprobantes vinculados. El saldo y el aviso de umbral los decide el servidor.
        </p>
      </header>

      {aviso && (
        <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          {aviso}
        </div>
      )}

      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <span>{error}</span>
          <Button variant="ghost" size="sm" onClick={() => { void recargar().catch(() => undefined); }}>
            Reintentar
          </Button>
        </div>
      )}

      <AvisoSinEmpresa mensaje="Elige una empresa activa en la barra lateral. Caja chica no puede mostrar un saldo sin saber a qué empresa pertenece." />

      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="w-full sm:max-w-sm">
            <p className="text-sm font-medium text-gray-700">Empresa</p>
            <p className="mt-1 text-sm text-gray-900" title={companies.find((c) => c.id === companyId)?.name}>
              {companies.find((c) => c.id === companyId)?.name ?? 'Sin empresa activa'}
            </p>
          </div>
          <div role="tablist" aria-label="Secciones de caja chica" className="flex gap-2">
            <Button
              variant={pestana === 'cajas' ? 'primary' : 'secondary'}
              size="sm"
              aria-pressed={pestana === 'cajas'}
              onClick={() => setPestana('cajas')}
            >
              Cajas
            </Button>
            <Button
              variant={pestana === 'duplicados' ? 'primary' : 'secondary'}
              size="sm"
              aria-pressed={pestana === 'duplicados'}
              onClick={() => setPestana('duplicados')}
            >
              Duplicados fiscales
            </Button>
          </div>
        </div>
      </Card>

      {pestana === 'cajas' ? (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-1">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-gray-900">Cajas registradas</h2>
              <Button size="sm" onClick={() => setModalCaja(true)} disabled={!companyId}>
                Nueva caja
              </Button>
            </div>
            {cargando ? (
              <Loading message="Cargando cajas chicas..." />
            ) : cajas.length === 0 ? (
              <EmptyState
                message={companyId ? 'No hay cajas chicas para esta empresa.' : 'Selecciona una empresa para ver sus cajas.'}
                action={companyId ? { label: 'Crear la primera caja', onClick: () => setModalCaja(true) } : undefined}
              />
            ) : (
              <Table
                keyField="id"
                data={cajas}
                onRowClick={(caja: CajaChicaItem) => { void seleccionarCaja(caja.id); }}
                columns={[
                  { key: 'nombre', header: 'Caja', render: (caja: CajaChicaItem) => <span className="font-medium">{caja.nombre}</span> },
                  { key: 'saldo_actual', header: 'Saldo', render: (caja: CajaChicaItem) => dinero(caja.saldo_actual) },
                  {
                    key: 'estado', header: 'Estado',
                    render: (caja: CajaChicaItem) => (
                      <span aria-label={`Estado de la caja: ${ESTADO_LABEL[caja.estado]}`}>
                        <Badge variant={caja.estado === 'abierta' ? 'info' : caja.estado === 'en_revision' ? 'warning' : 'default'}>
                          {ESTADO_LABEL[caja.estado]}
                        </Badge>
                      </span>
                    ),
                  },
                ]}
              />
            )}
          </Card>

          <Card className="lg:col-span-2">
            {!seleccionada ? (
              <EmptyState message="Selecciona una caja para ver su detalle y sus movimientos." />
            ) : (
              <div className="space-y-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-xl font-semibold text-gray-900">{seleccionada.nombre}</h2>
                    <p className="text-sm text-gray-500">Abierta el {fechaHora(seleccionada.opened_at)}</p>
                  </div>
                  <span aria-label={`Estado de la caja: ${ESTADO_LABEL[seleccionada.estado]}`}>
                    <Badge variant={seleccionada.estado === 'abierta' ? 'info' : seleccionada.estado === 'en_revision' ? 'warning' : 'default'}>
                      {ESTADO_LABEL[seleccionada.estado]}
                    </Badge>
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Dato titulo="Saldo actual" valor={dinero(seleccionada.saldo_actual)} />
                  <Dato titulo="Monto inicial" valor={dinero(seleccionada.monto_inicial)} />
                  <Dato titulo="Umbral de alerta" valor={dinero(seleccionada.umbral_alerta)} />
                  <Dato titulo="Cerrada" valor={seleccionada.closed_at ? fechaHora(seleccionada.closed_at) : '—'} />
                </div>

                {(seleccionada.efectivo_contado !== null || seleccionada.diferencia_cierre !== null) && (
                  <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                    Conteo del cierre: {dinero(seleccionada.efectivo_contado ?? '0.00')}.
                    {' '}Diferencia calculada por el servidor: {dinero(seleccionada.diferencia_cierre ?? '0.00')}.
                  </div>
                )}

                {seleccionada.saldo_bajo_umbral && (
                  <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                    Saldo bajo el umbral: {dinero(seleccionada.saldo_actual)} de {dinero(seleccionada.umbral_alerta)}.
                    {' '}El aviso lo emite el backend; no se recalcula en el navegador.
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <Button onClick={abrirMovimiento} disabled={!puedeMoverse || procesando}>
                    Registrar movimiento
                  </Button>
                  {seleccionada.estado === 'abierta' && (
                    <Button variant="secondary" disabled={procesando} onClick={() => { void cambiarEstado('enRevision', 'Caja enviada a revisión.'); }}>
                      Enviar a revisión
                    </Button>
                  )}
                  {seleccionada.estado === 'en_revision' && (
                    <>
                      <div className="w-full sm:max-w-xs">
                        <label htmlFor="efectivo-contado" className="text-sm font-medium text-gray-700">
                          Efectivo contado (opcional)
                        </label>
                        <Input
                          id="efectivo-contado"
                          value={efectivoContado}
                          onChange={(e) => setEfectivoContado(e.target.value)}
                          placeholder="0.00"
                          inputMode="decimal"
                        />
                      </div>
                      <Button variant="secondary" disabled={procesando} onClick={() => { void cambiarEstado('reabrir', 'Caja reabierta.'); }}>
                        Reabrir
                      </Button>
                      <Button variant="success" disabled={procesando} onClick={() => { void cambiarEstado('cerrar', 'Caja cerrada.'); }}>
                        Cerrar caja
                      </Button>
                    </>
                  )}
                </div>

                {conciliacion && (
                  <div className="rounded-lg border border-gray-200 p-3 text-sm">
                    <p className="font-semibold text-gray-900">Reposiciones desde banco</p>
                    <p className="mt-1 text-gray-600">
                      {conciliacion.cruzadas} cruzadas · {conciliacion.sin_cruzar} sin cruce
                    </p>
                    {conciliacion.advertencia && (
                      <p role="status" className="mt-1 text-amber-700">{conciliacion.advertencia}</p>
                    )}
                    <p className="mt-1 text-xs text-gray-500">
                      Solo lectura: no marca el cargo bancario como reconciliado.
                    </p>
                  </div>
                )}

                <div>
                  <h3 className="mb-2 text-base font-semibold text-gray-900">Movimientos</h3>
                  {seleccionada.movimientos.length === 0 ? (
                    <EmptyState message="Esta caja todavía no tiene movimientos." />
                  ) : (
                    <Table
                      keyField="id"
                      data={seleccionada.movimientos}
                      columns={[
                        {
                          key: 'created_at', header: 'Fecha',
                          render: (mov: MovimientoCaja) => fechaHora(mov.created_at),
                        },
                        {
                          key: 'tipo', header: 'Tipo',
                          render: (mov: MovimientoCaja) => TIPO_LABEL[mov.tipo],
                        },
                        {
                          key: 'monto', header: 'Monto',
                          render: (mov: MovimientoCaja) => (
                            <span className={mov.tipo === 'gasto_ticket' || mov.tipo === 'cierre' ? 'text-red-700' : ''}>
                              {dinero(mov.monto)}
                            </span>
                          ),
                        },
                        {
                          key: 'origen', header: 'Origen',
                          render: (mov: MovimientoCaja) => mov.banco_nombre
                            ? `Banco: ${mov.banco_nombre}${mov.referencia_bancaria ? ` (${mov.referencia_bancaria})` : ''}`
                            : mov.origen_fondo || '—',
                        },
                        {
                          key: 'ticket', header: 'Ticket',
                          render: (mov: MovimientoCaja) => mov.ticket_id ?? '—',
                        },
                        {
                          key: 'notas', header: 'Notas',
                          render: (mov: MovimientoCaja) => mov.notas ?? '—',
                        },
                      ]}
                    />
                  )}
                </div>
              </div>
            )}
          </Card>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <h2 className="mb-1 text-lg font-semibold text-gray-900">Comprobar una huella fiscal</h2>
            <p className="mb-4 text-sm text-gray-600">
              Antes de contar un comprobante: RFC, folio, fecha y total dentro de la misma empresa.
            </p>
            <form onSubmit={(e) => { void buscarDuplicado(e); }} className="space-y-3">
              <Input
                label="RFC o identificación fiscal"
                value={formaBusqueda.provider_tax_id}
                onChange={(e) => setFormaBusqueda({ ...formaBusqueda, provider_tax_id: e.target.value })}
                placeholder="Opcional"
              />
              <Input
                label="Folio"
                value={formaBusqueda.folio}
                onChange={(e) => setFormaBusqueda({ ...formaBusqueda, folio: e.target.value })}
                placeholder="Opcional"
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <Input
                  label="Fecha del comprobante"
                  type="date"
                  value={formaBusqueda.expense_date}
                  onChange={(e) => setFormaBusqueda({ ...formaBusqueda, expense_date: e.target.value })}
                />
                <Input
                  label="Total"
                  inputMode="decimal"
                  value={formaBusqueda.total_amount}
                  onChange={(e) => setFormaBusqueda({ ...formaBusqueda, total_amount: e.target.value })}
                  placeholder="0.00"
                />
              </div>
              <Button type="submit" disabled={buscando || !companyId}>
                {buscando ? 'Buscando...' : 'Buscar duplicado'}
              </Button>
            </form>

            {resultadoBusqueda && (
              <div
                role={resultadoBusqueda.es_duplicado ? 'alert' : 'status'}
                className={`mt-4 rounded-lg border p-3 ${resultadoBusqueda.es_duplicado ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}
              >
                <p className="text-sm font-medium">{resultadoBusqueda.mensaje}</p>
                <Candidatos candidatos={resultadoBusqueda.candidatos} />
              </div>
            )}
          </Card>

          <Card>
            <h2 className="mb-1 text-lg font-semibold text-gray-900">Resumen de la empresa</h2>
            <p className="mb-4 text-sm text-gray-600">
              Los grupos se cuentan por huella fiscal. El total lo calcula el backend contando una sola vez cada comprobante.
            </p>
            {!resumen ? (
              <Loading message="Cargando resumen de duplicados..." />
            ) : (
              <div className="space-y-4">
                {resumen.advertencia && (
                  <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                    {resumen.advertencia}
                  </div>
                )}
                <Dato titulo="Conciliado sin duplicados" valor={dinero(resumen.total_conciliado_sin_duplicados)} />
                {resumen.grupos.length === 0 ? (
                  <EmptyState message="No hay grupos de comprobantes repetidos." />
                ) : (
                  <ul className="space-y-3">
                    {resumen.grupos.map((grupo, indice) => {
                      const primero = grupo.tickets[0];
                      return (
                        <li key={grupo.hash_duplicado || `grupo-${indice}`} className="rounded-lg border border-gray-200 p-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-sm font-medium text-gray-900">
                              {primero?.provider_name || 'Comprobante'}
                            </span>
                            <Badge variant="warning">
                              {grupo.tickets.length} comprobantes
                            </Badge>
                          </div>
                          <p className="mt-1 text-sm text-gray-600">
                            {fecha(primero?.expense_date)} · {dinero(grupo.monto_total)}
                          </p>
                          <p className="mt-1 text-xs break-all text-gray-500">
                            Huella: {grupo.hash_duplicado || 'sin hash'}
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            )}
          </Card>
        </div>
      )}

      <Modal isOpen={modalCaja} onClose={() => setModalCaja(false)} title="Abrir caja chica">
        <form onSubmit={(e) => { void guardarCaja(e); }} className="space-y-4">
          <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-800">
            Empresa: {companies.find((compania) => compania.id === companyId)?.name || '—'}.
            El responsable se toma de la sesión ({usuario?.nombre || '—'}).
          </p>
          <Input
            label="Nombre de la caja"
            value={nuevaCaja.nombre}
            onChange={(e) => setNuevaCaja({ ...nuevaCaja, nombre: e.target.value })}
            placeholder="Caja de oficina"
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              label="Monto inicial"
              inputMode="decimal"
              value={nuevaCaja.monto_inicial}
              onChange={(e) => setNuevaCaja({ ...nuevaCaja, monto_inicial: e.target.value })}
            />
            <Input
              label="Umbral de alerta"
              inputMode="decimal"
              value={nuevaCaja.umbral_alerta}
              onChange={(e) => setNuevaCaja({ ...nuevaCaja, umbral_alerta: e.target.value })}
            />
          </div>
          <div className="flex justify-end gap-3 border-t pt-4">
            <Button variant="ghost" onClick={() => setModalCaja(false)}>Cancelar</Button>
            <Button type="submit" disabled={procesando}>{procesando ? 'Guardando...' : 'Crear caja'}</Button>
          </div>
        </form>
      </Modal>

      <Modal isOpen={modalMovimiento} onClose={() => setModalMovimiento(false)} title="Registrar movimiento">
        <form onSubmit={(e) => { void guardarMovimiento(e); }} className="space-y-4">
          <Select
            label="Tipo de movimiento"
            value={movimiento.tipo}
            onChange={(e) => {
              const tipo = e.target.value as TipoMovimientoCaja;
              // Cambiar de tipo no debe arrastrar el origen de una reposicion
              // anterior a un gasto o a un ajuste.
              setMovimiento(tipo === 'reposicion'
                ? { ...movimiento, tipo }
                : { ...movimiento, tipo, origen_fondo: '', banco_nombre: '', referencia_bancaria: '' });
            }}
            options={tiposPermitidos}
          />
          <Input
            label="Monto"
            inputMode="decimal"
            value={movimiento.monto}
            onChange={(e) => setMovimiento({ ...movimiento, monto: e.target.value })}
            placeholder="0.00"
          />

          {movimiento.tipo === 'reposicion' && (
            <Select
              label="Origen del fondo"
              value={movimiento.origen_fondo}
              onChange={(e) => setMovimiento({ ...movimiento, origen_fondo: e.target.value as OrigenFondo })}
              options={[
                { value: '', label: 'Selecciona el origen' },
                { value: 'efectivo', label: 'Efectivo' },
                { value: 'banco', label: 'Banco' },
              ]}
            />
          )}

          {movimiento.origen_fondo === 'banco' && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                label="Nombre del banco"
                value={movimiento.banco_nombre}
                onChange={(e) => setMovimiento({ ...movimiento, banco_nombre: e.target.value })}
                placeholder="Obligatorio si el fondo viene de banco"
              />
              <Input
                label="Referencia bancaria"
                value={movimiento.referencia_bancaria}
                onChange={(e) => setMovimiento({ ...movimiento, referencia_bancaria: e.target.value })}
                placeholder="Opcional"
              />
            </div>
          )}

          {movimiento.tipo === 'gasto_ticket' && (
            <div className="space-y-2 rounded-lg border border-gray-200 p-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <Input
                  label="ID del ticket"
                  value={movimiento.ticket_id}
                  onChange={(e) => { setMovimiento({ ...movimiento, ticket_id: e.target.value }); setDuplicadoTicket(null); }}
                  placeholder="UUID del comprobante"
                  className="flex-1"
                />
                <Button variant="secondary" onClick={() => { void comprobarTicket(); }} disabled={verificandoTicket}>
                  {verificandoTicket ? 'Comprobando...' : 'Comprobar huella'}
                </Button>
              </div>
              {duplicadoTicket && (
                <div
                  role={duplicadoTicket.es_duplicado ? 'alert' : 'status'}
                  className={`rounded-lg border p-3 text-sm ${duplicadoTicket.es_duplicado ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}
                >
                  <p className="font-medium">{duplicadoTicket.mensaje}</p>
                  <Candidatos candidatos={duplicadoTicket.candidatos} />
                </div>
              )}
            </div>
          )}

          {movimiento.tipo === 'ajuste' && (
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={movimiento.resta}
                onChange={(e) => setMovimiento({ ...movimiento, resta: e.target.checked })}
                className="h-4 w-4"
              />
              Restar del saldo (sin esta marca, el ajuste suma)
            </label>
          )}

          {movimiento.tipo === 'cierre' && (
            <Input
              label="Fecha y hora del cierre"
              type="datetime-local"
              value={movimiento.closed_at}
              onChange={(e) => setMovimiento({ ...movimiento, closed_at: e.target.value })}
            />
          )}

          <Input
            label="Notas"
            value={movimiento.notas}
            onChange={(e) => setMovimiento({ ...movimiento, notas: e.target.value })}
            placeholder="Opcional"
          />

          <div className="flex justify-end gap-3 border-t pt-4">
            <Button variant="ghost" onClick={() => setModalMovimiento(false)}>Cancelar</Button>
            <Button
              type="submit"
              disabled={procesando || (movimiento.tipo === 'gasto_ticket' && duplicadoTicket?.es_duplicado === true)}
            >
              {procesando ? 'Guardando...' : 'Registrar movimiento'}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
