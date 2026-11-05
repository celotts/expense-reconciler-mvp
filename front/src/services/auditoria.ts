import { fetchApi } from './api';

export interface EventoFrontend {
  accion: string;
  modulo: string;
  registro_afectado?: string;
  detalles?: Record<string, unknown>;
}

/** Envia un evento de auditoria al backend. Fire-and-forget: falla en
 *  silencio para no romper la UI por problemas de red. */
export async function registrarEventoFrontend(ev: EventoFrontend): Promise<void> {
  try {
    await fetchApi('/auditoria/evento', {
      method: 'POST',
      body: JSON.stringify(ev),
    });
  } catch {
    // No se reporta: la auditoria no puede tumba la experiencia de usuario.
  }
}
