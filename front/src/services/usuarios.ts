/** Cliente de Mantenimiento: cuentas y roles por empresa.
 *
 *  La contrasena del administrador NO se guarda aqui ni en `localStorage`: se
 *  pasa en cada llamada y viaja como `X-Contrasena-Actual`. Un token robado
 *  puede abrir la pantalla, pero no firmar un cambio de rol sin esa clave.
 */
import { fetchApi } from './api';
import type {
  MembresiaCreate,
  MembresiaUpdate,
  MembresiaUsuario,
  Usuario,
  UsuarioCreate,
  UsuarioUpdate,
} from '../types/api';

export const usuariosApi = {
  listar: () => fetchApi<Usuario[]>('/usuarios'),

  crear: (datos: UsuarioCreate, firma: string) =>
    fetchApi<Usuario>('/usuarios', {
      method: 'POST',
      body: JSON.stringify(datos),
      contrasenaActual: firma,
    }),

  actualizar: (id: string, datos: UsuarioUpdate, firma: string) =>
    fetchApi<Usuario>(`/usuarios/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(datos),
      contrasenaActual: firma,
    }),

  /** Reset administrativo: la clave nueva es de la cuenta objetivo; la firma
   *  es la del administrador. No es el cambio propio. */
  resetearContrasena: (id: string, contrasenaNueva: string, firma: string) =>
    fetchApi<void>(`/usuarios/${id}/contrasena-admin`, {
      method: 'POST',
      body: JSON.stringify({ contrasena_nueva: contrasenaNueva }),
      contrasenaActual: firma,
    }),

  listarMembresias: () => fetchApi<MembresiaUsuario[]>('/usuarios/membresias'),

  asignarMembresia: (datos: MembresiaCreate, firma: string) =>
    fetchApi<MembresiaUsuario>('/usuarios/membresias', {
      method: 'POST',
      body: JSON.stringify(datos),
      contrasenaActual: firma,
    }),

  actualizarMembresia: (id: string, datos: MembresiaUpdate, firma: string) =>
    fetchApi<MembresiaUsuario>(`/usuarios/membresias/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(datos),
      contrasenaActual: firma,
    }),
};
