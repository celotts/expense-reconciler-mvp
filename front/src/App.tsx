import { useEffect, useState } from 'react';
import { registrarEventoFrontend } from './services/auditoria';
import { estaVencido, leerToken, notificarCaducidad } from './services/sesion';
import { Companies } from './pages/Companies';
import { Tickets } from './pages/Tickets';
import { BankTransactions } from './pages/BankTransactions';
import { Reconciliations } from './pages/Reconciliations';
import { ReviewQueue } from './pages/ReviewQueue';
import { SpotCheck } from './pages/SpotCheck';
import { Dashboard } from './pages/Dashboard';
import { Login } from './pages/Login';
import { Scan } from './pages/Scan';
import { Cierre } from './pages/Cierre';
import { CajaChica } from './pages/CajaChica';
import { Auditoria } from './pages/Auditoria';
import { Mantenimiento } from './pages/Mantenimiento';
import { NavegacionProvider } from './contextos/Navegacion';
import { useNavegacion, type Page } from './contextos/NavegacionContexto';
import { SesionProvider } from './contextos/Sesion';
import { useSesion } from './contextos/SesionContexto';
import { EmpresaProvider } from './contextos/Empresa';
import { useEmpresa } from './contextos/EmpresaContexto';
import { paginasPermitidas, puedeVerPagina } from './permisos';
import { Loading } from './components/ui';
import './App.css';

// El tipo `Page` se importa del contexto y NO se redeclara aqui.
//
// Este archivo tenia su propia copia de la lista de pantallas, y ya habia
// divergido: incluia `'scan'` en el tipo pero no en la lista del sidebar, y le
// faltaban las que se anadieron despues. Con `as Page` en cada entrada, esa
// divergencia no daba error de compilacion -- `as` es una asercion, no una
// comprobacion -- y el sintoma era una pantalla que se podia abrir pero no
// aparecian la barra lateral. Una lista de pantallas en dos sitios es una lista
// que va a diferir otra vez.
/** Las etiquetas y los iconos estan aqui; QUIEN puede ver cada pantalla esta
 *  en `permisos.ts`. Tener las dos cosas separadas evita que una entrada nueva
 *  herede una lista de roles copiada de la vecina. */
const navigation: { key: Page; label: string; icon: typeof HomeIcon }[] = [
  { key: 'dashboard', label: 'Inicio', icon: HomeIcon },
  { key: 'scan', label: 'Escáner', icon: FolderScanIcon },
  { key: 'tickets', label: 'Tickets', icon: DocumentIcon },
  { key: 'review', label: 'Cola de revisión', icon: InboxIcon },
  { key: 'spotcheck', label: 'Muestreo', icon: CheckBadgeIcon },
  { key: 'bank', label: 'Banco', icon: BankIcon },
  { key: 'reconciliations', label: 'Conciliación', icon: ArrowPathIcon },
  { key: 'cajachica', label: 'Caja chica', icon: WalletIcon },
  { key: 'cierre', label: 'Cierre mensual', icon: ClipboardCheckIcon },
  { key: 'companies', label: 'Empresas', icon: BuildingIcon },
  { key: 'auditoria', label: 'Auditoría', icon: ClipboardCheckIcon },
  { key: 'mantenimiento', label: 'Mantenimiento', icon: WrenchIcon },
];

function ClipboardCheckIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  );
}

function BuildingIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
    </svg>
  );
}

function HomeIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
    </svg>
  );
}

function DocumentIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  );
}

function BankIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}

function ArrowPathIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
    </svg>
  );
}

function InboxIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-4l-1 3h-6l-1-3H4" />
    </svg>
  );
}

function CheckBadgeIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.745 3.745 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" />
    </svg>
  );
}



function FolderScanIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7zM9 12h6m-6 4h4" />
    </svg>
  );
}

function WalletIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7.5A2.5 2.5 0 015.5 5H18a2 2 0 012 2v10a2 2 0 01-2 2H5.5A2.5 2.5 0 013 16.5v-9zM3 9h15m-4 5h3" />
    </svg>
  );
}

function WrenchIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.3 5.7a4 4 0 00-5.1 5.1l6.9 6.9a4 4 0 005.1-5.1l-6.9-6.9zM14.5 7.5l2-2m-2.6 6.6l3.6 3.6" />
    </svg>
  );
}

