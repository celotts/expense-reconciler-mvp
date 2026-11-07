import { useCallback, useEffect, useRef, useState } from 'react';
import { registrarEventoFrontend } from '../services/auditoria';
import { companiesApi, lotesApi, ticketsApi, type Company, type Ticket } from '../services/api';
import type {
  ScanConfig, ScanCorrida, ScanEvent, ScanFile, ScanFileDetail, ScanItem, ScanOcrEstado,
  ScanResultado, ScanStats, ScanStatus,
} from '../types/scan';
import type { Lote, LoteDetalle, TipoPeriodo } from '../types/lote';
import { Button, Card, Badge, Loading, EmptyState, Select, Modal, Input } from '../components/ui';
import { SCAN_STATUS_META, motorLabel, accionLabel, COLOR_ESTADO, hayDiscrepanciaDeFormato } from '../utils/scan';
import { VerDocumento } from '../components/TicketDocumento';
import { PanelProgreso } from '../components/ScanProgreso';
import { CasillaArchivar } from '../components/CasillaArchivar';
import { EstadoDeIa } from '../components/EstadoDeIa';

/**
 * Escaner de carpeta: leer comprobantes de un directorio sin subir uno por uno.
 *
 * Por que existe. Subir 400 comprobantes a mano es el trabajo que hace que la
 * gente no los suba. Este recorrido los lee todos de una carpeta y decide por
 * archivo si hay que tocarlo, y el registro de cada uno queda escrito para que
 * la segunda pasada no vuelva a gastar OCR en lo mismo.
 *
 * La regla de la pantalla, y es la que la hace util: NO se mezclan los dos
 * estados. `PROCESADO` dice que el ARCHIVO se leyo; el ticket que salio puede
 * seguir en la cola de revision porque el papel no daba para cerrarlo solo. Si
 * esta pantalla pintara "listo" al lado de un ticket en la cola, alguien
 * assumiria que ya esta contabilizado y no lo esta. El enlace al ticket va con
 * su estado al lado, y el estado del archivo va aparte.
 *
 * La segunda regla: el boton de reprocesar esta en el detalle de UN archivo y
 * no en el escaneo global. Es el unico camino que sobreescribe un ticket con
 * correcciones de una persona, y que sea un boton por archivo es lo que deja
 * claro que es una decision, no un efecto de correr un escaneo.
 */
