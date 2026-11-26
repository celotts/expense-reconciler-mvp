/** Mantenimiento medido en HTML, no en el retorno de funciones.
 *
 *  `tsc` pasa con un botón que no muestra nada, y con una contraseña que no se
 *  envía. Aqui se monta la pantalla con datos fijos y se lee lo renderizado,
 *  además de comprobar el orden de inyección de las cabeceras en `api.ts`.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';

import { EmpresaContext, type MembresiaEmpresa } from '../src/contextos/Empresa';
import { Mantenimiento } from '../src/pages/Mantenimiento';
import {
  ORDEN_PAGINAS, PAGINAS_POR_ROL, puedeVerPagina,
} from '../src/permisos';
import type { MembresiaUsuario, Usuario } from '../src/types/api';

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

const ahora = '2026-11-25T12:00:00Z';
const usuarios: Usuario[] = [
  {
    id: '11111111-1111-4111-8111-111111111111',
    email: 'ana@example.com',
    nombre: 'Ana Admin',
    is_active: true,
    created_at: ahora,
    last_login_at: ahora,
    perfil: 'admin',
    grupo: 'operaciones',
  },
  {
    id: '22222222-2222-4222-8222-222222222222',
    email: 'luis@example.com',
    nombre: 'Luis Operador',
    is_active: true,
    created_at: ahora,
    last_login_at: null,
    perfil: 'digitalizador',
    grupo: '',
  },
];

const membresias: MembresiaUsuario[] = [
  {
    company_id: '33333333-3333-4333-8333-333333333333',
    user_id: usuarios[0].id,
    email: usuarios[0].email,
    nombre: usuarios[0].nombre,
    rol: 'admin',
    activo: true,
  },
  {
    company_id: '33333333-3333-4333-8333-333333333333',
    user_id: usuarios[1].id,
    email: usuarios[1].email,
    nombre: usuarios[1].nombre,
    rol: 'operator',
    activo: true,
  },
];

const empresa: MembresiaEmpresa = {
  company_id: '33333333-3333-4333-8333-333333333333',
  name: 'Comercial Ejemplo',
  tax_id: 'EJE010101AAA',
  rol: 'admin',
};

const contexto = {
  empresas: [empresa],
  empresaActiva: empresa,
  rol: 'admin',
  cargando: false,
  necesitaEleccion: false,
  error: null,
  elegirEmpresa: () => undefined,
};

function montar(props: Record<string, unknown> = {}): string {
  return renderToStaticMarkup(
    <EmpresaContext.Provider value={contexto}>
      <Mantenimiento
        usuariosIniciales={usuarios}
        membresiasIniciales={membresias}
        {...props}
      />
    </EmpresaContext.Provider>,
  );
}

console.log('Pantalla de Mantenimiento y firma del administrador:');

comprobar(
  'Mantenimiento esta al final del flujo y solo lo ven owner y admin',
  ORDEN_PAGINAS[ORDEN_PAGINAS.length - 1] === 'mantenimiento'
    && PAGINAS_POR_ROL.owner.includes('mantenimiento')
    && PAGINAS_POR_ROL.admin.includes('mantenimiento')
    && !PAGINAS_POR_ROL.accountant.includes('mantenimiento')
    && !puedeVerPagina('operator', 'mantenimiento'),
);

const htmlUsuarios = montar();
comprobar('el titulo y las dos pestañas salen en pantalla',
  htmlUsuarios.includes('Mantenimiento')
    && htmlUsuarios.includes('Usuarios')
    && htmlUsuarios.includes('Roles por empresa'));
comprobar('la firma se pide sin guardarla',
  htmlUsuarios.includes('Contrasena del administrador')
    && htmlUsuarios.includes('type="password"')
    && htmlUsuarios.includes('No se guarda'));
comprobar('las cuentas salen con acciones reales',
  htmlUsuarios.includes('ana@example.com')
    && htmlUsuarios.includes('Nueva cuenta')
    && htmlUsuarios.includes('Editar')
    && htmlUsuarios.includes('Resetear clave'));
comprobar('no se inventa una etiqueta de rol nueva en la pantalla',
  !htmlUsuarios.includes('Rol en esta empresa:'));

const htmlRoles = montar({ pestanaInicial: 'roles' });
comprobar('la pestaña de roles muestra la empresa activa y sus accesos',
  htmlRoles.includes('Comercial Ejemplo')
    && htmlRoles.includes('operator')
    && htmlRoles.includes('Asignar rol')
    && htmlRoles.includes('Dar de baja'));
comprobar('el aviso de empresa no sale cuando ya hay una elegida',
  !htmlRoles.includes('Elige una empresa activa'));

const htmlReset = montar({
  modalInicial: 'reset',
  seleccionadoInicial: usuarios[1],
});
comprobar('el reset pide la clave nueva y explica quien firma',
  htmlReset.includes('Resetear clave de luis@example.com')
    && htmlReset.includes('Contrasena nueva')
    && htmlReset.includes('No se necesita la clave de la otra cuenta'));

// Se mide dentro de `fetchApi`: fuera de esa funcion hay menciones en
// comentarios y en el guardado de la empresa que no deciden ninguna cabecera.
const api = leer('src/services/api.ts');
const bloque = api.slice(api.indexOf('export async function fetchApi'));
const iFirma = bloque.indexOf("'X-Contrasena-Actual': contrasenaActual");
const iEmpresa = bloque.indexOf('cabeceraDeEmpresa()');
const iHeaders = bloque.indexOf('...(resto.headers');
comprobar(
  'la firma se inyecta DESPUES de los headers sueltos y antes de la empresa',
  iFirma > iHeaders && iFirma < iEmpresa && iFirma >= 0,
  `${iHeaders} < ${iFirma} < ${iEmpresa}`,
);
comprobar('la contrasena no se guarda en el cliente',
  api.includes('contrasenaActual?: string')
    && !api.includes("localStorage.setItem('contrasena"));

const servicio = leer('src/services/usuarios.ts');
comprobar('el reset usa la ruta firmada, no el cambio propio',
  servicio.includes('/contrasena-admin')
    && servicio.includes('contrasenaActual: firma')
    && (servicio.match(/contrasenaActual: firma/g) ?? []).length >= 5);
comprobar('las membresias nunca llevan company_id en el cuerpo',
  servicio.includes('/usuarios/membresias')
    && !servicio.includes('company_id:'));

const app = leer('src/App.tsx');
comprobar('la ruta de Mantenimiento pasa por la guardia comun',
  app.includes("case 'mantenimiento': return <Mantenimiento />")
    && app.includes('!puedeVerPagina(rol, paginaActual)'));

if (fallos) {
  console.error(`\n${fallos} comprobacion(es) fallida(s).`);
  process.exitCode = 1;
} else {
  console.log('\nTodas las comprobaciones de Mantenimiento pasaron.');
}
