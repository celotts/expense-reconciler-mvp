/** Contexto de navegación simple.
 *
 *  Mientras no hay hash router, usa el estado de App. Cuando llegue el router,
 *  solo hay que cambiar la implementación de `useNavegacion` aquí. */
import { createContext, useContext, useState, ReactNode } from 'react';

type Page = 'dashboard' | 'companies' | 'tickets' | 'review' | 'spotcheck' | 'bank' | 'reconciliations';

interface NavegacionContextValue {
  paginaActual: Page;
  irA: (pagina: Page) => void;
}

const NavegacionContext = createContext<NavegacionContextValue | null>(null);

export function NavegacionProvider({ children }: { children: ReactNode }) {
  const [paginaActual, setPaginaActual] = useState<Page>('companies');
  const irA = (pagina: Page) => setPaginaActual(pagina);
  return (
    <NavegacionContext.Provider value={{ paginaActual, irA }}>
      {children}
    </NavegacionContext.Provider>
  );
}

export function useNavegacion() {
  const ctx = useContext(NavegacionContext);
  if (!ctx) {
    throw new Error('useNavegacion debe usarse dentro de NavegacionProvider');
  }
  return ctx;
}