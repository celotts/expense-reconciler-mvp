/** Confirmacion de salida, con una pregunta al servidor antes de cerrar.
 *
 *  El boton de "Salir" solia llamar a `cerrarSesion()` sin preguntar. Dos
 *  problemas: un clic accidental cerraba la sesion, y si habia una digitalizacion
 *  en marcha la persona se iba dejando el trabajo corriendo en el servidor sin
 *  saberlo (el POST no se corta cuando el navegador se cierra: esta documentado
 *  en el caso del 504 de nginx).
 *
 *  La verdad de "hay una digitalizacion en curso" la da `GET /scan/runs`, que
 *  devuelve `en_curso` desde el registro de corridas del servidor. No se adivina
 *  mirando un spinner local: otra pestaña puede estar escaneando.
 *
 *  `ModalSalirVista` es puro y se monta en el verificador con cada estado. El
 *  envoltorio hace la peticion; la vista solo pinta. Con `renderToStaticMarkup`
 *  no corren los `useEffect`, y sin esa separacion el verificador no podria ver
 *  el estado "ocupado" sin simular una corrida real.
 */
import { useCallback, useEffect, useState } from 'react';
import { ticketsApi } from '../services/api';

export type EstadoSalida = 'comprobando' | 'libre' | 'ocupado' | 'error';

interface VistaProps {
  estado: EstadoSalida;
  enCurso: number;
  onCancelar: () => void;
  onConfirmar: () => void;
  onRevisar: () => void;
}

/** Cuantas corridas siguen vivas. Cero NO es "no sabemos": es "libre". */
export function cuantasEnCurso(enCurso: string[]): number {
  return enCurso.length;
}

export function ModalSalirVista({
  estado,
  enCurso,
  onCancelar,
  onConfirmar,
  onRevisar,
}: VistaProps) {
  // Solo el estado "libre" autoriza el boton. "comprobando" y "error" se
  // bloquean tambien: ante la duda, cerrar la sesion puede cortar el rastro de
  // una corrida que nadie va a poder explicar despues.
  const libre = estado === 'libre';
  const puedeRevisar = estado === 'ocupado' || estado === 'error';

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-salir-titulo"
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
      >
        <h2 id="modal-salir-titulo" className="text-lg font-semibold text-gray-900">
          Desea salir de la aplicacion?
        </h2>

        {estado === 'comprobando' && (
          <p className="mt-3 text-sm text-gray-600">
            Comprobando si hay una digitalizacion en curso...
          </p>
        )}

        {estado === 'libre' && (
          <p className="mt-3 text-sm text-gray-600">
            No hay digitalizaciones en curso. Puede salir.
          </p>
        )}

        {estado === 'ocupado' && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            No se puede salir: hay {enCurso} digitalizacion(es) en curso. Espere a que terminen.
          </p>
        )}

        {estado === 'error' && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            No se pudo comprobar si hay una digitalizacion en curso. Intente de nuevo.
          </p>
        )}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button
            onClick={onCancelar}
            className="rounded-lg bg-gray-200 px-4 py-2.5 text-sm font-medium text-gray-800 hover:bg-gray-300 sm:py-2"
          >
            Cancelar
          </button>
          {puedeRevisar && (
            <button
              onClick={onRevisar}
              className="rounded-lg border border-blue-200 px-4 py-2.5 text-sm font-medium text-blue-700 hover:bg-blue-50 sm:py-2"
            >
              Volver a comprobar
            </button>
          )}
          <button
            onClick={onConfirmar}
            disabled={!libre}
            title={libre ? 'Cierra la sesion en el servidor' : 'No se puede salir mientras se comprueba o hay una digitalizacion en curso'}
            className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 sm:py-2"
          >
            Salir
          </button>
        </div>
      </div>
    </div>
  );
}

export function ModalSalir({
  onCancelar,
  onConfirmar,
}: {
  onCancelar: () => void;
  onConfirmar: () => void;
}) {
  const [estado, setEstado] = useState<EstadoSalida>('comprobando');
  const [enCurso, setEnCurso] = useState(0);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    let vivo = true;
    setEstado('comprobando');

    (async () => {
      try {
        const respuesta = await ticketsApi.scanRuns();
        const n = cuantasEnCurso(respuesta.en_curso);
        if (!vivo) return;
        setEnCurso(n);
        setEstado(n > 0 ? 'ocupado' : 'libre');
      } catch {
        // Si no se puede preguntar, no se finge respuesta: el boton sigue
        // bloqueado y la persona puede reintentar o cancelar.
        if (vivo) {
          setEnCurso(0);
          setEstado('error');
        }
      }
    })();

    return () => {
      vivo = false;
    };
  }, [intento]);

  const revisar = useCallback(() => setIntento((n) => n + 1), []);

  const confirmar = useCallback(() => {
    // El `disabled` protege el clic de una persona; esto protege un `onClick`
    // disparado por codigo o por un estado que cambio entre el render y el clic.
    if (estado !== 'libre') return;
    onConfirmar();
  }, [estado, onConfirmar]);

  return (
    <ModalSalirVista
      estado={estado}
      enCurso={enCurso}
      onCancelar={onCancelar}
      onConfirmar={confirmar}
      onRevisar={revisar}
    />
  );
}
