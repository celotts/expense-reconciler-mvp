/**
 * El comprobante al lado de los campos SE VE, y se puede ampliar.
 *
 * ## POR QUE ESTE ARCHIVO EXISTE
 *
 * MEDIDO: `ComprobanteAlLado` pedia solo `/documento` y decidia por
 * `blob.type`. Un HEIC real (`IMG_4409.heic`, ticket
 * `d4c814b1-226b-4718-a497-8b24955d7923`) se sirve como
 * `application/octet-stream`, asi que caia en el `<object>` y el recuadro de
 * "Comprobante original" quedaba vacio. El boton de Tickets que decia
 * "verlo en grande" abria ESE original: el navegador lo descargaba.
 *
 * `tsc` pasa con un recuadro vacio. Por eso esto monta el componente con los
 * datos del HEIC (la previa JPEG que devuelve el backend y el nombre del
 * `Content-Disposition` del original) y mira **el HTML**, no el retorno de una
 * funcion: es lo que pintara el navegador.
 *
 * ## QUE SE COMPRUEBA
 *
 * 1. Se pinta un `<img>` con la previa, no un `<object>` con el octet-stream.
 * 2. Hay una forma explicita de ampliar (boton y clic en la imagen).
 * 3. Se dice que lo que se ve es una conversion y que el original no cambio.
 * 4. El original sigue teniendo su nombre y su enlace de descarga.
 * 5. El dialogo ampliado trae acercar/alejar/ajustar y la imagen crece.
 * 6. El cargador pide original Y previa en paralelo, y el tipo que decide como
 *    se pinta es el de la previa. Una sola peticion es el bug de origen.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { VistaDelComprobante } from '../src/components/ComprobanteAlLado';

let fallos = 0;

function comprobar(nombre: string, condicion: boolean, porQue: string): void {
  if (condicion) {
    console.log(`  ok   ${nombre}`);
  } else {
    fallos++;
    console.error(`  FALLA ${nombre} — ${porQue}`);
  }
}

function fuente(de: string): string {
  return readFileSync(resolve(process.cwd(), 'src', de), 'utf-8');
}

/** Los datos MEDIDOS del ticket HEIC: la previa se pinta, el original se nombra. */
const HEIC = {
  url: 'blob:vista-previa-jpeg',
  tipo: 'image/jpeg',
  nombreArchivo: 'IMG_4409.heic',
  esVistaPrevia: true,
  proveedor: 'GRUPO COMERCIAL DSW S.A. DE C.V.',
};

const normal = renderToStaticMarkup(<VistaDelComprobante {...HEIC} />);
const ampliado = renderToStaticMarkup(
  <VistaDelComprobante {...HEIC} ampliarInicial zoomInicial={2} />
);

console.log('\nLA IMAGEN SE PINTA EN EL RECUADRO:');
comprobar('se pinta un <img>', normal.includes('<img'),
  'el recuadro sigue vacio: no se pinta la vista previa');
comprobar('el <img> es el comprobante de ese proveedor',
  normal.includes('alt="Comprobante original de GRUPO COMERCIAL DSW S.A. DE C.V."'),
  'la imagen mostrada no se identifica con el ticket');
comprobar('no se cae al visor de PDF con el octet-stream', !normal.includes('<object'),
  'un HEIC vuelve a caer en el <object> que no pinta');
comprobar('la imagen se puede ampliar haciendo clic',
  normal.includes('cursor-zoom-in') && normal.includes('title="Ampliar comprobante"'),
  'la imagen no invita a ampliarla');
comprobar('hay un boton explicito para ampliar',
  normal.includes('>Ampliar comprobante</button>'),
  'sin boton, ampliar depende de adivinar que la imagen es clicable');

console.log('\nSE DICE QUE ES UNA CONVERSION, Y EL ORIGINAL SIGUE AHI:');
comprobar('se explica que la vista previa es JPEG',
  normal.includes('Vista previa convertida a JPEG'),
  'quien mira no sabe por que puede ver un HEIC');
comprobar('se nombra el archivo original', normal.includes('IMG_4409.heic'),
  'se perdio el Content-Disposition del original');
