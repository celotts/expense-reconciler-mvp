import { useEffect, useState } from 'react';
import { ticketsApi } from '../services/api';
import type { ScanIaEstado } from '../types/scan';

/**
 * Que modelo esta leyendo los comprobantes, y si de verdad esta disponible.
 *
 * ## POR QUE ESTO ESTA EN PANTALA Y NO EN UN LOG
 *
 * Porque "el modelo no responde" y "el modelo no existe" se ven igual desde
 * afuera, y pedir acciones opuestas. MEDIDO en esta maquina:
 *
 *     la `.env` declaraba   OLLAMA_MODEL=qwen2.5:3b
 *     el contenedor tiene  moondream:latest     <- y solo
 *
 * Ese modelo de texto no habia contestado ni una vez. No habia forma de saberlo:
 * `motor` dice `llm` para todos los motores, asi que comparar dos modelos en el
 * reporte de exactitud es imposible, y el unico sintoma era un `ConnectionError`
 * en un log que nadie abre.
 *
 * ## POR QUE NO ES UN CARTEL QUE ANDA SIEMPRE A LA VISTA
 *
 * Porque cuando todo esta bien no aporta nada y ocupa sitio. Se muestra solo si
 * hay algo que arreglar —un modelo configurado que no esta instalado, o la IA
 * apagada— y en cualquier momento se puede preguntar por que. Ese es el patron
 * que ya usa `GET /scan/ocr`, y el mismo motivo: *poder preguntar* en vez de
 * deducir de un numero que no dice cual de las dos cosas paso.
 *
 * ## POR QUE ACEPTA `estadoInicial`
 *
 * Porque `renderToStaticMarkup` **no corre `useEffect`**: el verificador
 * montaria el componente, no habria estado, y todas las comprobaciones darian
 * verde por no estar mirando nada. Es el mismo problema y la misma solucion que
 * en `Cierre` con `informeInicial`: el estado entra por props y el `useEffect`
 * solo lo pisa cuando hay red.
 */
export function EstadoDeIa({ estadoInicial = null }: { estadoInicial?: ScanIaEstado | null } = {}) {
  const [estado, setEstado] = useState<ScanIaEstado | null>(estadoInicial);
  const [error, setError] = useState(false);

  useEffect(() => {
    let vivo = true;
    ticketsApi
      .scanIa()
      .then((d: ScanIaEstado) => {
        if (vivo) setEstado(d);
      })
      .catch(() => {
        // No se muestra el cartel de "no se pudo consultar". Un estado que
        // depende de un endpoint y falla a veces ensucia la pantalla con un
        // error que no es del escaneo. Si el modelo esta bien, esto no dice nada
        // —que es exactamente lo que debe pasar.
        if (vivo) setError(true);
      });
    return () => {
      vivo = false;
    };
  }, []);

  if (error || !estado) return null;

  const { configurado, faltan, error: motivo, como_instalar } = estado;

  // La IA apagada es un estado legitimo, no un fallo: se escanea con reglas y
  // con OCR sin ella. Por eso NO es un cartel de error.
  if (!configurado.ai_habilitado) {
    return (
      <div className="px-4 py-2 bg-gray-100 border-b border-gray-200 text-xs text-gray-600">
        IA apagada: los comprobantes se leen con reglas y OCR, sin modelo.
      </div>
    );
  }

  // Todo en su sitio: no se dice nada.
  if (faltan.length === 0 && !motivo) return null;

  // ## POR QUE HAY QUE DISTINGUIR TEXTO DE VISION
  //
  // MEDIDO el 2026-10-15. Faltaba `moondream` —el de TEXTO— y el cartel decia:
  //
  //     "Mientras falte, esos comprobantes caen a la lectura por reglas o se
  //      quedan en la cola."
  //
  // Y es **FALSO**: `qwen2.5vl:3b` si estaba instalado y con `IMAGENES_VISION_
  // PRIMERO` las fotos las lee el modelo. La ausencia del de texto no toca las
  // fotos: solo impide escalar los PDF con capa de texto cuando las reglas no
  // bastan.
  //
  // Un aviso que dice una cosa que no esta pasando entrena a ignorar el aviso,
  // y el dia que diga la verdad nadie lo va a leer. Ademas los dos ausentes
  // piden acciones distintas: sin vision hay que bajar una foto al escaner
  // entero; sin texto, nada de lo que hace falta para las fotos se entera.
  const sinVision = configurado.modelo_vision
    ? faltan.some((m) => m.split(':')[0] === configurado.modelo_vision!.split(':')[0])
    : false;
  const sinTexto = configurado.modelo_texto
    ? faltan.some((m) => m.split(':')[0] === configurado.modelo_texto!.split(':')[0])
    : false;

  return (
    <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs text-amber-900">
      {motivo && <p className="font-medium">{motivo}</p>}

      {faltan.length > 0 && (
        <>
          <p className="font-medium">
            Configurado pero no instalado:{' '}
            {sinVision && sinTexto ? 'el modelo de visión y el de texto' : sinVision ? 'el modelo de visión' : 'el modelo de texto'}
            {' '}({faltan.join(', ')})
          </p>
          {/* El comando va con el nombre DENTRO. Un `ollama pull` sin argumentos
              es media hora de diagnostica, y el nombre es justo lo que se ha
              escrito mal. */}
          <p className="mt-1 font-mono text-[11px] text-amber-800">
            {como_instalar.join('   ')}
          </p>
          {/* Lo que de verdad se ve afectado, y no "esos comprobantes". */}
          {sinVision && (
            <p className="mt-1 text-amber-700">
              <strong>Las fotos no se pueden leer con modelo</strong>: caen al OCR y las
              reglas, y muchas van a la cola a mano. Es lo que hay que arreglar primero.
            </p>
          )}
          {!sinVision && sinTexto && (
            <p className="mt-1 text-amber-700">
              Las fotos <strong>sí</strong> las lee el modelo de visión; esto no las
              afecta. Lo que falta es el de texto, que se usa para los PDF con capa de
              texto cuando las reglas no bastan: esos sí se quedan en la cola.
            </p>
          )}
        </>
      )}
    </div>
  );
}