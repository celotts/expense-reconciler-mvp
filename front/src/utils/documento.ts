/**
 * El nombre del archivo de un `Content-Disposition`.
 *
 *  `"attachment; filename=\"IMG_4399.HEIC\""` -> `IMG_4399.HEIC`.
 *
 *  Se mira `filename*` primero, que es la forma con codificacion UTF-8 y la
 *  unica que sobrevive a un nombre con acentos: un `filename=` a secas llega en
 *  latin-1 y un comprobante de "Café Milano" se llamaria `Caf_.pdf`, que es
 *  justo el nombre que hace dudar de que el archivo sea el correcto.
 *
 * Vive aqui y no dentro de un componente porque lo necesitan los tres sitios
 * que muestran el papel (`VerDocumento`, `VerArchivoEscaneado` y
 * `ComprobanteAlLado`). Tres copias de la misma lectura del header es la forma
 * de que una de ellas deje de entender `filename*` sin que nadie lo note.
 */
export function nombreDelContentDisposition(cd: string | null): string | null {
  if (!cd) return null;
  const Star = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(cd);
  if (Star) {
    try {
      return decodeURIComponent(Star[1].trim());
    } catch {
      /* mal codificado: se cae al `filename=` de abajo */
    }
  }
  const simple = /filename\s*=\s*"?([^";]+)"?/i.exec(cd);
  return simple ? simple[1].trim() : null;
}
