/** La unica matriz de pantallas y acciones permitidas por rol de empresa.
 *
 *  Antes el menu se filtraba con `users.perfil`, un texto libre que no sabe
 *  en que empresa vale. El servicio ya resolvia el rol desde
 *  `usuarios_empresas` en cada peticion, asi que el front podia ofrecer una
 *  pantalla que el backend despues rechazaba con 403. Ahora la misma lista
 *  decide que se ve y hacia donde puede ir el hash.
 *
 *  El `perfil` sigue siendo una ETIQUETA del usuario. No autoriza nada: dos
 *  cuentas con el mismo perfil pueden tener roles distintos en empresas
 *  distintas, y un cambio de rol surte efecto sin volver a hacer login.
 *
 *  Esto es comodidad, no autoridad. El servidor devuelve los permisos en
 *  `/auth/empresas` y vuelve a comprobarlos en cada escritura (`guardia_de_
 *  permisos`). Un front desactualizado puede esconder de mas; nunca puede
 *  conceder una accion que el backend va a rechazar.
 */
import type { Page } from './contextos/NavegacionContexto';

export type RolDeEmpresa = 'owner' | 'admin' | 'accountant' | 'operator' | 'viewer';

export type PermisoDeEmpresa =
  | 'leer'
  | 'crear'
  | 'editar'
  | 'borrar'
  | 'aprobar'
  | 'conciliar'
  | 'exportar'
  | 'cerrar'
  | 'administrar'
  | 'auditar';

export const TODOS_LOS_PERMISOS: readonly PermisoDeEmpresa[] = [
  'leer', 'crear', 'editar', 'borrar', 'aprobar', 'conciliar',
  'exportar', 'cerrar', 'administrar', 'auditar',
];

/** El orden aprobado: sigue el flujo Digitalizar -> Registrar -> Conciliar.
 *
 *  Estaba desordenado: Auditoria aparecia en segundo lugar y Empresas antes
 *  que Tickets. Un menu no es una lista de modulos, es el recorrido de trabajo,
 *  y el orden tiene que contar esa historia.
 */
export const ORDEN_PAGINAS: readonly Page[] = [
  'dashboard',
  'scan',
  'tickets',
  'review',
  'spotcheck',
  'bank',
  'reconciliations',
  'cajachica',
  'cierre',
  'companies',
  'auditoria',
  'mantenimiento',
];

const TODAS: readonly Page[] = ORDEN_PAGINAS;
const SOLO_CONSULTA: readonly Page[] = [
  'dashboard', 'tickets', 'review', 'spotcheck', 'bank', 'reconciliations', 'cierre', 'auditoria',
];

export const PAGINAS_POR_ROL: Record<RolDeEmpresa, readonly Page[]> = {
  owner: TODAS,
  admin: TODAS,
  accountant: [
    'dashboard',
    'tickets',
    'review',
    'spotcheck',
    'bank',
    'reconciliations',
    'cierre',
    'auditoria',
  ],
  operator: ['dashboard', 'scan', 'tickets', 'cajachica'],
  // Puede mirar, no puede digitalizar ni abrir caja ni administrar. Las
  // pantallas de revision y conciliacion se ven SIN botones de aprobar,
  // ejecutar ni descargar: ver el resultado no es firmarlo.
  viewer: SOLO_CONSULTA,
};

/** Espejo del mapa de `app/core/permisos.py`, en el mismo orden.
 *
 *  Se usa como respaldo si el API no trae `permisos` (clientes antiguos). Si el
 *  backend anade una accion nueva, el front la oculta hasta que se actualice:
 *  ese es el lado seguro de la divergencia.
 */
export const PERMISOS_POR_ROL: Record<RolDeEmpresa, readonly PermisoDeEmpresa[]> = {
  owner: TODOS_LOS_PERMISOS,
  admin: TODOS_LOS_PERMISOS,
  accountant: TODOS_LOS_PERMISOS.filter((p) => p !== 'administrar'),
  operator: ['leer', 'crear', 'editar', 'cerrar'],
  viewer: ['leer'],
};

function esRolDeEmpresa(rol: string | null | undefined): rol is RolDeEmpresa {
  return rol === 'owner' || rol === 'admin' || rol === 'accountant'
    || rol === 'operator' || rol === 'viewer';
}

export function paginasPermitidas(rol: string | null | undefined): Page[] {
  if (!esRolDeEmpresa(rol)) return [];
  const permitidas = PAGINAS_POR_ROL[rol];
  return ORDEN_PAGINAS.filter((pagina) => permitidas.includes(pagina));
}

export function puedeVerPagina(rol: string | null | undefined, pagina: Page): boolean {
  return paginasPermitidas(rol).includes(pagina);
}

export function permisosDeRol(rol: string | null | undefined): PermisoDeEmpresa[] {
  if (!esRolDeEmpresa(rol)) return [];
  return [...PERMISOS_POR_ROL[rol]];
}

/** Un rol que no entendemos NO recibe permisos. Nunca el beneficio de la duda. */
export function rolPuede(rol: string | null | undefined, permiso: PermisoDeEmpresa): boolean {
  return permisosDeRol(rol).includes(permiso);
}
