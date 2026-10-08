/** Inicio: el tablero.
 *
 * La regla que ordena esta pantalla
 * --------------------------------
 * **Un tablero no se lee entero.** Quien lo abre quiere saber dos cosas y ya:
 * cuanto gasté, y en qué se va eso. Todo lo demás es contexto que se busca
 * cuando se necesita, no al abrir.
 *
 * La versión anterior no cumplía eso. Tenía siete bloques del mismo tamaño y
 * del mismo peso: la cifra del mes, diez categorías con tres líneas cada una,
 * doce barras de tendencia, ocho proveedores, tres tarjetas de pendientes y el
 * reporte de exactitud. Todo competía con todo, y el resultado se veía
 * saturado y no se sabía por dónde empezar. Saturado no es "mucha información":
 * es "toda la información es igual de importante", que es lo contrario de
 * información.
 *
 * Así que el diseño es de pirámide, y el orden es el de la pregunta:
 *
 *   1. **La cifra.** Un número grande, su variación, y cuatro datos de apoyo en
 *      una línea. Es lo único que ocupa el ancho entero.
 *   2. **La respuesta: en qué se va.** Una sola gráfica, la que justifica todo
 *      lo demás.
 *   3. **El contexto.** Tendencia y proveedores, uno al lado del otro, más
 *      chicos y sin competir con el paso 2.
 *   4. **Lo que falta por hacer.** Abajo, porque es una lista de trabajo, no un
 *      resultado.
 *
 * Tres cosas más que se ajustaron a propósito, y que son las que más hacen que
 * un tablero parezca profesional o cutre:
 *
 *  - **Una sola línea por dato.** Cada categoría ocupa dos renglones (nombre y
 *    monto, con la barra debajo), no tres. Diez categorías con tres líneas son
 *    treinta renglones de scroll antes de llegar a cualquier parte.
 *  - **El número pegado a la barra.** El porcentaje va al lado del monto, en
 *    gris, y no en una columna aparte: el ojo lee izquierda a derecha.
 *  - **Menos color.** El color codifica lo que el texto ya dice. Aquí el color
 *    solo distingue dos cosas que no se distinguen por la palabra: gasto
 *    variable contra gasto fijo, y el gasto que nadie ha clasificado.
 */
import { useEffect, useState } from 'react';
import { companiesApi, dashboardApi, type Company } from '../services/api';
import type {
  CategoriaGasto, DashboardResponse, Hallazgo, MesConCategorias, MesGasto, ResumenPeriodo,
} from '../types/dashboard';
import { useNavegacion, type FiltroTickets, type Page } from '../contextos/Navegacion';
import { Card, Loading, EmptyState, Badge, Select, Button } from '../components/ui';
import { dinero, monto, numero, variacion } from '../utils/format';

// ---------------------------------------------------------------------------
// Color
// ---------------------------------------------------------------------------

/** El color Codifica una distincion que el texto no puede hacer sola.
 *
 *  Tres estados y no mas. Un tablero con once colores distintos parece un
 *  grafico de torta y deja de leerse al segundo uso: cuando todo resalta, nada
 *  resalta. */
const COLOR_VARIABLE = 'bg-blue-500';
const COLOR_FIJO = 'bg-slate-700';
const COLOR_SIN_CLASIFICAR = 'bg-amber-400';

const VERDE = 'bg-emerald-500';
const AMARILLO = 'bg-amber-500';
const ROJO = 'bg-red-500';

// ---------------------------------------------------------------------------
// 1. La cifra
// ---------------------------------------------------------------------------

/** El número que resume el mes, con su variación.
 * * * La variación tiene dos lecturas y aquí se muestran las dos, pero **la grande
 *  es la honesta**: el mes anterior hasta el mismo día. La de mes completo se
 *  queda en una nota, porque durante un mes a medias comparar contra un mes
 *  entero siempre da una caída y eso no dice nada del negocio. Un tablero que
 *  muestra un "-90%" en rojo cada día 3 entrena a su lector para ignorar las
 *  variaciones, que es peor que no mostrar ninguna. */
