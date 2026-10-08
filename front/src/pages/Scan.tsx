import { useCallback, useEffect, useRef, useState } from 'react';
import { companiesApi, ticketsApi, type Company, type Ticket } from '../services/api';
import type {
  ScanConfig, ScanCorrida, ScanEvent, ScanFile, ScanFileDetail, ScanItem, ScanOcrEstado,
  ScanResultado, ScanStats, ScanStatus,
} from '../types/scan';
import { Button, Card, Badge, Loading, EmptyState, Select, Modal } from '../components/ui';
import { SCAN_STATUS_META, motorLabel, accionLabel, COLOR_ESTADO } from '../utils/scan';
import { VerDocumento } from '../components/TicketDocumento';
import { PanelProgreso } from '../components/ScanProgreso';

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
      const [c, o, s, f] = await Promise.allSettled([
        ticketsApi.scanConfig(),
        ticketsApi.scanOcr(),
        ticketsApi.scanStats(),
        ticketsApi.scanFiles({
          estado: filtro || undefined,
          companyId: companyId || undefined,
          limit: POR_PAGINA,
          offset: pagina * POR_PAGINA,
        }),
      ]);
      setConfig(c.status === 'fulfilled' ? c.value : null);
      setOcr(o.status === 'fulfilled' ? o.value : null);
      setStats(s.status === 'fulfilled' ? s.value : null);
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
  useEffect(() => { companiesApi.list().then(setCompanies).catch(() => setCompanies([])); }, []);

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

  const escanear = async (conEmpresa: boolean) => {
    setEscaneando(true);
    setError(null);
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

      const r = await ticketsApi.escanear(conEmpresa && companyId ? { company_id: companyId } : {});
      setUltimo(r);
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

      <div className="mt-6 flex flex-wrap gap-3">
        {/*
          Dos botones y no uno con una casilla. "Escanear" sin empresa inventaria
          y es seguro: nadie puede crear un gasto por accidente. "Escanear y
          crear tickets" SI crea gastos, y por eso exige que haya una empresa
          elegida y se apaga sin ella. Un solo boton con un parametro opcional
          deja la decision de crear tickets escondida en un estado que nadie ve.
        */}
        <Button onClick={() => escanear(false)} disabled={escaneando}>
          {escaneando ? 'Escaneando...' : 'Escanear (solo inventariar)'}
        </Button>
        <Button
          onClick={() => escanear(true)}
          disabled={escaneando || !companyId}
          title={companyId ? undefined : 'Elige una empresa: sin ella no se puede atribuir ningún gasto'}
        >
          Escanear y crear tickets
        </Button>
      </div>

      {corrida && <PanelProgreso corrida={corrida} />}

      {ultimo && <PanelResultado resultado={ultimo} />}

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

function PanelResultado({ resultado }: { resultado: ScanResultado }) {
  const { nuevos, actualizados, sin_cambios, duplicados, con_error, no_soportados,
    omitidos_por_tope, archivos_vistos } = resultado;

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
      </div>
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
                {d.confianza !== null && ` · ${d.confianza.toFixed(2)}`}
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
              <td className="px-4 py-2">
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${COLOR_ESTADO[f.status]}`}>
                  {SCAN_STATUS_META[f.status]?.label ?? f.status}
                </span>
              </td>
              <td className="px-4 py-2 text-gray-600">{motorLabel(f.read_by)}</td>
              <td className="px-4 py-2 text-gray-600">{f.attempts}</td>
              <td className="px-4 py-2 text-gray-600">
                {f.ticket_id ? 'ver' : <span className="text-gray-400">—</span>}
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

      {detalle.declared_extension && detalle.detected_format
        && !detalle.declared_extension.includes(detalle.detected_format) && (
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
              <li key={e.id} className="flex items-baseline gap-2">
                <span className="text-xs text-gray-400 whitespace-nowrap">
                  {new Date(e.created_at).toLocaleString()}
                </span>
                <span className="font-medium">{accionLabel(e.action)}</span>
                {e.actor && <span className="text-gray-500 text-xs">por {e.actor}</span>}
                {e.detail && <span className="text-gray-500 text-xs">— {e.detail}</span>}
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
