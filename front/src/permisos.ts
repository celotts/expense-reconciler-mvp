/** La unica lista de pantallas permitidas por rol.
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
 */
import type { Page } from './contextos/Navegacion';

export type RolDeEmpresa = 'owner' | 'admin' | 'accountant' | 'operator';

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
};

function esRolDeEmpresa(rol: string | null | undefined): rol is RolDeEmpresa {
  return rol === 'owner' || rol === 'admin' || rol === 'accountant' || rol === 'operator';
}

export function paginasPermitidas(rol: string | null | undefined): Page[] {
  if (!esRolDeEmpresa(rol)) return [];
  const permitidas = PAGINAS_POR_ROL[rol];
  return ORDEN_PAGINAS.filter((pagina) => permitidas.includes(pagina));
}

export function puedeVerPagina(rol: string | null | undefined, pagina: Page): boolean {
  return paginasPermitidas(rol).includes(pagina);
}
