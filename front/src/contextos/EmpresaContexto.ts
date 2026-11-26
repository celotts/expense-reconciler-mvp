/** La empresa activa y su rol, separados del provider.
 *
 *  `Empresa.tsx` exportaba el contexto, el hook, el tipo y `rolPuedeCaja`
 *  ademas de dos componentes. Fast-refresh solo recarga un modulo cuando todos
 *  sus exports son componentes: con mezcla, cambiar la lista de roles tiraba
 *  el estado de la aplicacion entera. Aqui estan los datos y las reglas; en
 *  `Empresa.tsx` queda quien los carga y los pone en el provider.
 */
import { createContext, useContext } from 'react';

export interface MembresiaEmpresa {
  company_id: string;
  name: string;
  tax_id: string;
  rol: string;
}

/** Roles que pueden entrar a caja chica. `operator` entra, pero el backend
 *  limita sus cajas a las que el responsabiliza; el front no puede hacer esa
 *  comprobacion sin volver a consultar la BD, y por eso tampoco la intenta. */
const ROLES_CAJA = new Set(['owner', 'admin', 'accountant', 'operator']);

export function rolPuedeCaja(rol: string | null): boolean {
  return rol !== null && ROLES_CAJA.has(rol);
}

export interface EmpresaContextValue {
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

/** No lanza si no hay proveedor: los verificadores montan pantallas sueltas
 *  con `renderToStaticMarkup` y necesitan ver el HTML, no un error de contexto.
 *  En la aplicacion real el proveedor siempre esta montado dentro de la puerta.
 */
export function useEmpresa(): EmpresaContextValue {
  return useContext(EmpresaContext) ?? VACIO;
}