function MenuIcon({ className = 'w-6 h-6' }: { className?: string }) {  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function XIcon({ className = 'w-6 h-6' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

function AppInner() {
  const { paginaActual, irA, filtroTickets } = useNavegacion();
  const { usuario, perfil, grupo, cerrarSesion } = useSesion();
  const {
    empresas, empresaActiva, elegirEmpresa, rol,
    cargando: cargandoEmpresas, necesitaEleccion,
  } = useEmpresa();
  // La puerta ya no deja entrar sin sesion. `perfil` es solo una etiqueta:
  // decidir acceso con el daria por bueno un "admin" global en una empresa
  // donde la cuenta es solo `operator`.
  const hayAcceso = !!usuario;
  const permitidas = paginasPermitidas(rol);
  const menuFiltrado = navigation.filter(item => permitidas.includes(item.key));

  // Mientras carga la lista de empresas no se decide nada: redirigir con
  // `rol=null` sacaria de la pantalla a quien si tiene acceso. La guardia
  // tambien alcanza la URL escrita a mano, no solo los botones del menu.
  useEffect(() => {
    if (cargandoEmpresas) return;
    if (paginaActual !== 'dashboard' && !puedeVerPagina(rol, paginaActual)) {
      irA('dashboard');
    }
  }, [cargandoEmpresas, rol, paginaActual, irA]);

  // Auditoria de navegacion: cada cambio de pantalla queda registrado.
  // La decision de registrar ya no depende de `perfil`: una cuenta sin perfil
  // pero con rol en una empresa tambien navega, y esa navigation es real.
  useEffect(() => {
    if (!usuario) return;
    registrarEventoFrontend({
      accion: 'navegar',
      modulo: 'frontend',
      detalles: { pantalla: paginaActual },
    });
  }, [paginaActual, usuario]);

  const perfilLabels: Record<string, string> = {
    consultor: 'Consultor',
    digitalizador: 'Digitalizador',
    supervisor: 'Supervisor',
    admin: 'Administrador',
    qa: 'QA',
    programador: 'Programador',
    develop: 'Developer',
  };
  const perfilLabel = perfil ? perfilLabels[perfil] || 'Sin perfil' : 'Sin perfil';

  const [sidebarOpen, setSidebarOpen] = useState(false);

  const renderPage = () => {
    switch (paginaActual) {
      case 'dashboard': return <Dashboard />;
      case 'companies': return <Companies />;
      // El filtro viene del tablero: al pulsar la barra gris de "sin clasificar"
      // se abre esta pantalla ya filtrada, no la lista completa.
      case 'tickets': return <Tickets filtroInicial={filtroTickets ?? 'todos'} />;
      case 'scan': return <Scan />;
      case 'review': return <ReviewQueue />;
      case 'spotcheck': return <SpotCheck />;
      case 'bank': return <BankTransactions />;
      case 'reconciliations': return <Reconciliations />;
      case 'cierre': return <Cierre />;
      case 'cajachica': return <CajaChica />;
      case 'auditoria': return <Auditoria />;
      case 'mantenimiento': return <Mantenimiento />;
      default: return <Dashboard />;
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div 
          className="fixed inset-0 z-40 bg-black/50 lg:hidden" 
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-gray-200 transform transition-transform duration-300 lg:translate-x-0 ${
        sidebarOpen ? 'translate-x-0' : '-translate-x-full'
      }`}>
        <div className="flex flex-col h-full">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b">
            <h1 className="text-xl font-bold text-blue-600">Expense Reconciler</h1>
            {/* `p-3` y no `p-2`: el icono de 24 con `p-2` da 40px de alto, y el
                minimo tactil es 44. Es el unico control sin etiqueta de esta
                pantalla, asi que ademas necesita el margen para no pegarse al
                borde al alcanzarlo con el pulgar. */}
            <button
              className="lg:hidden p-3 -mr-2 text-gray-500 hover:text-gray-700"
              onClick={() => setSidebarOpen(false)}
            >
              <XIcon />
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
            {menuFiltrado.map(item => (
              <button
                key={item.key}
                onClick={() => {
                  if (!hayAcceso) return;
                  const t = leerToken();
                  if (estaVencido(t)) {
                    notificarCaducidad(false);
                    return;
                  }
                  irA(item.key);
                  setSidebarOpen(false);
                }}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  paginaActual === item.key
                    ? 'bg-blue-50 text-blue-700'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                }`}
              >
                <item.icon />
                <span>{item.label}</span>
              </button>
            ))}
          </nav>

          {/* Footer */}
          <div className="p-4 border-t">
            <div className="mb-3 rounded-lg border border-blue-100 bg-blue-50 p-2">
              <label className="text-xs font-medium text-blue-800 block mb-1" htmlFor="empresa-activa">
                Empresa activa
              </label>
              {empresas.length > 1 ? (
                <select
                  id="empresa-activa"
                  className="w-full text-xs border border-blue-200 rounded px-2 py-1.5 bg-white"
                  value={empresaActiva?.company_id ?? ''}
                  onChange={e => elegirEmpresa(e.target.value)}
                >
                  <option value="">Elegir empresa...</option>
                  {empresas.map(m => (
                    <option key={m.company_id} value={m.company_id}>{m.name}</option>
                  ))}
                </select>
              ) : (
                <p className="text-xs text-blue-800 truncate" title={empresaActiva?.name}>
                  {cargandoEmpresas
                    ? 'Cargando empresas...'
                    : empresaActiva?.name ?? 'Sin empresas asignadas'}
                </p>
              )}
              <p className="text-xs text-blue-600 mt-1">
                {rol ? `Rol en esta empresa: ${rol}` : necesitaEleccion ? 'Elige una empresa para continuar' : '—'}
              </p>
            </div>
            <div className="mb-2">
              <p className="text-sm font-medium text-gray-800 truncate">{usuario?.nombre}</p>
              <p className="text-xs text-gray-500 truncate">{usuario?.email}</p>
              <p className="text-xs text-blue-600 truncate font-medium">{perfilLabel}</p>
              <p className="text-xs text-gray-400 truncate">{grupo ? `Grupo: ${grupo}` : ''}</p>
              {perfil === 'develop' && (
                <div className="mt-2">
                  <label className="text-xs text-gray-500 block mb-1">Entorno</label>
                  <select
                    className="w-full text-xs border border-gray-300 rounded px-2 py-1.5 bg-white"
                    defaultValue="dev"
                    onChange={e => console.log('ambiente:', e.target.value)}
                  >
                    <option value="dev">Dev</option>
                    <option value="qa">QA</option>
                    <option value="prod">Prod</option>
                  </select>
                </div>
              )}
            </div>
            <button
              onClick={cerrarSesion}
              className="w-full text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg py-3 sm:py-1.5 transition-colors"
            >
              Salir
            </button>
            <p className="text-xs text-gray-400 text-center mt-3">
              Expense Reconciler MVP v0.1.0
            </p>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="lg:ml-64 min-h-screen">
        {/* Mobile header */}
        <header className="lg:hidden fixed top-0 left-0 right-0 z-30 bg-white border-b shadow-sm">
          <div className="flex items-center justify-between p-4">
            {/* `p-3` y no `p-2`: mismo 40px medido que el de cerrar. */}
            <button onClick={() => setSidebarOpen(true)} className="p-3 -ml-2 text-gray-500 hover:text-gray-700">
              <MenuIcon />
            </button>
            <h1 className="text-lg font-semibold text-gray-900">Expense Reconciler</h1>
            <div className="w-10" />
          </div>
        </header>

        {/* Page content */}
        <main className="pt-16 lg:pt-0">
          {renderPage()}
        </main>
      </div>
    </div>
  );
}

