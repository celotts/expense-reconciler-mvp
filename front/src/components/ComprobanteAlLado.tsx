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
 * LA FOTO NO SE VEIA, Y AMPLIAR ABRIA EL ARCHIVO EQUIVOCADO
 * ---------------------------------------------------------
 * MEDIDO: este componente pedia SOLO `/documento` y decidia por `blob.type`.
 * Un HEIC de iPhone se sirve como `application/octet-stream` (esta fuera de la
 * lista cerrada de tipos servibles, a proposito: ningun navegador de escritorio
 * lo pinta), asi que caia en el `<object>` y el recuadro quedaba vacio. Tenia
 * ademas un boton que abria ESE original "en grande": en un HEIC el navegador
 * lo descargaba en vez de mostrarlo, que es exactamente lo contrario de lo que
 * prometia.
 *
 * La solucion no es adivinar la extension ni relajar la lista cerrada del
 * backend: es pedir la MISMA vista previa que ya usa `VerDocumento`. El backend
 * convierte el HEIC a JPEG solo para mostrar y deja el original byte por byte.
 * Se piden los dos en paralelo: la previa se PINTA y el original aporta el
 * `Content-Disposition` (lo unico que distingue un HEIC de un `.zip` que tambien
 * llega como octet-stream) y el enlace de descarga.
 *
 * Y "ver en grande" ocurre AQUI, sobre la imagen ya descargada y con token.
 * Ampliar no vuelve a pedir el papel ni abre una pestana que no lleva la
 * cabecera `Authorization`: muestra la misma imagen a pantalla casi completa,
 * con acercar/alejar.
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
import { nombreDelContentDisposition } from '../utils/documento';

/** El comprobante de UN ticket, cargado una vez y cacheado mientras se mire. */
export function ComprobanteAlLado({ ticket }: { ticket: Ticket }) {
  const [url, setUrl] = useState<string | null>(null);
  /** El tipo con el que se PINTA. En un HEIC es `image/jpeg` (la previa), no
   *  `application/octet-stream` (el original): con el del original caeria otra
   *  vez en el visor de PDF teniendo la foto ahi mismo. */
  const [tipo, setTipo] = useState<string | undefined>();
  const [nombreArchivo, setNombreArchivo] = useState<string | null>(null);
  const [esVistaPrevia, setEsVistaPrevia] = useState(false);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // El `Blob` del comprobante no se vuelve a pedir mientras siga en pantalla:
  // el operador va y vuelve entre la lista y el formulario dozens de veces, y
  // cada una sería una descarga de 2 MB. Se cancela al cambiar de ticket, que
  // es cuando el anterior ya no se mira.
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    let vivo = true;
    setUrl(null);
    urlRef.current = null;
    setError(null);
    setTipo(undefined);
    setNombreArchivo(null);
    setEsVistaPrevia(false);
    setCargando(false);

    if (!ticket.tiene_documento) return;

    setCargando(true);
    Promise.allSettled([
      ticketsApi.documento(ticket.id),
      ticketsApi.vistaPrevia(ticket.id),
    ])
      .then(([original, previa]) => {
        if (!vivo) return;
        if (original.status === 'rejected') {
          setError(original.reason instanceof Error
            ? original.reason.message
            : 'No se pudo cargar el comprobante');
          return;
        }

        const blobOriginal = original.value;
        setNombreArchivo(nombreDelContentDisposition(blobOriginal.disposition));

        if (previa.status === 'fulfilled' && previa.value.type.startsWith('image/')) {
          setEsVistaPrevia(true);
          setTipo(previa.value.type);
          const nueva = URL.createObjectURL(previa.value);
          urlRef.current = nueva;
          setUrl(nueva);
        } else {
          // Sin conversion disponible se pinta el original tal cual. Un fallo
          // de la vista previa significa "no hay conversion", nunca "no hay
          // comprobante": son dos cosas que piden acciones opuestas.
          setEsVistaPrevia(false);
          setTipo(blobOriginal.type || undefined);
          const nueva = URL.createObjectURL(blobOriginal);
          urlRef.current = nueva;
          setUrl(nueva);
        }
      })
      .catch((e: unknown) => {
        if (!vivo) return;
        setError(e instanceof Error ? e.message : 'No se pudo cargar el comprobante');
      })
      .finally(() => {
        if (vivo) setCargando(false);
      });

    // El `Blob` anterior se libera. Sin esto, revisar 40 tickets deja 40
    // objetos de 2 MB vivos en la pestana: en un escritorio con muchas pestanas
    // abiertas es la diferencia entre 100 MB y 1 GB.
    return () => {
      vivo = false;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    };
  }, [ticket.id, ticket.tiene_documento]);

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
      ) : url ? (
        /* `key={url}` remite el visor cuando cambia el papel. Sin esto, pasar de
           un ticket a otro conservaria el zoom y el dialogo abierto del anterior. */
        <VistaDelComprobante
          key={url}
          url={url}
          tipo={tipo}
          nombreArchivo={nombreArchivo}
          esVistaPrevia={esVistaPrevia}
          proveedor={ticket.provider_name}
        />
      ) : null}
    </div>
  );
}

