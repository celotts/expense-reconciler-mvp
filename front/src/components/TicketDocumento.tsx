import { useEffect, useState } from 'react';
import { ticketsApi, type Ticket } from '../services/api';

/**
 * El comprobante original, junto a los datos que el sistema leyo de el.
 *
 * Por que es un componente y no un boton suelto en cada pantalla
 * --------------------------------------------------------------
 *
 * Lo usan dos pantallas -la cola de revision y el muestreo de exactitud- y las
 * dos necesitan exactamente lo mismo: abrir el papel, ver cuanto pesa, y saber
 * distinguir "no hay documento" de "el sistema fallo". Duplicado en las dos, el
 * primer arreglo de una no llega a la otra, y el caso grave es justo ese: el
 * muestreo se queda sin ver el papel y sigue dando numeros, y nadie se entera
 * de que se estan midiendo a ciegas.
 *
 * Por que se descarga con `fetch` y no se pinta con `<img src>`
 * ------------------------------------------------------------
 *
 * El endpoint exige cabecera `Authorization`, y un `src` no la manda. Con
 * `<img src={ticket.documento_url}>` el revisor ve un recuadro vacio y no sabe
 * si el documento no esta o si la peticion fallo, que son dos cosas que se
 * comprueban de forma distinta. Descargarlo con el token y hacer un `Blob` URL
 * hace que el fallo se vea, y que se pueda distinguir de "aun no lo he abierto".
 *
 * El `Blob` URL se revoca al cerrar. Sin eso, cada apertura deja el comprobante
 * completo en la memoria del navegador hasta que se recargue la pagina: hasta
 * 10 MB por cada ticket que se mira. Es la clase de fuga que no da error y se
 * paga con que la pestana se come la memoria del equipo.
 *
 * Por que el aviso de "no hay documento" distingue las dos causas
 * --------------------------------------------------------------
 *
 * Porque `tiene_documento: false` no es un fallo del sistema, y tratarlo como
 * uno hace que se deje de confiar en la cola entera. Hay dos causas legitimas
 * y piden cosas opuestas: la captura manual no viene de un archivo y no se le
 * puede subir uno, y una captura anterior al almacenamiento se resuelve
 * volviendo a subir el comprobante. Con un aviso generico, ambas se leen como
 * "el sistema no esta funcionando", y quien lo ve deja de mirar los otros
 * mensajes de la pantalla.
 */
/** El nombre del archivo de un `Content-Disposition`.
 *
 *  `"attachment; filename=\"IMG_4399.HEIC\""` -> `IMG_4399.HEIC`.
 *
 *  Se mira `filename*` primero, que es la forma con codificacion UTF-8 y la
 *  unica que sobrevive a un nombre con acentos: un `filename=` a secas llega en
 *  latin-1 y un comprobante de "Café Milano" se llamaria `Caf_.pdf`, que es
 *  justo el nombre que hace dudar de que el archivo sea el correcto. */
function nombreDelContentDisposition(cd: string | null): string | null {
  if (!cd) return null;
  const Star = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(cd);
  if (Star) {
    try { return decodeURIComponent(Star[1].trim()); } catch { /* mal codificado */ }
  }
  const simple = /filename\s*=\s*"?([^";]+)"?/i.exec(cd);
  return simple ? simple[1].trim() : null;
}

