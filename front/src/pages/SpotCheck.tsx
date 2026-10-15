import { useState, useEffect, useCallback } from 'react';
import {
  ticketsApi, companiesApi, type Company, type SpotCheckItem, type SpotCheckField,
  type ReporteExactitud, type ExactitudPorOrigen,
} from '../services/api';
import { Button, Card, Badge, Loading, EmptyState, Select, Input } from '../components/ui';
import { confidenceLabel, sourceLabel, nombreProvisorLegible } from '../utils/extraction';
import { VerDocumento } from '../components/TicketDocumento';
import {
  veredictoMeta, accionSegunVeredicto, exactitudConIntervalo, porcentaje,
  etiquetaCampo, CAMPOS_MUESTRABLES, SPOT_CHECK_META, VEREDICTO_META,
} from '../utils/spotcheck';

/**
 * Muestreo de exactitud: la pantalla que permite AFIRMAR el objetivo, y no
 * solo decirlo.
 *
 * Por que existe. El sistema promete ~96% de exactitud. Un objetivo que no se
 * mide es una frase de marketing. Con esta pantalla, alguien abre el 5% de lo
 * que el gate aprobo solo, lo contrasta contra el papel, y de ahi sale un
 * numero con la evidencia que lo sostiene.
 *
 * La regla de la pantalla, y es la que la hace util: NO se puede marcar
 * "correcto" sin ver lo que el sistema leyo. Por eso cada tarjeta muestra los
 * valores leidos y el texto crudo del documento. Un veredicto dado a ciegas
 * mide la confianza del revisor, no la exactitud del extractor, que es
 * exactamente lo contrario de lo que se quiere medir.
 *
 * La segunda regla: esta pantalla NO corrige el ticket. El muestreo produce
 * evidencia, no reparaciones. Si una muestra mal hecha pudiera cambiar un
 * total, la exactitud dependeria de quien reviso y dejaria de medir el
 * automatismo. Por eso el unico boton de "arreglar" no existe aqui, y por eso
 * se dice en la pantalla, para que nadie lo busque.
 */
