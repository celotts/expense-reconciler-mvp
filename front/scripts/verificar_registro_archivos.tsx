/**
 * En "Registro de archivos", el NOMBRE del archivo abre su ticket.
 *
 * ## POR QUE ESTE ARCHIVO EXISTE
 *
 * La columna Archivo era texto. La unica forma de ver el papel estaba en la
 * ultima columna ("Detalle"), sin ninguna pista de que esa fila y ese boton
 * tuvieran que ver. La columna que muestra `IMG_4409.heic` es la que invita a
 * pulsar: ahi tiene que estar el boton, y el boton tiene que llevar el nombre
 * de la imagen.
 *
 * `tsc` no ve esto: un `<span>` con el nombre compila igual que un `<button>`.
 * Por eso se monta la tabla con filas reales y se mira **el HTML**.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { TablaArchivos, nombreDelArchivo } from '../src/pages/Scan';
import type { ScanFile } from '../src/types/scan';

let fallos = 0;

function comprobar(nombre: string, condicion: boolean, porQue: string): void {
  if (condicion) {
    console.log(`  ok   ${nombre}`);
  } else {
    fallos++;
    console.error(`  FALLA ${nombre} — ${porQue}`);
  }
}

function fila(id: string, ruta: string, ticketId: string | null): ScanFile {
  return {
    id,
    relative_path: ruta,
    content_hash: 'abc',
    file_size: 1234,
    file_mtime: null,
    detected_format: 'image',
    declared_extension: 'heic',
    status: 'PROCESADO',
    attempts: 1,
    read_by: 'ocr',
    last_error: null,
    ticket_id: ticketId,
    company_id: 'f219f621-b5f1-40a7-985e-798cd50c229a',
    ticket_negocio: 'GRUPO COMERCIAL DSW',
    ticket_fecha: '2026-10-16',
    ticket_total: '279.93',
    first_seen_at: '2026-10-16T00:00:00',
    last_scanned_at: null,
    processed_at: null,
  };
}

const html = renderToStaticMarkup(
  <TablaArchivos
    archivos={[
      fila('ticket-1', '2026/octubre/IMG_4409.heic', 'd4c814b1-226b-4718-a497-8b24955d7923'),
      fila('sin-ticket', 'IMG_4253 2.HEIC', null),
    ]}
    onAbrir={() => undefined}
  />
);

console.log('\nEL NOMBRE DE LA IMAGEN ES UN BOTON:');
comprobar('el nombre del archivo esta en un boton',
  /<button[^>]*title="Ver el archivo IMG_4409\.heic y su ticket, si tiene"[^>]*>IMG_4409\.heic<\/button>/.test(html),
  'la columna Archivo sigue siendo texto muerto');
comprobar('el boton no muestra la carpeta en la etiqueta',
  !/>[^<]*2026\/octubre\/IMG_4409\.heic</.test(html),
  'la etiqueta lleva la ruta entera en vez del nombre de la imagen');
comprobar('la ruta completa sigue disponible en el title',
  html.includes('title="2026/octubre/IMG_4409.heic"'),
  'se perdio la ruta que distingue dos archivos con el mismo nombre');
comprobar('un archivo sin ticket tambien se puede abrir',
  /<button[^>]*title="Ver el archivo IMG_4253 2\.HEIC y su ticket, si tiene"/.test(html),
  'el papel rechazado es justo el que alguien tiene que mirar');

console.log('\nEL BOTON ABRE EL MISMO DETALLE QUE "DETALLE":');
const fuente = readFileSync(resolve(process.cwd(), 'src', 'pages', 'Scan.tsx'), 'utf-8');
const inicio = fuente.indexOf('title={`Ver el archivo');
// El `onClick` va ANTES del `title` en el JSX, asi que se toma el trozo que lo
// contiene a los dos. Buscar solo hacia adelante daria falso: el boton abriria
// y el verificador diria que no.
const celda = inicio >= 0 ? fuente.slice(Math.max(0, inicio - 400), inicio + 500) : '';
comprobar('el boton llama a onAbrir con el id del archivo',
  /onAbrir\(f\.id\)/.test(celda),
  'el boton existe pero no abre nada');
comprobar('la columna Ticket sigue teniendo su propio acceso',
  html.includes('>Ver ticket</button>'),
  'se quito la forma explicita de abrir el ticket desde su columna');

console.log('\nEL NOMBRE SE SACA DEL ARCHIVO, NO DE LA CARPETA:');
comprobar('nombreDelArchivo recorta la carpeta',
  nombreDelArchivo('2026/octubre/IMG_4409.heic') === 'IMG_4409.heic',
  'el boton mostrara la ruta en vez del nombre');
comprobar('nombreDelArchivo respeta un nombre con espacios',
  nombreDelArchivo('IMG_4253 2.HEIC') === 'IMG_4253 2.HEIC',
  'un nombre real de la camara se trunco');

if (fallos > 0) {
  console.error(`\n${fallos} comprobacion(es) fallaron.`);
  process.exit(1);
}
console.log('\nTodas las comprobaciones pasaron.');
