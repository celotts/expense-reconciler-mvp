/**
 * Renderiza `PanelProgreso` con el feed REAL que salio de la prueba de fuego
 * contra la base, y comprueba que el operador ve los datos del ticket.
 *
 * Por que esto y no un login con una cuenta nueva: crear un usuario con una
 * contrasena conocida en la base de trabajo del usuario para ver una pantalla es
 * dejar una puerta abierta por una prueba. Este script no toca la base.
 *
 * Por que no basta con `tsc`: un componente que compila puede pintar `"undefined"`
 * en cada campo, que es exactamente el fallo que se quiere cazar. Aqui se mira
 * el TEXTO que sale.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { PanelProgreso } from '../src/components/ScanProgreso';
import type { ScanCorrida } from '../src/types/scan';

// Las 7 entradas que produjo `_registrar_en_el_feed` sobre los tickets reales.
const procesados = [
  {
    relative_path: 'AA4D0E8F-EC9C-4159-9A9F-C4C84C22303E 2.JPG',
    ticket_id: '38a1c061', monto: '97.56', extraction_status: 'PENDIENTE',
    motor: 'ocr', accion: 'CREADO', confiable: false, es_duplicado: false,
    proveedor: 'AAA AI', rfc: null, fecha: '2026-10-08', subtotal: null,
    iva: '0.00', ieps: null, lineas: null, validation_errors: 'date_missing',
  },
  {
    relative_path: 'FC3CB9F8-5AB1-4546-B9A9-016C69A158C5 2.JPG',
    ticket_id: 'd03f853c', monto: '234.00', extraction_status: 'PENDIENTE',
    motor: 'ocr', accion: 'CREADO', confiable: false, es_duplicado: false,
    proveedor: 'Pog ae — TAPA', rfc: null, fecha: '2026-10-08',
    subtotal: '217.27', iva: '0.00', ieps: null, lineas: null,
    validation_errors: 'subtotal_plus_tax_mismatch(leido=234.00,esperado=217.27); date_missing',
  },
  {
    relative_path: 'IMG_4222.jpeg',
    ticket_id: 'c9d3b6c1', monto: '0.00', extraction_status: 'PENDIENTE',
    motor: 'ocr', accion: 'CREADO', confiable: false, es_duplicado: false,
    proveedor: 'A ~ NERCASTAR |', rfc: null, fecha: '2026-10-08', subtotal: null,
    iva: '0.00', ieps: null, lineas: null,
    validation_errors: 'total_not_positive; date_missing',
  },
  {
    relative_path: 'IMG_4253 2.HEIC',
    ticket_id: '963d90e7', monto: '119.97', extraction_status: 'REQUIERE_REVISION',
    motor: 'ocr', accion: 'CREADO', confiable: false, es_duplicado: false,
    proveedor: 'y UPO COMERCIAL DSW S.A. DE L.”.', rfc: 'GCD170101GS6',
    fecha: '2026-01-10', subtotal: '279.33', iva: '0.00', ieps: null,
    lineas: 28,
    validation_errors: 'subtotal_plus_tax_mismatch(leido=119.97,esperado=279.33)',
  },
];

const corrida: ScanCorrida = {
  id: 'c1', carpeta: '/tickets', actor: 'carlos', iniciada_at: '2026-10-08T20:00:00Z',
  terminada_at: '2026-10-08T20:03:00Z', terminada: true, simulado: false,
  archivos_vistos: 4, con_ticket: 4, con_error: 0, actual: null,
  procesados, segundos: 180,
  resumen: {
    importe_total_leido: '451.53', importe_total_confiable: '0.00',
    importe_requiere_revision: '451.53',
  },
};

const html = renderToStaticMarkup(React.createElement(PanelProgreso, { corrida }));

const falla = [];
const ok = [];
function comprobar(cond, nombre) { (cond ? ok : falla).push(nombre); }

// 1. Los DATOS del ticket aparecen, no solo el importe.
comprobar(html.includes('AAA AI'), 'proveedor visible');
comprobar(html.includes('Pog ae — TAPA'), 'proveedor del Walmart visible');
comprobar(html.includes('GCD170101GS6'), 'RFC visible cuando existe');
comprobar(html.includes('subtotal $217.27'), 'subtotal visible');
comprobar(html.includes('28 líneas'), 'conteo de lineas visible');
// `2026-10-08` -> `08 oct 2026`. El formato se comprueba contra el dato REAL de
// la fila, no contra un ejemplo inventado: la primera version de esta comprobacion
// miraba "10 oct" (que es el dia del Mercastar) contra un ticket de octubre y
// fallo por el error del test, no del componente.
comprobar(html.includes('08 oct 2026'), 'fecha legible, no el ISO crudo');
comprobar(!html.includes('2026-10-08T'), 'la fecha no se pinta como timestamp');

// 2. Los MOTIVOS del gate, con su explicacion y su detalle.
comprobar(html.includes('date_missing'), 'codigo del motivo visible');
comprobar(html.includes('subtotal_plus_tax_mismatch'), 'mismatch visible');
comprobar(html.includes('no se leyó un total'), 'traduccion del motivo');
comprobar(html.includes('subtotal + IVA no da el total'), 'traduccion del mismatch');
comprobar(html.includes('leido=234.00,esperado=217.27'), 'detalle numerico del motivo');

// 3. La distincion que hay que ensenar sigue ahi: leido != afirmado.
comprobar(html.includes('sin afirmar'), 'marca de "sin afirmar"');
comprobar(html.includes('no se atreve a afirmar nada'), 'el piehonesto del resumen');

// 4. LO QUE NO DEBE APARECER. Este es el que mas importa:
//    un `undefined` o un `[object Object]` en pantalla es un fallo silencioso
//    que `tsc` no ve y que un snapshot del DOM delata.
comprobar(!html.includes('undefined'), 'ningun undefined en pantalla');
comprobar(!html.includes('NaN'), 'ningun NaN en pantalla');
comprobar(!html.includes('[object Object]'), 'ningun objeto sin renderizar');
comprobar(!html.includes('null'), 'ningun null impreso');

// 5. El feed se pinta en orden inverso (lo ultimo primero), que es lo que quiere
//    el operador: lo que acaba de salir esta arriba.
const i4253 = html.indexOf('IMG_4253');
const i4222 = html.indexOf('IMG_4222');
comprobar(i4253 > 0 && i4222 > 0 && i4253 < i4222, 'lo ultimo leido va arriba');

console.log(`\n  ${ok.length} comprobaciones OK:`);
ok.forEach((n) => console.log(`    ok  ${n}`));
if (falla.length) {
  console.log(`\n  ${falla.length} FALLAN:`);
  falla.forEach((n) => console.log(`    XX  ${n}`));
  process.exit(1);
}
console.log('\n  Todas pasan.\n');