export function SpotCheck() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<string>('');
  const [cola, setCola] = useState<Awaited<ReturnType<typeof ticketsApi.spotCheckQueue>> | null>(null);
  const [reporte, setReporte] = useState<ReporteExactitud | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorReporte, setErrorReporte] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // Cola y reporte van en paralelo y por separado a proposito. Si el
      // reporte falla y la cola no, mostrar "no hay evidencia" junto a una cola
      // llena de pendientes parece un bug de la base; y al reves, un badge en
      // cero con 200 tickets esperando parece que no hay nada que revisar. Son
      // dos hechos y se reportan por separado.
      const [c, r] = await Promise.allSettled([
        ticketsApi.spotCheckQueue(companyId || undefined),
        ticketsApi.reporteExactitud(companyId || undefined),
      ]);
      if (c.status === 'fulfilled') setCola(c.value);
      else setCola(null);
      setError(c.status === 'rejected'
        ? (c.reason instanceof Error ? c.reason.message : 'No se pudo cargar la muestra')
        : null);

      if (r.status === 'fulfilled') {
        setReporte(r.value);
        setErrorReporte(null);
      } else {
        setReporte(null);
        setErrorReporte(r.reason instanceof Error
          ? r.reason.message
          : 'No se pudo cargar el reporte');
      }
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => { cargar(); }, [cargar]);

  useEffect(() => {
    companiesApi.list().then(setCompanies).catch(() => setCompanies([]));
  }, []);

  return (
    <div className="p-4 lg:p-8 max-w-7xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Muestreo de exactitud</h1>
          <p className="text-sm text-gray-500 mt-1">
            El 5% de lo que el sistema aprobó solo, para contrastarlo contra el papel.
            Revisar aquí no corrige el ticket: solo deja constancia de si la lectura fue correcta.
          </p>
        </div>
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
      </div>

      {errorReporte && (
        <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-yellow-900 text-sm flex items-start gap-2">
          <span className="font-medium">No se pudo calcular la exactitud:</span>
          <span className="flex-1">{errorReporte}</span>
          <Button variant="secondary" onClick={cargar} className="!py-1 !px-2 !text-xs">Reintentar</Button>
        </div>
      )}

      {reporte && <PanelEvidencia reporte={reporte} />}

      <div className="mt-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">Muestra por revisar</h2>
        <p className="text-sm text-gray-500 mb-3">
          Contrasta cada fila contra el documento original. Abre “lo que leyó el sistema”
          antes de decidir: sin ver la lectura no hay veredicto, solo una suposición.
        </p>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm flex items-start gap-2">
            <span className="font-medium">No se pudo cargar la muestra:</span>
            <span className="flex-1">{error}</span>
            <Button variant="secondary" onClick={cargar} className="!py-1 !px-2 !text-xs">Reintentar</Button>
          </div>
        )}

        {cola && (
          <ResumenMuestra
            pendientes={cola.total_pendientes}
            revisados={cola.total_revisados}
            aciertos={cola.aciertos}
            incorrectos={cola.incorrectos}
            antiguedad={cola.antiguedad_promedio_dias}
            listados={cola.tickets.length}
          />
        )}

        {loading ? (
          <Loading message="Cargando muestra..." />
        ) : !cola || cola.tickets.length === 0 ? (
          <Card>
            <EmptyState
              message={error
                ? 'No se pudo leer la muestra. Revisa el mensaje arriba.'
                : cola && cola.total_pendientes > 0
                  ? `Hay ${cola.total_pendientes} pendientes pero ninguno cabe en esta página. `
                    + 'No se está mostrando nada, y eso no significa que no haya trabajo.'
                  : cola && cola.total_revisados > 0
                    ? 'La muestra está al día. Se elige automáticamente un 5% de cada ticket nuevo.'
                    : 'Todavía no hay nada que revisar. Se marca un ticket cuando el sistema lo aprueba solo.'}
            />
          </Card>
        ) : (
          <div className="space-y-3">
            {cola.tickets.map(item => (
              <TarjetaMuestra key={item.ticket.id} item={item} onRefresh={cargar} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// La evidencia
// ---------------------------------------------------------------------------

/** El encabezado dice si se puede afirmar el objetivo, y con que margen.
 *
 *  El orden importa. Primero el veredicto, despues el numero, y despues el
 *  intervalo. Al reves, el ojo se queda en el 96% y no lee el 92% que esta
 *  debajo, y el 92% es la parte que decide si se puede afirmar algo. */
export function PanelEvidencia({ reporte }: { reporte: ReporteExactitud }) {
  const meta = veredictoMeta(reporte.veredicto_global);
  const [detalle, setDetalle] = useState(false);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span title={meta.help}><Badge variant={meta.variant}>{meta.label}</Badge></span>
            <span className="text-xs text-gray-500">
              Objetivo: {porcentaje(reporte.objetivo, 0)}
              {' · '}confianza al {porcentaje(reporte.nivel_confianza, 0)}
            </span>
          </div>
          <p className="mt-2 text-sm text-gray-700 max-w-3xl">{reporte.explicacion}</p>
          <p className="mt-1 text-xs text-gray-500">{accionSegunVeredicto(reporte.veredicto_global)}</p>
        </div>
      </div>

      <div className="mt-4">
        <button
          onClick={() => setDetalle(d => !d)}
          // Mismo criterio que el boton de "Abrir cola": el texto es pequeno
          // pero el area tiene que ser de 44. Medido en 20px sin esto.
          className="text-sm text-blue-600 hover:text-blue-800 font-medium py-3 -my-3"
        >
          {detalle ? 'Ocultar el desglose por vía de lectura' : 'Ver el desglose por vía de lectura'}
        </button>
      </div>

      {detalle && (
        <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3">
          {reporte.por_origen.map(o => (
            <TarjetaOrigen key={o.origen} origen={o} />
          ))}
        </div>
      )}
    </Card>
  );
}

/**
 * Una via de lectura, con su veredicto y lo que falta para poder afirmar algo.
 *
 * Cada via se reporta SEPARADA y nunca se promedian. Un sistema con PDF al
 * 100% y vision al 90% no cumple el objetivo: promediar las dos daria 95% y
 * esconderia justo la via que hay que arreglar, que es la que manda.
 */
export function TarjetaOrigen({ origen }: { origen: ExactitudPorOrigen }) {
  const meta = veredictoMeta(origen.veredicto);
  const exacto = exactitudConIntervalo(
    origen.exactitud, origen.intervalo_inferior, origen.intervalo_superior,
  );
  // Un veredicto desconocido se avisa VISIBLE, no en el title del badge. La
  // regla de "no se esconde" se cumple de verdad cuando el aviso no depende
  // de que el mouse pase por encima: en un escritorio, nadie va a pasar el
  // mouse por un badge que no reconoce, y ese es justamente el caso que
  // necesita la advertencia.
  const desconocido = !Object.values(VEREDICTO_META).some(m => m.label === meta.label);

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold text-gray-900">{origen.origen}</p>
        <span title={meta.help}><Badge variant={meta.variant}>{meta.label}</Badge></span>
      </div>

      {desconocido && (
        <p className="mt-2 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-900">
          {meta.help}
        </p>
      )}

      {/* `null` se dice en palabras. Un 0% aqui se leeria como "fallo todo",
          que es lo contrario de "nadie miró". */}
      <p className="mt-2 text-lg font-semibold text-gray-900">
        {exacto ?? 'Sin medir'}
      </p>
      <p className="text-xs text-gray-500">
        {origen.revisados} revisados · {origen.aciertos} correctos · {origen.incorrectos} no
        {origen.pendientes > 0 && ` · ${origen.pendientes} sin revisar`}
      </p>

      <p className="mt-2 text-xs text-gray-600">{origen.motivo_faltante}</p>

      {origen.campo_mas_fallido && (
        <p className="mt-1 text-xs text-red-700">
          Campo que más falla: <strong>{etiquetaCampo(origen.campo_mas_fallido)}</strong>
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// La cola
// ---------------------------------------------------------------------------

/**
 * Conteos de la muestra, con la distinction que los hace utiles.
 *
 * `listados` esta aparte de `pendientes` a proposito. La lista se limita a 50
 * por peticion, y si solo se dijera "50 pendientes" con 200 reales, el que lo
 * ve pensaria que quedan 50. Los conteos vienen SIN recortar y la lista se
 * recortan; que se note la diferencia es parte del contrato.
 */
export function ResumenMuestra({
  pendientes, revisados, aciertos, incorrectos, antiguedad, listados,
}: {
  pendientes: number; revisados: number; aciertos: number; incorrectos: number;
  antiguedad: number | null; listados: number;
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
      <Card>
        <p className="text-xs text-gray-500 uppercase tracking-wider">Sin revisar</p>
        <p className={`text-3xl font-bold mt-1 ${pendientes > 0 ? 'text-blue-600' : 'text-gray-400'}`}>
          {pendientes}
        </p>
        {antiguedad !== null && pendientes > 0 && (
          <p className="text-xs text-gray-500 mt-1">
            {antiguedad === 0 ? 'Entraron hoy' : `Esperan hace ${antiguedad} días en promedio`}
          </p>
        )}
        {pendientes > listados && (
          <p className="text-xs text-amber-700 mt-1">
            Se muestran {listados} de {pendientes}
          </p>
        )}
      </Card>

      <Card>
        <p className="text-xs text-gray-500 uppercase tracking-wider">Ya revisados</p>
        <p className="text-3xl font-bold mt-1 text-gray-900">{revisados}</p>
        <p className="text-xs text-gray-500 mt-1">Quedan registrados para siempre</p>
      </Card>

      <Card>
        <p className="text-xs text-gray-500 uppercase tracking-wider">Coincidieron</p>
        <p className="text-3xl font-bold mt-1 text-green-600">{aciertos}</p>
      </Card>

      <Card>
        <p className="text-xs text-gray-500 uppercase tracking-wider">No coincidieron</p>
        <p className={`text-3xl font-bold mt-1 ${incorrectos > 0 ? 'text-red-600' : 'text-gray-400'}`}>
          {incorrectos}
        </p>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Una tarjeta de la muestra
// ---------------------------------------------------------------------------

export function TarjetaMuestra({
  item, onRefresh,
}: {
  item: SpotCheckItem;
  onRefresh: () => void | Promise<void>;
}) {
  const t = item.ticket;
  const [viendoTexto, setViendoTexto] = useState(false);
  const [marcandoIncorrecto, setMarcandoIncorrecto] = useState(false);
  const [campos, setCampos] = useState<SpotCheckField[]>([]);
  const [nota, setNota] = useState('');
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const conf = confidenceLabel(t.confidence, t.confidence_source);
  const meta = SPOT_CHECK_META[item.spot_check_status] ?? {
    label: `No reconocido: ${item.spot_check_status}`, variant: 'warning' as const,
  };

  const enviar = async (correct: boolean) => {
    setOcupado(true);
    setError(null);
    try {
      await ticketsApi.registrarVeredicto(t.id, {
        correct,
        campos_incorrectos: correct ? [] : campos,
        ...(nota.trim() ? { notes: nota.trim() } : {}),
      });
      await onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo registrar el veredicto');
    } finally {
      setOcupado(false);
      setMarcandoIncorrecto(false);
      setCampos([]);
      setNota('');
    }
  };

  const alternarCampo = (c: SpotCheckField) =>
    setCampos(v => v.includes(c) ? v.filter(x => x !== c) : [...v, c]);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={meta.variant}>{meta.label}</Badge>
            <span title={conf.help} className="text-xs text-gray-500">
              Confianza: <span className="font-medium text-gray-700">{conf.texto}</span>
            </span>
            <span className="text-xs text-gray-500">
              Leído por: <span className="font-medium text-gray-700">{t.confidence_source ?? 'sin origen'}</span>
            </span>
            <span className="text-xs text-gray-500">
              Origen: <span className="font-medium text-gray-700">{sourceLabel(t.source_type)}</span>
            </span>
            {t.source_file && (
              <span className="text-xs text-gray-400 font-mono truncate" title={t.source_file}>
                {t.source_file}
              </span>
            )}
          </div>

          <p className="mt-2 text-lg font-semibold text-gray-900 truncate">
            {nombreProvisorLegible(t.provider_name)
              ?? <span className="text-red-600">Sin proveedor identificado</span>}
          </p>

          <TablaLoLeido ticket={t} />

          {/* El papel va antes de "lo que leyó el sistema". La instruccion de
              esta pantalla es "contrasta cada fila contra el documento
              original", y el orden importa: leer primero lo que dijo el sistema
              y despues el papel hace que se verifique lo que ya se leyo, que es
              confirmar la propia lectura y no contrastarla. El `raw_text` y el
              archivo son dos vistas del mismo documento y se necesitan las
              dos: el texto es lo que el sistema extrajo, el archivo es de donde
              salio. */}
          <VerDocumento ticket={t} />

          {t.raw_text && (
            <div className="mt-2">
              <button
                onClick={() => setViendoTexto(v => !v)}
                className="text-xs text-blue-600 hover:text-blue-800 font-medium"
              >
                {viendoTexto ? 'Ocultar lo que leyó el sistema' : 'Ver lo que leyó el sistema'}
              </button>
              {viendoTexto && (
                <pre className="mt-1 p-2 bg-gray-50 border border-gray-200 rounded text-xs text-gray-700 whitespace-pre-wrap max-h-64 overflow-y-auto font-mono">
                  {t.raw_text}
                </pre>
              )}
            </div>
          )}

          {/* Aqui se dice, y no solo en el encabezado de la pagina, porque esta
              tarjeta se parece mucho a una de la cola de revision y el gesto es
              el mismo: "corregir". Quien llega aqui con esa costumbre busca el
              boton de arreglar, y no hay. Sin esta linea, la ausencia se lee
              como que se le olvido, no como que es a proposito. */}
          <p className="mt-2 text-xs text-gray-500 italic">
            Registrar aquí no cambia el ticket. Si algo se leyó mal, se corrige en la cola de revisión.
          </p>

          {/* Solo cuando NO hay documento. Con el archivo disponible, decir
              "sin él no hay contra qué contrastar" seria una alarma falsa: si
              esta arriba, si se puede contrastar, y la condicion real es que
              exista `raw_text` Y no exista el archivo. */}
          {!t.raw_text && !t.tiene_documento && (
            <p className="mt-2 text-xs text-amber-700">
              Este comprobante no guardó texto ni el archivo original. Sin ninguno de los dos no
              hay contra qué contrastar la lectura: un veredicto aquí no mide la exactitud.
            </p>
          )}

          {item.spot_check_status !== 'PENDIENTE' && (
            <p className="mt-2 text-xs text-gray-600">
              Revisado el {item.spot_checked_at ? new Date(item.spot_checked_at).toLocaleString('es-MX') : 'sin fecha'}
              {item.spot_check_wrong_fields.length > 0 && (
                <> · Fallaron: {item.spot_check_wrong_fields.map(etiquetaCampo).join(', ')}</>
              )}
              {item.spot_check_notes && <> · “{item.spot_check_notes}”</>}
            </p>
          )}
        </div>

        {item.spot_check_status === 'PENDIENTE' && !marcandoIncorrecto && (
          <div className="flex gap-2 shrink-0">
            <Button
              variant="secondary"
              onClick={() => setMarcandoIncorrecto(true)}
              className="!text-red-700"
              disabled={ocupado}
            >
              No coincidió
            </Button>
            <Button onClick={() => enviar(true)} disabled={ocupado} className="!bg-green-600 hover:!bg-green-700">
              {ocupado ? 'Guardando...' : 'Sí coincidió'}
            </Button>
          </div>
        )}
      </div>

      {marcandoIncorrecto && (
        <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg space-y-3">
          <p className="text-sm text-red-900">
            ¿Qué se leyó mal? Esto no corrige el ticket: solo dice qué campo falló, que es lo que
            el reporte usa para decir cuál arreglar primero.
          </p>
          <div className="flex flex-wrap gap-2">
            {CAMPOS_MUESTRABLES.map(({ campo, label }) => {
              const activo = campos.includes(campo);
              return (
                <button
                  key={campo}
                  onClick={() => alternarCampo(campo)}
                  aria-pressed={activo}
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
                    activo
                      ? 'bg-red-600 text-white border-red-600'
                      : 'bg-white text-red-800 border-red-300 hover:bg-red-100'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <Input
            label="Nota (opcional)"
            value={nota}
            onChange={(e) => setNota(e.target.value)}
            placeholder="Foto cortada, el IVA no se ve, el RFC está borroso..."
          />
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={() => setMarcandoIncorrecto(false)} disabled={ocupado}>
              Cancelar
            </Button>
            <Button
              onClick={() => enviar(false)}
              disabled={ocupado}
              className="!bg-red-600 hover:!bg-red-700"
            >
              {ocupado ? 'Guardando...' : 'Registrar que no coincidió'}
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-3 p-2 bg-red-50 border border-red-200 rounded text-sm text-red-800">
          {error}
        </p>
      )}
    </Card>
  );
}

/** Los campos leidos, uno por renglon, con el subtotal incluido.
 *
 *  Se muestran aunque valgan `null`. Un campo ausente tiene que verse como
 *  ausente: si la fila se calla, no hay forma de saber si el sistema no lo leyo
 *  o si la pantalla lo esta escondiendo. Y el subtotal esta porque sin el la
 *  pregunta "¿se leyo bien?" no tiene con que responderse. */
export function TablaLoLeido({ ticket }: { ticket: SpotCheckItem['ticket'] }) {
  const filas: Array<[string, string | null]> = [
    ['RFC', ticket.provider_tax_id],
    ['Fecha', ticket.expense_date],
    ['Subtotal', ticket.subtotal],
    ['IVA', ticket.tax_amount],
    ['Total', ticket.total_amount],
  ];
  return (
    <div className="mt-2 grid grid-cols-2 sm:grid-cols-5 gap-2">
      {filas.map(([etiqueta, valor]) => (
        <div key={etiqueta} className="px-2 py-1 bg-gray-50 rounded border border-gray-200">
          <p className="text-[10px] text-gray-500 uppercase tracking-wide">{etiqueta}</p>
          <p className={`text-sm font-semibold ${valor === null ? 'text-gray-400 italic' : 'text-gray-900'}`}>
            {valor ?? 'no se leyó'}
          </p>
        </div>
      ))}
    </div>
  );
}
