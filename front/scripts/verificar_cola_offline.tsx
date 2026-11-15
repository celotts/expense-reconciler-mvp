/**
 * Verificador del aviso de gastos offline.
 *
 * Lo que se comprueba es el HTML que se pinta, no el retorno de funciones:
 *   1. Sin pendientes, no se pinta NADA.
 *   2. Con pendientes: dice cuantos son, el proveedor de cada uno, y los
 *      botones "Reintentar ahora" y "Descartar".
 *   3. Sin conexión (no se puede saber en node), el botón dice "Sin conexión"
 *      solo si `navigator.onLine` fuera falso — aqui node no define
 *      `navigator`, asi que cuenta como en linea.
 *
 * Corre con `npm run verify:cola` en front/.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { AvisoGastosPendientes } from '../src/components/AvisoGastosPendientes';
import type { GastoPendiente } from '../src/services/colaOffline';

const SEED: GastoPendiente[] = [
  {
    id: 'p1',
    payload: {
      company_id: 'c1',
      provider_name: 'OXXO',
      total_amount: '65.00',
      expense_date: '2026-11-13',
    },
    creadoEn: '2026-11-13T10:00:00.000Z',
    intentos: 0,
    ultimoError: null,
  },
  {
    id: 'p2',
    payload: {
      company_id: 'c1',
      provider_name: 'PANADERIA',
      total_amount: '250.00',
      expense_date: '2026-11-13',
    },
    creadoEn: '2026-11-13T10:05:00.000Z',
    intentos: 1,
    ultimoError: 'No se pudo contactar',
  },
];

let fallos = 0;

function comprobar(nombre: string, condicion: boolean) {
  if (condicion) {
    console.log(`  ok  ${nombre}`);
  } else {
    fallos += 1;
    console.error(`  FAIL ${nombre}`);
  }
}

// 1. Sin pendientes no se pinta nada.
const nada = renderToStaticMarkup(<AvisoGastosPendientes pendientesIniciales={[]} />);
console.log('1. sin pendientes:');
comprobar('no pinta banner', nada === '');

// 2 y 3. Con pendientes: el HTML dice la verdad.
const html = renderToStaticMarkup(<AvisoGastosPendientes pendientesIniciales={SEED} />);
console.log('2. con pendientes:');
comprobar('dice cuantos son', html.includes('2 gastos sin enviar'));
comprobar('muestra OXXO', html.includes('OXXO'));
comprobar('muestra PANADERIA', html.includes('PANADERIA'));
comprobar('muestra los montos', html.includes('65.00') && html.includes('250.00'));
comprobar('boton Reintentar ahora', html.includes('Reintentar ahora'));
comprobar('un Descartar por item', (html.match(/>Descartar</g) || []).length === 2);
comprobar('role=status para lectores de pantalla', html.includes('role="status"'));

if (fallos > 0) {
  console.error(`\n${fallos} comprobacion(es) fallaron`);
  process.exit(1);
}
console.log('\ncola offline: verificado');
process.exit(0);
