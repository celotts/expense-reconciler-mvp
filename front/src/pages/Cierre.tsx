import { useState, useEffect, useCallback } from 'react';
import {
  companiesApi, reportsApi,
  type Company, type ReporteCierreMensual,
} from '../services/api';
import { Button, Card, Badge, Loading, EmptyState, Select } from '../components/ui';
import { dinero, numero, porcentaje } from '../utils/format';
import { useNavegacion } from '../contextos/Navegacion';
import {
  periodoActual, periodosDisponibles, nombrePeriodo, lineaEstado, lineaAdvertencia,
  lineaComparativo, lineaExactitud, nombreArchivo, descargarBlob,
  type LineaEstado, type TonoEstado,
} from '../utils/cierre';

/**
 * El informe de cierre mensual, en HTML.
 *
 * ## LA RESTRICCION DE ESTA PANTALLA, Y ES LA IMPORTANTE
 *
 * **El preview y el PDF salen del MISMO objeto.** El backend construye un
 * `ReporteCierreMensual` y de ELO salen el JSON que se ve aqui y el PDF que se
 * descarga. No se recalcula nada al maquetar, ni aqui ni alla (contrato §5.2).
 *
 * Por eso esta pantalla no suma importes, no calcula porcentajes y no decide
 * veredictos: todo lo que se ve sale del objeto, formateado. Si esta pantalla
 * tuviera su propia logica de presentacion de la exactitud, el contador veria
 * dos numeros distintos en la misma pantalla —uno del PDF y otro de aqui— y
 * la promesa entera del producto se caeria. Es el unico punto donde el front
 * puede romper lo que el backend garantiza, y por eso la aritmetica vive en
 * `utils/cierre.ts` reusando `spotcheck.ts`, no aqui.
 *
 * ## LAS SIETE SECCIONES, EN ORDEN
 *
 * El orden es el del contrato y no estetico: se lee de arriba abajo como lo
 * leeria un contador, y por eso el bloque 5 (exactitud) queda en el medio y no
 * al final, donde nadie llegaria.
 */

const TONOS_CARD: Record<TonoEstado, string> = {
  verde: 'bg-green-50 border-green-200 text-green-800',
  ambar: 'bg-amber-50 border-amber-200 text-amber-800',
  rojo: 'bg-red-50 border-red-200 text-red-800',
  neutro: 'bg-gray-50 border-gray-200 text-gray-700',
};

/** Un bloque con borde de tono. Es lo unico que se usa para los tres tipos de
 *  linea, y por eso los tres se leen igual. */
function Bloque({ linea }: { linea: LineaEstado }) {
  return (
    <div className={`rounded-lg border p-4 ${TONOS_CARD[linea.tono]}`}>
      <p className="font-semibold">{linea.titulo}</p>
      <p className="mt-1 text-sm whitespace-pre-line">{linea.detalle}</p>
    </div>
  );
}

function Seccion({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <Card>
      <h2 className="text-lg font-semibold text-gray-900 mb-4">{titulo}</h2>
      {children}
    </Card>
  );
}

/** Una fila etiqueta / valor del informe. */
function Fila({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-gray-100 last:border-0">
      <span className="text-sm text-gray-600">{k}</span>
      <span className="text-sm font-medium text-gray-900 text-right">{v}</span>
    </div>
  );
}

/** `informeInicial` existe SOLO para el verificador de pantalla.
 *
 *  La pantalla normal pide el informe al backend por su cuenta. Este parametro
 *  permite montarla con el JSON real de la API sin levantar ni un servidor, que
 *  es lo que hace falta para mirar el TEXTO que sale y no solo que compile.
 *
 *  Con `null` (que es como la usa la aplicacion) el comportamiento es el de
 *  siempre: pedir el informe. */
