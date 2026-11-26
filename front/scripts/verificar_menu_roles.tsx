/** El orden del menu y la visibilidad por rol, medidos donde se ven.
 *
 *  `tsc` pasaba con el menu desordenado y con el perfil decidiendo acceso,
 *  asi que compilar no es la prueba. Aqui se compara la matriz y se lee el
 *  codigo que decide el menu y la guardia de ruta. La etiqueta del rol ya
 *  existe en el pie de la barra lateral y no se duplica aqui.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  ORDEN_PAGINAS,
  PAGINAS_POR_ROL,
  paginasPermitidas,
  puedeVerPagina,
} from '../src/permisos';

const leer = (ruta: string) => readFileSync(resolve(process.cwd(), ruta), 'utf8');

let fallos = 0;
function comprobar(nombre: string, condicion: boolean, detalle = ''): void {
  if (condicion) {
    console.log(`    ok   ${nombre}`);
  } else {
    fallos += 1;
    console.error(`    FAIL ${nombre}${detalle ? `: ${detalle}` : ''}`);
  }
}

function mismoContenido(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((valor, indice) => valor === b[indice]);
}

console.log('Menu ordenado por flujo de trabajo y visibilidad por rol de empresa:');

const ordenEsperado = [
  'dashboard', 'scan', 'tickets', 'review', 'spotcheck',
  'bank', 'reconciliations', 'cajachica', 'cierre', 'companies', 'auditoria', 'mantenimiento',
];
comprobar(
  'el orden sigue Digitalizar -> Registrar -> Conciliar',
  mismoContenido(ORDEN_PAGINAS, ordenEsperado),
  ORDEN_PAGINAS.join(','),
);

comprobar(
  'owner y admin ven todo',
  mismoContenido(PAGINAS_POR_ROL.owner, ordenEsperado)
    && mismoContenido(PAGINAS_POR_ROL.admin, ordenEsperado),
);
comprobar(
  'accountant no ve Escaner ni Caja chica ni Empresas',
  !PAGINAS_POR_ROL.accountant.includes('scan')
    && !PAGINAS_POR_ROL.accountant.includes('cajachica')
    && !PAGINAS_POR_ROL.accountant.includes('companies')
    && PAGINAS_POR_ROL.accountant.includes('auditoria'),
);
comprobar(
  'Mantenimiento solo lo ve owner o admin',
  PAGINAS_POR_ROL.owner.includes('mantenimiento')
    && PAGINAS_POR_ROL.admin.includes('mantenimiento')
    && !PAGINAS_POR_ROL.accountant.includes('mantenimiento')
    && !PAGINAS_POR_ROL.operator.includes('mantenimiento')
    && puedeVerPagina('accountant', 'mantenimiento') === false,
);
comprobar(
  'operator solo ve Inicio, Escaner, Tickets y Caja chica',
  mismoContenido(PAGINAS_POR_ROL.operator, [
    'dashboard', 'scan', 'tickets', 'cajachica',
  ]),
  PAGINAS_POR_ROL.operator.join(','),
);
comprobar(
  'sin empresa no se inventa un rol',
  paginasPermitidas(null).length === 0 && !puedeVerPagina(undefined, 'auditoria'),
);
comprobar(
  'un rol desconocido no hereda permisos de admin',
  paginasPermitidas('administrador').length === 0,
);

const app = leer('src/App.tsx');
const inicioNav = app.indexOf('const navigation');
const bloqueNav = app.slice(inicioNav, app.indexOf('function ClipboardCheckIcon', inicioNav));
const etiquetas = [...bloqueNav.matchAll(/label: '([^']+)'/g)].map((m) => m[1]);
comprobar(
  'la lista pintada esta en el mismo orden que la matriz',
  mismoContenido(etiquetas, [
    'Inicio', 'Escáner', 'Tickets', 'Cola de revisión', 'Muestreo',
    'Banco', 'Conciliación', 'Caja chica', 'Cierre mensual', 'Empresas', 'Auditoría', 'Mantenimiento',
  ]),
  etiquetas.join(','),
);
comprobar(
  'el menu sale del rol por empresa, no del perfil',
  app.includes('paginasPermitidas(rol)') && !app.includes('item.roles')
    && !app.includes('perfil ? item.roles'),
);
comprobar(
  'la guardia alcanza la pagina escrita a mano',
  app.includes('!puedeVerPagina(rol, paginaActual)') && app.includes("irA('dashboard')"),
);
comprobar('no queda el guardia viejo de caja suelto', !app.includes('rolPuedeCaja'));
comprobar(
  'la pantalla de Mantenimiento esta montada y protegida por la guardia comun',
  app.includes("case 'mantenimiento': return <Mantenimiento />"),
);
comprobar(
  'la etiqueta existente del rol no se pierde',
  app.includes('Rol en esta empresa: ${rol}'),
);

if (fallos) {
  console.error(`\n${fallos} comprobacion(es) fallida(s).`);
  process.exitCode = 1;
} else {
  console.log('\nTodas las comprobaciones de menu por rol pasaron.');
}
