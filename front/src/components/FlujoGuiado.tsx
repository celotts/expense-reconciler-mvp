/**
 * La barra que dice en qué paso del camino está la empresa.
 *
 * POR QUÉ EXISTE
 * ==============
 *
 * Digitalizar, registrar el gasto y conciliar son tres trabajos que van en orden,
 * y hoy son **tres pantallas sueltas** en un menú lateral sin ninguna pista de
 * cuál sigue. Quien digitaliza 40 comprobantes y cierra la laptop se encuentra al
 * volver con 40 tickets en estado `PENDIENTE` y ninguna señal de que el siguiente
 * paso es revisarlos, no volver a escanear.
 *
 * Esta barra no reemplaza a las pantallas: las **encadena**. Cada paso es un
 * botón que lleva a la pantalla que hace ese trabajo, con el número al lado.
 *
 * LOS TRES PASOS Y POR QUÉ EN ESE ORDEN
 * ======================================
 *
 * 1. **Digitalizar** → `/scan`. Lee los comprobantes de la carpeta.
 * 2. **Registrar** → `/review`. Solo los `AUTO_APROBADO`/`APROBADO` cuentan como
 *    registrados; el resto está en la cola.
 * 3. **Conciliar** → `/reconciliations`. Solo entra lo que ya está en
 *    `SETTLED_STATUSES`.
 *
 * El orden no es decorativo y **por eso el paso 3 puede estar en cero sin que el
 * paso 2 esté completo**: un ticket `PENDIENTE` no se concilia ni se exporta
 * (`AGENTS.md`, regla de `SETTLED_STATUSES`). Mostrar "0 conciliados" al lado de
 * "38 por registrar" no es un error del sistema, es el estado real, y esconderlo
 * haría que alguien preguntara por qué.
 *
 * EL NÚMERO DE CADA PASO
 * ======================
 *
 * Sale de `DashboardResponse`, que ya trae `por_estado`, `cola_revision` y
 * `por_estado_conciliacion`. **No hay ningún endpoint nuevo**: el tablero ya
 * contaba esto, y duplicar el conteo en un segundo sitio es exactamente la
 * "segunda verdad" que este proyecto no sostiene — dos números que pueden
 * discrepar y nadie sabe cuál mandar.
 *
 * CADA NÚMERO DICE SI ALGO ES UN DATO O UNA AFIRMACIÓN
 * =====================================================
 *
 * `SETTLED_STATUSES` son los dos estados donde el sistema **se atreve** a afirmar
 * el importe. Con OCR al 80% de los totales, casi nada llega ahí, y por eso el
 * paso 2 suele estar en rojo con muchos tickets esperando. **Eso no es un
 * malfunctionamiento a arreglar: es el gate haciendo su trabajo.** La barra lo
 * dice en el texto de cada paso, para que "38 por registrar" se lea como trabajo
 * pendiente y no como un fallo.
 *
 * LO QUE ESTE COMPONENTE NO HACE
 * ==============================
 *
 * **No es un asistente ni un wizard.** No guarda dónde estabas, no avanza solo, no
 * impide salir. Es una barra que muestra el estado y ofrece el siguiente paso. Un
 * asistente que avanza solo por ti Metería las manos en el camino que decide qué
 * se afirma sobre un gasto, y esa decisión es de una persona.
 */

import type { Page } from '../contextos/Navegacion';
import type { DashboardResponse } from '../types/dashboard';

/** Los estados donde el sistema se atreve a afirmar el importe.
 *
 *  Es una COPIA de `SETTLED_STATUSES` (`app/core/enums.py`), no un import: el
 *  front no puede importar de Python, y una lista escrita a mano es el unico
 *  punto donde pueden separarse. Si un dia se agrega un estado aqui, esta lista
 *  tiene que cambiar con el — y por eso esta el comentario.
 */
const AFIRMADOS = ['AUTO_APROBADO', 'APROBADO'];

export type EstadoPaso = 'hecho' | 'en_curso' | 'sin_empezar' | 'bloqueado';

interface Props {
  dashboard: DashboardResponse;
  /** Navegar al paso. Viene del contexto, no se importa aqui. */
  irA: (pagina: Page) => void;
  /** La empresa elegida. `null` = todas, y cambia los conteos. */
  companyId: string | null;
}

interface Paso {
  clave: string;
  titulo: string;
  /** Qué hace este paso, en una frase. Es lo que no se deduce del título. */
  queHace: string;
  pagina: Page;
  /** El número de cosas que hay que hacer aquí. */
  pendientes: number;
  /** El total de cosas de este tipo, para el "3 de 40". */
  total: number;
  /** Qué impide avanzar, si es que hay algo. */
  nota: string;
}