function CifraDelMes({ d, irA }: { d: DashboardResponse; irA: (p: Page, f?: FiltroTickets) => void }) {
  const { mes } = d;
  const enCurso = mes.mes_en_curso;

  const pct = enCurso ? mes.variacion_pct_a_la_fecha : mes.variacion_pct;
  const referencia = enCurso ? mes.monto_anterior_a_la_fecha : mes.monto_anterior;

  // Rojo por subir, verde por bajar. Al revés de lo que sugiere el color, y a
  // proposito: en un tablero de gastos, subir es malo. Un tablero donde subir es
  // verde obliga a leer la etiqueta al lado de cada numero, y eso es trabajo
  // para quien solo queria saber cuanto gasto.
  const tono = pct === null ? 'text-gray-400' : pct > 0 ? 'text-red-600' : pct < 0 ? 'text-emerald-600' : 'text-gray-400';

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-1">
        <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          Gasto de {enCurso ? 'este mes' : 'mes cerrado'}
        </p>
        <p className="text-4xl font-bold text-gray-900 mt-1.5 tabular-nums">{dinero(mes.monto)}</p>

        <div className="flex items-baseline gap-2 mt-2">
          <span className={`text-lg font-semibold tabular-nums ${tono}`}>{variacion(pct)}</span>
          <span className="text-xs text-gray-500">
            {referencia
              ? `frente a ${dinero(referencia, true)} de ${mes.nombre_anterior}`
              : `sin base (${mes.nombre_anterior} sin gasto)`}
          </span>
        </div>

        {enCurso && (
          <p className="text-xs text-gray-400 mt-2 leading-relaxed">
            Van {mes.dias_transcurridos} de {mes.dias_del_mes} días, comparado contra el mismo
            día de {mes.nombre_anterior}. El mes completo fue {dinero(mes.monto_anterior, true)}
            {' '}({variacion(mes.variacion_pct)}), pero ahí se compara un mes entero contra uno a medias.
          </p>
        )}
      </Card>

      <DatosDeApoyo d={d} irA={irA} />
    </div>
  );
}

/** Cuatro datos en una línea. Supporting, no protagonistas: sin borde propio,
 *  sin título grande, solo el número y su etiqueta.
 *
 *  Van juntos y no en cuatro tarjetas porque son el mismo tipo de dato: el
 *  contexto que hace legible la cifra grande. En cuatro cajas cada una compite
 *  con la cifra; en una fila, la sostienen. */
