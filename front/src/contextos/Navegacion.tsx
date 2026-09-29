/** Contexto de navegación simple.
 *
 *  Mientras no hay hash router, usa el estado de App. Cuando llegue el router,
 *  solo hay que cambiar la implementación de `useNavegacion` aquí. */
import { createContext, useContext, useState, ReactNode } from 'react';

/** Las pantallas. Se exporta el tipo para que un boton que navegue se compile
 *  solo contra esta lista: con `any` en el parametro, escribir `irA('revisin')`
 *  --con una falta-- no da error de tipos, navega a una pantalla que no existe y
 *  la aplicacion cae en el `default` del switch. */
export type Page =
  | 'dashboard'
  | 'companies'
  | 'tickets'
  | 'review'
  | 'spotcheck'
  | 'bank'
  | 'reconciliations';

interface NavegacionContextValue {
  paginaActual: Page;
  /** El segundo parametro es opcional a proposito: casi ningun `irA` lo usa, y
   *  hacerlo obligatorio obligaria a escribir `irA('dashboard', undefined)` en
   *  todas partes. */
  irA: (pagina: Page, filtro?: FiltroTickets) => void;
  /** El filtro con el que sepidio abrir la pantalla actual, o `null`. */
  filtroTickets: FiltroTickets | null;
}

const NavegacionContext = createContext<NavegacionContextValue | null>(null);

/** Un filtro que la pantalla de destino puede recibir.
 *
 *  Existe para el camino del tablero a la cola: la barra gris de "sin
 *  clasificar" es accionable y al pulsing lleva a Tickets ya filtrado. Sin esto,
 *  el clic lleva a la lista completa y quien llega tiene que volver a aplicar el
 *  filtro a mano, y entonces la barra deja de ser un atajo. */
export type FiltroTickets = 'todos' | 'sinClasificar' | 'clasificados';

export function NavegacionProvider({ children }: { children: ReactNode }) {
  // Empieza en Inicio, que es el dashboard. Antes abria en Empresas, que es la
  // pantalla de configuracion: obligaba a pasar por un formulario de alta para
  // ver el estado del negocio, y la primera pregunta de quien abre la aplicacion
  // es "como van mis gastos", no "creo otra empresa".
  const [paginaActual, setPaginaActual] = useState<Page>('dashboard');
  const [filtroTickets, setFiltroTickets] = useState<FiltroTickets | null>(null);

  const irA = (pagina: Page, filtro?: FiltroTickets) => {
    setPaginaActual(pagina);
    setFiltroTickets(filtro ?? null);
  };
  return (
    <NavegacionContext.Provider value={{ paginaActual, irA, filtroTickets }}>
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