/** La puerta. Se decide aqui, antes de montar ninguna pantalla.
 *
 *  El orden importa y no es negociable: primero `cargando`, despues
 *  `usuario === null`, y solo al final la aplicacion. Al reves, durante el
 *  arranque se veria la aplicacion entera un instante y luego saltaria el
 *  login, que es un parpadeo que hace pensar que algo se rompio. Ademas, con la
 *  aplicacion montada sin sesion, las pantallas lanzan sus peticiones al vacio
 *  y llenan la consola de 401 antes de que el login aparezca.
 *
 *  La comprobacion no se hace aqui sino en `SesionProvider`, y por eso este
 *  componente no tiene un `useEffect` de arranque: si la hiciera, el usuario se
 *  comprobaria dos veces y la segunda podria dar un resultado distinto del
 *  primero (el token caduca entremedias) y la app quedaria en un estado que
 *  nadie ha pedido. */
function Puerta() {
  const { usuario, cargando, aviso, entro } = useSesion();

  if (cargando) return <Loading message="Comprobando sesion..." />;
  if (!usuario) return <Login onEntro={entro} aviso={aviso} />;

  return (
    <EmpresaProvider>
      <NavegacionProvider>
        <AppInner />
      </NavegacionProvider>
    </EmpresaProvider>
  );
}

export default function App() {
  return (
    <SesionProvider>
      <Puerta />
    </SesionProvider>
  );
}
