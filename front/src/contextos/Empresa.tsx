/** La empresa activa, una sola para toda la aplicacion.
 *
 *  Antes cada pantalla tenia su propio `useState(companyId)` y llamaba a
 *  `companiesApi.list()`. Eso respondia "qué empresas existen", no "a cuáles
 *  tiene acceso esta cuenta", y permitia que Escáner y Tickets defendieran
 *  empresas distintas con el mismo token. Ahora la lista viene de
 *  `GET /auth/empresas` y la eleccion se manda en un unico sitio (`api.ts`).
 *
 *  El rol de aqui es solo para NO ofrecer acciones imposibles en la interfaz.
 *  No autoriza nada: el servidor lo relee de la BD en cada peticion. Un front
 *  que se fiara de su propia copia del rol se quedaria creyendo que alguien
 *  sigue siendo admin despues de que le cambiaran el rol.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { empresaActivaGuardada, fetchApi, fijarEmpresaActiva } from '../services/api';
import { useSesion } from './Sesion';

export interface MembresiaEmpresa {
  company_id: string;
  name: string;
  tax_id: string;
  rol: string;
}

/** Roles que pueden entrar a caja chica. `operator` entra, pero el backend
 *  limita sus cajas a las que él responsabiliza; el front no puede hacer esa
 *  comprobacion sin volver a consultar la BD, y por eso tampoco la intenta. */
const ROLES_CAJA = new Set(['owner', 'admin', 'accountant', 'operator']);

export function rolPuedeCaja(rol: string | null): boolean {
  return rol !== null && ROLES_CAJA.has(rol);
}

interface EmpresaContextValue {
  empresas: MembresiaEmpresa[];
  empresaActiva: MembresiaEmpresa | null;
  rol: string | null;
  cargando: boolean;
  necesitaEleccion: boolean;
  error: string | null;
  elegirEmpresa: (companyId: string) => void;
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

export const EmpresaContext = createContext<EmpresaContextValue>(VACIO);

export function EmpresaProvider({ children }: { children: ReactNode }) {
  const { usuario } = useSesion();
  const userId = usuario?.id;
  const [empresas, setEmpresas] = useState<MembresiaEmpresa[]>([]);
  const [elegida, setElegida] = useState<string | null>(() => empresaActivaGuardada());
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!userId) {
      setEmpresas([]);
      setError(null);
      fijarEmpresaActiva(null);
      return;
    }

    let vivo = true;
    setCargando(true);
    fetchApi<MembresiaEmpresa[]>('/auth/empresas')
      .then((lista) => {
        if (!vivo) return;
        setEmpresas(lista);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!vivo) return;
        // Un fallo de red no puede borrar la empresa elegida: la ultima lista
        // valida sigue siendo mejor que una pantalla sin empresa y sin motivo.
        // Borrar `empresas` aqui deria la empresa derivada a null y el efecto
        // de abajo quitaria la cabecera, asi que el comentario mentiria.
        setError(e instanceof Error ? e.message : 'No se pudieron cargar las empresas');
      })
      .finally(() => {
        if (vivo) setCargando(false);
      });

    return () => {
      vivo = false;
    };
  }, [userId]);

  // La memoria del modulo y el localStorage siguen a la eleccion derivada, no
  // al `useState`: si la cuenta solo tiene una empresa, esa empresa queda
  // activa aunque nadie haya pulsado nada.
  const empresaActiva = useMemo(
    () =>
      empresas.find((m) => m.company_id === elegida)
      ?? (empresas.length === 1 ? empresas[0] : null),
    [empresas, elegida],
  );

  useEffect(() => {
    fijarEmpresaActiva(empresaActiva?.company_id ?? null);
  }, [empresaActiva?.company_id]);

  const elegirEmpresa = useCallback((companyId: string) => {
    setElegida(companyId);
    fijarEmpresaActiva(companyId);
  }, []);

  const valor = useMemo<EmpresaContextValue>(
    () => ({
      empresas,
      empresaActiva,
      rol: empresaActiva?.rol ?? null,
      cargando,
      necesitaEleccion: !cargando && empresas.length > 1 && empresaActiva === null,
      error,
      elegirEmpresa,
    }),
    [empresas, empresaActiva, cargando, error, elegirEmpresa],
  );

  return <EmpresaContext.Provider value={valor}>{children}</EmpresaContext.Provider>;
}

/** No lanza si no hay proveedor: los verificadores montan pantallas sueltas
 *  con `renderToStaticMarkup` y necesitan ver el HTML, no un error de contexto.
 *  En la aplicacion real el proveedor siempre esta montado dentro de la puerta.
 */
export function useEmpresa(): EmpresaContextValue {
  return useContext(EmpresaContext) ?? VACIO;
}

/** El mismo aviso en las tres pantallas que escriben datos de empresa.
 *
 *  No es un `if` copiado tres veces: un aviso que solo existe en Escáner se
 *  queda sin decir nada en Tickets, y cada copia puede cambiar de texto. Se
 *  monta con `renderToStaticMarkup` en el verificador, asi que lo que se prueba
 *  es el HTML y no el retorno de una funcion.
 */
export function AvisoSinEmpresa({ mensaje }: { mensaje?: string }) {
  const { empresaActiva, cargando } = useEmpresa();
  if (cargando || empresaActiva) return null;
  return (
    <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
      {mensaje ?? 'Elige una empresa activa en la barra lateral antes de continuar.'}
    </div>
  );
}
