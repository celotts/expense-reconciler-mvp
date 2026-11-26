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

import { AvisoSinEmpresa, EmpresaContext, type EmpresaContextValue } from '../src/contextos/Empresa';

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
  cargando: false,
  necesitaEleccion: false,
  error: null,
  elegirEmpresa: () => undefined,
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
  empresas: [{ company_id: 'c1', name: 'Una', tax_id: 'RFC1', rol: 'admin' }],
  empresaActiva: { company_id: 'c1', name: 'Una', tax_id: 'RFC1', rol: 'admin' },
  rol: 'admin',
});
comprobar('con empresa activa no se ensucia la pantalla con un aviso', !conEmpresa.includes('role="alert"'));

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
]) {
  const texto = leer(archivo);
  comprobar(`${archivo} usa la empresa del contexto`, texto.includes('useEmpresa'));
  comprobar(`${archivo} muestra el aviso comun`, texto.includes('AvisoSinEmpresa'));
  comprobar(
    `${archivo} no decide la empresa por su cuenta`,
    !/companiesApi|setCompanyId|setSelectedCompany/.test(texto),
  );
}

const contexto = leer('src/contextos/Empresa.tsx');
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

if (fallos) {
  console.error(`\n${fallos} comprobacion(es) fallida(s).`);
  process.exitCode = 1;
} else {
  console.log('\nTodas las comprobaciones de empresa activa pasaron.');
}
