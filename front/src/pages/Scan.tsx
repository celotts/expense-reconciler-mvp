import { useCallback, useEffect, useRef, useState } from 'react';
import { registrarEventoFrontend } from '../services/auditoria';
import { ticketsApi, type Ticket } from '../services/api';
import { cajaChicaApi, type CajaChica } from '../services/cajaChica';
import { AvisoSinEmpresa } from '../contextos/Empresa';
import { useEmpresa } from '../contextos/EmpresaContexto';
import { useNavegacion } from '../contextos/NavegacionContexto';
import type {
  ArchivoNoPermitido, DigitalizacionDirectorio,
  ScanConfig, ScanCorrida, ScanEvent, ScanFile, ScanFileDetail, ScanItem, ScanOcrEstado,
  ScanResultado, ScanStats, ScanStatus,
} from '../types/scan';
import { Button, Card, Badge, Loading, EmptyState, Select, Modal } from '../components/ui';
import { SCAN_STATUS_META, motorLabel, accionLabel, COLOR_ESTADO, hayDiscrepanciaDeFormato } from '../utils/scan';
import { VerArchivoEscaneado, VerDocumento } from '../components/TicketDocumento';
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
  // La empresa se elige UNA vez en la barra lateral y viaja en `X-Company-ID`.
  // Un selector aqui permitiria escanear una empresa mientras el resto de la
  // pantalla (y el token) defendia otra; el servidor lo rechazaria con 409 y el
  // operador no entenderia por que.
  const { empresaActiva } = useEmpresa();
  const { irA } = useNavegacion();
  const companyId = empresaActiva?.company_id ?? '';
  const [cajasAbiertas, setCajasAbiertas] = useState<CajaChica[]>([]);
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
  /** El progreso en vivo de la corrida que esta corriendo. */
  const [corrida, setCorrida] = useState<ScanCorrida | null>(null);
  /** El id de esa corrida, para poder seguirla por su propio endpoint. */
  const [corridaId, setCorridaId] = useState<string | null>(null);
  /** El intervalo de sondeo, en un `useRef` y no en un estado.
   *
   *  En un estado provocaria un re-render en cada tick, y el `useEffect` de
   *  limpieza lo volvería a crear —el intervalo se recrea cada 2s y el
   *  watcher nunca llega a disparar limpio. Un `useRef` no re-renderiza y
   *  `clearInterval` lo alcanza siempre. */
  const watcherRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [detalle, setDetalle] = useState<ScanFileDetail | null>(null);
  const [ticketDelDetalle, setTicketDelDetalle] = useState<Ticket | null>(null);

  const POR_PAGINA = 20;

  const cargar = useCallback(async () => {
    try {
      setCargando(true);
      setError(null);
      // Config, OCR y stats van en paralelo y se reportan por separado, por la
      // misma razon que en el muestreo: si uno falla y los otros no, mostrar una
      // sola pantalla de error hides lo que si funciona.
      const [c, o, s, f, cajas] = await Promise.allSettled([
        ticketsApi.scanConfig(),
        ticketsApi.scanOcr(),
        ticketsApi.scanStats(),
        // Sin empresa activa NO se pide el registro: antes esta pantalla
        // ofrecia "Todas las empresas" y mostraba comprobantes ajenos. El
        // aviso de arriba dice por que la lista esta vacia.
        companyId
          ? ticketsApi.scanFiles({
            estado: filtro || undefined,
            companyId,
            limit: POR_PAGINA,
            offset: pagina * POR_PAGINA,
          })
          : Promise.resolve({ archivos: [], total: 0 }),
        companyId ? cajaChicaApi.list() : Promise.resolve([]),
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
      setCajasAbiertas(
        cajas.status === 'fulfilled'
          ? cajas.value.filter((caja) => caja.estado !== 'cerrada')
          : [],
      );
      if (f.status === 'fulfilled') {
        setArchivos(f.value.archivos);
        setTotalArchivos(f.value.total);
      } else {
        setArchivos([]);
        setError(f.reason instanceof Error ? f.reason.message : 'No se pudo cargar el registro');
      }
    } finally {
      setCargando(false);
    }
  }, [filtro, companyId, pagina]);

  useEffect(() => { cargar(); }, [cargar]);
  useEffect(() => { if (corrida) setPreparando(false); }, [corrida]);

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
    setPreparando(true);
    setEscaneando(true);
    setError(null);
    setConexionCortada(false);
    setCorrida(null);
    try {
      // `POST /scan` tarda minutos con fotos, asi que no se espera a que
      // responda para empezar a mostrar progreso: se arranca a consultar el
      // canal mientras el POST sigue abierto. `GET /scan/runs` es lo que dice
      // que hay una corrida en marcha, y a partir de ahi se sigue por su id.
      const watching = setInterval(() => {
        ticketsApi
          .scanRuns(5)
          .then((r) => {
            const viva = r.corridas.find((c) => !c.terminada);
            if (viva) {
              setCorrida(viva);
              setCorridaId(viva.id);
            }
          })
          .catch(() => {
            /* si el canal falla, el POST sigue: no se interrumpe nada */
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
      try {
        const r = await ticketsApi.escanear({
          ...(conEmpresa && companyId ? { company_id: companyId } : {}),
          archivar,
        });
        setUltimo(r);
        await registrarEventoFrontend({ accion: 'escanear', modulo: 'scan', detalles: { con_empresa: conEmpresa, company_id: companyId || null, archivos_vistos: r.archivos_vistos ?? null } });
      } catch (e) {
        // MEDIDO el 2026-10-16: con vision el escaneo tarda **7 a 9 minutos por
        // foto** y nginx corta la conexion a los 300 s. El trabajo **si se hizo**
        // -el ticket quedo guardado con el total leido- pero el POST cae con 504.
        //
        // Antes este `catch` hacia `setError(...)` y el `finally` apagaba el
        // canal: la pantalla decia "no se pudo escanear" con el escaneo YA
        // terminado y los tickets en la base. Un error que dice que no paso algo
        // que si paso es peor que un error: hace que el operador repita la
        // corrida y duplique los tickets.
        //
        // Aqui NO se apaga el canal: se sigue consultando hasta que la corrida
        // diga `terminada`, y el resultado sale de alla.
        setConexionCortada(true);
      }

      // Un ultimo Hald del canal para recoger el resumen final, que en el
      // POST puede venir con `terminada: false` si se respondio justo al cerrar.
      try {
        const { corridas } = await ticketsApi.scanRuns(5);
        const mia = corridas.find((c) => c.id === corridaId) ?? corridas[0];
        if (mia) setCorrida(mia);
      } catch {
        /* sinUltimo no es un error: ya vino el resultado del POST */
      }
      await cargar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo escanear');
    } finally {
      clearIntervalActivo();
      setEscaneando(false);
      setPreparando(false);
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
          <p className="text-xs font-medium text-gray-500">Empresa activa</p>
          <p className="mt-1 text-sm font-semibold text-gray-900 truncate" title={empresaActiva?.name}>
            {empresaActiva?.name ?? 'Sin empresa activa'}
          </p>
        </div>
      </div>

      <div className="mb-4">
        <AvisoSinEmpresa mensaje="Elige una empresa activa en la barra lateral antes de escanear. Sin ella no se puede atribuir ningún comprobante ni crear tickets." />
      </div>

      {cajasAbiertas.length > 0 && (
        <Card className="mb-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-medium text-gray-900">Cajas chicas abiertas</p>
              <p className="mt-1 text-sm text-gray-600">
                {cajasAbiertas.map((caja) => caja.nombre).join(' · ')}
              </p>
              <p className="mt-1 text-xs text-gray-500">
                Los comprobantes con huella fiscal repetida se avisan en el resultado de la pasada.
              </p>
            </div>
            <Button variant="secondary" onClick={() => irA('cajachica')}>Ver caja chica</Button>
          </div>
        </Card>
      )}

      {config && <PanelConfig config={config} />}
      {ocr && <PanelOcr ocr={ocr} />}

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
        <Button onClick={() => escanear(false)} disabled={escaneando || !companyId} title={companyId ? undefined : 'Elige una empresa para escanear'} className={preparando ? 'opacity-80 cursor-wait' : ''}>
          {preparando ? (
            <span className="flex items-center gap-2"><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" /> Iniciando…</span>
          ) : escaneando ? 'Escaneando...' : 'Escanear (solo inventariar)'}
        </Button>
        <Button
          onClick={() => escanear(true)}
          disabled={escaneando || !companyId}
          title={companyId ? undefined : 'Elige una empresa: sin ella no se puede atribuir ningún gasto'}
        >
          {preparando ? (
            <span className="flex items-center gap-2"><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" /> Iniciando…</span>
          ) : escaneando ? 'Escaneando...' : 'Escanear y crear tickets'}
        </Button>
      </div>

      {/* La carga por lote es la OTRA puerta de entrada: la de arriba lee una
          carpeta del SERVIDOR; esta manda archivos de ESTA maquina. Las dos
          pasan por el mismo `procesar_archivo`, asi que no hay dos lectores
          que puedan divergir. Va junto a los botones y no en Tickets porque
          ahi el archivo se sube uno a uno y se puede corregir antes de crear
          el ticket; aqui se digitaliza un monton de una vez. */}
      <CargaPorLote
        companyId={companyId}
        carpetaDefecto={config?.carpeta}
        rutasPermitidas={config?.rutas_permitidas}
        extensionesPermitidas={config?.extensiones_permitidas}
      />

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
      {corrida && <PanelProgreso corrida={corrida} totalPosible={stats?.total_archivos} />}

      {ultimo && <PanelResultado resultado={ultimo} />}

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

/** Carga de archivos de ESTA maquina, en una sola accion y con formatos mezclados.
 *
 *  La ruta de arriba lee una carpeta del servidor; esta manda los bytes desde
 *  el navegador. Las dos caen en `procesar_archivo`, asi que el PDF con capa
 *  de texto, el JPG con OCR y el HEIC con vision no pueden divergir: no hay
 *  un segundo lector aqui, solo una segunda puerta de entrada.
 *
 *  El `accept` del selector es una comodidad, no una decision: si el nombre
 *  miente (hay un `IMG_4253.HEIC` que por sus bytes es JPEG), el backend lee
 *  los bytes y el resultado sigue siendo el mismo. Por eso el texto de abajo
 *  lo dice, y por eso no se calcula `file_type` en el front.
 */
export function CargaPorLote({
  carpetaDefecto = '',
  rutasPermitidas = [],
  extensionesPermitidas = [],
}: {
  /** Se conserva como prop opcional solo para los verificadores que montan
   *  este componente sin la aplicacion. La peticion usa la empresa activa del
   *  contexto; un prop de una pantalla no puede decidirla. */
  companyId?: string;
  carpetaDefecto?: string;
  rutasPermitidas?: string[];
  extensionesPermitidas?: string[];
}) {
  const { empresaActiva } = useEmpresa();
  const companyId = empresaActiva?.company_id ?? '';
  const [archivos, setArchivos] = useState<File[]>([]);
  const [procesando, setProcesando] = useState(false);
  const [resultado, setResultado] = useState<ScanItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [override, setOverride] = useState(false);
  const [motivo, setMotivo] = useState('');
  const carpetaRef = useRef<HTMLInputElement>(null);

  // La ruta por omision NO se escribe a mano en el front: sale de `/scan/config`,
  // que es el mismo sitio del que sale la carpeta del escaner de arriba. Si la
  // configuracion cambia, el campo la sigue; si el operador ya escribio otra,
  // no se la pisamos por debajo.
  const [ruta, setRuta] = useState(carpetaDefecto);
  const [resultadoRuta, setResultadoRuta] = useState<DigitalizacionDirectorio | null>(null);
  const [procesandoRuta, setProcesandoRuta] = useState(false);
  const [errorRuta, setErrorRuta] = useState<string | null>(null);

  useEffect(() => {
    setRuta(anterior => anterior || carpetaDefecto);
  }, [carpetaDefecto]);

  useEffect(() => {
    // `webkitdirectory` no esta en los tipos de React. Se pone como atributo
    // crudo para que el navegador deje elegir una carpeta entera: es la unica
    // forma de mandar "la carpeta con jpg, pdf y lo demas" en un solo gesto.
    const entrada = carpetaRef.current;
    if (!entrada) return;
    entrada.setAttribute('webkitdirectory', '');
    entrada.setAttribute('directory', '');
  }, []);

  const tomar = (lista: FileList | null) => {
    if (!lista) return;
    setArchivos(Array.from(lista));
    setResultado(null);
    setError(null);
  };

  const enviar = async () => {
    if (archivos.length === 0) return;
    setProcesando(true);
    setError(null);
    try {
      setResultado(await ticketsApi.uploadBatch(archivos, companyId, {
        override,
        motivo: motivo.trim() || undefined,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo digitalizar el lote');
      setResultado(null);
    } finally {
      setProcesando(false);
    }
  };

  const enviarDirectorio = async () => {
    setProcesandoRuta(true);
    setErrorRuta(null);
    try {
      setResultadoRuta(await ticketsApi.digitalizarDirectorio(companyId, {
        carpeta: ruta.trim() || undefined,
        override,
        motivo: motivo.trim() || undefined,
      }));
    } catch (e) {
      setErrorRuta(e instanceof Error ? e.message : 'No se pudo digitalizar la ruta');
      setResultadoRuta(null);
    } finally {
      setProcesandoRuta(false);
    }
  };

  // El accept se arma con la lista del servidor para no ofrecer `.txt` (que el
  // backend rechaza) ni quedarse viejo cuando se agregue un formato nuevo. Es
  // comodidad, no decision: si el nombre miente, los bytes mandan.
  const accept = extensionesPermitidas.length > 0
    ? extensionesPermitidas.join(',')
    : '.pdf,.jpg,.jpeg,.png,.gif,.webp,.bmp,.tif,.tiff,.heic,.heif,.psd,.xml';

  return (
    <Card className="mt-4">
      <h3 className="font-semibold text-gray-900">Digitalizar desde esta máquina</h3>
      <p className="text-sm text-gray-600 mt-1">
        PDF, JPG, PNG, HEIC, TIFF o XML: <strong>mezcla los formatos en la misma carga</strong>.
        El tipo lo decide el backend por los bytes, no por el nombre ni por lo que
        diga el selector. Los archivos se leen con el mismo pipeline que el escáner
        de carpeta y se registran en `scan_files`, así que reenviar los mismos bytes
        no crea tickets duplicados.
      </p>

      <input
        id="carga-lote-archivos"
        type="file"
        multiple
        accept={accept}
        onChange={e => tomar(e.target.files)}
        className="sr-only"
      />
      <input
        ref={carpetaRef}
        id="carga-lote-carpeta"
        type="file"
        multiple
        onChange={e => tomar(e.target.files)}
        className="sr-only"
      />

      <div className="mt-3 flex flex-wrap gap-3">
        <Button
          variant="secondary"
          onClick={() => document.getElementById('carga-lote-archivos')?.click()}
          disabled={procesando}
        >
          Elegir archivos
        </Button>
        <Button
          variant="secondary"
          onClick={() => document.getElementById('carga-lote-carpeta')?.click()}
          disabled={procesando}
          title="Selecciona una carpeta completa; se mandan todos sus archivos"
        >
          Elegir carpeta
        </Button>
        <Button
          onClick={enviar}
          disabled={archivos.length === 0 || procesando || !companyId}
          title={!companyId ? 'Elige una empresa: sin ella no se puede atribuir ningún gasto' : undefined}
        >
          {procesando
            ? 'Digitalizando…'
            : `Digitalizar ${archivos.length} ${archivos.length === 1 ? 'archivo' : 'archivos'}`}
        </Button>
      </div>

      {archivos.length > 0 && !procesando && (
        <p className="text-xs text-gray-500 mt-2">
          {archivos.length} seleccionados: {archivos.slice(0, 5).map(a => a.name).join(', ')}
          {archivos.length > 5 ? ` … y ${archivos.length - 5} más` : ''}
        </p>
      )}

      {/* La otra forma de leer un directorio: escribir la ruta en el SERVIDOR.
          La de arriba elige carpetas de ESTA maquina; esta no puede leer el
          disco del navegador, asi que el backend valida que la ruta caiga
          dentro de su lista blanca. No es una sugerencia: `/etc` da 400. */}
      <div className="mt-4 pt-4 border-t border-gray-200">
        <h4 className="font-medium text-gray-900">Ruta del servidor</h4>
        <p className="text-xs text-gray-600 mt-1">
          Vacía usa la carpeta configurada. Para otra, elige o escribe una ruta
          dentro de las permitidas por el servidor. Se leen imágenes y PDF (y
          XML), y se informa por nombre qué archivos se quedaron fuera.
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            id="carga-lote-ruta"
            type="text"
            list="carga-lote-rutas"
            value={ruta}
            onChange={e => setRuta(e.target.value)}
            placeholder={carpetaDefecto || '/ruta/permitida'}
            className="flex-1 min-w-64 border border-gray-300 rounded-md px-3 py-2 text-sm font-mono"
            disabled={procesandoRuta}
          />
          <datalist id="carga-lote-rutas">
            {rutasPermitidas.map(r => <option key={r} value={r} />)}
          </datalist>
          <Button
            onClick={enviarDirectorio}
            disabled={procesandoRuta || !companyId}
            title={!companyId ? 'Elige una empresa antes de digitalizar' : undefined}
          >
            {procesandoRuta ? 'Digitalizando ruta…' : 'Digitalizar esta ruta'}
          </Button>
        </div>
        {rutasPermitidas.length > 0 && (
          <p className="text-xs text-gray-500 mt-2">
            Rutas permitidas:{' '}
            {rutasPermitidas.map((r, i) => (
              <span key={r}>
                {i > 0 && ' · '}
                <button
                  type="button"
                  className="font-mono underline decoration-dotted"
                  onClick={() => setRuta(r)}
                  title="Usar esta ruta"
                >
                  {r}
                </button>
              </span>
            ))}
          </p>
        )}
        {errorRuta && (
          <div className="mt-2 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm">
            {errorRuta}
          </div>
        )}
        {resultadoRuta && <PanelResultadoDirectorio resultado={resultadoRuta} />}
      </div>

      <label className="mt-3 flex items-start gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          checked={override}
          onChange={e => setOverride(e.target.checked)}
          disabled={procesando}
          className="mt-0.5 w-4 h-4 text-blue-600 rounded"
        />
        <span>
          Autorizar la lectura de periodos cerrados. Quien firma sale de tu sesión,
          no de este formulario; el motivo queda en el ticket.
        </span>
      </label>
      {override && (
        <div className="mt-2">
          <label htmlFor="carga-lote-motivo" className="block text-xs text-gray-600 mb-1">
            Motivo (obligatorio para que la autorización sea trazable)
          </label>
          <input
            id="carga-lote-motivo"
            type="text"
            value={motivo}
            onChange={e => setMotivo(e.target.value)}
            placeholder="Factura tardía del mes cerrado, revisada por la contadora"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </div>
      )}

      {error && (
        <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm">
          {error}
        </div>
      )}

      {resultado && <PanelResultadoLote resultado={resultado} archivos={archivos} />}
    </Card>
  );
}

/** El resultado se empareja con el archivo LOCAL por posicion.
 *
 *  El backend devuelve los items en el mismo orden en que los mando el router,
 *  y el router los procesa en secuencia. `relative_path` es el sha256 por
 *  diseno (es la clave de dedup), asi que no sirve para decirle al operador
 *  cual de sus archivos fallo: el nombre que elijo aqui viene del `File`. */
export function PanelResultadoLote({
  resultado,
  archivos,
  nombres,
  formatos,
}: {
  resultado: ScanItem[];
  archivos: File[];
  /** Nombres del directorio, en el mismo orden que `resultado`. */
  nombres?: string[];
  /** Tipos detectados por los bytes, en el mismo orden. Distinto de `origen`. */
  formatos?: string[];
}) {
  if (resultado.length === 0) return null;

  const creados = resultado.filter(r => r.accion === 'CREADO').length;
  const periodoCerrado = resultado.filter(r => r.accion === 'PERIODO_CERRADO').length;

  return (
    <div className="mt-4">
      <div className="flex flex-wrap gap-2 text-sm mb-2">
        <Badge variant="info">{resultado.length} archivos</Badge>
        <Badge variant="success">{creados} creados</Badge>
        {periodoCerrado > 0 && (
          <Badge variant="warning">{periodoCerrado} en periodo cerrado</Badge>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="text-xs uppercase text-gray-500 border-b">
            <tr>
              <th className="py-2 pr-3 text-left">Archivo</th>
              <th className="py-2 pr-3 text-left">Qué pasó</th>
              <th className="py-2 pr-3 text-left">Estado</th>
              <th className="py-2 pr-3 text-left">Bytes y lector</th>
              <th className="py-2 pr-3 text-left">Datos leídos y veredicto</th>
            </tr>
          </thead>
          <tbody>
            {resultado.map((item, indice) => (
              <tr key={item.scan_file_id ?? `${item.relative_path}-${indice}`} className="border-b last:border-0 align-top">
                <td className="py-2 pr-3 font-mono text-xs break-all">
                  {nombres?.[indice] ?? archivos[indice]?.name ?? item.relative_path}
                </td>
                <td className="py-2 pr-3">{accionLabel(item.accion)}</td>
                <td className="py-2 pr-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${COLOR_ESTADO[item.status] ?? ''}`}>
                    {SCAN_STATUS_META[item.status]?.label ?? item.status}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  {formatos?.[indice] ? (
                    <span className="text-xs font-mono">
                      bytes={formatos[indice]} · lector={motorLabel(item.origen)}
                    </span>
                  ) : (
                    motorLabel(item.origen)
                  )}
                </td>
                <td className="py-2 pr-3">
                  {item.datos ? (
                    <span className="text-xs">
                      {item.datos.provider_name ?? 'sin proveedor'} ·{' '}
                      {item.datos.total_amount ? `$${item.datos.total_amount}` : 'sin total'} ·{' '}
                      <span className="font-medium">
                        veredicto: {item.datos.extraction_status ?? 'sin decidir'}
                      </span>
                      {item.datos.validation_errors && (
                        <span className="block text-amber-700">
                          {item.datos.validation_errors}
                        </span>
                      )}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">
                      {item.detalle ?? 'sin ticket'}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Resultado de una ruta del servidor: primero lo que NO se leyó.
 *
 *  Los rechazados van arriba y con su nombre. Es la unica parte de la respuesta
 *  que el operador tiene que ARREGLAR en su carpeta: un PDF y un JPG ya se
 *  leyeron, pero un `.docx` sigue ahi esperando a que alguien decida que hacer.
 *  Si se pintara abajo, despues de una tabla de veinte filas, nadie lo veria.
 */
export function PanelResultadoDirectorio({ resultado }: { resultado: DigitalizacionDirectorio }) {
  const rechazados = resultado.archivos_no_permitidos;

  return (
    <div className="mt-4">
      <div className="flex flex-wrap gap-2 text-sm mb-2">
        <Badge variant="info">{resultado.archivos.length} leídos</Badge>
        {rechazados.length > 0 && (
          <Badge variant="warning">{rechazados.length} no permitidos</Badge>
        )}
        {resultado.archivos_omitidos_por_tope > 0 && (
          <Badge variant="warning">
            {resultado.archivos_omitidos_por_tope} por tope de corrida
          </Badge>
        )}
      </div>

      <p className="text-xs text-gray-500 mb-2 font-mono break-all">
        {resultado.carpeta}
      </p>

      {rechazados.length > 0 && (
        <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
          <p className="text-sm font-medium text-amber-900 mb-2">
            Estos archivos NO se digitalizaron:
          </p>
          <ul className="list-disc pl-5 text-sm text-amber-900">
            {rechazados.map((archivo: ArchivoNoPermitido, indice: number) => (
              <li key={`${archivo.nombre}-${indice}`}>
                <span className="font-mono break-all">{archivo.nombre}</span>
                <span className="text-amber-700"> — {archivo.motivo}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <PanelResultadoLote
        resultado={resultado.archivos}
        archivos={[]}
        nombres={resultado.nombres}
        formatos={resultado.formatos}
      />
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

function PanelResultado({ resultado }: { resultado: ScanResultado }) {
  const { nuevos, actualizados, sin_cambios, duplicados, con_error, no_soportados,
    omitidos_por_tope, archivos_vistos, ilegibles = [] } = resultado;

  return (
    <Card className="mt-4">
      <h3 className="font-semibold text-gray-900 mb-2">Última pasada</h3>
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
      {omitidos_por_tope > 0 && (
        <p className="text-sm text-gray-600 mt-2">
          Se alcanzó el tope de la pasada: <strong>{omitidos_por_tope}</strong> archivos quedan
          para la siguiente. Vuelve a escanear para seguir.
        </p>
      )}
      {resultado.detalles.length > 0 && (
        <ul className="mt-3 text-sm text-gray-700 space-y-1 max-h-48 overflow-y-auto">
          {resultado.detalles.map((d: ScanItem) => (
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
                {d.datos?.advertencia_duplicado_fiscal && (
                  <span className="ml-1 text-xs text-amber-700" role="status">
                    · huella fiscal repetida
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
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
        El original, al lado de los datos leidos, haya ticket o no. Con
        ticket se muestra el documento guardado (`VerDocumento`); sin ticket,
        el archivo escaneado de la carpeta (`VerArchivoEscaneado`), que es el
        mismo papel. Antes, un archivo "omitido" no mostraba nada y el aviso
        mandaba a la pantalla de Tickets, donde no hay ticket que ver.
      */}
      {ticket ? (
        <VerDocumento ticket={ticket} abiertoPorDefecto />
      ) : (
        <VerArchivoEscaneado fileId={detalle.id} nombre={detalle.relative_path} />
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
