/** El valor del contexto de sesion, separado del provider.
 *
 *  Antes `Sesion.tsx` exportaba a la vez el provider, el contexto, el hook y el
 *  tipo. Con eso, cualquier cambio en el provider recargaba tambien el modulo
 *  que define `useSesion` y React perdia el estado de la sesion al editar. El
 *  valor y el hook viven aqui; `Sesion.tsx` solo monta el provider.
 *
 *  Es un `.ts` y no un `.tsx` a proposito: no hay JSX, y el sufijo le dice a
 *  eslint que este archivo no puede contener componentes.
 */
import { createContext, useContext } from 'react';
import type { Usuario } from '../types/api';
import type { AvisoSesion } from '../services/sesion';

export interface SesionContextValue {
  /** `null` = todavia no se sabe. La app NO se monta en ese estado: no hay
   *  forma de distinguir "cargando" de "no hay sesion" si el valor es el mismo. */
  usuario: Usuario | null;
  cargando: boolean;
  /** El aviso de caducidad, para mostrarlo encima del login. Se limpia al
   *  entrar, para que el exito borre el mensaje de fracaso anterior. */
  aviso: AvisoSesion | null;
  /** Perfil de rol del usuario (consultor, digitalizador, supervisor, admin, qa, programador, develop) */
  perfil: string | null;
  /** Grupo asociado al perfil, controla el acceso CRUD y a informacion sensible. */
  grupo: string | null;
  entro: (u: Usuario) => void;
  cerrarSesion: () => void;
}

export const SesionContext = createContext<SesionContextValue | null>(null);

export function useSesion(): SesionContextValue {
  const ctx = useContext(SesionContext);
  if (!ctx) {
    throw new Error('useSesion debe usarse dentro de SesionProvider');
  }
  return ctx;
}