/** El veredicto de vida, con los mismos datos que el PDF que se descarga. */
export function VerDocumento({ ticket, abiertoPorDefecto = false }: { ticket: Ticket; abiertoPorDefecto?: boolean }) {
  const [abierto, setAbierto] = useState(abiertoPorDefecto);
  const [url, setUrl] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** El nombre del archivo, del `Content-Disposition` de la respuesta.
   *
   *  Es lo unico que distingue un HEIC de cualquier otra cosa que llegue como
   *  `application/octet-stream` —un `.zip`, un `.bin`— porque el backend sirve
   *  `image/heic` como octet-stream a proposito (ningun navegador lo pinta). Sin
   *  esto el mensaje seria el mismo para los dos, y no se podria decir cual es. */
  const [nombreArchivo, setNombreArchivo] = useState<string | null>(null);
  /** Si lo que se esta pintando es el JPEG derivado y no el original.
   *
   *  Importa porque el mensaje de "no se puede mostrar" se decide por el TIPO que
   *  se Pinto, y en un HEIC ese tipo es `image/jpeg` aunque el archivo guardado
   *  siga siendo HEIC. Sin esta bandera, un `<img>` que funciona caeria en el
   *  mensaje de "tu navegador no puede mostrarlo" por mirar el tipo equivocado. */
  const [esVistaPrevia, setEsVistaPrevia] = useState(false);
  // El tipo con el que el backend sirvio los bytes. Lo decide el backend desde una
  // lista cerrada (`content_type_servible`) y no el cliente: ver
  // `app/models/ticket_document.py`. Un `undefined` significa octet-stream.
  const [tipo, setTipo] = useState<string | undefined>(undefined);

  // El `Blob` URL se libera al cerrar o al cambiar de ticket. Sin este efecto,
  // abrir cinco comprobantes deja los cinco en memoria: un comprobante de
  // ticket es de hasta 10 MB, y eso no lo nota la persona que revisa, lo nota
  // el equipo cuando la pestana tarda diez segundos en abrir.
  useEffect(() => {
    if (url) {
      return () => URL.revokeObjectURL(url);
    }
  }, [url]);

  // Cambiar de ticket con el panel abierto dejaria mostrando el papel del
  // anterior, que es peor que no mostrar nada: es la forma de que alguien
  // apruebe un ticket mirando el comprobante equivocado.
  useEffect(() => {
    setAbierto(abiertoPorDefecto);
    setUrl(null);
    setError(null);
    setTipo(undefined);
  }, [ticket.id]);

  if (!ticket.tiene_documento) {
    const esManual = ticket.source_type === 'manual';
    return (
      <p className="mt-3 text-xs text-amber-700">
        {esManual
          ? 'Ticket capturado a mano: no hay archivo que revisar, los datos los escribió una persona.'
          : 'No hay comprobante guardado. Se puede volver a subir el archivo para revisarlo contra el papel.'}
      </p>
    );
  }

  const peso = ticket.documento_tamano
    ? `${(ticket.documento_tamano / 1024).toFixed(0)} KB`
    : null;

  // El backend decide si esto se puede pintar como imagen, y no esta pantalla: el
  // `Content-Type` sale de una lista cerrada de tipos que el navegador no ejecuta
  // (`content_type_servible`). Lo que no esta en ella llega como
  // `application/octet-stream` y cae al visor de PDF, que para un octet-stream
  // muestra un recuadro vacio con su mensaje de "no se puede mostrar" — el
  // mismo mensaje que veria alguien a quien de verdad no se le puede pintar.
  const esImagen = tipo?.startsWith('image/') ?? false;

  // ## SI EL DOCUMENTO ES UN HEIC/HEIF
  //
  // No sale por la EXTENSION sino por el `Content-Type`, porque ahi esta el
  // problema: el backend sirve `application/octet-stream` para lo que no esta en
  // su lista cerrada de tipos servibles, y `image/heic` NO esta en ella a
  // proposito (ningun navegador de escritorio lo pinta). O sea que para el front
  // un HEIC y un `.zip` llegan exactamente igual, y el mensaje que se le da a la
  // persona tiene que ser util para los dos.
  //
  // MEDIDO: el mensaje anterior decia "Tu navegador no puede mostrar el
  // comprobante aqui. **Abrirlo en una pestana**". Ese enlace no funcionaba:
  // `octet-stream` hace que el navegador DESCARGUE en vez de abrir, y el propio
  // componente lo dice mas abajo ("este pide token y en una pestana nueva no lo
  // lleva"). Ofrecer la accion que no funciona es peor que no ofrecer ninguna:
  // hace que el revisor concluya que el documento no existe.
  const octetStream = tipo === 'application/octet-stream' || !tipo;
  const esHeic = octetStream && (nombreArchivo ?? '').match(/\.(heic|heif)$/i) !== null;

  const nombreLegible = tipo === 'application/pdf' ? 'PDF' : (esHeic ? 'archivo HEIC/HEIF' : 'archivo');

  // Se carga al abrir, y tambien al montar cuando viene `abiertoPorDefecto`:
  // en el detalle del Escaner el papel es lo que se viene a ver, no un
  // desplegable mas. Sin esto, el original seguiria escondido detras de un
  // boton justo donde se contrastan los datos leidos contra el papel.
  const cargar = async () => {
    if (url) return; // ya esta cargado

    setCargando(true);
    setError(null);
    try {

    // ## POR QUE SE PIDE LA VISTA PREVIA Y LUEGO EL ORIGINAL
    //
    // La vista previa es la que se PINTA: el backend convierte a JPEG lo que el
    // navegador no sabe pintar (un HEIC de iPhone) y deja intacto el original.
    // El original se pide ademas porque aporta dos cosas que la vista previa no
    // tiene: el `Content-Disposition`, que es lo unico que distingue un HEIC de
    // cualquier otro `application/octet-stream` (un `.zip` llega igual), y el
    // enlace de descarga.
    //
    // Se piden en paralelo y no en serie a proposito: son dos peticiones
    // independientes y encadenarlas sumaria las dos latencias. MEDIDO, la
    // conversion de un HEIC de 3 MP tarda 0.18-0.25 s, que es justo lo que se
    // paga esperando de mas si se hace una y luego la otra.
    //
    // Y si la vista previa falla NO se cae la pantalla: se usa el original. Un
    // fallo ahi significa "no hay conversion", nunca "no hay comprobante", y
    // esas dos cosas piden acciones opuestas.
    const [original, previa] = await Promise.allSettled([
      ticketsApi.documento(ticket.id),
      ticketsApi.vistaPrevia(ticket.id),
    ]);

    if (original.status === 'rejected') {
      setError(original.reason instanceof Error
        ? original.reason.message
        : 'No se pudo cargar el comprobante');
      return;
    }

    const blobOriginal = original.value;
    // El `Blob` conserva el `Content-Type` de la respuesta, asi que el tipo real
    // viaja con los bytes sin pedirlo por separado.
    setTipo(blobOriginal.type || undefined);
    // Y el nombre del archivo, del `Content-Disposition`, porque es lo unico
    // que distingue un HEIC de cualquier otra cosa que llegue como
    // `application/octet-stream` (y lo hace el backend, que lo manda siempre).
    setNombreArchivo(nombreDelContentDisposition(blobOriginal.disposition));

    if (previa.status === 'fulfilled' && previa.value.type.startsWith('image/')) {
      setEsVistaPrevia(true);
      // El tipo que decide COMO SE PINTA es el de la vista previa
      // (`image/jpeg`), no el del original (`application/octet-stream` para un
      // HEIC). Con el del original, un HEIC caeria al visor de PDF y apareceria
      // el mensaje de "no se puede mostrar" TENIENDO la foto ahi mismo.
      setTipo(previa.value.type);
      setUrl(URL.createObjectURL(previa.value));
    } else {
      // Sin conversion disponible: el original tal cual, con su tipo. Un JPEG o
      // un PDF llegan por aqui con su propio `image/*` y se pintan igual, porque
      // la vista previa devuelve los bytes originales cuando no hay nada que
      // convertir.
      setEsVistaPrevia(false);
      setTipo(blobOriginal.type || undefined);
      setUrl(URL.createObjectURL(blobOriginal));
    }
  } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo cargar el comprobante');
    } finally {
      setCargando(false);
    }
  };

  const alternar = async () => {
    if (abierto) {
      setAbierto(false);
      return;
    }
    setAbierto(true);
    await cargar();
  };

  useEffect(() => {
    if (abiertoPorDefecto) void cargar();
    // Solo al montar: recargar en cada render pediria el comprobante entero
    // una y otra vez.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mt-3">
      <div className="mb-3 p-3 rounded-lg bg-gray-50 border border-gray-200 text-xs space-y-2">
        <div className="min-w-0">
          <p className="font-semibold text-gray-900 truncate" title={ticket.provider_name || 'Sin proveedor'}>{ticket.provider_name || 'Sin proveedor'}</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-500">
            {ticket.provider_tax_id && <span className="font-mono">{ticket.provider_tax_id}</span>}
            {ticket.expense_date && <span>Fecha: {ticket.expense_date}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 tabular-nums pt-0.5">
          {ticket.subtotal && <p><span className="text-gray-500">Subtotal:</span> ${ticket.subtotal}</p>}
          {ticket.tax_amount && <p><span className="text-gray-500">IVA:</span> ${ticket.tax_amount}</p>}
          {ticket.ieps_amount && <p><span className="text-gray-500">IEPS:</span> ${ticket.ieps_amount}</p>}
          <p className="font-semibold"><span className="text-gray-500">Total:</span> ${ticket.total_amount}</p>
        </div>
      </div>

      <button
        onClick={alternar}
        disabled={cargando}
        className="text-xs text-blue-600 hover:text-blue-800 font-medium disabled:opacity-50"
      >
        {abierto ? 'Ocultar comprobante original' : 'Ver comprobante original'}
        {peso && <span className="text-gray-500"> ({peso})</span>}
      </button>

      {cargando && (
        <p className="mt-1 text-xs text-gray-500">Cargando comprobante…</p>
      )}

      {error && (
        <p className="mt-1 text-xs text-red-700">
          No se pudo cargar el comprobante: {error}. Puede que la sesión haya expirado; recarga la página.
        </p>
      )}

      {abierto && url && esImagen && (
        <>
          <img
            src={url}
            alt={`Comprobante original de ${ticket.provider_name}`}
            className="mt-2 max-h-[70vh] w-full rounded border border-gray-200 object-contain"
          />
          {/* Cuando lo que se ve es el JPEG derivado, se dice. */}
          {esVistaPrevia && (
            <p className="mt-1 text-[11px] text-gray-500">
              Vista previa convertida a JPEG para poder mostrarla aquí. El archivo
              original ({nombreArchivo ?? 'el HEIC'}) no se ha modificado.
            </p>
          )}
        </>
      )}

      {abierto && url && !esImagen && (
        <object
          data={url}
          type={tipo ?? 'application/pdf'}
          className="mt-2 w-full h-[70vh] border border-gray-200 rounded"
          aria-label={`Comprobante original de ${ticket.provider_name}`}
        >
          {/* Aqui solo llega lo que NO se pinto, y ya se sabe por que: o es un
              HEIC sin vista previa (conversion fallida), o es un formato que el
              backend sirve como `application/octet-stream`.

              MEDIDO: este bloque antes decia "Tu navegador no puede mostrar el
              comprobante aqui. **Abrirlo en una pestana**". Ese enlace no
              funcionaba —`octet-stream` hace que el navegador DESCARGUE en vez
              de abrir—, y el propio componente lo decia mas abajo ("este pide
              token y en una pestana nueva no lo lleva"). Ofrecer la accion que
              no funciona hace que el revisor concluya que el documento no
              existe. */}
          <p className="text-xs text-gray-600">
            Este comprobante es un <strong>{nombreLegible}</strong>, un formato
            que el navegador no pinta.
            {esHeic && (
              <> Descárgalo y ábrelo con una app que lo lea (en macOS, Vista Previa).</>
            )}
          </p>
        </object>
      )}

      {abierto && url && (
        <p className="mt-1 text-xs text-gray-500">
          {/* El enlace al archivo guardado, no al de la API: este ultimo pide
              token y en una pestaña nueva no lo lleva.

              MEDIDO: en un HEIC este enlace tampoco abria nada util, porque el
              backend lo sirve como `application/octet-stream` y eso hace que el
              navegador lo descargue. Para esos casos lo que sirve es la
              descarga explicita, que si deja el archivo en `Descargas` donde se
              puede abrir con una app que lo lea. */}
          {esHeic ? (
            <a
              href={url}
              download="comprobante"
              className="text-blue-600 underline"
            >
              Descargar el archivo
            </a>
          ) : (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 underline"
            >
              Abrir en tamaño completo
            </a>
          )}
        </p>
      )}
    </div>
  );
}

/**
 * El archivo escaneado de la carpeta, tal como esta en el disco.
 *
 * Es lo que faltaba en el Escaner: un archivo "omitido" no tiene ticket y
 * `VerDocumento` no tenia de donde sacar su papel, asi que el detalle solo
 * mostraba los datos leidos. Este componente pide el original con
 * `ticketsApi.documentoArchivo` (el endpoint nuevo, con token) y la vista
 * previa con `ticketsApi.vistaPreviaArchivo` (solo HEIC/HEIF a JPEG, para
 * poder pintarlo; un PDF, JPEG o PNG salen con sus bytes originales).
 *
 * Se carga solo, sin boton: en el detalle del Escaner el papel es lo que se
 * viene a ver, al lado de los datos leidos. El archivo no se modifica al
 * mirarlo: la conversion a JPEG es solo una representacion para la pantalla.
 */
export function VerArchivoEscaneado({ fileId, nombre }: { fileId: string; nombre: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nombreArchivo, setNombreArchivo] = useState<string | null>(null);
  const [esVistaPrevia, setEsVistaPrevia] = useState(false);
  const [tipo, setTipo] = useState<string | undefined>(undefined);

  useEffect(() => {
    let vivo = true;
    setCargando(true);
    setError(null);
    Promise.allSettled([
      ticketsApi.documentoArchivo(fileId),
      ticketsApi.vistaPreviaArchivo(fileId),
    ]).then(([original, previa]) => {
      if (!vivo) return;
      if (original.status === 'rejected') {
        setError(original.reason instanceof Error
          ? original.reason.message
          : 'No se pudo cargar el archivo escaneado');
        setCargando(false);
        return;
      }
      const blobOriginal = original.value;
      setTipo(blobOriginal.type || undefined);
      setNombreArchivo(nombreDelContentDisposition(blobOriginal.disposition));
      if (previa.status === 'fulfilled' && previa.value.type.startsWith('image/')) {
        setEsVistaPrevia(true);
        setTipo(previa.value.type);
        setUrl(URL.createObjectURL(previa.value));
      } else {
        setEsVistaPrevia(false);
        setUrl(URL.createObjectURL(blobOriginal));
      }
      setCargando(false);
    });
    return () => {
      vivo = false;
      setUrl((previo) => {
        if (previo) URL.revokeObjectURL(previo);
        return null;
      });
    };
  }, [fileId]);

  const esImagen = tipo?.startsWith('image/') ?? false;
  const octetStream = tipo === 'application/octet-stream' || !tipo;
  const esHeic = octetStream && (nombreArchivo ?? nombre).match(/\.(heic|heif)$/i) !== null;
  const nombreLegible = tipo === 'application/pdf' ? 'PDF' : (esHeic ? 'archivo HEIC/HEIF' : 'archivo');

  return (
    <div>
      <p className="text-sm font-medium text-gray-700">Comprobante original escaneado</p>
      <p className="text-xs text-gray-500 truncate" title={nombre}>{nombreArchivo ?? nombre}</p>
      {cargando && (
        <p className="mt-1 text-xs text-gray-500">Cargando comprobante…</p>
      )}
      {error && (
        <p className="mt-1 text-xs text-red-700">
          No se pudo cargar el archivo escaneado: {error}. Puede que el archivo ya no esté en la carpeta.
        </p>
      )}
      {url && esImagen && (
        <>
          <img
            src={url}
            alt={`Archivo escaneado ${nombre}`}
            className="mt-2 max-h-[70vh] w-full rounded border border-gray-200 object-contain"
          />
          {esVistaPrevia && (
            <p className="mt-1 text-[11px] text-gray-500">
              Vista previa convertida a JPEG para poder mostrarla aquí. El archivo original no se ha modificado.
            </p>
          )}
        </>
      )}
      {url && !esImagen && (
        <object
          data={url}
          type={tipo ?? 'application/pdf'}
          className="mt-2 w-full h-[70vh] border border-gray-200 rounded"
          aria-label={`Archivo escaneado ${nombre}`}
        >
          <p className="text-xs text-gray-600">
            Este comprobante es un <strong>{nombreLegible}</strong>, un formato que el navegador no pinta.
            {esHeic && (
              <> Descárgalo y ábrelo con una app que lo lea (en macOS, Vista Previa).</>
            )}
          </p>
        </object>
      )}
      {url && (
        <p className="mt-1 text-xs text-gray-500">
          {esHeic ? (
            <a href={url} download={nombreArchivo ?? 'comprobante'} className="text-blue-600 underline">
              Descargar el archivo original
            </a>
          ) : (
            <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline">
              Abrir en tamaño completo
            </a>
          )}
        </p>
      )}
    </div>
  );
}