/**
 * Lo que se pinta, separado de la carga.
 *
 * No es por estilo: es para poder verificarlo. `renderToStaticMarkup` no corre
 * `useEffect`, asi que el componente que pide los blobs no se puede montar en
 * un verificador sin un DOM. Aqui todo entra por props y `ampliarInicial` /
 * `zoomInicial` existen solo para que el verificador pueda mirar el HTML del
 * dialogo y del zoom, igual que `pestanaInicial` en Mantenimiento.
 */
export function VistaDelComprobante({
  url,
  tipo,
  nombreArchivo,
  esVistaPrevia,
  proveedor,
  ampliarInicial = false,
  zoomInicial = 1,
}: {
  url: string;
  tipo?: string;
  nombreArchivo: string | null;
  esVistaPrevia: boolean;
  proveedor: string;
  ampliarInicial?: boolean;
  zoomInicial?: number;
}) {
  const [ampliar, setAmpliar] = useState(ampliarInicial);
  const [zoom, setZoom] = useState(zoomInicial);

  const esImagen = tipo?.startsWith('image/') ?? false;
  // El nombre del archivo es lo unico que distingue un HEIC de cualquier otra
  // cosa que llegue como octet-stream: el backend lo sirve asi a proposito.
  // Se mira el NOMBRE y no el `tipo` de la imagen pintada, porque en un HEIC
  // ese tipo es `image/jpeg` (la previa) y hacerlo caeria en "no es HEIC" justo
  // cuando lo es: el enlace de descarga perderia el nombre original.
  const esHeic = /\.(heic|heif)$/i.test(nombreArchivo ?? '');
  const nombreLegible = tipo === 'application/pdf'
    ? 'PDF'
    : (esHeic ? 'archivo HEIC/HEIF' : 'archivo');
  const alt = `Comprobante original de ${proveedor}`;

  useEffect(() => {
    if (!ampliar) return;
    const alTeclear = (evento: KeyboardEvent) => {
      if (evento.key === 'Escape') setAmpliar(false);
    };
    document.addEventListener('keydown', alTeclear);
    return () => document.removeEventListener('keydown', alTeclear);
  }, [ampliar]);

  return (
    <>
      <div className="flex-1 overflow-auto bg-gray-900/5 border border-gray-200 rounded-lg">
        {esImagen ? (
          <>
            {/* `<img>` y no `<object type="application/pdf">`: el navegador
               intenta pintar un JPEG con el visor de PDF y sale un recuadro
               vacio. Y `<object>` vuelve a pedir el recurso SIN la cabecera
               `Authorization`, que sale 401. El `Blob` ya viene autenticado. */}
            <button
              type="button"
              onClick={() => setAmpliar(true)}
              title="Ampliar comprobante"
              aria-label="Ampliar comprobante"
              className="block w-full cursor-zoom-in"
            >
              <img src={url} alt={alt} className="w-full h-auto max-h-[60vh] object-contain" />
            </button>
            <div className="p-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-600 border-t border-gray-200">
              <button
                type="button"
                onClick={() => setAmpliar(true)}
                className="text-blue-600 hover:text-blue-800 font-medium"
              >
                Ampliar comprobante
              </button>
              {esVistaPrevia && (
                <span>
                  Vista previa convertida a JPEG para poder mostrarla aqui. El
                  archivo original ({nombreArchivo ?? 'el HEIC'}) no se ha modificado.
                </span>
              )}
              {esHeic && (
                <a href={url} download={nombreArchivo ?? 'comprobante'} className="text-blue-600 underline">
                  Descargar original
                </a>
              )}
            </div>
          </>
        ) : (
          <div className="min-h-64">
            <object
              data={url}
              type={tipo ?? 'application/pdf'}
              className="w-full h-64"
              aria-label={alt}
            >
              {/* El fallback de `<object>`: si el navegador no pinta el visor,
                  sale esto. Un PDF que se ve en blanco sin explicar nada es el
                  peor resultado posible para quien esta corrigiendo un importe. */}
              <div className="p-4 text-sm text-gray-700">
                <p>
                  Este comprobante es un <strong>{nombreLegible}</strong>, un
                  formato que el navegador no puede mostrar aqui.
                </p>
                {esHeic && (
                  <p className="mt-1">Descargalo y abrilo con una app que lo lea (en macOS, Vista Previa).</p>
                )}
                <a
                  href={url}
                  download={esHeic ? (nombreArchivo ?? 'comprobante') : undefined}
                  target={esHeic ? undefined : '_blank'}
                  rel="noopener noreferrer"
                  className="text-blue-600 underline mt-1 inline-block"
                >
                  {esHeic ? 'Descargar el archivo original' : 'Abrir en otra pestaña'}
                </a>
              </div>
            </object>
          </div>
        )}
      </div>

      {ampliar && esImagen && (
        /* El dialogo va dentro del modal de Edicion (`z-50`), por eso `z-[60]`.
           `stopPropagation` evita que un clic aqui cierre el modal de abajo. */
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Comprobante ampliado de ${proveedor}`}
          onClick={(evento) => evento.stopPropagation()}
          className="fixed inset-0 z-[60] flex flex-col bg-black/90 p-4"
        >
          <div className="flex items-center justify-between gap-3 text-white">
            <p className="text-sm truncate">{alt}</p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setZoom((z) => Math.max(1, +(z - 0.5).toFixed(1)))}
                disabled={zoom <= 1}
                aria-label="Alejar"
                className="rounded bg-white/10 hover:bg-white/20 px-2 py-1 text-sm disabled:opacity-40"
              >
                −
              </button>
              <span className="text-xs tabular-nums">{Math.round(zoom * 100)}%</span>
              <button
                type="button"
                onClick={() => setZoom((z) => Math.min(4, +(z + 0.5).toFixed(1)))}
                disabled={zoom >= 4}
                aria-label="Acercar"
                className="rounded bg-white/10 hover:bg-white/20 px-2 py-1 text-sm disabled:opacity-40"
              >
                +
              </button>
              <button
                type="button"
                onClick={() => setZoom(1)}
                aria-label="Ajustar"
                className="rounded bg-white/10 hover:bg-white/20 px-2 py-1 text-xs"
              >
                Ajustar
              </button>
              <button
                type="button"
                onClick={() => setAmpliar(false)}
                aria-label="Cerrar"
                className="rounded bg-white/10 hover:bg-white/20 px-2 py-1 text-xl leading-none"
              >
                ×
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-auto flex items-start justify-center mt-3">
            <img
              src={url}
              alt={`Comprobante ampliado de ${proveedor}`}
              className={zoom === 1 ? 'max-h-[92vh] max-w-[92vw] object-contain' : 'h-auto max-w-none'}
              style={zoom === 1 ? undefined : { width: `${zoom * 100}%` }}
            />
          </div>
          <p className="mt-2 text-center text-xs text-white/70">
            Escape para cerrar. La imagen ampliada es la misma ya descargada; no se vuelve a pedir al servidor.
          </p>
        </div>
      )}
    </>
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
