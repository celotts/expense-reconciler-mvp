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
 */
export function EstadoDeIa() {
  const [estado, setEstado] = useState<ScanIaEstado | null>(null);
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

  return (
    <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs text-amber-900">
      {motivo && <p className="font-medium">{motivo}</p>}

      {faltan.length > 0 && (
        <>
          <p className="font-medium">
            Configurado pero no instalado: {faltan.join(', ')}
          </p>
          {/* El comando va con el nombre DENTRO. Un `ollama pull` sin argumentos
              es media hora de diagnostica, y el nombre es justo lo que se ha
              escrito mal. */}
          <p className="mt-1 font-mono text-[11px] text-amber-800">
            {como_instalar.join('   ')}
          </p>
          <p className="mt-1 text-amber-700">
            Mientras falte, esos comprobantes caen a la lectura por reglas o se
            quedan en la cola.
          </p>
        </>
      )}
    </div>
  );
}