function DatosDeApoyo({ d, irA }: { d: DashboardResponse; irA: (p: Page, f?: FiltroTickets) => void }) {
  const cola = Object.values(d.cola_revision).reduce((a, b) => a + b, 0);
  const sinClasificar = d.sin_clasificar;

  const datos = [
    { etiqueta: 'Tickets', valor: numero(d.mes.tickets), nota: `en el mes` },
    {
      etiqueta: 'Por revisar',
      valor: numero(cola),
      nota: cola > 0 ? 'esperan' : 'nada',
      alerta: cola > 20,
    },
    {
      etiqueta: 'Banco conciliado',
      valor: `${d.banco.porcentaje.toFixed(0)}%`,
      nota: `${numero(d.banco.sin_conciliar)} sin cuadrar`,
      alerta: d.banco.sin_conciliar > 0,
    },
    {
      // El total de TODO el historico, no el del mes. Y es un boton, porque es
      // la unica de estas cuatro cifras que se puede resolver haciendo algo
      // ahora mismo: twenty-four tickets esperando son veinticuatro clics de
      // separacion.
      etiqueta: 'Sin clasificar',
      valor: numero(sinClasificar.tickets),
      nota: sinClasificar.tickets > 0 ? `${dinero(sinClasificar.monto, true)} pendientes` : 'todo clasificado',
      alerta: sinClasificar.tickets > 0,
      accion: sinClasificar.tickets > 0 ? () => irA('tickets', 'sinClasificar') : undefined,
    },
  ];

  return (
    <Card className="lg:col-span-2">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-5 h-full content-center">
        {datos.map((x) => (
          <button
            key={x.etiqueta}
            onClick={x.accion}
            disabled={!x.accion}
            className={`text-left lg:border-l lg:border-gray-100 lg:first:border-l-0 lg:pl-4 first:lg:pl-0 ${
              x.accion ? 'cursor-pointer' : 'cursor-default'
            }`}
            title={x.accion ? 'Ir a clasificarlos' : undefined}
          >
            <p className="text-xs text-gray-500">{x.etiqueta}</p>
            <p
              className={`text-2xl font-semibold tabular-nums mt-0.5 ${
                x.alerta ? 'text-amber-600' : 'text-gray-900'
              }`}
            >
              {x.valor}
            </p>
            {x.nota && <p className="text-xs text-gray-400 mt-0.5">{x.nota}</p>}
          </button>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 2. La respuesta: en qué se va el gasto
// ---------------------------------------------------------------------------

/** La gráfica que justifica el tablero.
 *
 *  Barras horizontales y no pastel, por una razón que se puede comprobar: el
 *  ojo compara longitudes mucho mejor que ángulos, y con ocho o diez categorías
 *  un pastel se llena debangos de dos grados que nadie distingue. La barra
 *  compara longitudes, que es lo que se sabe hacer de un vistazo.
 *
 *  El ancho de la barra es relativo a la categoría MÁS GRANDE y no al total, para
 *  que las barras usen todo el ancho y las comparaciones entre ellas sean
 *  largas. El porcentaje del total va como texto, que es donde se lee con
 *  precisión: un 34.2% necesita el número, no una barra de un tercio de ancho.
 *
 *  Y todo en dos renglones por categoría: nombre y monto arriba, barra abajo.
 *  Con el conteo de tickets en una tercera línea, diez categorías son treinta
 *  renglones y el resto del tablero queda abajo del doblez. */
function RepartoPorCategoria({
  categorias,
  irA,
  hayMovimientos,
}: {
  categorias: CategoriaGasto[];
  irA: (p: Page, f?: FiltroTickets) => void;
  hayMovimientos: boolean;
}) {
  if (categorias.length === 0) {
    // Tres casos distintos que se ven igual si solo se dice "no hay datos", y
    // que llevan a acciones opuestas:
    //   - hay movimientos bancarios y ningun ticket: se subio el extracto y
    //     faltan los comprobantes, o al reves.
    //   - no hay nada de nada: la empresa es nueva.
    //   - hay tickets pero ninguno este mes: el gasto es de otro periodo.
    return (
      <Card>
        <h2 className="text-sm font-semibold text-gray-900">En qué se va el gasto</h2>
        {hayMovimientos ? (
          <div className="mt-3">
            <p className="text-sm text-gray-600">
              No hay tickets registrados este mes, pero sí hay movimientos
              bancarios. Faltan los comprobantes que los respalden.
            </p>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => irA('tickets')}
            >
              Registrar tickets
            </Button>
          </div>
        ) : (
          <div className="mt-3">
            <p className="text-sm text-gray-600">
              Esta empresa todavía no tiene tickets registrados. En cuanto
              registres el primero, aquí aparecerá el reparto por categoría.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-3"
              onClick={() => irA('tickets')}
            >
              Registrar el primer ticket
            </Button>
          </div>
        )}
      </Card>
    );
  }

  const mayor = Math.max(...categorias.map((c) => monto(c.monto)), 1);
  const sinClasificar = categorias.find((c) => c.sin_clasificar);

  return (
    <Card>
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-900">En qué se va el gasto</h2>
        <span className="text-xs text-gray-400">mes en curso</span>
      </div>

      <div className="space-y-3.5">
        {categorias.map((c) => {
          const valor = monto(c.monto);
          const ancho = Math.max((valor / mayor) * 100, valor > 0 ? 1 : 0);
          const color = c.sin_clasificar
            ? COLOR_SIN_CLASIFICAR
            : c.variable
              ? COLOR_VARIABLE
              : COLOR_FIJO;

          return (
            <div key={c.clave}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-gray-800 truncate">
                  {c.etiqueta}
                  {!c.variable && !c.sin_clasificar && (
                    <span className="ml-1.5 text-[11px] text-gray-400">fijo</span>
                  )}
                </span>
                <span className="text-sm tabular-nums shrink-0">
                  <span className="text-gray-900 font-medium">{dinero(c.monto)}</span>
                  <span className="text-gray-400 ml-2 w-11 text-right inline-block">
                    {c.porcentaje.toFixed(1)}%
                  </span>
                </span>
              </div>
              <div className="mt-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${color}`} style={{ width: `${ancho}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      {sinClasificar && (
        <button
          onClick={() => irA('tickets', 'sinClasificar')}
          className="w-full text-left text-xs text-gray-500 mt-4 pt-3 border-t border-gray-100 hover:text-blue-600 transition-colors"
        >
          <span className="inline-block w-2 h-2 rounded-full bg-amber-400 mr-1.5 align-middle" />
          Sin clasificar no es un rubro, es trabajo pendiente.{' '}
          <span className="text-blue-600 font-medium">
            Clasificar {sinClasificar.tickets}{' '}
            {sinClasificar.tickets === 1 ? 'ticket' : 'tickets'} →
          </span>
        </button>
      )}

    </Card>
  );
}

// ---------------------------------------------------------------------------
// 3. Los últimos tres meses
// ---------------------------------------------------------------------------

/** Los tres meses completos, uno al lado del otro, con su desglose.
 *
 *  Esto responde la pregunta que la gráfica de doce barras deja abierta: "el
 *  gasto subió, ¿subió por qué?". Un total sin desglose obliga a cambiar de
 *  pantalla a averiguarlo, y mientras se averigua se adivina.
 *
 *  Por qué tres columnas y no una tabla con una fila por mes: la comparación
 *  que interesa es **entre columnas** ("agosto tuvo más MATERIALES que julio"),
 *  y eso se lee comparando verticalmente. En una tabla por filas hay que bajar la
 *  vista y comparar un dato con uno que está tres renglones más arriba.
 *
 *  Cada columna muestra solo las **cuatro** categorías más grandes del mes. Las
 *  demás no se esconden: se suman en una línea de resto. Recortar sin avisar
 *  haría que las columnas no sumaran el total y alguien compararía dos cifras que
 *  no son del mismo tamaño.
 *
 *  Y lleva su propia variación contra el mes anterior, calculada aquí y no en el
 *  servidor, porque comparar dos números que ya están en la misma tarjeta es
 *  aritmética de cliente y no vale la pena un viaje de ida y vuelta por ello.
 */
function ComparativoMeses({ meses, mes }: { meses: MesConCategorias[]; mes: ResumenPeriodo }) {
  if (meses.length < 2) return null;

  // De mas antiguo a mas reciente para que la lectura vaya izquierda a derecha,
  // como una linea de tiempo. El servidor ya los manda asi.
  const CATEGORIAS_POR_COLUMNA = 4;

  return (
    <Card>
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-900">Últimos {meses.length} meses</h2>
        <span className="text-xs text-gray-400">por categoría</span>
      </div>

      <div className="grid gap-5 sm:grid-cols-3">
        {meses.map((m, i) => {
          const anterior = i > 0 ? meses[i - 1] : null;
          const enCurso = i === meses.length - 1;

          // **La ultima columna se compara contra el mismo dia del mes anterior,
          // no contra el mes anterior entero.**
          //
          // Los dos meses anteriores estan cerrados y se comparan bien. El que
          // esta en curso no: son N dias contra 30, y eso siempre da una caida
          // que no significa nada. El dia 3 del mes, este numero dira cerca de
          // -90% y el gasto estara exactamente donde deberia. Por eso la
          // columna en curso usa la cifra `a_la_fecha` que ya viene calculada
          // para la cabecera, en vez de la que se podria sacar aqui.
          //
          // Se ve rare el dia 29 y se ve absurdo el dia 3, y por eso el numero
          // tiene que decir a que se esta comparando en vez de dejar que se
          // suponga.
          const pct = enCurso
            ? mes.variacion_pct_a_la_fecha
            : anterior && Number(anterior.monto) > 0
              ? ((Number(m.monto) - Number(anterior.monto)) / Number(anterior.monto)) * 100
              : null;

          const contraQuien = enCurso
            ? `mismo día de ${mes.nombre_anterior}`
            : anterior
              ? `vs ${anterior.nombre}`
              : null;

          const principales = m.por_categoria.slice(0, CATEGORIAS_POR_COLUMNA);
          const resto = m.por_categoria.slice(CATEGORIAS_POR_COLUMNA);
          const montoResto = resto.reduce((a, c) => a + monto(c.monto), 0);

          return (
            <div key={`${m.anio}-${m.mes}`} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-gray-900 capitalize">
                  {m.nombre}
                </span>
                {enCurso && (
                  <span
                    className="text-[10px] text-gray-500 bg-gray-100 rounded px-1.5 py-0.5 whitespace-nowrap"
                    title={`Este mes no termina. Solo van ${mes.dias_transcurridos} de ${mes.dias_del_mes} días, así que su total todavía no se puede comparar con un mes cerrado.`}
                  >
                    van {mes.dias_transcurridos}/{mes.dias_del_mes}
                  </span>
                )}
              </div>

              <p className="text-lg font-semibold text-gray-900 tabular-nums mt-0.5">
                {dinero(m.monto, true)}
              </p>
              <p className="text-xs text-gray-500">
                {numero(m.tickets)} tickets
                {pct !== null && (
                  <span
                    className={`ml-1.5 tabular-nums ${
                      pct > 0 ? 'text-red-500' : pct < 0 ? 'text-emerald-600' : 'text-gray-400'
                    }`}
                    title={contraQuien ?? undefined}
                  >
                    {variacion(pct)}
                  </span>
                )}
              </p>
              {contraQuien && (
                <p className="text-[11px] text-gray-400 -mt-0.5">{contraQuien}</p>
              )}

              <ul className="mt-3 space-y-1.5 pt-3 border-t border-gray-100">
                {principales.map((c) => (
                  <li key={c.clave} className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="text-gray-600 truncate" title={c.etiqueta}>
                      {c.etiqueta}
                    </span>
                    <span className="text-gray-900 tabular-nums shrink-0">
                      {dinero(c.monto, true)}
                    </span>
                  </li>
                ))}
                {montoResto > 0 && (
                  <li className="flex items-baseline justify-between gap-2 text-xs text-gray-400">
                    <span>+{resto.length} más</span>
                    <span className="tabular-nums">{dinero(montoResto, true)}</span>
                  </li>
                )}
              </ul>
            </div>
          );
        })}
      </div>

      <p className="text-xs text-gray-400 mt-4 pt-3 border-t border-gray-100">
        Cada mes muestra sus cuatro categorías más grandes. La última columna es
        el mes en curso, y su porcentaje se compara contra el mismo día del mes
        anterior: comparar días sueltos contra un mes completo daría una caída
        que no significa nada.
      </p>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 4. El contexto
// ---------------------------------------------------------------------------

/** La serie de meses. Comprimida a la mitad de alto que antes.
 *
 *  Que la barra más alta toque el tope del área es un desperdicio: la
 *  diferencia entre "octubre fue el mes más caro" y "octubre fue un poco más
 *  caro" no se lee en la altura, se lee en el número. Y la altura que se le
 *  quita se le devuelve a las tarjetas de al lado. */
/** La serie de meses, con las barras medidas en PIXELES.
 *
 *  La version anterior usaba `style={{ height: `${alto}%` }}` dentro de un
 *  contenedor `flex flex-col` sin altura fija, y **no se veia ninguna barra**.
 *
 *  El motivo es una regla de CSS que no se intuye: **un porcentaje de altura
 *  solo resuelve contra un padre de altura DEFINIDA**. El padre directo era
 *  `flex flex-col items-center`, cuya altura la decidia el contenido (la
 *  etiqueta del mes), o sea `auto`. Contra un padre `auto`, un `50%` no tiene
 *  contra que calcularse y la barra se va a cero.
 *
 *  El fallo es invisible desde fuera: el contenedor de fuera si tenia `h-24`, asi
 *  que uno ve el alto correcto, ve los doce nombres de mes, y concluye que los
 *  datos no llegaron. Los datos llegaban bien; lo que no llegaba era el alto.
 *
 *  Por eso aqui la altura se calcula en pixeles: la misma cuenta, con el mismo
 *  maximo como referencia, pero sin depender de como resuelve el navegador un
 *  porcentaje contra una altura automatica.
 *
 *  El area de barras y la de las etiquetas van con alturas separadas y
 *  explicitas. Si la etiqueta compartiera columna con la barra, la mas alta se
 *  pasaria de su caja, porque mediria desde el fondo de la columna --que incluye
 *  la etiqueta-- en vez de desde el fondo del grafico.
 */
const ALTO_BARRAS = 88;
const ALTO_ETIQUETA = 16;

function Tendencia({ meses }: { meses: MesGasto[] }) {
  if (meses.length === 0) return null;
  const mayor = Math.max(...meses.map((m) => monto(m.monto)), 1);

  return (
    <Card>
      <h2 className="text-sm font-semibold text-gray-900 mb-4">Tendencia</h2>
      <div className="flex items-end gap-1" style={{ height: ALTO_BARRAS + ALTO_ETIQUETA }}>
        {meses.map((m, i) => {
          const enCurso = i === meses.length - 1;
          const valor = monto(m.monto);
          // El piso de 2px es para que un mes con gasto pequeno se vea. Un mes
          // de $50 en una escala de $150,000 es 0.03% y sin piso seria un pixel
          // invisible, que se lee como "este mes no se gasto" en vez de "este
          // mes se gasto muy poco".
          const px = Math.max(Math.round((valor / mayor) * ALTO_BARRAS), valor > 0 ? 2 : 1);

          return (
            <div key={`${m.anio}-${m.mes}`} className="flex-1 flex flex-col items-center group relative">
              <div className="w-full flex items-end" style={{ height: ALTO_BARRAS }}>
                <div
                  className={`w-full rounded-sm ${enCurso ? 'bg-emerald-400' : 'bg-blue-200'}`}
                  style={{ height: px }}
                />
              </div>
              <span
                className="text-[10px] text-gray-400"
                style={{ height: ALTO_ETIQUETA, lineHeight: `${ALTO_ETIQUETA}px` }}
              >
                {m.etiqueta}
              </span>
              <div className="absolute bottom-full mb-1 hidden group-hover:block bg-gray-900 text-white text-xs rounded px-2 py-1 whitespace-nowrap z-10">
                {m.nombre} {m.anio}: {dinero(m.monto, true)} · {m.tickets} t
                {enCurso ? ' (mes en curso)' : ''}
              </div>
            </div>
          );
        })}
      </div>

      {/* La leyenda. Sin ella, el verde de la ultima barra no significa nada:
          se ve un color distinto y hay que adivinar que distingue. Una leyenda
          es una frase, no dos cuadritos de color. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-[11px] text-gray-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-blue-200 shrink-0" />
          Mes cerrado
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-emerald-400 shrink-0" />
          Mes en curso
          {enCursoEnSerie(meses) && (
            <span className="text-gray-400">
              (van {meses[meses.length - 1].tickets} tickets, todavía no cierra)
            </span>
          )}
        </span>
      </div>
    </Card>
  );
}

function enCursoEnSerie(meses: MesGasto[]): boolean {
  return meses.length > 0;
}

/** Lo que la grafica quiere decir, en palabras.
 *
 *  **Por que no lo calcula aqui.** La conclusion viene del servidor
 *  (`app/services/hallazgos.py`) y llega ya hecha. Podria haberse calculado en el
 *  navegador y seria un error: la comparacion contra la mediana y los umbrales
 *  tienen que dar el mismo numero en la pantalla, en la API y en los tests, y
 *  tener la misma cuenta en tres sitios garantiza que tarde o temprano deja de
 *  dar. Ademas una conclusion equivocada aqui no sale un error, sale un texto
 *  conven convincent y equivocado, que es la peor forma de equivocarse.
 *
 *  El backend devuelve estructura (tipo, titulo, detalle, tono) y no una frase,
 *  porque la redaccion cambia con mucha mas frecuencia que la aritmetica: un
 *  texto mal escrito se corrige en un segundo y no toca los tests.
 *
 *  **El tono no es decorativo.** `atencion` va en ambar, `bien` en verde, y en un
 *  tablero de gastos un hallazgo en verde significa "gasto abajo". Es lo mismo
 *  que en la cifra grande, y por la misma razon: un tablero donde subir es verde
 *  obliga a leer la etiqueta de al lado de cada numero.
 */
function ResumenHallazgos({ hallazgos }: { hallazgos: Hallazgo[] }) {
  if (!hallazgos || hallazgos.length === 0) return null;

  const tono: Record<string, { punto: string; texto: string }> = {
    bien: { punto: 'bg-emerald-500', texto: 'text-gray-700' },
    atencion: { punto: 'bg-amber-500', texto: 'text-gray-700' },
    mal: { punto: 'bg-red-500', texto: 'text-gray-700' },
    neutro: { punto: 'bg-gray-300', texto: 'text-gray-700' },
  };

  return (
    <Card>
      <h2 className="text-sm font-semibold text-gray-900 mb-1">Qué dice el gráfico</h2>
      <p className="text-xs text-gray-500 mb-4">
        Calculado sobre los meses cerrados, comparados contra la mediana en vez
        del promedio.
      </p>

      <ul className="space-y-3">
        {hallazgos.map((h, i) => {
          const t = tono[h.tono] || tono.neutro;
          return (
            <li key={`${h.tipo}-${i}`} className="flex gap-2.5">
              <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${t.punto}`} />
              <div className="min-w-0">
                <p className={`text-sm font-medium capitalize ${t.texto}`}>
                  {h.titulo.charAt(0).toUpperCase() + h.titulo.slice(1)}
                </p>
                <p className="text-sm text-gray-600">{h.detalle}</p>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** Los proveedores, como lista y no como gráfica.
 *
 *  Antes eran ocho barras horizontales, que es tinta gastada en ocho filas de un
 *  dato que cabe en una línea. Aquí es una tabla: una línea por proveedor, con
 *  el monto a la derecha. Se lee igual de rápido y ocupa la mitad.
 *
 *  Se muestran cinco y no ocho porque la cola de la lista no se mira: los tres
 *  últimos proveedores casi nunca cambian la conclusión de "quién se lleva la
 *  platita". */
function Proveedores({ d }: { d: DashboardResponse }) {
  const proveedores = d.top_proveedores.slice(0, 5);
  if (proveedores.length === 0) return null;
  const mayor = Math.max(...proveedores.map((p) => monto(p.monto)), 1);

  return (
    <Card>
      <h2 className="text-sm font-semibold text-gray-900 mb-4">Principales proveedores</h2>
      <div className="space-y-2.5">
        {proveedores.map((p) => {
          const valor = monto(p.monto);
          return (
            // `flex-wrap` y las clases `sm:` de abajo son un arreglo MEDIDO, no
            // una prevision. A 390px esta tarjeta pedia 403px y solo tenia 302,
            // y eso hacia que TODO el tablero se pudiera desplazar de lado.
            //
            // La fila era `flex` con el nombre en `flex-1`, la barra en `w-24`
            // y el monto en `w-20`. Con "CONCRETOS DEL VALLE" salen 161 + 96 +
            // 80 + 24 de gaps = 361, y el `Card` es un grid item con
            // `min-width:auto`, que es el min-content de su contenido: la
            // columna implicita del grid se dimensionaba a 403 y el tablero
            // entero se salia 37px.
            //
            // Lo que se midio para llegar aqui, y por que el arreglo NO es el
            // que se suele poner:
            //
            //   - `min-w-0` en el span que trunca: NO cambia nada (403 -> 403).
            //     `truncate` ya trae `overflow:hidden`, y eso ya pone su minimo
            //     automatico en 0. El nombre no es lo que estira: estira su
            //     *contribucion min-content*, que `min-width` no toca.
            //   - `min-width:0` en linea: tampoco (403 -> 403). Mismo motivo.
            //   - acortar la barra a `w-16`: 371. A `w-12`: 355. A `w-8`: 339.
            //     Ni la mas pequena cabe, porque el nombre sigue pidiendo 163.
            //   - quitar la barra: 295, y cabe — por 7px. Siete de holgura no es
            //     una defensa: el siguiente nombre un poco mas largo la rompe, y
            //     se vuelve a ver el mismo defecto.
            //   - dos lineas (el nombre arriba, barra y monto abajo): 203, con
            //     99px de holgura.
            //
            // Se eligio la de 99 porque las otras dos "caben" por unos pixeles.
            // En escritorio el `flex-wrap` no hace nada —no hay que partir— y
            // las clases `sm:` devuelven exactamente la fila de antes.
            <div key={p.proveedor} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span
                className="text-sm text-gray-700 truncate w-full sm:w-auto sm:flex-1"
                title={p.proveedor}
              >
                {p.proveedor}
              </span>
              {/* En movil la barra se estira para llenar la segunda linea; desde
                  `sm` vuelve a su ancho fijo de 96px. */}
              <div className="flex-1 sm:flex-none sm:w-24 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-slate-400 rounded-full"
                  style={{ width: `${Math.max((valor / mayor) * 100, 2)}%` }}
                />
              </div>
              <span className="text-sm text-gray-900 tabular-nums w-20 text-right shrink-0">
                {dinero(p.monto, true)}
              </span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 4. Lo que falta
// ---------------------------------------------------------------------------

/** La lista de trabajo. Abajo del todo, y en una línea.
 *
 *  Va abajo porque no es un resultado: es una lista de cosas pendientes, y es lo
 *  unico que se puede actuar. Primero las cifras de lo que ya pasó, después lo
 *  que falta: si no, la pantalla abre con una lista de pendientes que parece un
 *  aviso y no un resumen. */
function Pendientes({ d, irA }: { d: DashboardResponse; irA: (p: Page) => void }) {
  const cola = Object.values(d.cola_revision).reduce((a, b) => a + b, 0);
  const conc = d.por_estado_conciliacion;
  const totalConc = Object.values(conc).reduce((a, b) => a + b, 0);
  const perfect = conc.PERFECT || 0;
  const discrepancias = conc.DISCREPANCY || 0;

  return (
    <Card>
      <h2 className="text-sm font-semibold text-gray-900 mb-4">Lo que falta</h2>

      <div className="grid gap-4 sm:grid-cols-3">
        <FilaPendiente
          titulo="Revisión de tickets"
          detalle={cola === 0 ? 'Cola vacía' : `${numero(cola)} por revisar`}
          barra={cola === 0 ? 0 : 100}
          color={cola === 0 ? VERDE : AMARILLO}
          accion={cola > 0 ? { texto: 'Abrir cola', onClick: () => irA('review') } : undefined}
        />
        <FilaPendiente
          titulo="Conciliación"
          detalle={`${numero(d.banco.sin_conciliar)} movimientos sin cuadrar`}
          barra={d.banco.porcentaje}
          color={d.banco.porcentaje >= 90 ? VERDE : d.banco.porcentaje >= 60 ? AMARILLO : ROJO}
          accion={{ texto: 'Ver', onClick: () => irA('reconciliations') }}
        />
        <FilaPendiente
          titulo="Resultado"
          detalle={
            totalConc === 0
              ? 'Sin conciliaciones'
              : `${numero(discrepancias)} con diferencia de ${numero(totalConc)}`
          }
          barra={totalConc === 0 ? 0 : (perfect / totalConc) * 100}
          color={discrepancias === 0 ? VERDE : ROJO}
        />
      </div>
    </Card>
  );
}

function FilaPendiente({
  titulo,
  detalle,
  barra,
  color,
  accion,
}: {
  titulo: string;
  detalle: string;
  barra: number;
  color: string;
  accion?: { texto: string; onClick: () => void };
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm text-gray-800">{titulo}</span>
        {accion && (
          <button
            onClick={accion.onClick}
            // `py-3.5 -my-3.5`: da 44px de area pulsable sin mover la fila. El
            // margen negativo es lo que permite eso — sin el, el boton
            // empujaria hacia abajo la linea de 20px en la que vive. Medido a
            // 16px sin esto. Con `py-2` daba 32, todavia corto.
            className="text-xs text-blue-600 hover:text-blue-700 shrink-0 py-3.5 -my-3.5"
          >
            {accion.texto} →
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500 mt-0.5">{detalle}</p>
      <div className="mt-2 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${Math.min(barra, 100)}%` }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 5. Exactitud
// ---------------------------------------------------------------------------

/** El veredicto de exactitud, al final y en una línea por origen.
 *
 *  Mide el sistema, no el gasto. Es importante, pero no es lo que se abre la
 *  aplicacion a mirar, así que va en el puro final y sin tarjeta propia por
 *  origen: una sola línea por vía de captura con su número al lado. */
function Exactitud({ d }: { d: DashboardResponse }) {
  if (!d.exactitud) {
    return (
      <Card>
        <h2 className="text-sm font-semibold text-gray-900">Exactitud de la lectura</h2>
        <p className="text-xs text-gray-500 mt-1.5">
          Se necesita una empresa seleccionada y al menos una muestra revisada.
        </p>
      </Card>
    );
  }

  const tono: Record<string, 'success' | 'warning' | 'danger' | 'default'> = {
    CUMPLE: 'success',
    NO_CUMPLE: 'danger',
    INCONCLUYENTE: 'warning',
    SIN_EVIDENCIA: 'default',
  };

  return (
    <Card>
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-gray-900">Exactitud de la lectura</h2>
        <Badge variant={tono[d.exactitud.veredicto_global] || 'default'}>
          {d.exactitud.veredicto_global}
        </Badge>
      </div>
      <p className="text-xs text-gray-500 mb-4">{d.exactitud.explicacion}</p>

      <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
        {d.exactitud.por_origen.map((o) => (
          <div key={o.origen} className="flex items-center justify-between text-sm">
            <span className="text-gray-700">{o.origen}</span>
            <span className="flex items-center gap-3">
              <span className="text-gray-400 tabular-nums text-xs">
                {o.aciertos}/{o.revisados}
              </span>
              <span className="text-gray-900 tabular-nums w-12 text-right">
                {o.exactitud !== null ? `${(o.exactitud * 100).toFixed(0)}%` : '—'}
              </span>
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// La pantalla
// ---------------------------------------------------------------------------

export function Dashboard() {
  const { irA } = useNavegacion();
  const [datos, setDatos] = useState<DashboardResponse | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [empresas, setEmpresas] = useState<Company[]>([]);
  const [empresa, setEmpresa] = useState('');

  useEffect(() => {
    let vivo = true;
    (async () => {
      setCargando(true);
      setError(null);
      try {
        const [lista, tablero] = await Promise.all([
          companiesApi.list(),
          dashboardApi.get(empresa || undefined),
        ]);
        if (!vivo) return;
        setEmpresas(lista);
        setDatos(tablero);
      } catch (e) {
        if (!vivo) return;
        setError(e instanceof Error ? e.message : 'No se pudo cargar el tablero');
      } finally {
        if (vivo) setCargando(false);
      }
    })();
    return () => {
      vivo = false;
    };
  }, [empresa]);

  if (cargando) return <Loading message="Cargando tablero..." />;

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
          {error}
        </div>
      </div>
    );
  }

  if (!datos) return <EmptyState message="No hay datos" />;

  return (
    <div className="p-6 space-y-4 max-w-6xl">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Inicio</h1>
          <p className="text-sm text-gray-500">
            {datos.company_name || 'Todas las empresas'}
          </p>
        </div>
        <div className="w-full sm:w-64">
          <Select
            label="Empresa"
            value={empresa}
            onChange={e => setEmpresa(e.target.value)}
            options={[
              { value: '', label: 'Todas las empresas' },
              ...empresas.map((c) => ({ value: c.id, label: c.name })),
            ]}
          />
        </div>
      </div>

      <CifraDelMes d={datos} irA={irA} />

      <ComparativoMeses meses={datos.comparativo} mes={datos.mes} />

      {/* Las dos columnas se miden antes de repartirse, y por eso los proveedores
          van a la izquierda y no donde estaban.

          Con una tarjeta a la izquierda y tres a la derecha, la fila queda
          definida por la columna alta: en la de abajo aparecia un bloque de
          ~274px de blanco, y un tablero con un agujero al medio se lee como que
          falta algo, no como que se opto porairy la informacion. Mover los
          proveedores deja las columnas en 502 y 484: una diferencia de 18px que
          no se nota.

          Y de paso quedan mejor emparejadas por lo que cuentan: el reparto por
          categoria y los proveedores responden las dos "a donde se va la
          plata", y "que dice el grafico" queda pegado a la tendencia que
          interpreta, que es donde se lee util.

          `items-start` evita que las columnas se estiren hasta la altura de la
          mas alta: estiradas, las tarjetas bajas quedan con un vacio debajo que
          es el mismo problema por otro lado. */}
      <div className="grid gap-4 lg:grid-cols-5 lg:items-start">
        <div className="lg:col-span-3 space-y-4">
          <RepartoPorCategoria
            categorias={datos.por_categoria}
            irA={irA}
            hayMovimientos={datos.banco.total > 0}
          />
          <Proveedores d={datos} />
        </div>
        <div className="lg:col-span-2 space-y-4">
          <Tendencia meses={datos.tendencia} />
          <ResumenHallazgos hallazgos={datos.hallazgos} />
        </div>
      </div>

      <Pendientes d={datos} irA={irA} />
      <Exactitud d={datos} />
    </div>
  );
}
