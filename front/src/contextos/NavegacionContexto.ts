/** La pagina actual, el filtro y el contexto que los reparte.
 *
 *  Antes vivian en `Navegacion.tsx` junto al provider. Type-only exports no
 *  rompen fast-refresh, pero `useNavegacion` si: al editar el provider se
 *  recargaba el modulo y cualquier pantalla con el hook en uso se remontaba.
 *  El provider importa de aqui; nadie mas importa del `.tsx`.
 */
import { createContext, useContext } from 'react';

/** Las pantallas. Se exporta el tipo para que un boton que navegue se compile
 *  solo contra esta lista: con `any` en el parametro, escribir `irA('revisin')`
 *  --con una falta-- no da error de tipos, navega a una pantalla que no existe y
 *  la aplicacion cae en el `default` del switch. */
export type Page =
  | 'dashboard'
  | 'companies'
  | 'tickets'
  | 'scan'
  | 'review'
  | 'spotcheck'
  | 'bank'
  | 'reconciliations'
  | 'cierre'
  | 'cajachica'
  | 'auditoria'
  | 'mantenimiento';

/** Un filtro que la pantalla de destino puede recibir.
 *
 *  Existe para el camino del tablero a la cola: la barra gris de "sin
 *  clasificar" es accionable y al pulsar lleva a Tickets ya filtrado. Sin esto,
 *  el clic lleva a la lista completa y quien llega tiene que volver a aplicar el
 *  filtro a mano, y entonces la barra deja de ser un atajo. */
export type FiltroTickets = 'todos' | 'sinClasificar' | 'clasificados';

interface NavegacionContextValue {
  paginaActual: Page;
  /** El segundo parametro es opcional a proposito: casi ningun `irA` lo usa, y
   *  hacerlo obligatorio obligaria a escribir `irA('dashboard', undefined)` en
   *  todas partes. */
  irA: (pagina: Page, filtro?: FiltroTickets) => void;
  /** El filtro con el que se pidio abrir la pantalla actual, o `null`. */
  filtroTickets: FiltroTickets | null;
}

export const NavegacionContext = createContext<NavegacionContextValue | null>(null);

export function useNavegacion() {
  const ctx = useContext(NavegacionContext);
  if (!ctx) {
    throw new Error('useNavegacion debe usarse dentro de NavegacionProvider');
  }
  return ctx;
}
