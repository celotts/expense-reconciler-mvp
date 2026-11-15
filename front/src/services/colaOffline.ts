/**
 * Cola offline de gastos.
 *
 * EL PROBLEMA
 * -----------
 * El formulario de "Nuevo gasto" guarda en el servidor; si no hay red, la
 * petición falla y lo único que queda es el mensaje de error — lo que
 * tecleó el operador se pierde si se cierra el formulario.
 *
 * LA SOLUCIÓN
 * -----------
 * El gasto que no se pudo enviar se guarda en IndexedDB —sobrevive a
 * recargar y a cerrar el navegador— y se reenvía solo cuando vuelve la
 * red. `localStorage` no vale: es síncrono (bloquea la pantalla al
 * guardar), tiene un límite pequeño compartido con todo lo demás y está
 * hecho para texto, no para guardar el comprobante adjunto.
 *
 * QUE SE GUARDA
 * -------------
 * `payload`: exactamente el cuerpo que se mandaría a `POST /tickets/`
 * (mismo `TicketCreate`). `comprobante`: el archivo del comprobante, si
 * lo había — sin él el gasto queda "sin papel", que es recuperable (se
 * vuelve a subir desde el ticket), pero si cabe en el dispositivo, se
 * guarda. Nada más: sin el payload completo no hay reenvío posible.
 *
 * OJO, EL REENVÍO NO ES IDEMPOTENTE EN EL SERVIDOR.
 * `POST /tickets/` no acepta `client_scan_id` como sí lo hace el escáner,
 * así que doble reenvío puede duplicar el gasto si la respuesta original
 * se perdió después de que el servidor lo guardara. Hasta que el backend
 * acepte una llave de idempotencia, el aviso de la cola dice siempre
 * "gastos sin enviar" y quien los revisa sabe lo que hay.
 *
 * LOS EVENTOS
 * -----------
 * Cada cambio en la cola dispara `gastos-offline:cambio` en `document`
 * con `{ quedan }`: la vista de Tickets rescata el badge y el aviso de
 * pendientes. Sin importar vistas, sin ciclos.
 */

import type { TicketCreate } from '../types/api';
import { ticketsApi } from './api';

const DB_NOMBRE = 'expense-reconciler';
const DB_VERSION = 1;
const TABLA = 'gastos_pendientes';
export const EVENTO_GASTOS_OFFLINE = 'gastos-offline:cambio';

export interface GastoPendiente {
  /** UUID del cliente, generado al encolar. Clave primaria en IndexedDB. */
  id: string;
  /** Lo mismo que se mandaría a `POST /tickets/`. */
  payload: TicketCreate;
  /** El comprobante como Blob, si había. Se sube DESPUÉS de crear el ticket. */
  comprobante?: Blob;
  nombreComprobante?: string;
  /** ISO 8601: cuando se ENCOLÓ, no cuando se escaneó. */
  creadoEn: string;
  intentos: number;
  ultimoError: string | null;
}

let promesaDB: Promise<IDBDatabase> | null = null;

/** Si este navegador puede guardar pendientes. Sin IndexedDB no hay cola. */
export function compatible(): boolean {
  return typeof indexedDB !== 'undefined' && indexedDB !== null;
}

function abrirDB(): Promise<IDBDatabase> {
  if (promesaDB) return promesaDB;

  promesaDB = new Promise((resolver, rechazar) => {
    const pedido = indexedDB.open(DB_NOMBRE, DB_VERSION);

    pedido.onupgradeneeded = () => {
      const db = pedido.result;
      if (!db.objectStoreNames.contains(TABLA)) {
        db.createObjectStore(TABLA, { keyPath: 'id' });
      }
    };
    pedido.onsuccess = () => resolver(pedido.result);
    pedido.onerror = () => rechazar(pedido.error);
    pedido.onblocked = () => rechazar(new Error('IndexedDB bloqueada por otra pestaña'));
  });

  return promesaDB;
}

function pedir<T>(solicitud: IDBRequest<T>): Promise<T> {
  return new Promise((resolver, rechazar) => {
    solicitud.onsuccess = () => resolver(solicitud.result);
    solicitud.onerror = () => rechazar(solicitud.error);
  });
}

function avisarCambio() {
  if (typeof document !== 'undefined') {
    document.dispatchEvent(new CustomEvent(EVENTO_GASTOS_OFFLINE));
  }
}

