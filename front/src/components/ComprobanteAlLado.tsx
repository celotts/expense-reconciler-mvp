/**
 * El comprobante original, al lado de los campos que se corrigen.
 *
 * POR QUE ESTO EXISTE, Y POR QUE NO ES UN EXTRA
 * ==============================================
 *
 * `VerDocumento` (el de `TicketDocumento.tsx`) es un desplegable debajo de la
 * lista: collapsed, y con un max-height de 96. Para VER un comprobante esta
 * bien. Para CORREGIRLO no: obligaria a alternar el desplegable, hacer scroll
 * entre el papel y los campos, y alternar otra vez para el siguiente campo.
 *
 * Y la pregunta de la cola de revision es exactamente "¿este 97.56 es el del
 * papel?". Con el papel escondido debajo de la lista, la respuesta cuesta tres
 * interacciones por ticket. Con el papel al lado, cuesta cero.
 *
 * **No es una mejora de estetica: es el cuello de botella del producto.** Un
 * contador con 40 tickets pendientes hace 120 interacciones de mas, y a los 20
 * los esta haciendo a ojo.
 *
 * RESPONSIVE, Y POR QUE EL CAMBIO DE DISPOSICION IMPORTA
 * ------------------------------------------------------
 * En escritorio son dos columnas: el papel a la izquierda, los campos a la
 * derecha. En movil se apilan con el paper PRIMERO, porque el operador abre la
 * cola en el celular y lee el comprobante antes de tocar nada.
 *
 * Un `md:grid-cols-2` solo no basta: hace falta decidir el ORDEN, y el orden
 * cambia por tamano de pantalla.
 */

import { useEffect, useRef, useState } from 'react';
import { ticketsApi } from '../services/api';
import type { Ticket } from '../types/api';

/** El comprobante de UN ticket, cargado una vez y cacheado mientras se mire. */
export function ComprobanteAlLado({ ticket }: { ticket: Ticket }) {
  const [url, setUrl] = useState<string | null>(null);
  const [tipo, setTipo] = useState<string | undefined>();
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // El `Blob` del comprobante no se vuelve a pedir mientras siga en pantalla:
  // el operador va y vuelve entre la lista y el formulario dozens de veces, y
  // cada una sería una descarga de 2 MB. Se cancela al cambiar de ticket, que
  // es cuando el anterior ya no se mira.
  const pedidoEnCurso = useRef(false);

  useEffect(() => {
    let vivo = true;
    setUrl(null);
    setError(null);
    setTipo(undefined);

    if (!ticket.tiene_documento) return;

    pedidoEnCurso.current = true;
    setCargando(true);
    ticketsApi
      .documento(ticket.id)
      .then((blob) => {
        if (!vivo) return;
        setTipo(blob.type || undefined);
        setUrl(URL.createObjectURL(blob));
      })
      .catch((e: unknown) => {
        if (!vivo) return;
        setError(e instanceof Error ? e.message : 'No se pudo cargar el comprobante');
      })
      .finally(() => {
        if (!vivo) return;
        pedidoEnCurso.current = false;
        setCargando(false);
      });

    // El `Blob` anterior se libera. Sin esto, revisar 40 tickets deja 40
    // objetos de 2 MB vivos en la pestana: en un escritorio con muchas pestanas
    // abiertas es la diferencia entre 100 MB y 1 GB.
    return () => {
      vivo = false;
      setUrl((previo) => {
        if (previo) URL.revokeObjectURL(previo);
        return null;
      });
    };
  }, [ticket.id, ticket.tiene_documento]);

  const esImagen = tipo?.startsWith('image/') ?? false;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="text-sm font-semibold text-gray-900">Comprobante original</h3>
        {ticket.documento_tamano != null && (
          <span className="text-xs text-gray-500 tabular-nums">
            {(ticket.documento_tamano / 1024).toFixed(0)} KB
          </span>
        )}
      </div>

      {!ticket.tiene_documento ? (
        <Aviso
          tono="gris"
          titulo="Este ticket no tiene comprobante guardado"
          detalle="Es lo normal en captura manual. Si el comprobante existe en papel, no hay forma de contrastarlo: la lectura se tiene que aceptar o rechazar de memoria."
        />
      ) : cargando ? (
        <div className="flex-1 flex items-center justify-center bg-gray-50 border border-gray-200 rounded-lg min-h-64">
          <p className="text-sm text-gray-500">Cargando comprobante…</p>
        </div>
      ) : error ? (
        <Aviso
          tono="rojo"
          titulo="No se pudo cargar el comprobante"
          detalle={`${error}. Puede que la sesión haya expirado; recarga la página.`}
        />
      ) : url && esImagen ? (
        /* `<img>` y no `<object type="application/pdf">`: el navegador intenta
           pintar un JPEG con el visor de PDF y sale un recuadro vacio. Y
           `<object>` ademas vuelve a pedir el recurso SIN la cabecera
           `Authorization`, que sale 401. El `Blob` ya viene autenticado. */
        <div className="flex-1 overflow-auto bg-gray-900/5 border border-gray-200 rounded-lg">
          <img
            src={url}
            alt={`Comprobante original de ${ticket.provider_name}`}
            className="w-full h-auto max-h-[70vh] object-contain"
          />
        </div>
      ) : url ? (
        <div className="flex-1 min-h-64 border border-gray-200 rounded-lg overflow-hidden">
          <object
            data={url}
            type={tipo ?? 'application/pdf'}
            className="w-full h-full"
            aria-label={`Comprobante original de ${ticket.provider_name}`}
          >
            {/* El fallback de `<object>`: si el navegador no pinta el visor, sale
                esto. Un PDF que se ve en blanco sin explicar nada es el peor
                resultado posible para quien esta corrigiendo un importe. */}
            <div className="p-4 text-sm text-gray-700">
              <p>Este navegador no puede mostrar el comprobante aquí.</p>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 underline mt-1 inline-block"
              >
                Abrirlo en otra pestaña
              </a>
            </div>
          </object>
        </div>
      ) : null}
    </div>
  );
}

function Aviso({
  tono,
  titulo,
  detalle,
}: {
  tono: 'gris' | 'rojo';
  titulo: string;
  detalle: string;
}) {
  const clases =
    tono === 'rojo'
      ? 'bg-red-50 border-red-200 text-red-900'
      : 'bg-gray-50 border-gray-200 text-gray-700';
  return (
    <div className={`flex-1 p-4 border rounded-lg ${clases}`}>
      <p className="text-sm font-medium">{titulo}</p>
      <p className="text-xs mt-1 opacity-90">{detalle}</p>
    </div>
  );
}

/** El texto crudo que el OCR leyo del papel, para cuando la foto no alcanza. */
export function TextoLeido({ texto }: { texto: string | null }) {
  if (!texto) return null;
  return (
    <details className="mt-3 group">
      <summary className="text-xs text-gray-600 cursor-pointer hover:text-gray-900 select-none">
        Ver el texto que el sistema leyó del papel
      </summary>
      <pre className="mt-2 p-3 bg-gray-50 border border-gray-200 rounded text-xs font-mono whitespace-pre-wrap break-words max-h-64 overflow-y-auto text-gray-800">
        {texto}
      </pre>
    </details>
  );
}