export function Cierre({ informeInicial }: { informeInicial?: ReporteCierreMensual | null } = {}) {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<string>('');
  const [periodo, setPeriodo] = useState<string>(periodoActual());
  const [reporte, setReporte] = useState<ReporteCierreMensual | null>(informeInicial ?? null);
  // `loading` arranca en `false` cuando el informe YA viene dado. Con `true`, el
  // verificador de pantalla — que monta el componente con `renderToStaticMarkup`
  // y por lo tanto NO corre los `useEffect`— se queda viendo el spinner y la
  // pantalla no pinta ni una seccion. El `useEffect` de abajo lo pone en `true`
  // en cuanto empieza a pedir, que es el caso normal.
const [loading, setLoading] = useState(!informeInicial);
  const [descargando, setDescargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { irA } = useNavegacion();

  // Las empresas se cargan una vez y no en cada cambio de periodo: no cambian
  // con el mes, y volver a pedirlas en cada cambio de selector hace parpadear la
  // pantalla por algo que no cambio.
  useEffect(() => {
    companiesApi.list()
      .then(c => {
        setCompanies(c);
        // Se escoge la primera por omision. Sin esto la pantalla abre sin
        // empresa y el informe no se puede pedir: el backend exige
        // `company_id` (required: true), y una pantalla que nace vacia obliga a
        // un clic extra antes de poder hacer nada.
        setCompanyId(prev => prev || (c.length > 0 ? c[0].id : ''));
      })
      .catch(e => setError(e instanceof Error ? e.message : 'No se pudieron cargar las empresas'));
  }, []);

  const cargar = useCallback(async () => {
    if (!companyId) {
      setReporte(null);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const r = await reportsApi.cierreMensual(companyId, periodo);
      setReporte(r);
    } catch (e) {
      setReporte(null);
      setError(e instanceof Error ? e.message : 'No se pudo cargar el informe');
    } finally {
      setLoading(false);
    }
  }, [companyId, periodo]);

  // Con `informeInicial` no se pide nada: el verificador monta la pantalla con el
  // JSON real de la API y necesita que lo pinte tal cual, sin que un `fetch`
  // intente replacingarlo. En la aplicacion normal el parametro no viene.
  useEffect(() => {
    if (informeInicial) {
      setLoading(false);
      return;
    }
    void cargar();
  }, [cargar, informeInicial]);

  const descargarPdf = async () => {
    if (!reporte) return;
    setDescargando(true);
    setError(null);
    try {
      const blob = await reportsApi.cierreMensualPdf(companyId, periodo);
      descargarBlob(blob, nombreArchivo(reporte));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo descargar el PDF');
    } finally {
      setDescargando(false);
    }
  };

  const opcionesEmpresa = companies.map(c => ({ value: c.id, label: c.name }));
  const opcionesPeriodo = periodosDisponibles().map(p => ({
    value: p, label: nombrePeriodo(p),
  }));

  return (
    <div className="p-4 lg:p-6 max-w-5xl mx-auto space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Cierre mensual</h1>
          <p className="text-sm text-gray-600 mt-1">
            El informe del periodo, con los mismos datos que el PDF que se descarga.
          </p>
        </div>
      </header>

      {/* Selector. El periodo va SIEMPRE explicito al backend, incluso siendo el
          mes en curso: el `periodo` es `required` y su regex es
          `^\d{4}-(0[1-9]|1[0-2])$`. */}
      <Card>
        <div className="grid gap-3 sm:grid-cols-3">
          <Select
            label="Empresa"
            options={opcionesEmpresa.length > 0
              ? opcionesEmpresa
              : [{ value: '', label: 'Sin empresas' }]}
            value={companyId}
            onChange={e => setCompanyId(e.target.value)}
          />
          <Select
            label="Periodo"
            options={opcionesPeriodo}
            value={periodo}
            onChange={e => setPeriodo(e.target.value)}
          />
          <div className="flex items-end">
            <Button
              onClick={descargarPdf}
              disabled={!reporte || descargando}
              className="w-full"
            >
              {descargando ? 'Generando...' : 'Descargar PDF'}
            </Button>
          </div>
        </div>
        {error && (
          <p className="mt-3 text-sm text-red-600" role="alert">{error}</p>
        )}
      </Card>

      {loading && <Loading message="Calculando el informe..." />}

      {!loading && !reporte && !error && (
        <EmptyState message="Elige una empresa y un periodo para ver su informe." />
      )}

      {!loading && reporte && (
        <div className="space-y-4">
          {/* ---- R2: LA ADVERTENCIA DEL BACKEND, ARRIBA DE TODO ----
              `advertencia_principal` lo compone el servidor y va en la PRIMERA
              pagina, no como nota al pie ni en gris: es el aviso de que la
              evidencia no alcanza para afirmar el objetivo de exactitud.

              Va ANTES del banner de estado y no pegado a el por una razon: son
              dos preguntas distintas. El banner dice si el periodo se puede
              declarar cerrado; esta dice si la evidencia alcanza. Un periodo
              puede cerrar perfectamente y aun asi no tener evidencia de
              exactitud, y —al reves— tener evidencia y no poder cerrar. */}
          {lineaAdvertencia(reporte) && <Bloque linea={lineaAdvertencia(reporte)!} />}

          {/* ---- BANNER DE ESTADO ----
              Sale SIEMPRE, no solo cuando hay problema: un periodo que SI cierra
              necesita su afirmacion en pantalla, porque "no hay nada que decir" y
              "todo bien" se leen igual y no son lo mismo. */}
          <Bloque linea={lineaEstado(reporte)} />

          {/* 1. Identificacion */}
          <Seccion titulo="1. Identificación">
            <Fila k="Empresa" v={reporte.empresa_nombre} />
            <Fila k="RFC" v={<span className="font-mono">{reporte.empresa_tax_id}</span>} />
            <Fila k="Periodo" v={`${nombrePeriodo(reporte.periodo)} (${reporte.periodo_inicio} a ${reporte.periodo_fin})`} />
            <Fila k="Fecha de referencia" v={reporte.fecha_referencia} />
            <Fila k="Firmado por" v={reporte.firmado_por} />
          </Seccion>

          {/* 2. Resumen del gasto */}
          <Seccion titulo="2. Resumen del gasto">
            <div className="flex flex-wrap items-baseline gap-3 mb-3">
              <span className="text-3xl font-bold text-gray-900">{dinero(reporte.gasto.total, true)}</span>
              <span className="text-sm text-gray-600">
                en {numero(reporte.gasto.tickets)} ticket{reporte.gasto.tickets === 1 ? '' : 's'}
              </span>
            </div>

            {lineaComparativo(reporte) && (
              <div className="mt-3">
                <Bloque linea={lineaComparativo(reporte)!} />
              </div>
            )}

            {reporte.gasto.por_categoria.length > 0 ? (
              <table className="mt-4 w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b">
                    <th className="pb-2 font-medium">Categoría</th>
                    <th className="pb-2 font-medium text-right">Monto</th>
                    <th className="pb-2 font-medium text-right">Tickets</th>
                    <th className="pb-2 font-medium text-right">% del total</th>
                  </tr>
                </thead>
                <tbody>
                  {reporte.gasto.por_categoria.map(c => (
                    <tr key={c.clave} className="border-b border-gray-100 last:border-0">
                      <td className="py-2 text-gray-900">
                        {c.etiqueta}
                        {c.sin_clasificar && (
                          <Badge variant="warning">sin clasificar</Badge>
                        )}
                      </td>
                      <td className="py-2 text-right text-gray-900">{dinero(c.monto)}</td>
                      <td className="py-2 text-right text-gray-600">{numero(c.tickets)}</td>
                      <td className="py-2 text-right text-gray-600">{porcentaje(c.porcentaje)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              /* R7: se dice que no hay, no se omite la seccion. Una tabla vacia
                 sin explicar se lee como "no se calculo". */
              <p className="text-sm text-gray-500">No hay gasto registrado en el periodo.</p>
            )}
          </Seccion>

          {/* 3. Hallazgos */}
          <Seccion titulo="3. Hallazgos">
            {reporte.hallazgos.length === 0 ? (
              <p className="text-sm text-gray-500">No hay hallazgos con la evidencia disponible.</p>
            ) : (
              <ul className="space-y-3">
                {reporte.hallazgos.map((h, i) => (
                  <li key={`${h.tipo}-${i}`}>
                    <p className="font-medium text-gray-900">{h.titulo}</p>
                    <p className="text-sm text-gray-600">{h.detalle}</p>
                  </li>
                ))}
              </ul>
            )}
          </Seccion>

          {/* 4. Estado de conciliacion */}
          <Seccion titulo="4. Estado de conciliación">
            {Object.entries(reporte.conciliacion.por_estado).map(([estado, n]) => (
              <Fila key={estado} k={estado} v={numero(n)} />
            ))}
            <Fila k="Conciliados" v={numero(reporte.conciliacion.conciliados)} />
            <Fila k="Sin conciliar" v={numero(reporte.conciliacion.sin_conciliar)} />
            <Fila k="Monto en discrepancia" v={dinero(reporte.conciliacion.monto_discrepancias)} />
            <p className="mt-3 text-xs text-gray-500">
              DISCREPANCY no cuenta como conciliado: hay un movimiento en la fecha
              correcta con otro monto, y eso lo tiene que ver una persona.
            </p>

            {/* La partida conciliatoria va en la seccion de conciliacion y no en
                la de pendientes, y no por criterio de seccion: no es trabajo
                pendiente de esta persona. Es una diferencia que ya ocurrio y que
                hay que REPORTAR — el gasto es de este periodo por su fecha de
                compra, y la salida de dinero es del siguiente.

                Se condiciona a `> 0` a proposito. Con cero sale una fila que
                dice "no hay", que es una afirmacion sobre un dato que el usuario
                todavia no conoce; y con el cero al lado de un numero que se ve
                arriba, se lee como "el sistema no encontro ninguno", que es otra
                cosa. */}
            {reporte.conciliacion.pagados_en_otro_periodo_tickets > 0 && (
              <>
                <Fila
                  k="Comprados aquí, pagados en otro periodo"
                  v={`${numero(reporte.conciliacion.pagados_en_otro_periodo_tickets)} t. — ${dinero(reporte.conciliacion.pagados_en_otro_periodo_monto)}`}
                />
                <p className="mt-3 text-xs text-gray-500">
                  Gastos que forman parte de este periodo por su fecha de compra y
                  que se pagaron en otro mes. En contabilidad el gasto es de aquí
                  y en el banco la salida es de allá; las dos fechas son ciertas.
                  No es un error de captura.
                </p>
              </>
            )}
          </Seccion>

          {/* 5. Exactitud. EL CORAZON */}
          <Seccion titulo="5. Exactitud de la lectura automática">
            {lineaExactitud(reporte) && <Bloque linea={lineaExactitud(reporte)!} />}

            {/* El bloque 5 tiene el enlace a la evidencia: quien lee que no hay
               exactitud necesita saber DONDE se produce. */}
            <div className="mt-3">
              <Button variant="secondary" onClick={() => irA('spotcheck')}>
                Ir al muestreo (la evidencia se produce ahí)
              </Button>
            </div>

            {reporte.exactitud && (
              <table className="mt-4 w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b">
                    <th className="pb-2 font-medium">Origen</th>
                    <th className="pb-2 font-medium text-right">Revisados</th>
                    <th className="pb-2 font-medium text-right">Aciertos</th>
                    <th className="pb-2 font-medium text-right">Veredicto</th>
                  </tr>
                </thead>
                <tbody>
                  {reporte.exactitud.por_origen.map(o => (
                    <tr key={o.origen} className="border-b border-gray-100 last:border-0">
                      <td className="py-2 text-gray-900">{o.origen}</td>
                      <td className="py-2 text-right text-gray-600">{numero(o.revisados)}</td>
                      <td className="py-2 text-right text-gray-600">{numero(o.aciertos)}</td>
                      <td className="py-2 text-right">
                        {/* El intervalo va JUNTO al punto (R1): un 96% sin el es un
                            numero que no significa nada. */}
                        {o.exactitud !== null
                          ? `${porcentaje(o.exactitud * 100)}`
                          : 'no disponible'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Seccion>

          {/* 6. Verificacion humana. R5: los dos numeros NUNCA juntos. */}
          <Seccion titulo="6. Registro de verificación humana">
            <Fila k="Leído por el sistema" v={numero(reporte.lectura.leido_por_el_sistema)} />
            <Fila k="Verificado por una persona" v={numero(reporte.lectura.verificado_por_una_persona)} />
            <Fila k="Pendiente de revisión" v={numero(reporte.lectura.pendiente_de_revision)} />
            <div className="mt-3 border-t border-gray-100 pt-3">
              <Fila k="En la muestra" v={numero(reporte.verificacion.en_muestra)} />
              <Fila k="Revisadas" v={numero(reporte.verificacion.revisadas)} />
              <Fila k="Firmadas" v={numero(reporte.verificacion.firmadas)} />
              <Fila k="Pendientes de firmar" v={numero(reporte.verificacion.pendientes_de_firmar)} />
            </div>
            <p className="mt-3 text-xs text-gray-500">
              «Leído por el sistema» y «verificado por una persona» son afirmaciones
              de distinta fuerza y por eso van separados: lo que el sistema leyo no
              cuenta como verificado por nadie.
            </p>
          </Seccion>

          {/* 7. Pendientes */}
          <Seccion titulo="7. Pendientes">
            {/* El "sin fecha" va PRIMERO, y no por jerarquia: es el unico
                pendiente que hace que el TOTAL de la seccion 1 este incompleto.
                Los otros tres son trabajo normal —clasificar, conciliar— y el
                total del periodo sigue siendo real mientras tanto. Este no: la
                fecha que hay es el dia de la captura, no el de la compra, asi que
                estos importes NO estan en el total de arriba y sin esta fila el
                encabezado y el detalle dirian dos cosas distintas. */}
            <Fila
              k="Sin fecha del comprobante"
              v={`${numero(reporte.pendientes.sin_fecha_tickets)} t. — ${dinero(reporte.pendientes.sin_fecha_monto)}`}
            />
            {reporte.pendientes.sin_fecha_tickets > 0 && (
              <p className="mt-3 text-xs text-gray-500">
                Su fecha es el día en que se digitalizó, no el de la compra. No suman
                al total de este periodo ni aparecen en ninguna categoría hasta que
                alguien escriba la fecha mirando el comprobante.
              </p>
            )}
            <div className="mt-3">
              <Fila k="Sin categoría" v={`${numero(reporte.pendientes.sin_categoria_tickets)} t. — ${dinero(reporte.pendientes.sin_categoria_monto)}`} />
              <Fila k="Sin conciliar" v={`${numero(reporte.pendientes.sin_conciliar_tickets)} t. — ${dinero(reporte.pendientes.sin_conciliar_monto)}`} />
              <Fila k="Discrepancias abiertas" v={`${numero(reporte.pendientes.discrepancias_abiertas)} — ${dinero(reporte.pendientes.discrepancias_monto)}`} />
            </div>
            {reporte.pendientes.motivo && (
              <p className="mt-3 text-sm text-amber-700">
                Motivo por el que no cierra: {reporte.pendientes.motivo}
              </p>
            )}
          </Seccion>
        </div>
      )}
    </div>
  );
}