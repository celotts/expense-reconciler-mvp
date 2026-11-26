/** El veredicto de cada paso de la barra de flujo, sin React.
 *
 *  Vivia exportado dentro de `FlujoGuiado.tsx` para poder llamarlo desde el
 *  verificador sin montar la pantalla. Eso hacia que el modulo tuviera un export
 *  que no es componente y fast-refresh recargara la barra al editarlo. Aqui
 *  sigue siendo una funcion pura y testeable, pero el `.tsx` solo exporta el
 *  componente.
 */
import type { Page } from '../contextos/NavegacionContexto';

export type EstadoPaso = 'hecho' | 'en_curso' | 'sin_empezar' | 'bloqueado';

export interface Paso {
  clave: string;
  titulo: string;
  /** Que hace este paso, en una frase. Es lo que no se deduce del titulo. */
  queHace: string;
  pagina: Page;
  /** El numero de cosas que hay que hacer aqui. */
  pendientes: number;
  /** El total de cosas de este tipo, para el "3 de 40". */
  total: number;
  /** Que impide avanzar, si es que hay algo. */
  nota: string;
}

/** El estado visual de un paso, y sobre todo lo que NO implica.
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