comprobar('se dice que el original no se modifica',
  normal.includes('no se ha modificado'),
  'conversion para mostrar no es lo mismo que alterar el papel');
comprobar('se puede descargar el original con su nombre',
  normal.includes('download="IMG_4409.heic"'),
  'la descarga volvio a ser un nombre generico');

console.log('\nAMPLIAR MUESTRA LA IMAGEN GRANDE Y DEJA ACERCAR:');
comprobar('se abre un dialogo', ampliado.includes('role="dialog"') && ampliado.includes('aria-modal="true"'),
  'no hay una vista ampliada');
comprobar('el dialogo queda por encima del modal de edicion', ampliado.includes('z-[60]'),
  'el dialogo queda detras del modal z-50 y no se ve');
comprobar('hay controles de acercar y alejar',
  ampliado.includes('aria-label="Acercar"') && ampliado.includes('aria-label="Alejar"'),
  'ampliar es solo abrir, no ampliar');
comprobar('hay ajustar y cerrar',
  ampliado.includes('aria-label="Ajustar"') && ampliado.includes('aria-label="Cerrar"'),
  'no se puede volver al tamano ajustado ni salir');
comprobar('el zoom agranda la imagen de verdad', ampliado.includes('width:200%'),
  'los botones de zoom no cambian el tamano de la imagen');
comprobar('la imagen ampliada sigue siendo la misma URL',
  ampliado.includes('alt="Comprobante ampliado de GRUPO COMERCIAL DSW S.A. DE C.V."'),
  'al ampliar se pidio otra vez el comprobante');

console.log('\nEL CARGADOR PIDE LAS DOS PIEZAS:');
const componente = fuente('components/ComprobanteAlLado.tsx');
comprobar('pide el original', /ticketsApi\.documento\(ticket\.id\)/.test(componente),
  'sin original no hay nombre ni descarga');
comprobar('pide la vista previa', /ticketsApi\.vistaPrevia\(ticket\.id\)/.test(componente),
  'el bug de origen era pedir solo el original');
comprobar('las pide en paralelo', /Promise\.allSettled\(/.test(componente),
  'dos peticiones en serie suman latencia en cada ticket');
comprobar('el tipo que decide como pintar es el de la previa',
  /setTipo\(previa\.value\.type\)/.test(componente),
  'se pinta con el tipo del original y un HEIC cae otra vez al visor de PDF');
comprobar('lee el nombre del Content-Disposition del original',
  /nombreDelContentDisposition\(blobOriginal\.disposition\)/.test(componente),
  'un HEIC vuelve a ser indistinguible de un .zip');
comprobar('un fallo de la previa no tumba la carga del original',
  /previa\.status === 'fulfilled'/.test(componente),
  'sin conversion se pierde tambien el comprobante');

console.log('\nUNA SOLA FORMA DE LEER EL NOMBRE DEL ARCHIVO:');
const compartido = fuente('utils/documento.ts');
comprobar('el lector shared entiende filename*',
  /filename\\\*/.test(compartido) && /decodeURIComponent/.test(compartido),
  'un nombre con acentos se lee mal');
comprobar('TicketDocumento reusa el lector',
  /import \{ nombreDelContentDisposition \} from '\.\.\/utils\/documento'/.test(
    fuente('components/TicketDocumento.tsx')),
  'hay dos copias del lector y una dejara de entender filename*');

console.log('\nTICKETS NO ABRE EL ORIGINAL SIN CONVERTIR:');
const tickets = fuente('pages/Tickets.tsx');
comprobar('Tickets no crea object URLs por su cuenta', !/createObjectURL/.test(tickets),
  'volvio la segunda forma de cargar el comprobante');
comprobar('Tickets no ofrece abrir el original en otra pestana',
  !/para verlo en grande/.test(tickets),
  'el boton que prometia ampliar y descargaba el HEIC volvio');

if (fallos > 0) {
  console.error(`\n${fallos} comprobacion(es) fallaron.`);
  process.exit(1);
}
console.log('\nTodas las comprobaciones pasaron.');