export function FlujoGuiado({ dashboard, irA, companyId }: Props) {
  const afirmados = AFIRMADOS.reduce(
    (suma, e) => suma + (dashboard.por_estado[e] ?? 0),
    0
  );
  const enCola =
    dashboard.cola_revision?.PENDIENTE ??
    dashboard.cola_revision?.REQUIERE_REVISION ??
    0;
  const pendientesRevision = Number(enCola) || 0;

  const conciliados =
    (dashboard.por_estado_conciliacion?.PERFECT ?? 0) +
    (dashboard.por_estado_conciliacion?.MANUAL ?? 0);
  const sinConciliar = Number(dashboard.por_estado_conciliacion?.SIN_CONCILIAR) || 0;

  const pasos: Paso[] = [
    {
      clave: 'digitalizar',
      titulo: 'Digitalizar',
      queHace: 'Leer los comprobantes de la carpeta.',
      pagina: 'scan',
      // El numero de archivos sin procesar. `stats.total_archivos` ya esta en
      // `ScanStats`, pero el dashboard no lo trae, asi que se usa lo que si hay.
      pendientes: 0,
      total: dashboard.tickets_historico,
      nota: '',
    },
    {
      clave: 'registrar',
      titulo: 'Registrar',
      queHace: 'Confirmar los importes contra el papel.',
      pagina: 'review',
      pendientes: pendientesRevision,
      total: dashboard.tickets_historico,
      nota: '',
    },
    {
      clave: 'conciliar',
      titulo: 'Conciliar',
      queHace: 'Cruzar los gastos con el banco.',
      pagina: 'reconciliations',
      pendientes: sinConciliar,
      total: dashboard.tickets_historico,
      nota: '',
    },
  ];

  return (
    <section
      className="bg-white rounded-xl border border-gray-200 p-4 mb-6"
      aria-label="Camino del gasto"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2 mb-4">
        <h2 className="text-sm font-semibold text-gray-900">Camino del gasto</h2>
        <p className="text-xs text-gray-500">
          {companyId
            ? 'De la empresa elegida.'
            : 'De todas las empresas. Elige una arriba para ver sus números.'}
        </p>
      </header>

      {/* Los tres pasos. `ol` y no `div`: el orden ES el significado, y la lista
          ordenada lo dice sin tener que explicarlo.

          `list-none` NO es cosmetico: sin el, el navegador pinta sus propios
          "1." "2." "3." al margen Y el badge de cada paso pinta el numero otra
          vez. Salia una lista 1-2-3 con un 1-2-3 repetido dentro, que se leia
          como seis pasos. Solo se noto al MIRAR la pantalla: los tests de texto
          no distinguen el numero del `ol` del numero del badge. */}
      <ol className="list-none grid gap-3 sm:grid-cols-3">
        {pasos.map((paso, i) => {
          const estado = _estadoDe(paso, i, pasos);
          return (
            <li key={paso.clave}>
              <button
                onClick={() => irA(paso.pagina)}
                className={`w-full text-left rounded-lg border p-3 transition-colors h-full flex flex-col ${
                  estado === 'hecho'
                    ? 'border-green-200 bg-green-50 hover:bg-green-100'
                    : estado === 'en_curso'
                      ? 'border-blue-300 bg-blue-50 hover:bg-blue-100'
                      : 'border-gray-200 bg-gray-50 hover:bg-gray-100'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  {/* El numero del paso. Es la parte que hace que la lista se lea
                      como un camino y no como tres botones sueltos. */}
                  <span
                    className={`w-5 h-5 rounded-full text-[11px] font-semibold flex items-center justify-center shrink-0 ${
                      estado === 'hecho'
                        ? 'bg-green-600 text-white'
                        : 'bg-gray-300 text-gray-700'
                    }`}
                    aria-hidden="true"
                  >
                    {estado === 'hecho' ? '✓' : i + 1}
                  </span>
                  <span className="text-sm font-semibold text-gray-900">
                    {paso.titulo}
                  </span>
                </div>

                <p className="text-xs text-gray-600 mb-2">{paso.queHace}</p>

                {/* El numero. `pendientes` primero y en grande: es la accion, y el
                    total al lado es la referencia. Al reves —"40 tickets, 3
                    hechos"— el numero grande invita a pensar que hay 40 pendientes
                    cuando hay 37. */}
                <p className="mt-auto">
                  {paso.clave === 'registrar' ? (
                    <>
                      <span className="text-lg font-bold tabular-nums text-gray-900">
                        {pendientesRevision}
                      </span>
                      <span className="text-xs text-gray-600 ml-1.5">
                        por confirmar
                        {afirmados > 0 && (
                          <span className="text-green-700"> · {afirmados} ya están</span>
                        )}
                      </span>
                    </>
                  ) : paso.clave === 'conciliar' ? (
                    <>
                      <span className="text-lg font-bold tabular-nums text-gray-900">
                        {sinConciliar}
                      </span>
                      <span className="text-xs text-gray-600 ml-1.5">
                        sin cruzar
                        {conciliados > 0 && (
                          <span className="text-green-700"> · {conciliados} cruzados</span>
                        )}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="text-lg font-bold tabular-nums text-gray-900">
                        {dashboard.tickets_historico}
                      </span>
                      <span className="text-xs text-gray-600 ml-1.5">
                        comprobantes leídos
                      </span>
                    </>
                  )}
                </p>
              </button>
            </li>
          );
        })}
      </ol>

      {/* La nota que hace legible un numero que parece malo. */}
      {pendientesRevision > 0 && (
        <p className="mt-3 text-xs text-gray-600 leading-relaxed">
          Hay <strong className="text-gray-900">{pendientesRevision}</strong>{' '}
          comprobantes que el sistema leyó pero <strong>no se atrevió a dar por
          buenos</strong>: falta confirmar el importe contra el papel. Eso no es un
          error — con OCR al 80% de los totales, es lo que evita que un número mal
          leído entre al cierre.
        </p>
      )}

      {pendientesRevision === 0 && dashboard.tickets_historico === 0 && (
        <p className="mt-3 text-xs text-gray-500">
          Todavía no hay comprobantes leídos. Empieza por <strong>Digitalizar</strong>.
        </p>
      )}
    </section>
  );
}

/** El estado visual de un paso, y sobre todo lo que NO implica.
 *
 *  Se exporta aunque solo la use este archivo, porque el render tiene que poder
 *  distinguir "hecho" de "en curso" y el calculo separado lo deja escribir un test
 *  sin montar React.
 *
 *  **`hecho` NO significa que el paso se pueda saltar.** Significa que no hay nada
 *  pendiente AHORA. Un paso con cero pendientes y el siguiente con diez es el caso
 *  normal, no un error.
 */
export function _estadoDe(paso: Paso, indice: number, pasos: Paso[]): EstadoPaso {
  if (paso.clave === 'digitalizar') {
    // El paso 1 esta "hecho" cuando ya se leyo algo. No hay forma de saber si
    // quedan archivos en la carpeta sin llamar a /scan/stats, y adivinar "hecho"
    // cuando quedan comprobantes sin leer seria la peor clase de mentira.
    return paso.total > 0 ? 'hecho' : 'sin_empezar';
  }

  const anterior = pasos[indice - 1];
  if (anterior && _estadoDe(anterior, indice - 1, pasos) === 'sin_empezar') {
    return 'bloqueado';
  }
  if (paso.clave === 'registrar') {
    return paso.pendientes === 0 ? 'hecho' : 'en_curso';
  }

  // Conciliar.
  //
  // EL BLOQUEO VA PRIMERO, Y ESO ES LO QUE ARREGLO UNA CAPTURA DE PANTALLA.
  //
  // La primera version hacia esto:
  //
  //     if (!registradas && paso.pendientes > 0) return 'bloqueado';
  //     return paso.pendientes === 0 ? 'hecho' : 'en_curso';
  //
  // Con 7 tickets sin confirmar y 0 sin cruzar, `pendientes` es 0, la primera
  // linea no dispara, y la segunda devuelve **`hecho`**: una palomita verde
  // sobre "Conciliar" cuando **no se ha conciliado nada y no se puede**. Los
  // tests de texto no lo cazaron porque buscaban la palomita DESPUES de la
  // palabra "Conciliar", y en el HTML la palomita va antes. Solo se vio al
  // mirar la pantalla.
  //
  // El orden correcto: si hay algo sin confirmar, el paso esta bloqueado
  // **pase lo que pase su propio contador**. Un `0 sin cruzar` con 7 sin confirmar
  // no es "conciliado", es "todavia no le toca".
  const registradas = pasos[1]?.pendientes === 0;
  if (!registradas) return 'bloqueado';
  return paso.pendientes === 0 ? 'hecho' : 'en_curso';
}