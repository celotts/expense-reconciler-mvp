/** Contexto de navegación simple.
 *
 *  Mientras no hay hash router, usa el estado de App. Cuando llegue el router,
 *  solo hay que cambiar la implementación de `useNavegacion` en
 *  `NavegacionContexto.ts`, que es donde viven el tipo `Page`, el contexto y el
 *  hook. Este archivo solo monta el proveedor: asi fast-refresh puede recargar
 *  el proveedor sin desmontar cada pantalla que lee la pagina actual.
 */
import { useState, ReactNode } from 'react';
import {
  NavegacionContext, type FiltroTickets, type Page,
} from './NavegacionContexto';

export function NavegacionProvider({ children }: { children: ReactNode }) {
  // Empieza en la pagina del hash si esta presente, para que un refresco
  // (F5) preserve la ultima vista y la experiencia parezca un sitio real.
  const paginaInicial = (window.location.hash.replace(/^#/, '') || 'dashboard') as Page;
  const JOBS: Page[] = ['dashboard','companies','tickets','scan','review','spotcheck','bank','reconciliations','cierre','cajachica','auditoria','mantenimiento'];
  const [paginaActual, setPaginaActual] = useState<Page>(JOBS.includes(paginaInicial) ? paginaInicial : 'dashboard');
  const [filtroTickets, setFiltroTickets] = useState<FiltroTickets | null>(null);

  const irA = (pagina: Page, filtro?: FiltroTickets) => {
    setPaginaActual(pagina);
    setFiltroTickets(filtro ?? null);
    window.location.hash = pagina;
  };
  return (
    <NavegacionContext.Provider value={{ paginaActual, irA, filtroTickets }}>
      {children}
    </NavegacionContext.Provider>
  );
}