export function Scan() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<string>('');
  const [config, setConfig] = useState<ScanConfig | null>(null);
  const [ocr, setOcr] = useState<ScanOcrEstado | null>(null);
  const [stats, setStats] = useState<ScanStats | null>(null);
  const [archivos, setArchivos] = useState<ScanFile[]>([]);
  const [totalArchivos, setTotalArchivos] = useState(0);
  const [filtro, setFiltro] = useState<ScanStatus | ''>('');
  const [pagina, setPagina] = useState(0);
  const [cargando, setCargando] = useState(true);
  const [escaneando, setEscaneando] = useState(false);
  // MEDIDO el 2026-10-16: el POST /scan con vision tarda 7-9 min por foto y
  // nginx corta a los 300 s. El escaneo SE HACE igual; lo que se perdia era el
  // resultado en pantalla. Con esto la pantalla lo dice, en vez de fingir que
  // nada paso.
  const [conexionCortada, setConexionCortada] = useState(false);
  const [preparando, setPreparando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ultimo, setUltimo] = useState<ScanResultado | null>(null);
  /** Tiempo total medido en el CLIENTE, como respaldo cuando el POST se
   * corta (nginx 300s con Vision) y no llega `duracion_segundos` del
   * servidor. Se pone en cero al iniciar cada escaneo. */
  const [tiempoClienteSegundos, setTiempoClienteSegundos] = useState<number | null>(null);
  /** El progreso en vivo de la corrida que esta corriendo. */
  const [corrida, setCorrida] = useState<ScanCorrida | null>(null);
  /** El intervalo de sondeo, en un `useRef` y no en un estado.
   *
   *  En un estado provocaria un re-render en cada tick, y el `useEffect` de
   *  limpieza lo volvería a crear —el intervalo se recrea cada 2s y el
   *  watcher nunca llega a disparar limpio. Un `useRef` no re-renderiza y
   *  `clearInterval` lo alcanza siempre. */
  const watcherRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [detalle, setDetalle] = useState<ScanFileDetail | null>(null);
  const [ticketDelDetalle, setTicketDelDetalle] = useState<Ticket | null>(null);
  /** Lotes de digitalización (ver `types/lote.ts`).
   *
   *  `loteId` es el lote ELEGIDO para el próximo escaneo, o '' si no se eligió
   *  ninguno: en ese caso el `POST /scan` no lleva `lote_id` y el servidor
   *  crea o asocia uno. `loteDetalle` es el lote que se está mirando, que no
   *  es lo mismo: un lote CERRADO se puede mirar sin elegirlo. */
  const [lotes, setLotes] = useState<Lote[]>([]);
  const [lotesCargando, setLotesCargando] = useState(false);
  const [errorLotes, setErrorLotes] = useState<string | null>(null);
  const [loteId, setLoteId] = useState('');
  const [loteDetalle, setLoteDetalle] = useState<LoteDetalle | null>(null);
  /** La última corrida terminada que conoce el servidor, para el encabezado
   *  "Último escaneo" cuando esta pantalla todavía no corrió ninguna. */
  const [ultimaCorrida, setUltimaCorrida] = useState<ScanCorrida | null>(null);
  /** El lote con el que se lanzó el escaneo en curso (o el último): es el
   *  folio que muestran el progreso y la última pasada. Se congela al
   *  arrancar para que cambiar de lote a mitad de corrida no reescriba el
   *  folio de la corrida que ya va. */
  const [loteDeCorrida, setLoteDeCorrida] = useState<Lote | null>(null);
  const [mostrarFormLote, setMostrarFormLote] = useState(false);
  const [creandoLote, setCreandoLote] = useState(false);
  const [formLote, setFormLote] = useState({
    nombre: '',
    ejercicio: String(new Date().getFullYear()),
    mes: String(new Date().getMonth() + 1),
    tipo_periodo: 'mensual' as TipoPeriodo,
    total_esperado: '',
  });
  const [confirmarCerrar, setConfirmarCerrar] = useState(false);
  const [cerrandoLote, setCerrandoLote] = useState(false);
  const [mostrarReabrir, setMostrarReabrir] = useState(false);
  const [motivoReabrir, setMotivoReabrir] = useState('');
  const [accionLote, setAccionLote] = useState(false);

  const POR_PAGINA = 20;

  const cargar = useCallback(async () => {
    try {
      setCargando(true);
      setError(null);
      // Config, OCR y stats van en paralelo y se reportan por separado, por la
      // misma razon que en el muestreo: si uno falla y los otros no, mostrar una
      // sola pantalla de error hides lo que si funciona.
      const [c, o, s, f, r] = await Promise.allSettled([
        ticketsApi.scanConfig(),
        ticketsApi.scanOcr(),
        ticketsApi.scanStats(),
        ticketsApi.scanFiles({
          estado: filtro || undefined,
          companyId: companyId || undefined,
          limit: POR_PAGINA,
          offset: pagina * POR_PAGINA,
        }),
        ticketsApi.scanRuns(5),
      ]);
      setConfig(c.status === 'fulfilled' ? c.value : null);
      // La casilla se siembra con el valor REAL del servidor. Solo la primera
      // vez que llega la config: si se re-sembrara en cada recarga, alguien que
      // desmarcara para una corrida volveria a encontrarla marcada al cambiar de
      // empresa o de pagina, y creeria que el servidor lo decidio.
      if (c.status === 'fulfilled') {
        setArchivar(actual => (actual === null ? c.value.archivar_por_defecto : actual));
      }
      setOcr(o.status === 'fulfilled' ? o.value : null);
      setStats(s.status === 'fulfilled' ? s.value : null);
      if (f.status === 'fulfilled') {
        setArchivos(f.value.archivos);
        setTotalArchivos(f.value.total);
      } else {
        setArchivos([]);
        setError(f.reason instanceof Error ? f.reason.message : 'No se pudo cargar el registro');
      }
      // La última corrida terminada, para el encabezado "Último escaneo". Si
      // hay una en curso se ignora aquí: la viva la enseña `corrida`.
      if (r.status === 'fulfilled') {
        setUltimaCorrida(r.value.corridas.find((x) => x.terminada) ?? r.value.corridas[0] ?? null);
      }
    } finally {
      setCargando(false);
    }
  }, [filtro, companyId, pagina]);

  /** Carga los lotes de la empresa elegida (o de todas, si no hay ninguna).
   *
   *  Si el lote elegido deja de estar en la lista —cambió la empresa, o se
   *  cerró— la selección se suelta sola: un lote cerrado no se puede elegir
   *  para un nuevo escaneo, y mandar su id sería un 409 del servidor. El
   *  detalle, si se estaba mirando, se queda: cerrar un lote no borra su
   *  vista, que es donde están el complementario y la reapertura. */
  const cargarLotes = useCallback(async () => {
    try {
      setLotesCargando(true);
      setErrorLotes(null);
      const lista = await lotesApi.listar({ companyId: companyId || undefined, limit: 200 });
      setLotes(lista);
      setLoteId((actual) => {
        if (!actual) return actual;
        const elegido = lista.find((l) => l.id === actual);
        return elegido && elegido.estado !== 'CERRADO' ? actual : '';
      });
      // Y el detalle se suelta con él si ya no está en la lista: si no, la
      // pantalla seguiría mostrando un lote de otra empresa.
      setLoteDetalle((actual) => (actual && lista.some((l) => l.id === actual.id) ? actual : null));
    } catch (e) {
      setLotes([]);
      setErrorLotes(e instanceof Error ? e.message : 'No se pudieron cargar los lotes');
    } finally {
      setLotesCargando(false);
    }
  }, [companyId]);

  useEffect(() => { cargarLotes(); }, [cargarLotes]);

  useEffect(() => { cargar(); }, [cargar]);
  useEffect(() => { if (corrida) setPreparando(false); }, [corrida]);
  useEffect(() => { companiesApi.list().then(setCompanies).catch(() => setCompanies([])); }, []);
  // Si solo hay UNA empresa, preseleccionarla por defecto (no todas)
  useEffect(() => {
    if (companies.length === 1) setCompanyId(companies[0].id);
  }, [companies]);

  /** Corta el sondeo. Lo usan el `finally` del escaneo y el cleanup del unmount. */
  const clearIntervalActivo = useCallback(() => {
    if (watcherRef.current) {
      clearInterval(watcherRef.current);
      watcherRef.current = null;
    }
  }, []);
  const setIntervalActivo = useCallback((h: ReturnType<typeof setInterval>) => {
    watcherRef.current = h;
  }, []);

  // Un `setInterval` que sobrevive a la navegacion sigue consultando la API
  // desde una pantalla que ya no existe. Se corta al salir.
  useEffect(() => clearIntervalActivo, [clearIntervalActivo]);

  /** Si los comprobantes se mueven a la carpeta de escaneados al digitalizarlos.
   *
   *  `null` hasta que llega `GET /scan/config`: antes de eso no se sabe cual es el
   *  valor real del servidor, y sembrar la casilla con un `true` de aqui seria
   *  mentir en cuanto alguien apague `TICKETS_SCAN_ARCHIVAR` en el `.env`.
   */
  const [archivar, setArchivar] = useState<boolean | null>(null);

  const escanear = async (conEmpresa: boolean) => {
    const inicioCliente = Date.now();
    setPreparando(true);
    setEscaneando(true);
    setError(null);
    setConexionCortada(false);
    setCorrida(null);
    // La "Última pasada" anterior no se muestra durante el escaneo nuevo:
    // en segundo plano no hay un `ScanResponse` que poner ahi, y dejar la
    // corrida anterior seria mostrar numeros viejos como si fueran de esta.
    // El resultado vive en el panel de progreso de la corrida (PanelProgreso)
    // y en el registro de archivos, que `cargar()` refresca al terminar.
    setUltimo(null);
    setTiempoClienteSegundos(null);
    // El lote de ESTA corrida se congela al arrancar: si el operador cambia
    // de lote mientras corre, el folio que muestra el progreso sigue siendo
    // el de la corrida, no el de la selección nueva. Un lote CERRADO no llega
    // aquí (la lista no deja elegirlo), pero se vuelve a comprobar: mandar su
    // id sería un 409 del servidor.
    const loteParaEscanear = lotes.find((l) => l.id === loteId && l.estado !== 'CERRADO') ?? null;
    setLoteDeCorrida(loteParaEscanear);
    try {
      // Segundo plano (2026-11-06): `POST /scan` ya NO se espera. Responde
      // 202 de inmediato y el trabajo sigue en el servidor; el progreso se
      // sigue por `GET /scan/runs` hasta que la corrida dice `terminada`.
      // Es lo que evita el corte de nginx a los 300 s: antes el POST se
      // quedaba abierto minutos y caia con 504 aunque el trabajo si se
      // hiciera, y la pantalla decia "no se pudo escanear" con los tickets
      // ya guardados. Un error que dice que no paso algo que si paso es peor
      // que un error: hace que el operador repita la corrida y duplique los
      // tickets.
      //
      // Se guarda el id de las corridas que ya existian antes de pedir, para
      // saber cual es LA NUESTRA cuando aparezca: la primera corrida nueva
      // que se vea en el canal es la que este escaneo abrio.
      const conocidas = new Set<string>();
      try {
        const antes = await ticketsApi.scanRuns(5);
        antes.corridas.forEach((c) => conocidas.add(c.id));
      } catch {
        /* sin el "antes", la espera usa la primera corrida viva que vea */
      }

      const watching = setInterval(() => {
        ticketsApi
          .scanRuns(5)
          .then((r) => {
            const viva = r.corridas.find((c) => !c.terminada);
            if (viva) {
              setCorrida(viva);
            }
          })
          .catch(() => {
            /* si el canal falla, el escaneo sigue: no se interrumpe nada */
          });
      }, 2000);
      setIntervalActivo(watching);

      // `archivar` viaja explicito y no omitido. Si se omitiera, el servidor usaria
      // el `.env`, que es global: no habria forma de cambiarlo para UNA corrida
      // desde la pantalla, que es justo lo que hace falta al probar.
      //
      // Y si `archivar` es `null` todavia (la config no ha llegado), se manda
      // `null`, no `false`: `null` le dice al servidor "usa tu default", y `false`
      // le diria "no muevas nada", que es una decision que el operador todavia no
      // ha tomado.
      await ticketsApi.escanearEnSegundoPlano({
        ...(conEmpresa && companyId ? { company_id: companyId } : {}),
        // Solo si el operador eligió un lote: sin `lote_id` el servidor crea
        // o asocia uno, y mandar `lote_id: null` diría otra cosa.
        ...(loteParaEscanear ? { lote_id: loteParaEscanear.id } : {}),
        archivar,
      });
      await registrarEventoFrontend({ accion: 'escanear', modulo: 'scan', detalles: { con_empresa: conEmpresa, company_id: companyId || null, segundo_plano: true, lote_id: loteParaEscanear?.id ?? null } });

      // Espera a que la corrida NUEVA termine: se consulta el canal cada 2 s
      // hasta verla terminada, o hasta 30 minutos. Si el servidor tardo en
      // abrirla (otra corrida con el candado), la primera corrida que no estaba
      // en `conocidas` es la nuestra.
      const limiteEspera = Date.now() + 30 * 60 * 1000;
      let mia: ScanCorrida | undefined;
      while (Date.now() < limiteEspera) {
        await new Promise((r) => setTimeout(r, 2000));
        let corridas: ScanCorrida[] = [];
        try {
          corridas = (await ticketsApi.scanRuns(5)).corridas;
        } catch {
          continue;
        }
        mia = corridas.find((c) => !conocidas.has(c.id));
        if (mia) {
          setCorrida(mia);
          if (mia.terminada) break;
        }
      }
      if (mia && !mia.terminada) {
        setError('El escaneo sigue en curso después de 30 minutos. Revisa el progreso en esta misma pantalla.');
      }
      await cargar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo escanear');
    } finally {
      clearIntervalActivo();
      setEscaneando(false);
      setPreparando(false);
      setTiempoClienteSegundos((Date.now() - inicioCliente) / 1000);
    }
  };

  const abrirDetalle = async (id: string) => {
    try {
      const archivo = await ticketsApi.scanFile(id);
      setDetalle(archivo);
      // El ticket se pide aparte y su fallo NO cierra el detalle. El archivo y
      // su historial son el objeto de esta pantalla; el comprobante pegado es un
      // extra, y perderlo no puede hacer que el modal se quede en blanco.
      if (archivo.ticket_id) {
        ticketsApi.get(archivo.ticket_id)
          .then(setTicketDelDetalle)
          .catch(() => setTicketDelDetalle(null));
      } else {
        setTicketDelDetalle(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo abrir el archivo');
    }
  };

  const reprocesar = async (id: string) => {
    try {
      await ticketsApi.reprocesarScanFile(id, companyId || undefined);
      setDetalle(null);
      setTicketDelDetalle(null);
      await cargar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo reprocesar');
    }
  };

  /** Elige un lote para el próximo escaneo y abre su detalle. Solo se llama
   *  desde un lote que no está CERRADO: el botón de elegir de un cerrado está
   *  deshabilitado (ver la lista), y `verDetalleLote` es el camino para
   *  mirar un cerrado sin elegirlo. */
  const seleccionarLote = async (id: string) => {
    setLoteId(id);
    setConfirmarCerrar(false);
    setMostrarReabrir(false);
    try {
      setLoteDetalle(await lotesApi.detalle(id));
    } catch (e) {
      setLoteDetalle(null);
      setErrorLotes(e instanceof Error ? e.message : 'No se pudo cargar el lote');
    }
  };

  /** Abre el detalle de un lote SIN elegirlo para escanear. Es el camino
   *  para mirar un lote CERRADO (complementario, reapertura) sin que el
   *  próximo escaneo intente mandar su id. */
  const verDetalleLote = async (id: string) => {
    setConfirmarCerrar(false);
    setMostrarReabrir(false);
    try {
      setLoteDetalle(await lotesApi.detalle(id));
    } catch (e) {
      setLoteDetalle(null);
      setErrorLotes(e instanceof Error ? e.message : 'No se pudo cargar el lote');
    }
  };

  const crearLote = async () => {
    if (!companyId) {
      setErrorLotes('Elige una empresa arriba para crear el lote.');
      return;
    }
    const ejercicio = Number(formLote.ejercicio);
    const mes = Number(formLote.mes);
    const totalEsperado = formLote.total_esperado.trim() === '' ? undefined : Number(formLote.total_esperado);
    if (!formLote.nombre.trim()) {
      setErrorLotes('El lote necesita un nombre.');
      return;
    }
    if (!Number.isInteger(ejercicio) || ejercicio < 1900 || ejercicio > 2100) {
      setErrorLotes('El ejercicio tiene que ser un año entre 1900 y 2100.');
      return;
    }
    if (!Number.isInteger(mes) || mes < 1 || mes > 12) {
      setErrorLotes('El mes tiene que ser entre 1 y 12.');
      return;
    }
    if (totalEsperado !== undefined && (!Number.isInteger(totalEsperado) || totalEsperado < 0)) {
      setErrorLotes('El total esperado tiene que ser un número entero mayor o igual a 0.');
      return;
    }
    try {
      setCreandoLote(true);
      setErrorLotes(null);
      const nuevo = await lotesApi.crear({
        company_id: companyId,
        nombre: formLote.nombre.trim(),
        ejercicio,
        mes,
        tipo_periodo: formLote.tipo_periodo,
        ...(totalEsperado !== undefined ? { total_esperado: totalEsperado } : {}),
      });
      setMostrarFormLote(false);
      setFormLote((f) => ({ ...f, nombre: '', total_esperado: '' }));
      await cargarLotes();
      // El lote nuevo queda elegido: es el que se acaba de crear para este escaneo.
      await seleccionarLote(nuevo.id);
    } catch (e) {
      setErrorLotes(e instanceof Error ? e.message : 'No se pudo crear el lote');
    } finally {
      setCreandoLote(false);
    }
  };

  const cerrarLote = async () => {
    if (!loteDetalle) return;
    try {
      setCerrandoLote(true);
      const actualizado = await lotesApi.cerrar(loteDetalle.id);
      setLotes((actual) => actual.map((l) => (l.id === actualizado.id ? actualizado : l)));
      // La selección se suelta: un lote cerrado no se puede elegir para un
      // nuevo escaneo. El detalle se queda abierto, que es donde están el
      // complementario y la reapertura.
      setLoteId('');
      setConfirmarCerrar(false);
      await verDetalleLote(actualizado.id);
    } catch (e) {
      setErrorLotes(e instanceof Error ? e.message : 'No se pudo cerrar el lote');
    } finally {
      setCerrandoLote(false);
    }
  };

  const reabrirLote = async () => {
    if (!loteDetalle) return;
    if (motivoReabrir.trim().length < 3) {
      setErrorLotes('El motivo de la reapertura tiene que tener al menos 3 caracteres.');
      return;
    }
    try {
      setAccionLote(true);
      const actualizado = await lotesApi.reabrir(loteDetalle.id, motivoReabrir.trim());
      setLotes((actual) => actual.map((l) => (l.id === actualizado.id ? actualizado : l)));
      setMostrarReabrir(false);
      setMotivoReabrir('');
      await seleccionarLote(actualizado.id);
    } catch (e) {
      setErrorLotes(e instanceof Error ? e.message : 'No se pudo reabrir el lote');
    } finally {
      setAccionLote(false);
    }
  };

  const crearComplementario = async () => {
    if (!loteDetalle) return;
    try {
      setAccionLote(true);
      const nuevo = await lotesApi.complementario(loteDetalle.id);
      await cargarLotes();
      // El complementario nace ABIERTO y queda elegido: es donde van los faltantes.
      await seleccionarLote(nuevo.id);
    } catch (e) {
      setErrorLotes(e instanceof Error ? e.message : 'No se pudo crear el lote complementario');
    } finally {
      setAccionLote(false);
    }
  };

  /** "Nuevo escaneo": limpia solo la VISTA del formulario y los resultados.
   *
   *  No borra archivos ni lotes ni la selección de lote: lo que se va es lo
   *  que se estaba mirando (el progreso, la última pasada, el detalle de un
   *  archivo), para empezar otra pasada con la pantalla limpia. */
  const nuevoEscaneo = () => {
    setUltimo(null);
    setCorrida(null);
    setDetalle(null);
    setTicketDelDetalle(null);
    setError(null);
    setConexionCortada(false);
    setTiempoClienteSegundos(null);
    setLoteDeCorrida(null);
  };

  // La corrida en curso (o la que acaba de terminar) manda sobre la última
  // que conoce el servidor: es la que el operador acaba de ver correr.
  const corridaParaEncabezado = corrida ?? ultimaCorrida;

  if (cargando && !config && !ocr) return <Loading message="Cargando el escáner..." />;

  return (
    <div className="p-4 lg:p-8 max-w-7xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Escáner de carpeta</h1>
          <p className="text-sm text-gray-500 mt-1">
            Lee los comprobantes de una carpeta local. Las fotos pasan por OCR antes que por
            el modelo, y lo que no cambió desde la última pasada no se vuelve a leer.
          </p>
        </div>
        <div className="w-56">
          <Select
            value={companyId}
            onChange={(e) => { setCompanyId(e.target.value); setPagina(0); }}
            options={[
              { value: '', label: 'Todas las empresas' },
              ...companies.map(c => ({ value: c.id, label: c.name })),
            ]}
          />
        </div>
      </div>

      {config && <PanelConfig config={config} />}
      {ocr && <PanelOcr ocr={ocr} />}

      {/* Lotes de digitalización.
       *
       *  Va ANTES del formulario de escaneo y no después: el lote es la
       *  primera decisión de la pasada (a qué periodo van estos papeles), y
       *  elegirlo después de pulsar "Escanear" sería elegirlo tarde.
       */}
      <section aria-label="Lotes de digitalización" className="mt-6">
        <Card className="mb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Último escaneo</h2>
              {corridaParaEncabezado ? (
                <p className="text-sm text-gray-600 mt-1">
                  {new Date(corridaParaEncabezado.iniciada_at).toLocaleString('es-MX')}
                  {' · '}{corridaParaEncabezado.archivos_vistos} archivo(s)
                  {' · '}{corridaParaEncabezado.con_ticket} con ticket
                  {' · '}⏱ {formatoDuracion(corridaParaEncabezado.segundos)}
                  {!corridaParaEncabezado.terminada && ' (en curso)'}
                </p>
              ) : (
                <p className="text-sm text-gray-500 mt-1">Aún no hay escaneos.</p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                onClick={nuevoEscaneo}
                disabled={escaneando}
                title="Limpia la vista (progreso y última pasada). No borra archivos ni lotes."
              >
                Nuevo escaneo
              </Button>
              <Button
                variant="secondary"
                onClick={() => setMostrarFormLote((v) => !v)}
                disabled={escaneando || !companyId}
                title={companyId ? 'Crear un lote nuevo' : 'Elige una empresa para crear un lote'}
              >
                {mostrarFormLote ? 'Cancelar nuevo lote' : 'Nuevo lote'}
              </Button>
            </div>
          </div>
          <p className="text-xs text-gray-500 mt-3">
            Para agregar un documento que faltó, ponlo en la carpeta de entrada y vuelve a escanear:
            solo se leerá el nuevo, los demás salen SIN_CAMBIOS.
          </p>

          {errorLotes && (
            <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm flex items-start gap-2">
              <span className="flex-1">{errorLotes}</span>
              <Button variant="secondary" onClick={cargarLotes} className="!py-1 !px-2 !text-xs">Reintentar</Button>
            </div>
          )}

          {lotesCargando ? (
            <p className="text-sm text-gray-500 mt-3">Cargando lotes…</p>
          ) : lotes.length === 0 ? (
            <p className="text-sm text-gray-500 mt-3">
              {companyId
                ? 'No hay lotes para esta empresa. Crea el primero con “Nuevo lote”.'
                : 'Elige una empresa para ver sus lotes, o crea uno con “Nuevo lote”.'}
            </p>
          ) : (
            <div className="mt-4 space-y-4">
              {agruparLotes(lotes).map((grupo) => (
                <div key={grupo.clave}>
                  <h3 className="text-sm font-semibold text-gray-700 capitalize">{grupo.etiqueta}</h3>
                  <ul className="mt-1 divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
                    {grupo.lotes.map((lote) => {
                      const cerrado = lote.estado === 'CERRADO';
                      const seleccionado = lote.id === loteId;
                      return (
                        <li key={lote.id} className={`flex flex-wrap items-center gap-x-1 ${seleccionado ? 'bg-blue-50' : 'bg-white'}`}>
                          <button
                            type="button"
                            onClick={() => void seleccionarLote(lote.id)}
                            disabled={cerrado || escaneando}
                            title={cerrado
                              ? 'Lote cerrado: no acepta más escaneos. Usa “Detalle” para crear un lote complementario o reabrirlo.'
                              : seleccionado
                                ? 'Lote seleccionado para el próximo escaneo'
                                : 'Elegir este lote para el próximo escaneo'}
                            aria-pressed={seleccionado}
                            className="flex-1 min-w-0 text-left px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                          >
                            <span className="font-mono text-xs font-semibold text-gray-900">{lote.folio}</span>
                            <span className="text-sm text-gray-700 truncate flex-1 min-w-0">{lote.nombre}</span>
                            <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${colorEstadoLote(lote.estado)}`}>
                              {etiquetaEstadoLote(lote.estado)}
                            </span>
                            <span className="text-xs text-gray-600 tabular-nums">
                              {lote.total_escaneado}/{lote.total_esperado ?? '—'} escaneados
                            </span>
                            <span className="text-xs text-gray-900 font-medium tabular-nums">{dineroLote(lote.importe_total)}</span>
                            {seleccionado && <span className="text-[11px] font-medium text-blue-700">Seleccionado</span>}
                          </button>
                          <Button variant="ghost" size="sm" onClick={() => void verDetalleLote(lote.id)} title="Ver el detalle del lote">
                            Detalle
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          )}

          {mostrarFormLote && (
            <form
              className="mt-4 rounded-lg border border-gray-200 bg-white p-3 space-y-3"
              onSubmit={(e) => { e.preventDefault(); void crearLote(); }}
            >
              <h3 className="font-semibold text-gray-900">Nuevo lote</h3>
              {!companyId && (
                <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
                  Elige una empresa arriba: el lote pertenece a una empresa.
                </p>
              )}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Input
                  label="Nombre"
                  value={formLote.nombre}
                  onChange={(e) => setFormLote((f) => ({ ...f, nombre: e.target.value }))}
                  placeholder="Octubre 2026"
                  maxLength={150}
                />
                <Select
                  label="Tipo de periodo"
                  value={formLote.tipo_periodo}
                  onChange={(e) => setFormLote((f) => ({ ...f, tipo_periodo: e.target.value as TipoPeriodo }))}
                  options={[
                    { value: 'mensual', label: 'Mensual' },
                    { value: 'quincenal', label: 'Quincenal' },
                    { value: 'semanal', label: 'Semanal' },
                  ]}
                />
                <Input
                  label="Ejercicio"
                  type="number"
                  value={formLote.ejercicio}
                  onChange={(e) => setFormLote((f) => ({ ...f, ejercicio: e.target.value }))}
                  min={1900}
                  max={2100}
                />
                <Select
                  label="Mes"
                  value={formLote.mes}
                  onChange={(e) => setFormLote((f) => ({ ...f, mes: e.target.value }))}
                  options={MESES.map((m, i) => ({ value: String(i + 1), label: `${i + 1} — ${m}` }))}
                />
                <Input
                  label="Total esperado (opcional)"
                  type="number"
                  value={formLote.total_esperado}
                  onChange={(e) => setFormLote((f) => ({ ...f, total_esperado: e.target.value }))}
                  min={0}
                  placeholder="¿Cuántos comprobantes esperas?"
                />
              </div>
              <p className="text-xs text-gray-500">
                El folio (LOT-2026-10-001…) lo genera el servidor. Sin fechas, el lote cubre el periodo completo.
              </p>
              <div className="flex gap-2 justify-end">
                <Button variant="secondary" type="button" onClick={() => setMostrarFormLote(false)}>
                  Cancelar
                </Button>
                <Button type="submit" disabled={creandoLote || !companyId}>
                  {creandoLote ? 'Creando…' : 'Crear lote'}
                </Button>
              </div>
            </form>
          )}

          {loteDetalle && (
            <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold text-gray-900">
                  {loteDetalle.folio} · {loteDetalle.nombre}
                </h3>
                <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${colorEstadoLote(loteDetalle.estado)}`}>
                  {etiquetaEstadoLote(loteDetalle.estado)}
                </span>
              </div>
              <p className="text-xs text-gray-600 mt-1">
                {fechaCorta(loteDetalle.fecha_inicio)} – {fechaCorta(loteDetalle.fecha_fin)}
                {' · '}{loteDetalle.tipo_periodo}
                {loteDetalle.lote_padre_id ? ' · Complemento de otro lote' : ''}
                {loteDetalle.motivo ? ` · Motivo: ${loteDetalle.motivo}` : ''}
              </p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-sm text-gray-700">
                <span><strong className="text-gray-900 tabular-nums">{loteDetalle.total_archivos}</strong> archivos</span>
                <span><strong className="text-gray-900 tabular-nums">{loteDetalle.total_tickets}</strong> tickets</span>
                <span>Importe vivo: <strong className="text-gray-900 tabular-nums">{dineroLote(loteDetalle.importe_total_vivo)}</strong></span>
                {loteDetalle.duplicados > 0 && (
                  <span className="text-blue-700">{loteDetalle.duplicados} duplicado(s) por contenido</span>
                )}
              </div>
              {loteDetalle.estado !== 'CERRADO' ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {!confirmarCerrar ? (
                    <Button variant="secondary" onClick={() => setConfirmarCerrar(true)} disabled={escaneando || accionLote}>
                      Cerrar lote
                    </Button>
                  ) : (
                    <span className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="text-gray-700">¿Cerrar {loteDetalle.folio}? Ya no aceptará más archivos.</span>
                      <Button variant="danger" size="sm" onClick={() => void cerrarLote()} disabled={cerrandoLote}>
                        {cerrandoLote ? 'Cerrando…' : 'Sí, cerrar'}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmarCerrar(false)}>
                        Cancelar
                      </Button>
                    </span>
                  )}
                </div>
              ) : (
                <div className="mt-3 space-y-2">
                  <p className="text-sm text-gray-700">
                    El lote cerrado no se edita; los faltantes van en un lote complementario.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => void crearComplementario()} disabled={escaneando || accionLote}>
                      {accionLote ? 'Creando…' : 'Crear lote complementario'}
                    </Button>
                    <Button variant="secondary" onClick={() => setMostrarReabrir(true)} disabled={escaneando || accionLote}>
                      Reabrir
                    </Button>
                  </div>
                  <p className="text-xs text-gray-500">
                    Reabrir solo si procede: queda registrado quién lo reabrió y por qué.
                  </p>
                </div>
              )}
            </div>
          )}

          <Modal
            isOpen={mostrarReabrir}
            onClose={() => { setMostrarReabrir(false); setMotivoReabrir(''); }}
            title={`Reabrir ${loteDetalle?.folio ?? 'lote'}`}
          >
            <div className="space-y-3">
              <p className="text-sm text-gray-600">
                La reapertura queda registrada en auditoría con tu usuario y este motivo.
                Úsala solo si procede: por ejemplo, llegó un comprobante que pertenece a
                este periodo y no a un complementario.
              </p>
              <div className="flex flex-col gap-1">
                <label htmlFor="motivo-reabrir" className="text-sm font-medium text-gray-700">Motivo</label>
                <textarea
                  id="motivo-reabrir"
                  value={motivoReabrir}
                  onChange={(e) => setMotivoReabrir(e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  placeholder="Por qué se reabre este lote"
                />
              </div>
              <div className="flex gap-2 justify-end">
                <Button variant="secondary" onClick={() => { setMostrarReabrir(false); setMotivoReabrir(''); }}>
                  Cancelar
                </Button>
                <Button onClick={() => void reabrirLote()} disabled={accionLote || motivoReabrir.trim().length < 3}>
                  {accionLote ? 'Reabriendo…' : 'Reabrir lote'}
                </Button>
              </div>
            </div>
          </Modal>
        </Card>
      </section>

      {/* La casilla va ANTES de los botones, y no junto a ellos.
       *
       *  Es una opcion del escaneo, no del boton: los dos botones la respetan, y
       *  ponerla entre ellos haria que pareciera que solo aplica a uno. Ademas va
       *  antes para que se lea antes de pulsar, que es cuando tiene efecto.
       */}
      <CasillaArchivar
        valor={archivar}
        onCambio={setArchivar}
        ocupado={escaneando}
        config={config}
      />

      <div className="mt-6 flex flex-wrap gap-3">
        {/*
          Dos botones y no uno con una casilla. "Escanear" sin empresa inventaria
          y es seguro: nadie puede crear un gasto por accidente. "Escanear y
          crear tickets" SI crea gastos, y por eso exige que haya una empresa
          elegida y se apaga sin ella. Un solo boton con un parametro opcional
          deja la decision de crear tickets escondida en un estado que nadie ve.
        */}
        <div className="flex flex-col items-start gap-1">
          <Button onClick={() => escanear(false)} disabled={escaneando || !companyId} title={companyId ? 'Solo revisión: lee e inventaría, no crea tickets' : 'Elige una empresa para escanear'} className={preparando ? 'opacity-80 cursor-wait' : ''}>
            {preparando ? (
              <span className="flex items-center gap-2"><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" /> Iniciando…</span>
            ) : escaneando ? 'Escaneando...' : 'Solo revisar (sin crear tickets)'}
          </Button>
          <p className="text-xs text-gray-500 max-w-64">Solo lee e inventaría, no crea tickets.</p>
        </div>
        <div className="flex flex-col items-start gap-1">
          <Button
            onClick={() => escanear(true)}
            disabled={escaneando || !companyId}
            title={companyId ? 'Lee y crea los tickets en la empresa elegida' : 'Elige una empresa: sin ella no se puede atribuir ningún gasto'}
          >
            {preparando ? (
              <span className="flex items-center gap-2"><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" /> Iniciando…</span>
            ) : escaneando ? 'Escaneando...' : 'Escanear y crear tickets'}
          </Button>
          <p className="text-xs text-gray-500 max-w-64">Lee y crea los tickets en la empresa elegida.</p>
        </div>
      </div>

      {/* Antes del panel, no despues: si hay un modelo configurado que no esta
          instalado, eso explica POR QUE los tickets de abajo salieron como
          salieron, y verlo despues de leerlos es leerlo al reves. */}
      <EstadoDeIa />

      {preparando && (
        <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg text-blue-800 text-sm flex items-center gap-2">
          <span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
          <span>Preparando el escaneo, detectando archivos{stats?.total_archivos ? ` (0 de ${stats.total_archivos})` : '…'}</span>
        </div>
      )}
      {corrida && <PanelProgreso corrida={corrida} totalPosible={stats?.total_archivos} loteFolio={loteDeCorrida?.folio ?? null} />}

      {ultimo && (
        <PanelResultado
          resultado={ultimo}
          duracionClienteSegundos={tiempoClienteSegundos}
          corridaSegundos={corrida?.segundos ?? null}
          loteFolio={loteDeCorrida?.folio ?? null}
        />
      )}

      {/* MEDIDO el 2026-10-16. El texto importa mas que el color: el operador
          tiene que saber que el trabajo SIGUE, no que fallo. Antes esta pantalla
          decia "No se pudo escanear" con la corrida ya terminada y los tickets
          guardados, y lo unico que se veía era un error rojo. Repetir la corrida
          por ese aviso duplica los tickets. */}
      {conexionCortada && (
        <div className="mt-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-900 text-sm">
          <p className="font-medium">La conexion con el servidor se corto, pero el escaneo sigue</p>
          <p className="mt-1">
            Leer una foto con el modelo tarda varios minutos en esta maquina y el servidor
            cierra la conexion antes. <strong>Los comprobantes se estan guardando igual</strong>: el
            progreso de arriba es el real. No vuelvas a darle escanear, porque duplicaria los
            tickets.
          </p>
        </div>
      )}

      {error && (
        <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm flex items-start gap-2">
          <span className="flex-1">{error}</span>
          <Button variant="secondary" onClick={cargar} className="!py-1 !px-2 !text-xs">Reintentar</Button>
        </div>
      )}

      {stats && <PanelStats stats={stats} />}

      <div className="mt-6">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <h2 className="text-lg font-semibold text-gray-900">Registro de archivos</h2>
          <div className="w-52">
            <Select
              value={filtro}
              onChange={(e) => { setFiltro(e.target.value as ScanStatus | ''); setPagina(0); }}
              options={[
                { value: '', label: 'Todos los estados' },
                ...Object.entries(SCAN_STATUS_META).map(([k, v]) => ({ value: k, label: v.label })),
              ]}
            />
          </div>
        </div>

        {archivos.length === 0 ? (
          <EmptyState
            message={filtro ? 'No hay archivos en ese estado.' : 'Todavía no se ha escaneado nada.'}
          />
        ) : (
          <>
            <TablaArchivos archivos={archivos} onAbrir={abrirDetalle} />
            <Paginacion
              pagina={pagina}
              porPagina={POR_PAGINA}
              total={totalArchivos}
              onCambiar={setPagina}
            />
          </>
        )}
      </div>

      {detalle && (
        <Modal
          isOpen
          onClose={() => { setDetalle(null); setTicketDelDetalle(null); }}
          title={detalle.relative_path}
        >
          <DetalleArchivo
            detalle={detalle}
            ticket={ticketDelDetalle}
            onReprocesar={() => reprocesar(detalle.id)}
            onCerrar={() => { setDetalle(null); setTicketDelDetalle(null); }}
          />
        </Modal>
      )}
    </div>
  );
}

function PanelConfig({ config }: { config: ScanConfig }) {
  return (
    <Card className="mb-4">
      <div className="flex items-start gap-2">
        <Badge variant={config.carpeta_existe ? 'success' : 'danger'}>
          {config.carpeta_existe ? 'Carpeta lista' : 'No existe'}
        </Badge>
      </div>
      <p className="mt-2 font-mono text-sm text-gray-700 break-all">{config.carpeta}</p>
      <p className="text-xs text-gray-500 mt-1">
        {config.recursivo ? 'Baja a subcarpetas' : 'Solo el primer nivel'} · hasta{' '}
        {config.max_por_corrida} archivos por pasada · hasta{' '}
        {Math.round(config.max_bytes_por_archivo / 1024 / 1024)} MB por archivo
      </p>
      {!config.carpeta_existe && (
        <p className="text-sm text-red-700 mt-2">
          La carpeta no existe. El escáner la crea al arrancar si `TICKETS_INPUT_DIR_AUTOCREAR`
          está activo, y con Docker se monta desde el host.
        </p>
      )}
    </Card>
  );
}

function PanelOcr({ ocr }: { ocr: ScanOcrEstado }) {
  // El tipo del valor se escribe porque `Object.entries` sobre un `Record`
  // devuelve `unknown` en TS 5 con `noUncheckedIndexedAccess`, y `m.disponible`
  // deja de compilar. Se declara la forma del valor, no se castea a mano.
  const motores: Array<[string, { disponible: boolean; motivo: string | null }]> =
    Object.entries(ocr.motores);
  // El panel solo avisa cuando HAY un problema. Con todo funcionando, un cartel
  // verde de "OCR OK" en cada carga es ruido que entrena al ojo a ignorarlo, y
  // entonces el cartel rojo de verdad tampoco se lee.
  const algunoFalla = !ocr.ocr_habilitado || motores.some(([, m]) => !m.disponible);

  if (!algunoFalla) return null;

  return (
    <Card className="mb-4 border-yellow-300 bg-yellow-50">
      <h3 className="font-semibold text-yellow-900 mb-1">El OCR no está del todo listo</h3>
      <ul className="text-sm text-yellow-900 space-y-1">
        {!ocr.ocr_habilitado && (
          <li>El OCR está apagado (<code>OCR_ENABLED=false</code>). Las fotos irán directo al modelo.</li>
        )}
        {motores.filter(([, m]) => !m.disponible).map(([nombre, m]) => (
          <li key={nombre}>
            <span className="font-medium">{nombre}</span>: {m.motivo ?? 'no disponible'}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function formatoDuracion(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const resto = Math.round(s % 60);
  return resto ? `${m}m ${resto}s` : `${m}m`;
}

/** Los meses, en español y en minúscula: el encabezado del grupo y el
 *  desplegable del formulario dicen "octubre 2026", no "10/2026". */
const MESES = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

/** `2026-10-01` -> `01 oct 2026`. Se parte el texto en vez de pasar la cadena
 *  por `Date`: `new Date('2026-10-01')` lo interpreta en UTC y en una zona
 *  horaria detrás de UTC se renderiza como el día ANTERIOR. */
function fechaCorta(iso: string | null): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const mes = meses[Number(m[2]) - 1] ?? m[2];
  return `${m[3]} ${mes} ${m[1]}`;
}

/** `2026-10` -> `octubre 2026`, la etiqueta del grupo de lotes. */
function etiquetaPeriodo(ejercicio: number, mes: number): string {
  const nombre = MESES[mes - 1];
  return nombre ? `${nombre} ${ejercicio}` : `${mes}/${ejercicio}`;
}

/** Los lotes agrupados por periodo, del más reciente al más viejo. El
 *  backend ya los trae ordenados así; aquí solo se parte la lista por mes. */
function agruparLotes(lotes: Lote[]): Array<{ clave: string; etiqueta: string; lotes: Lote[] }> {
  const grupos = new Map<string, Lote[]>();
  for (const lote of lotes) {
    const clave = `${lote.ejercicio}-${String(lote.mes).padStart(2, '0')}`;
    const lista = grupos.get(clave) ?? [];
    lista.push(lote);
    grupos.set(clave, lista);
  }
  return [...grupos.entries()]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([clave, lista]) => {
      const primero = lista[0];
      return {
        clave,
        etiqueta: primero ? etiquetaPeriodo(primero.ejercicio, primero.mes) : clave,
        lotes: lista,
      };
    });
}

/** El color del estado, y por qué es ese: ABIERTO es trabajo en curso (verde),
 *  EN_REVISION es trabajo pendiente de una persona (ámbar) y CERRADO es
 *  archivo (gris). Un estado que el backend agregue y este archivo no conozca
 *  se muestra gris y con su texto crudo, no se esconde. */
function colorEstadoLote(estado: string): string {
  switch (estado) {
    case 'ABIERTO': return 'bg-green-100 text-green-800';
    case 'EN_REVISION': return 'bg-amber-100 text-amber-800';
    case 'CERRADO': return 'bg-gray-100 text-gray-500';
    default: return 'bg-gray-100 text-gray-500';
  }
}

function etiquetaEstadoLote(estado: string): string {
  switch (estado) {
    case 'ABIERTO': return 'Abierto';
    case 'EN_REVISION': return 'En revisión';
    case 'CERRADO': return 'Cerrado';
    default: return estado;
  }
}

/** Un importe que llega como texto desde la API (`Decimal`). */
function dineroLote(valor: string | null | undefined): string {
  if (!valor) return '—';
  const n = Number(valor);
  if (Number.isNaN(n)) return valor;
  return `$${n.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function PanelResultado({
  resultado,
  duracionClienteSegundos,
  corridaSegundos,
  loteFolio,
}: {
  resultado: ScanResultado;
  duracionClienteSegundos?: number | null;
  corridaSegundos?: number | null;
  /** El folio del lote de esta corrida, elegido en la pantalla antes de
   *  escanear. Es el folio LOCAL, no un campo de `ScanResultado`: la respuesta
   *  del escaneo todavía no trae el lote, y mostrar el elegido es honesto
   *  mientras el backend no lo devuelva. */
  loteFolio?: string | null;
}) {
  const { nuevos, actualizados, sin_cambios, duplicados, con_error, no_soportados,
    omitidos_por_tope, archivos_vistos, ilegibles = [], fotos_por_revisar = [],
    duplicados_por_contenido = [] } = resultado;
  const [ticketVer, setTicketVer] = useState<Ticket | null>(null);
  const [cargandoTicket, setCargandoTicket] = useState(false);
  async function abrirTicket(ticketId: string) {
    setCargandoTicket(true);
    try { const t = await ticketsApi.get(ticketId); setTicketVer(t); }
    finally { setCargandoTicket(false); }
  }
  // La lista principal es de comprobantes UNICOS. Un duplicado por contenido
  // llegaba aqui como un detalle mas con accion OMITIDO y se veia como otra
  // fila del escaneo, al lado del ticket correcto con el mismo importe. Los
  // duplicados van solo a su seccion aparte, con su original y su regla.
  const archivosDuplicados = new Set(duplicados_por_contenido.map((d) => d.archivo));
  const detallesUnicos = resultado.detalles.filter(
    (d: ScanItem) => d.status !== 'DUPLICADO' && !archivosDuplicados.has(d.relative_path),
  );
  // Los duplicados por HASH no van en `duplicados_por_contenido` (solo van
  // los de contenido): sin esto, al sacarlos del detalle principal
  // desaparecerian de la pantalla y solo quedaria el contador. Van a la
  // misma seccion colapsada, con la regla "mismos bytes".
  const duplicadosHash = resultado.detalles.filter(
    (d: ScanItem) => d.status === 'DUPLICADO' && !archivosDuplicados.has(d.relative_path),
  );
  const totalDuplicados = duplicados_por_contenido.length + duplicadosHash.length;
  // Tiempo total: primero el del servidor (el real, iniciada -> terminada),
  // luego el de la corrida en vivo, luego el medido en el cliente. Sin
  // ninguno no se inventa un cero: se esconde la linea.
  const duracion =
    resultado.duracion_segundos ?? corridaSegundos ?? duracionClienteSegundos ?? null;
  const porArchivo =
    duracion !== null && archivos_vistos > 0 ? duracion / archivos_vistos : null;

  return (
    <Card className="mt-4">
      <h3 className="font-semibold text-gray-900 mb-2">Última pasada</h3>
      {loteFolio && (
        <p className="text-sm text-gray-700 mb-2">
          Lote: <span className="font-mono font-semibold">{loteFolio}</span>
        </p>
      )}
      {duracion !== null && (
        <p className="text-sm text-gray-700 mb-2" data-testid="tiempo-total">
          ⏱ Tiempo total: <strong className="tabular-nums">{formatoDuracion(duracion)}</strong>
          {porArchivo !== null && (
            <span className="text-gray-500">
              {' '}· {archivos_vistos} archivo(s) · ~{formatoDuracion(porArchivo)} por archivo
            </span>
          )}
        </p>
      )}
      <div className="flex flex-wrap gap-2 text-sm">
        <Badge variant="info">{archivos_vistos} vistos</Badge>
        {nuevos > 0 && <Badge variant="success">{nuevos} nuevos</Badge>}
        {actualizados > 0 && <Badge variant="success">{actualizados} actualizados</Badge>}
        {sin_cambios > 0 && <Badge>{sin_cambios} sin cambios</Badge>}
        {duplicados > 0 && <Badge variant="info">{duplicados} duplicados</Badge>}
        {no_soportados > 0 && <Badge variant="warning">{no_soportados} no soportados</Badge>}
        {con_error > 0 && <Badge variant="danger">{con_error} con error</Badge>}
        {ilegibles.length > 0 && (
          <Badge variant="danger">{ilegibles.length} ilegibles</Badge>
        )}
        {fotos_por_revisar.length > 0 && (
          <Badge variant="warning">{fotos_por_revisar.length} fotos por retomar</Badge>
        )}
      </div>
      {/*
        ESTE BLOQUE ES EL QUE NO ESTABA, y es la diferencia entre "no se puede
        escanear" y "no hay nada que escanear". MEDIDO el 2026-10-16: los tres
        archivos de la carpeta dieron `[Errno 5] Input/output error` al abrirlos
        (un montaje de archivos de Docker roto), el `POST /scan` respondio 200
        con cero, y la pantalla no decia nada. El unico rastro era
        una linea de log, y quien escanea no abre logs.

        Con nombre y motivo, porque "3 ilegibles" no dice que hacer: si los tres
        son el mismo error de montaje, el problema es del entorno; si son tres
        errores distintos, hay que ir por los papeles.
      */}
      {ilegibles.length > 0 && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3">
          <p className="text-sm font-medium text-red-800">
            {ilegibles.length === 1
              ? 'Un archivo de la carpeta no se pudo abrir:'
              : `${ilegibles.length} archivos de la carpeta no se pudieron abrir:`}
          </p>
          <p className="text-xs text-red-700 mt-1">
            No se digitalizaron ni se movieron. Si todos dan el mismo error,
            probablemente el problema sea la carpeta montada y no los papeles.
          </p>
          <ul className="mt-2 space-y-1">
            {ilegibles.map((f) => (
              <li key={f.ruta} className="text-xs text-red-800">
                <span className="font-mono">{f.ruta}</span>
                <span className="text-red-600"> — {f.motivo}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {fotos_por_revisar.length > 0 && (
        <div className="mt-3 rounded-md border border-yellow-200 bg-yellow-50 p-3">
          <p className="text-sm font-medium text-yellow-800">
            {fotos_por_revisar.length === 1
              ? 'Una foto hay que volver a tomarla:'
              : `${fotos_por_revisar.length} fotos hay que volver a tomarlas:`}
          </p>
          <p className="text-xs text-yellow-700 mt-1">
            No se gastó tiempo en leerlas porque la foto no daba para leer el
            ticket. Vuelve a tomarlas con más luz y sin reflejo.
          </p>
          <ul className="mt-2 space-y-1">
            {fotos_por_revisar.map((f) => (
              <li key={f.archivo} className="text-xs text-yellow-800">
                <span className="font-mono">{f.archivo}</span>
                <span className="text-yellow-600"> — {f.motivo ?? 'foto por revisar'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {totalDuplicados > 0 && (
        <details className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3">
          <summary className="text-sm font-medium text-blue-800 cursor-pointer">
            Duplicados detectados ({totalDuplicados}) — no se sumaron dos veces, el original se conserva.
          </summary>
          <p className="text-xs text-blue-700 mt-1">
            Mismo proveedor, total y fecha que un ticket que ya existía, aunque
            la foto fuera distinta. El original se conserva; este intento queda
            en el registro, ligado a él.
          </p>
          <ul className="mt-2 space-y-1">
            {duplicados_por_contenido.map((d) => (
              <li key={`${d.archivo}-${d.ticket_id ?? ''}`} className="text-xs text-blue-800">
                <span className="font-mono">{d.archivo}</span>
                {' — '}{d.proveedor ?? 'sin proveedor'}
                {d.total != null && (
                  <span className="font-medium tabular-nums"> · ${Number(d.total).toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                )}
                {d.fecha && <span> · {d.fecha}</span>}
                {d.ticket_id && (
                  <span className="text-blue-600"> · original: <span className="font-mono" title={d.ticket_id}>{d.ticket_id.slice(0, 8)}…</span></span>
                )}
                {d.regla && <span className="text-blue-600"> · {d.regla}</span>}
                {d.ticket_id && (
                  <button
                    type="button"
                    onClick={() => abrirTicket(d.ticket_id!)}
                    className="ml-2 text-blue-600 hover:text-blue-800 hover:underline"
                  >
                    {cargandoTicket ? 'Abriendo…' : 'Ver original'}
                  </button>
                )}
              </li>
            ))}
          </ul>
          {duplicadosHash.length > 0 && (
            <ul className="mt-2 space-y-1">
              {duplicadosHash.map((d) => (
                <li key={d.relative_path} className="text-xs text-blue-800">
                  <span className="font-mono">{d.relative_path}</span>
                  {' — '}mismos bytes que un archivo ya procesado
                  <span className="text-blue-600"> · mismos bytes</span>
                  {d.ticket_id && (
                    <span className="text-blue-600"> · original: <span className="font-mono" title={d.ticket_id}>{d.ticket_id.slice(0, 8)}…</span></span>
                  )}
                  {d.ticket_id && (
                    <button
                      type="button"
                      onClick={() => abrirTicket(d.ticket_id!)}
                      className="ml-2 text-blue-600 hover:text-blue-800 hover:underline"
                    >
                      Ver original
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
      {omitidos_por_tope > 0 && (
        <p className="text-sm text-gray-600 mt-2">
          Se alcanzó el tope de la pasada: <strong>{omitidos_por_tope}</strong> archivos quedan
          para la siguiente. Vuelve a escanear para seguir.
        </p>
      )}
      {detallesUnicos.length > 0 && (
        <ul className="mt-3 text-sm text-gray-700 space-y-1 max-h-48 overflow-y-auto">
          {detallesUnicos.map((d: ScanItem) => (
            <li key={d.relative_path} className="flex items-start gap-2">
              <span className="font-mono text-xs text-gray-500 pt-0.5 truncate">
                {d.relative_path}
              </span>
              <span className="text-xs pt-0.5">
                {accionLabel(d.accion)}
                {d.origen && ` · ${motorLabel(d.origen)}`}
                {d.modelo_ia && (
                  <span
                    className="ml-1 px-1.5 py-0.5 rounded bg-purple-100 text-purple-800 font-mono"
                    title={`Modelo: ${d.modelo_ia}${
                      d.proveedor_ia ? ` (${d.proveedor_ia})` : ""
                    }`}
                  >
                    {d.modelo_ia}
                  </span>
                )}
                {d.confianza !== null && ` · ${d.confianza.toFixed(2)}`}
                {d.calidad_foto && (
                  <span className="text-yellow-700"> · 📷 {d.calidad_foto.mensaje}</span>
                )}
                {d.discrepancias && d.discrepancias.length > 0 && (
                  <span className="text-amber-700"> · ⚠ {d.discrepancias.join(', ')}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      <Modal isOpen={ticketVer !== null} onClose={() => setTicketVer(null)} title="Comprobante original">
        {ticketVer && <VerDocumento ticket={ticketVer} />}
      </Modal>
    </Card>
  );
}

function PanelStats({ stats }: { stats: ScanStats }) {
  return (
    <div className="mt-6 grid grid-cols-2 md:grid-cols-4 gap-3">
      <Contador etiqueta="Archivos" valor={stats.total_archivos} />
      <Contador etiqueta="Con ticket" valor={stats.tickets_vinculados} />
      <Contador etiqueta="Con error" valor={stats.archivos_con_error} tono="danger" />
      <Contador etiqueta="Sin ticket" valor={stats.archivos_sin_ticket} tono="warning" />
      {/*
        El desactualizado va aparte y con su propio tono, aunque se ponga en
        amarillo. Es un PROCESADO que no esta al dia: su archivo cambio y el
        ticket ya tiene correcciones de una persona, asi que el escaner no lo
        toco. Agrupado con los PROCESADOS se leeria como "todo bien".
      */}
      {stats.archivos_desactualizados > 0 && (
        <div className="col-span-2 md:col-span-4">
          <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
            <p className="text-sm text-yellow-900">
              <strong>{stats.archivos_desactualizados}</strong> archivo(s) cambiaron desde que se
              leyeron, pero su ticket ya fue corregido a mano y no se sobreescribió. Ábrelos uno
              por uno y usa "Reprocesar" si de verdad quieres reemplazar la corrección.
            </p>
          </div>
        </div>
      )}
      {Object.keys(stats.por_motor).length > 0 && (
        <div className="col-span-2 md:col-span-4">
          <p className="text-xs text-gray-500 mb-1">Leídos por escalón</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(stats.por_motor).map(([motor, n]) => (
              <Badge key={motor} variant={motor === 'ocr' ? 'success' : 'default'}>
                {motorLabel(motor)}: {n}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Contador({ etiqueta, valor, tono = 'default' }: {
  etiqueta: string; valor: number; tono?: 'default' | 'danger' | 'warning';
}) {
  const colores = {
    default: 'text-gray-900',
    danger: 'text-red-600',
    warning: 'text-yellow-700',
  };
  return (
    <Card>
      <p className="text-xs text-gray-500">{etiqueta}</p>
      <p className={`text-2xl font-bold ${colores[tono]}`}>{valor}</p>
    </Card>
  );
}

function TablaArchivos({ archivos, onAbrir }: {
  archivos: ScanFile[]; onAbrir: (id: string) => void;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left text-xs text-gray-500 uppercase">
          <tr>
            <th className="px-4 py-2">Archivo</th>
            <th className="px-4 py-2">Negocio</th>
            <th className="px-4 py-2">Fecha</th>
            <th className="px-4 py-2">Total</th>
            <th className="px-4 py-2">Estado</th>
            <th className="px-4 py-2">Leído por</th>
            <th className="px-4 py-2">Intentos</th>
            <th className="px-4 py-2">Ticket</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {archivos.map(f => (
            <tr key={f.id} className="hover:bg-gray-50">
              <td className="px-4 py-2 font-mono text-xs truncate max-w-xs" title={f.relative_path}>
                {f.relative_path}
              </td>
              <td className="px-4 py-2 text-xs text-gray-700 truncate max-w-[160px]" title={f.ticket_negocio ?? ''}>
                {f.ticket_negocio ?? '—'}
              </td>
              <td className="px-4 py-2 text-xs text-gray-600">
                {f.ticket_fecha ?? '—'}
              </td>
              <td className="px-4 py-2 text-xs text-gray-900 font-medium tabular-nums">
                {f.ticket_total ? `$${Number(f.ticket_total).toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
              </td>
              <td className="px-4 py-2">
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${COLOR_ESTADO[f.status]}`}>
                  {SCAN_STATUS_META[f.status]?.label ?? f.status}
                </span>
              </td>
              <td className="px-4 py-2 text-gray-600">{motorLabel(f.read_by)}</td>
              <td className="px-4 py-2 text-gray-600">{f.attempts}</td>
              {/* La columna del TICKET.
               *
               *  ESTO ERA TEXTO MUERTO. Decia `{f.ticket_id ? 'ver' : '-'}`: un
               *  `<span>` con la palabra "ver", que no era un enlace ni un boton
               *  y no hacia NADA al pulsarlo. Quien lo lea le hace click y no pasa.
               *  Es la columna que mas invita a usarla —"ver el ticket" es justo
               *  lo que uno quiere al escanear— y era la unica que no llevaba a
               *  ninguna parte.
               *
               *  Ahora es un boton de verdad y lleva al mismo detalle que la
               *  columna "Detalle": el modal ya pide el archivo, su historial y el
               *  ticket con su comprobante pegado. Dos botones al mismo sitio
               *  seria redundante; lo que cambia es que este deja de ser
               *  decorativo.
               *
               *  Y el guion cuando no hay ticket se queda siendo guion, con el
               *  motivo al lado cuando existe: los archivos leidos sin empresa son
               *  `PROCESADO` y no tienen ticket, y esa fila no es un fallo del
               *  escaner. */}
              <td className="px-4 py-2 text-gray-600">
                {f.ticket_id ? (
                  <button
                    onClick={() => onAbrir(f.id)}
                    className="text-blue-600 hover:text-blue-800 hover:underline"
                    title="Abrir el ticket y su comprobante original"
                  >
                    Ver ticket
                  </button>
                ) : (
                  <span
                    className="text-gray-400"
                    title={f.last_error ?? 'Este archivo no tiene ticket'}
                  >
                    &mdash;
                  </span>
                )}
              </td>
              <td className="px-4 py-2 text-right">
                <Button variant="ghost" size="sm" onClick={() => onAbrir(f.id)}>
                  Detalle
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Paginacion({ pagina, porPagina, total, onCambiar }: {
  pagina: number; porPagina: number; total: number; onCambiar: (p: number) => void;
}) {
  const paginas = Math.ceil(total / porPagina);
  if (paginas <= 1) return null;
  return (
    <div className="flex items-center justify-between mt-3 text-sm">
      <span className="text-gray-500">
        Página {pagina + 1} de {paginas} · {total} archivos
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" disabled={pagina === 0} onClick={() => onCambiar(pagina - 1)}>
          Anterior
        </Button>
        <Button variant="secondary" size="sm" disabled={pagina >= paginas - 1} onClick={() => onCambiar(pagina + 1)}>
          Siguiente
        </Button>
      </div>
    </div>
  );
}

function DetalleArchivo({ detalle, ticket, onReprocesar, onCerrar }: {
  detalle: ScanFileDetail;
  /** El ticket del archivo, o `null` si no se pudo cargar. `VerDocumento` lo
   *  necesita entero porque decide si hay documento con `tiene_documento`. */
  ticket: Ticket | null;
  onReprocesar: () => void;
  onCerrar: () => void;
}) {
  const meta = SCAN_STATUS_META[detalle.status];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge variant={meta?.variant ?? 'default'}>{meta?.label ?? detalle.status}</Badge>
        <Badge>{motorLabel(detalle.read_by)}</Badge>
        <Badge>{detalle.attempts} intento(s)</Badge>
        {detalle.detected_format && <Badge>formato real: {detalle.detected_format}</Badge>}
      </div>

      {/* El aviso sale SOLO con una discrepancia real: la extension pertenece a
          una categoria y los bytes son de otra. MEDIDO el 2026-10-12: salia en
          los 8 de 8, porque comparaba `'.jpeg'.includes('image')`, que es falso
          siempre — una extension y una categoria no son comparables. Un aviso
          que aparece siempre no informa: entrena a ignorar el que si. */}
      {hayDiscrepanciaDeFormato(detalle.declared_extension, detalle.detected_format) && (
        <p className="text-sm text-yellow-800 bg-yellow-50 border border-yellow-200 rounded p-2">
          El archivo se llama <code>{detalle.declared_extension}</code> pero sus bytes son{' '}
          <code>{detalle.detected_format}</code>. Se leyó como lo que dice el contenido, que es lo
          único que no se puede cambiar renombrando.
        </p>
      )}

      {detalle.last_error && (
        <div className="text-sm">
          <p className="font-medium text-gray-700 mb-1">Aviso</p>
          <p className="text-gray-600 bg-gray-50 border border-gray-200 rounded p-2">
            {detalle.last_error}
          </p>
        </div>
      )}

      {/*
        El comprobante se pide aqui y no en la tabla por una razon concreta:
        `VerDocumento` decide si hay documento mirando `ticket.tiene_documento`,
        y `scan_files` solo tiene el `ticket_id`. Pintar el boton a ciegas y que
        el propio boton diga "no hay documento" seria peor que no offercerlo: el
        escaner SI puede tener archivos sin ticket, y no todos los tickets
        tienen el papel pegado.
      */}
      {ticket
        ? (
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-600">Comprobante original:</span>
            <VerDocumento ticket={ticket} />
          </div>
        )
        : detalle.ticket_id && (
          <p className="text-sm text-gray-500">
            Ticket <span className="font-mono">{detalle.ticket_id.slice(0, 8)}</span> · el
            comprobante original se ve en la pantalla de Tickets.
          </p>
        )}

      <div>
        <p className="font-medium text-gray-700 mb-1 text-sm">Historial</p>
        {detalle.events.length === 0 ? (
          <p className="text-sm text-gray-500">Sin movimientos registrados.</p>
        ) : (
          <ul className="text-sm space-y-1">
            {detalle.events.map((e: ScanEvent) => (
              <li key={e.id} className="space-y-0.5 py-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-xs text-gray-400 whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString()}
                  </span>
                  <span className="font-medium">{accionLabel(e.action)}</span>
                  {e.actor && <span className="text-gray-500 text-xs">por {e.actor}</span>}
                </div>
                {e.detail && (
                  <p className="text-xs text-gray-500 pl-1 leading-relaxed">
                    — {e.detail}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex gap-2 justify-end pt-2 border-t">
        <Button variant="secondary" onClick={onCerrar}>Cerrar</Button>
        <Button onClick={onReprocesar}>Reprocesar</Button>
      </div>
    </div>
  );
}