/** Guarda un gasto que no se pudo enviar. Devuelve lo que quedó guardado. */
export async function encolarGasto(
  payload: TicketCreate,
  comprobante?: File | null,
): Promise<GastoPendiente> {
  const pendiente: GastoPendiente = {
    id: crypto.randomUUID(),
    payload,
    comprobante: comprobante ?? undefined,
    nombreComprobante: comprobante?.name,
    creadoEn: new Date().toISOString(),
    intentos: 0,
    ultimoError: null,
  };

  const db = await abrirDB();
  const tx = db.transaction(TABLA, 'readwrite');
  await pedir(tx.objectStore(TABLA).add(pendiente));
  avisarCambio();
  return pendiente;
}

export async function listarPendientes(): Promise<GastoPendiente[]> {
  const db = await abrirDB();
  const tx = db.transaction(TABLA, 'readonly');
  const todos = await pedir<GastoPendiente[]>(tx.objectStore(TABLA).getAll());
  return todos.sort((a, b) => a.creadoEn.localeCompare(b.creadoEn));
}

export async function contarPendientes(): Promise<number> {
  const db = await abrirDB();
  const tx = db.transaction(TABLA, 'readonly');
  return pedir<number>(tx.objectStore(TABLA).count());
}

export async function eliminarPendiente(id: string): Promise<void> {
  const db = await abrirDB();
  const tx = db.transaction(TABLA, 'readwrite');
  await pedir(tx.objectStore(TABLA).delete(id));
  avisarCambio();
}

async function _guardar(pendiente: GastoPendiente): Promise<void> {
  const db = await abrirDB();
  const tx = db.transaction(TABLA, 'readwrite');
  await pedir(tx.objectStore(TABLA).put(pendiente));
}

/** `fetch` rechaza con `TypeError` cuando no hay red.
 *
 *  NOTA: los 4xx/5xx no son error de red — el servidor contestó. Un 422
 *  repetido jamás va a pasar, así que se deja el pendiente con el error
 *  a la vista para que el operador lo descarte, en vez de reenviarlo
 *  para siempre. `SesionVencida` (401 que ya avisó la puerta) tampoco
 *  es error de red. */
export function esErrorDeRed(error: unknown): boolean {
  return error instanceof TypeError;
}

export interface ResumenSincronizacion {
  enviados: number;
  /** Pendientes que no se borran por su 4xx/5xx (se ven en el aviso). */
  rechazados: number;
  quedan: number;
}

/** Reenvía todo lo pendiente. Devuelve el resumen; nunca lanza. */
export async function sincronizarPendientes(): Promise<ResumenSincronizacion> {
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    return { enviados: 0, rechazados: 0, quedan: await contarPendientes() };
  }

  const pendientes = await listarPendientes();
  let enviados = 0;
  let rechazados = 0;

  for (const pendiente of pendientes) {
    try {
      const creado = await ticketsApi.create(pendiente.payload);

      // El comprobante se sube DESPUÉS de crear, y su fallo no deshace el
      // gasto: el ticket ya existe y el archivo se puede re-subir desde la
      // ficha (`PUT /tickets/{id}/documento`).
      if (pendiente.comprobante) {
        try {
          const archivo = new File(
            [pendiente.comprobante],
            pendiente.nombreComprobante ?? 'comprobante',
            { type: pendiente.comprobante.type || 'application/octet-stream' },
          );
          await ticketsApi.subirDocumento(creado.id, archivo, pendiente.payload.company_id);
        } catch (errorComprobante) {
          console.warn('El gasto se envió pero el comprobante no:', errorComprobante);
        }
      }

      await eliminarPendiente(pendiente.id);
      enviados += 1;
    } catch (error) {
      pendiente.intentos += 1;
      pendiente.ultimoError = error instanceof Error ? error.message : String(error);
      await _guardar(pendiente);

      if (esErrorDeRed(error)) {
        // Sin red no tiene sentido seguir golpeando: el resto esperará.
        break;
      }
      rechazados += 1;
    }
  }

  avisarCambio();
  return { enviados, rechazados, quedan: await contarPendientes() };
}

/** Reenvía al recuperar la conexión. Devuelve el limpiador (para useEffect).
 *
 *  También intenta al registrarse: la app puede abrirse sin red y la red
 *  volver antes de que el operador haga nada, y "ya estaba todo enviado"
 *  es un estado legítimo que el aviso de pendientes sabe pintar. */
export function iniciarSincronizacionAutomatica(): () => void {
  if (typeof window === 'undefined') return () => {};

  let enCurso = false;

  const intentar = () => {
    if (enCurso) return;
    enCurso = true;
    sincronizarPendientes()
      .catch((error) => console.warn('No se pudo reenviar la cola offline:', error))
      .finally(() => {
        enCurso = false;
      });
  };

  window.addEventListener('online', intentar);
  intentar();

  return () => {
    window.removeEventListener('online', intentar);
  };
}
