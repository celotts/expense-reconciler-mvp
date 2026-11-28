/** La empresa activa es UNA para toda la aplicacion, y este archivo lo mide.
 *
 *  Antes cada pantalla tenia su propio `useState(companyId)` y llamaba a
 *  `companiesApi.list()`: Escáner ofrecia "Todas las empresas", Tickets elegia
 *  la suya y Caja Chica otra. Con el mismo token, tres pantallas podian defender
 *  tres empresas distintas; el servidor respondia 409 y el operador no tenia
 *  forma de saber cual era la elegida.
 *
 *  Lo que aqui se comprueba no es que `tsc` pase. Es:
 *   1. el aviso se VE en HTML cuando no hay empresa, y NO se ve cuando hay una;
 *   2. la cabecera `X-Company-ID` la inyecta `fetchApi` DESPUES de los headers
 *      sueltos, para que una pantalla no pueda sobrescribirla;
 *   3. ninguna de las tres pantallas decide la empresa por su cuenta;
 *   4. el guardián de caja mira el rol POR EMPRESA, no el `perfil` del token.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { AvisoSinEmpresa, CartelEmpresaEnDigitalizacion } from '../src/contextos/Empresa';
import { EmpresaContext, type EmpresaContextValue } from '../src/contextos/EmpresaContexto';
import { CargaPorLote } from '../src/pages/Scan';

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

const VACIO: EmpresaContextValue = {
  empresas: [],
  empresaActiva: null,
  rol: null,
  permisos: [],
  cargando: false,
  necesitaEleccion: false,
  error: null,
  elegirEmpresa: () => undefined,
  recargar: () => undefined,
};

function pintar(valor: EmpresaContextValue): string {
  return renderToStaticMarkup(
    React.createElement(
      EmpresaContext.Provider,
      { value: valor },
      React.createElement(AvisoSinEmpresa, { mensaje: 'Elige una empresa activa para continuar.' }),
    ),
  );
}

console.log('La empresa activa se ve y se manda en un solo sitio:');

const sinEmpresa = pintar(VACIO);
comprobar(
  'sin empresa hay un aviso visible con role=alert',
  sinEmpresa.includes('role="alert"') && sinEmpresa.includes('Elige una empresa activa'),
  sinEmpresa,
);

const conEmpresa = pintar({
  ...VACIO,
  empresas: [{ company_id: 'c1', name: 'Una', tax_id: 'RFC1', estado: 'activo', motivo_baja: null, rol: 'admin', permisos: ['leer', 'crear'] }],
  empresaActiva: { company_id: 'c1', name: 'Una', tax_id: 'RFC1', estado: 'activo', motivo_baja: null, rol: 'admin', permisos: ['leer', 'crear'] },
  rol: 'admin',
});
comprobar('con empresa activa no se ensucia la pantalla con un aviso', !conEmpresa.includes('role="alert"'));

const anulada = pintar({
  ...VACIO,
  empresaActiva: {
    company_id: 'c1', name: 'Una', tax_id: 'RFC1', estado: 'inactivo',
    motivo_baja: 'Cierre del ejercicio', rol: 'admin', permisos: ['leer'],
  },
  rol: 'admin',
});
comprobar(
  'una empresa anulada dice que no se puede escanear ni procesar',
  anulada.includes('esta anulada') && anulada.includes('No se puede escanear ni procesar'),
  anulada,
);
comprobar('el aviso de anulada dice el motivo declarado', anulada.includes('Cierre del ejercicio'), anulada);

const sinMensaje = renderToStaticMarkup(
  React.createElement(
    EmpresaContext.Provider,
    { value: VACIO },
    React.createElement(AvisoSinEmpresa),
  ),
);
comprobar(
  'el aviso propio dice que active o cree una antes de procesar',
  sinMensaje.includes('Active o cree una') && sinMensaje.includes('procesar gastos'),
  sinMensaje,
);

const cargando = pintar({ ...VACIO, cargando: true });
comprobar('mientras carga no se dice que falta una empresa', !cargando.includes('role="alert"'));

const api = leer('src/services/api.ts');
const inyeccion = api.indexOf('...(sinToken ? {} : cabeceraDeEmpresa())');
const headersSueltos = api.indexOf('...(resto.headers');
comprobar('fetchApi inyecta la empresa', inyeccion > -1);
comprobar(
  'la empresa va DESPUES de los headers de la pantalla',
  inyeccion > headersSueltos && headersSueltos > -1,
  `headers=${headersSueltos} empresa=${inyeccion}`,
);
comprobar('existe una funcion que fija la empresa en un solo sitio', api.includes('export function fijarEmpresaActiva'));

for (const archivo of [
  'src/pages/CajaChica.tsx',
  'src/pages/Scan.tsx',
  'src/pages/Tickets.tsx',
  // El tablero, la cola y el muestreo tenian su propio `empresa` con la lista
  // vieja de empresas y mandaban un `?company_id=` distinto de la cabecera:
  // al elegir la segunda daban 409. La misma defensa que en las que escriben.
  'src/pages/Dashboard.tsx',
  'src/pages/ReviewQueue.tsx',
  'src/pages/SpotCheck.tsx',
]) {
  const texto = leer(archivo);
  comprobar(`${archivo} usa la empresa del contexto`, texto.includes('useEmpresa'));
  comprobar(`${archivo} muestra el aviso comun`, texto.includes('AvisoSinEmpresa'));
  comprobar(
    `${archivo} no decide la empresa por su cuenta`,
    !/companiesApi|setCompanyId|setSelectedCompany/.test(texto),
  );
}

const tablero = leer('src/pages/Dashboard.tsx');
comprobar(
  'el tablero no manda un company_id suelto que pelee con la cabecera',
  /dashboardApi\.get\(\)/.test(tablero),
);
comprobar(
  'el tablero muestra el id de la empresa dueña de las cifras',
  tablero.includes('ID de empresa: ${datos.company_id}'),
);
const inicioDashboard = api.indexOf('export const dashboardApi = {');
const bloqueDashboard = api.slice(
  inicioDashboard,
  api.indexOf('// Caja chica', inicioDashboard),
);
comprobar(
  'el cliente del tablero no manda company_id: manda la cabecera',
  inicioDashboard >= 0
    && !/params\.append\(['"]company_id['"]/.test(bloqueDashboard)
    && !/\?company_id=/.test(bloqueDashboard)
    && !/new URLSearchParams\(\{[^}]*company_id/.test(bloqueDashboard),
);
comprobar(
  'el selector del tablero llama al mismo contexto',
  /onChange=\{e => elegirEmpresa\(e\.target\.value\)\}/.test(tablero),
);

const contexto = leer('src/contextos/Empresa.tsx');
comprobar(
  'el cartel cambia de empresa por el mismo contexto, no por un company_id suelto',
  contexto.includes('onChange={e => elegirEmpresa(e.target.value)}')
    && contexto.includes('fijarEmpresaActiva(companyId)'),
);
comprobar(
  'al recargar no se borra la empresa guardada antes de cargar la lista',
  contexto.includes('useState(Boolean(userId))')
    && /if \(cargando\) return;\n    fijarEmpresaActiva\(empresaActiva\?\.company_id \?\? null\)/.test(contexto),
);

const inicioCatch = contexto.indexOf('.catch(');
const bloqueCatch = contexto.slice(
  inicioCatch,
  contexto.indexOf('.finally(', inicioCatch),
);
comprobar(
  'un fallo de red no borra la ultima lista de empresas',
  inicioCatch > -1 && !bloqueCatch.includes('setEmpresas('),
);

const app = leer('src/App.tsx');
comprobar('la aplicacion monta el proveedor de empresa', app.includes('<EmpresaProvider>'));
comprobar(
  'el menu y la guardia usan el rol por empresa',
  app.includes('paginasPermitidas(rol)') && app.includes('puedeVerPagina(rol, paginaActual)'),
);

const servicio = leer('src/services/cajaChica.ts');
comprobar(
  'el tipo de reposicion exige banco_nombre cuando el fondo es banco',
  /origen_fondo: 'banco'; banco_nombre: string/.test(servicio),
);
comprobar(
  'el tipo de reposicion prohíbe banco_nombre cuando el fondo es efectivo',
  /origen_fondo: 'efectivo'; banco_nombre\?: never/.test(servicio),
);

const caja = leer('src/pages/CajaChica.tsx');
const inicioBloque = caja.indexOf('if (movimiento.tipo === \'reposicion\' && movimiento.origen_fondo)');
const bloque = caja.slice(
  inicioBloque,
  caja.indexOf('if (movimiento.tipo === \'gasto_ticket\')', inicioBloque),
);
comprobar(
  'los datos de banco solo salen de una reposicion',
  bloque.includes('datos.banco_nombre') && bloque.includes('datos.referencia_bancaria'),
);

const tipos = leer('src/types/api.ts');
comprobar(
  'el estado de la empresa tiene tres valores, no un booleano',
  /type EstadoEmpresa = 'activo' \| 'inactivo' \| 'eliminado'/.test(tipos),
);
const contextoData = leer('src/contextos/EmpresaContexto.ts');
comprobar(
  'la lista de empresas del usuario lee el estado del servidor',
  contextoData.includes('estado: EstadoEmpresa'),
);

const apiEmpresas = api.slice(api.indexOf('export const companiesApi'), api.indexOf('// Tickets API'));
comprobar(
  'el listado por omision pide solo activas',
  apiEmpresas.includes(
    "incluirInactivas ? '/companies/?incluir_inactivas=true' : '/companies/'",
  ),
);
comprobar(
  'hay una via explicita para ver inactivas',
  apiEmpresas.includes('incluir_inactivas=true'),
);
comprobar('companiesApi.desactivar acepta el motivo', apiEmpresas.includes('desactivar: (id: string, motivo?: string)'));
for (const accion of ['activar', 'eliminar']) {
  comprobar(`companiesApi.${accion} existe`, apiEmpresas.includes(`${accion}: (id: string)`));
}
comprobar('no queda la ruta vieja de baja', !apiEmpresas.includes('/baja'));

const companiesPage = leer('src/pages/Companies.tsx');
comprobar(
  'la columna Estado sale del campo del servidor',
  companiesPage.includes("key: 'estado'") && companiesPage.includes('ETIQUETA_ESTADO[c.estado]'),
);
comprobar(
  'la plantilla muestra solo activas salvo que se pidan inactivas',
  companiesPage.includes('Mostrar inactivas') && companiesPage.includes('companiesApi.list(incluirInactivas)'),
);
comprobar(
  'anular, activar y eliminar son acciones distintas',
  companiesPage.includes('confirmarAnular') && companiesPage.includes('handleActivar')
    && companiesPage.includes('handleEliminar'),
);
comprobar(
  'el boton Anular sale del flag calculado por el servidor',
  companiesPage.includes('c.puede_eliminarse ?') && companiesPage.includes('>Anular<'),
);
comprobar(
  'el alta de una empresa anulada pide el motivo',
  companiesPage.includes('Motivo de la anulación') && companiesPage.includes('desactivar(anulando.id, motivoBaja.trim()'),
);
comprobar(
  'tras cambiar el estado se recarga la empresa del contexto',
  companiesPage.includes('recargar?.()'),
);

const scan = leer('src/pages/Scan.tsx');
const inicioCarga = scan.indexOf('Digitalizar desde esta máquina');
comprobar(
  'el form de digitalizar muestra el aviso de empresa no activa',
  inicioCarga > -1 && scan.indexOf('AvisoSinEmpresa', inicioCarga) > inicioCarga,
);

// Una corrida siempre se atribuye a una entidad fiscal. Con dos empresas el
// cartel tiene que seguir a la eleccion del contexto, no quedar congelado en la
// que habia al abrir la pantalla: se monta dos veces, con las dos elecciones.
const DOS = [
  { company_id: 'c1', name: 'Persona fisica', tax_id: 'RFC1', estado: 'activo' as const, motivo_baja: null, rol: 'admin', permisos: ['leer', 'crear'] },
  { company_id: 'c2', name: 'Persona moral', tax_id: 'RFC2', estado: 'activo' as const, motivo_baja: null, rol: 'admin', permisos: ['leer', 'crear'] },
];

function pintarCarga(
  empresaActiva: EmpresaContextValue['empresaActiva'],
  permisos: string[] = ['leer', 'crear'],
  empresas = DOS,
): string {
  return renderToStaticMarkup(
    React.createElement(
      EmpresaContext.Provider,
      { value: { ...VACIO, empresas, empresaActiva, rol: 'admin', permisos } },
      React.createElement(CargaPorLote),
    ),
  );
}

const enUna = pintarCarga(DOS[0]);
const enOtra = pintarCarga(DOS[1]);
comprobar('el form de digitalizar dice en que empresa se escribe', enUna.includes('Digitalizando en: Persona fisica'));
comprobar(
  'al cambiar la empresa el cartel sigue a la MISMA decision',
  !enUna.includes('Digitalizando en: Persona moral') && enOtra.includes('Digitalizando en: Persona moral'),
);
comprobar(
  'con varias empresas el cambio esta junto al boton de digitalizar',
  enUna.includes('aria-label="Empresa para digitalizar"')
    && enUna.includes('value="c2"')
    && enUna.includes('>Persona moral</option>'),
);
comprobar(
  'el cartel va antes de los botones de digitalizar',
  enUna.indexOf('Digitalizando en:') > -1 && enUna.indexOf('Digitalizando en:') < enUna.indexOf('Digitalizar 0'),
);
comprobar(
  'el cartel dice que una sucursal comparte el RFC',
  enUna.includes('Una sucursal no es otra empresa') && enUna.includes('RFC'),
);
comprobar('sin empresa no se pinta un cartel que contradiga al aviso', !pintarCarga(null).includes('Digitalizando en:'));
comprobar(
  'el cartel se pinta en las dos puertas del escaner',
  (scan.match(/<CartelEmpresaEnDigitalizacion \/>/g) ?? []).length === 2,
);

const cartelSuelto = renderToStaticMarkup(
  React.createElement(
    EmpresaContext.Provider,
    { value: { ...VACIO, empresas: DOS, empresaActiva: DOS[0], rol: 'admin' } },
    React.createElement(CartelEmpresaEnDigitalizacion),
  ),
);
comprobar(
  'el cartel suelto muestra la entidad fiscal, no una sucursal',
  cartelSuelto.includes('Digitalizando en: Persona fisica') && cartelSuelto.includes('RFC1'),
);

// Con datos, la empresa se ANULA, no se elimina. Anulada solo consulta: ni se
// ofrece en el selector ni quedan botones que inviten a cargar facturas, aunque
// el front traiga una lista de permisos vieja (el servidor tambien daria 403).
const ANULADA = {
  company_id: 'c9', name: 'Anulada', tax_id: 'RFC9', estado: 'inactivo' as const,
  motivo_baja: 'Cierre del ejercicio', rol: 'admin', permisos: ['leer'],
};
const conAnulada = pintarCarga(ANULADA);
comprobar(
  'una empresa anulada no se ofrece en el cartel de digitalizacion',
  !conAnulada.includes('Digitalizando en:'),
);
comprobar(
  'con empresa anulada el boton de digitalizar no invita a cargar facturas',
  conAnulada.includes('La empresa elegida no esta activa: solo se pueden consultar sus datos'),
);
comprobar(
  'con empresa anulada tampoco se ofrece la ruta del servidor',
  conAnulada.includes('no se pueden cargar facturas'),
);
const conAnuladaEnLista = pintarCarga(DOS[0], ['leer', 'crear'], [...DOS, ANULADA]);
comprobar(
  'la empresa anulada no es opcion del selector',
  !conAnuladaEnLista.includes('>Anulada</option>') && conAnuladaEnLista.includes('>Persona moral</option>'),
);

if (fallos) {
  console.error(`\n${fallos} comprobacion(es) fallida(s).`);
  process.exitCode = 1;
} else {
  console.log('\nTodas las comprobaciones de empresa activa pasaron.');
}
