/** La fecha de un gasto no se puede correr un dia, y este archivo lo mide.
 *
 *  MEDIDO el 2026-10-12: un movimiento bancario creado con
 *  `transaction_date = "2026-10-01"` se mostraba como `30/9/2026` en pantalla.
 *
 *  ## POR QUE PASA
 *
 *  `new Date('2026-10-01')` se interpreta como **medianoche UTC**. Al
 *  renderizarlo con `toLocaleDateString('es-MX')` se convierte a la zona horaria
 *  **local**, y en cualquier zona detras de UTC —Mexico es UTC-6— eso convierte
 *  el 1 de octubre en 30 de septiembre.
 *
 *  En un tablero eso es cosmético. En una conciliacion NO: la fecha es la que
 *  cruza el gasto con el movimiento del banco, y en el cierre mensual es la que
 *  decide a que periodo pertenece el gasto. Un gasto del dia 1 pintado como dia
 *  30 manda a buscar el papel en el mes equivocado.
 *
 *  ## POR QUE ESTE ARCHIVO EXISTE CUANDO YA HABIA UNA DEFENSA
 *
 *  El bug ya estaba escrito y corregido en `components/ScanProgreso.tsx`, con su
 *  comentario al lado. Y seguia en `Tickets.tsx`, `BankTransactions.tsx` y
 *  `Reconciliations.tsx`.
 *
 *  Eso es lo que hace esta seccion: **una defensa aplicada en un archivo no
 *  protege el proyecto.** `new Date(x).toLocaleDateString()` es lo que todo el
 *  mundo escribe, asi que la siguiente pantalla escribe la version vieja porque
 *  nadie se acuerda de la excepcion. Por eso la funcion vive en `utils/format.ts`
 *  y hay un test que falla si alguien vuelve a la forma anterior en CUALQUIER
 *  pantalla.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { fecha, fechaHora } from '../src/utils/format';

let fallos = 0;

function comprobar(nombre: string, condicion: boolean, porQue: string): void {
  if (condicion) {
    console.log(`  ok   ${nombre}`);
  } else {
    fallos++;
    console.error(`  FALLA ${nombre} — ${porQue}`);
  }
}

/* ## 1. LA FECHA NO SE CORRE.
 *
 * Estos casos se comprueban con la zona horaria del proceso fijada a una de las
 * que Detras de UTC, que es donde el bug se manifestaba. Sin fijarla, el test
 * pasaria en una maquina en UTC y fallaria en la de un usuario, que es al reves
 * de como tiene que funcionar un test de este tipo. */
console.log('\nLA FECHA NO SE CORRE UN DIA:');

const zonaOriginal = process.env.TZ;
for (const zona of ['America/Mexico_City', 'America/Chicago', 'America/New_York']) {
  process.env.TZ = zona;

  // El caso del incidente medido: 1 de octubre, mostrado como 30 de septiembre.
  comprobar(
    `2026-10-01 en ${zona}`,
    fecha('2026-10-01').includes('01') && !fecha('2026-10-01').includes('30'),
    `salio ${fecha('2026-10-01')}: la fecha se corrio un dia`,
  );

  // El primer dia del mes, que es donde el corrimiento es mas visible.
  comprobar(
    `2026-10-01 (primer dia) en ${zona}`,
    fecha('2026-10-01').includes('01'),
    `salio ${fecha('2026-10-01')}`,
  );

  // Un dia de springs, que es donde el corrimiento cruza de mes.
  comprobar(
    `2026-03-01 en ${zona}`,
    fecha('2026-03-01').includes('01') && !fecha('2026-03-01').includes('28'),
    `salio ${fecha('2026-03-01')}`,
  );
}
process.env.TZ = zonaOriginal;

/* ## 2. LO QUE NO HAY QUE ROMPER AL ARREGLARLO. */
console.log('\nLO QUE NO HAY QUE ROMPER:');

comprobar('null da guion', fecha(null) === '—', 'un dato ausente no es una fecha invalida');
comprobar('undefined da guion', fecha(undefined) === '—', 'idem');
comprobar('cadena vacia da guion', fecha('') === '—', 'idem');
comprobar('basura no lanza', fecha('no-es-fecha') === '—', 'una basura no debe romper la tabla');

comprobar(
  'un timestamp completo se parte igual',
  fecha('2026-10-01T13:45:00Z').includes('01'),
  `salio ${fecha('2026-10-01T13:45:00Z')}`,
);

/* ## 3. LA FECHA-HORA ES OTRA COSA, Y POR QUE. */
console.log('\nLA FECHA-HORA ES OTRA FUNCION, A PROPOSITO:');

comprobar('fechaHora con timestamp', fechaHora('2026-10-01T13:45:00Z').includes('01'),
  `salio ${fechaHora('2026-10-01T13:45:00Z')}`);
comprobar('fechaHora null da guion', fechaHora(null) === '—', 'idem');
comprobar('fechaHora basura no lanza', fechaHora('basura') === '—', 'una basura no rompe');

/* ## 4. NINGUNA PANTALLA VUELVE A LA FORMA VIEJA.
 *
 * Es la parte que mas importa y la que mas veces se ha-equivocado el equipo: la
 * funcion `fecha()` esta bien, y las tres pantallas laoduenden… no. Por eso se
 * LEE el codigo de las tres y se falla si aparece la comparacion cruda. */
console.log('\nNINGUNA PANTALLA USA LA FORMA QUE CORRE LA FECHA:');

const PANTALLAS = ['Tickets.tsx', 'BankTransactions.tsx', 'Reconciliations.tsx'];
for (const nombre of PANTALLAS) {
  let fuente = '';
  try {
    fuente = readFileSync(resolve(process.cwd(), 'src/pages', nombre), 'utf-8');
  } catch (e) {
    throw new Error(`no se pudo leer ${nombre}: ${String(e)}`);
  }

  const usaFecha = /import\s*\{[^}]*\bfecha\b[^}]*\}\s*from\s*'\.\.\/utils\/format'/.test(fuente);
  comprobar(`${nombre} importa fecha de utils/format`, usaFecha,
    'no usa la funcion compartida');

  // El patron prohibido: pasar la cadena por `Date` y formatear.
  const crudo = /new Date\(\s*\w+\.\s*(expense_date|transaction_date)/.test(fuente);
  comprobar(`${nombre} no pasa la fecha por new Date`, !crudo,
    'volvio el parseo en UTC que corre la fecha un dia');
}

/* ## 5. Y QUE ALGUIEN LO SIGA COMPRANDO. */
if (fallos > 0) {
  console.error(`\n${fallos} comprobacion(es) fallaron.`);
  process.exit(1);
}
console.log('\nTodas las comprobaciones pasaron.');