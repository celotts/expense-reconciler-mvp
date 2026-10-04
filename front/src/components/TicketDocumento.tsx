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
export function VerDocumento({ ticket }: { ticket: Ticket }) {
  const [abierto, setAbierto] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    setAbierto(false);
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

  const alternar = async () => {
    if (abierto) {
      setAbierto(false);
      return;
    }
    setAbierto(true);
    if (url) return; // ya esta cargado

    setCargando(true);
    setError(null);
    try {
      const blob = await ticketsApi.documento(ticket.id);
      // El `Blob` conserva el `Content-Type` de la respuesta, asi que el tipo real
      // viaja con los bytes sin pedirlo por separado.
      setTipo(blob.type || undefined);
      setUrl(URL.createObjectURL(blob));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo cargar el comprobante');
    } finally {
      setCargando(false);
    }
  };

  return (
    <div className="mt-3">
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
        /* Una foto con `<object type="application/pdf">` no se ve: el navegador
           intenta pintar un JPEG con el visor de PDF y sale un recuadro vacio. Es
           lo que pasaba con todas las fotos del escaner —no con los PDF, que si
           se veian— y por eso el tipo sale del Blob y no de una constante.

           `<img>` y no `<object>` porque `<object>` descarga el recurso por su
           cuenta con una segunda peticion sin cabecera `Authorization`, que sale
           401 y tampoco lo pinta. El `Blob` ya viene autenticado y el
           `createObjectURL` no vuelve a pedirlo. */
        <img
          src={url}
          alt={`Comprobante original de ${ticket.provider_name}`}
          className="mt-2 max-h-96 w-full rounded border border-gray-200 object-contain"
        />
      )}

      {abierto && url && !esImagen && (
        <object
          data={url}
          type={tipo ?? 'application/pdf'}
          className="mt-2 w-full h-96 border border-gray-200 rounded"
          aria-label={`Comprobante original de ${ticket.provider_name}`}
        >
          {/* Un PDF que el navegador no puede pintar. Se muestra el enlace para
              abrirlo aparte en vez de un recuadro en blanco, que es lo que hace
              que el revisor concluya que el documento no existe. */}
          <p className="text-xs text-gray-600">
            Tu navegador no puede mostrar el comprobante aquí.{' '}
            <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline">
              Abrirlo en una pestaña
            </a>
          </p>
        </object>
      )}

      {abierto && url && (
        <p className="mt-1 text-xs text-gray-500">
          {/* El enlace al archivo guardado, no al de la API: este ultimo pide
              token y en una pestaña nueva no lo lleva. */}
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 underline"
          >
            Abrir en tamaño completo
          </a>
        </p>
      )}
    </div>
  );
}
