/**
 * La carga por lote se ve en pantalla, y no solo compila.
 *
 * POR QUE ESTE SCRIPT
 * ------------------
 * `tsc` pasa con un boton que no sale, con un `datos.extraction_status`
 * undefined y con una lista que solo acepta PDF. Es el mismo motivo por el que
 * existen `verificar_panel_progreso.tsx` y `verificar_cierre_en_pantalla.tsx`:
 * montar el componente y mirar **el HTML que sale**, sin API viva ni login.
 *
 * QUE MIRA
 * --------
 * 1. Una sola accion para digitalizar: no hay un boton por formato.
 * 2. El texto dice que se pueden mezclar PDF, JPG, HEIC, TIFF y XML, y que el
 *    tipo lo decide el backend por los bytes (no el nombre ni el selector).
 * 3. El resultado muestra NOMBRES LOCALES (el `relative_path` es un sha256 y no
 *    sirve para decirle al operador que archivo fallo).
 * 4. Los datos y el veredicto van JUNTOS: si el gate mando a revision, el
 *    total se ve al lado de `REQUIERE_REVISION` y de su motivo.
 * 5. `datos=null` muestra el detalle, no `undefined` ni `null` crudos.
 * 6. Los tres lectores de una carga mezclada se distinguen en pantalla.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { fetchApi } from '../src/services/api';
import { CargaPorLote, PanelResultadoDirectorio, PanelResultadoLote } from '../src/pages/Scan';
import type { DatosTicket, DigitalizacionDirectorio, ScanItem } from '../src/types/scan';

const datos = (extra: Partial<DatosTicket> = {}): DatosTicket =>
  ({
    provider_name: 'GRUPO COMERCIAL DSW S.A. DE C.V.',
    provider_tax_id: 'GCD170101GS6',
    total_amount: '279.93',
    subtotal: '217.27',
    tax_amount: '8.14',
    ieps_amount: '8.59',
    expense_date: '2026-10-16',
    category: null,
    items: null,
    confidence: '0.91',
    confidence_source: 'llm',
    extraction_status: 'AUTO_APROBADO',
    validation_errors: null,
    reviewed_by: null,
    reviewed_at: null,
    pais: null,
    identificacion_fiscal: null,
    folio: null,
    metodo_pago: null,
    link_facturacion: null,
    hash_duplicado: null,
    estado_conciliacion: null,
    periodo_cerrado_override: false,
    periodo_cerrado_motivo: null,
    periodo_cerrado_autorizado_por: null,
    periodo_cerrado_autorizado_at: null,
    ...extra,
  }) as DatosTicket;

const item = (extra: Partial<ScanItem> = {}): ScanItem =>
  ({
    relative_path: '9e5a...sha256',
    scan_file_id: null,
    status: 'PROCESADO',
    accion: 'CREADO',
    ticket_id: '11111111-1111-1111-1111-111111111111',
    confianza: 0.91,
    origen: 'pdf_text',
    detalle: null,
    datos: datos(),
    ...extra,
  }) as ScanItem;

const fallos: string[] = [];
function comprobar(nombre: string, condicion: boolean) {
  if (condicion) {
    console.log(`    ok  ${nombre}`);
  } else {
    console.log(`    ??  ${nombre}`);
    fallos.push(nombre);
  }
}

const panel = renderToStaticMarkup(
  React.createElement(CargaPorLote, { companyId: '11111111-1111-1111-1111-111111111111' }),
);

console.log('La carga por lote se ve en pantalla:');

comprobar(
  'hay UNA accion de digitalizar, no una por formato',
  (panel.match(/Digitalizar 0 archivos/g) ?? []).length === 1 &&
    !/Digitalizar PDF|Digitalizar JPG|Digitalizar XML/.test(panel),
);
comprobar(
  'se ofrecen los formatos mezclados',
  ['PDF', 'JPG', 'HEIC', 'TIFF', 'XML'].every(f => panel.includes(f)),
);
comprobar(
  'se dice que el tipo lo decide el backend por los bytes',
  panel.includes('backend por los bytes') && panel.includes('no por el nombre'),
);
comprobar(
  'se puede elegir una carpeta entera y varios archivos',
  panel.includes('Elegir carpeta') && panel.includes('Elegir archivos') &&
    panel.includes('multiple=""'),
);
comprobar(
  'el selector es comodidad: no manda file_type',
  !/file_type/.test(panel),
);

// La configuracion NO se copia al front: si el servidor decide otras raices o
// anade un formato, el campo lo sigue. Miramos el HTML con las props puestas.
const configurado = renderToStaticMarkup(
  React.createElement(CargaPorLote, {
    companyId: '11111111-1111-1111-1111-111111111111',
    carpetaDefecto: '/tickets/entrada',
    rutasPermitidas: ['/tickets/entrada', '/tickets/otra'],
    extensionesPermitidas: ['.pdf', '.jpg', '.xml'],
  }),
);
comprobar(
  'el campo sugiere raices al escribir y las muestra como botones',
  configurado.includes('/tickets/entrada') && configurado.includes('/tickets/otra') &&
    configurado.includes('Rutas permitidas') &&
    configurado.includes('datalist id="carga-lote-rutas"') &&
    configurado.includes('list="carga-lote-rutas"') &&
    configurado.includes('<option value="/tickets/entrada"'),
);
comprobar(
  'el accept sale de las extensiones permitidas, sin .txt',
  configurado.includes('accept=".pdf,.jpg,.xml"') && !/accept="[^"]*\.txt/.test(configurado),
);

const archivos = [
  { name: 'factura.pdf' },
  { name: 'foto.jpg' },
  { name: 'foto.heic' },
  { name: 'cfdi.xml' },
] as File[];

const resultado = renderToStaticMarkup(
  React.createElement(PanelResultadoLote, {
    archivos,
    resultado: [
      item({ origen: 'pdf_text', datos: datos({ total_amount: '234.00', confidence_source: 'pdf_text' }) }),
      item({
        origen: 'ocr',
        datos: datos({
          total_amount: '193.65',
          extraction_status: 'REQUIERE_REVISION',
          confidence_source: 'ocr',
          validation_errors: 'subtotal_plus_tax_mismatch(leido=193.65,esperado=213.01)',
        }),
      }),
      item({
        accion: 'PERIODO_CERRADO',
        status: 'OMITIDO',
        origen: null,
        ticket_id: null,
        confianza: null,
        datos: null,
        detalle: 'PERIODO_CERRADO: el papel dice 2026-09-30 y el periodo esta cerrado',
      }),
      item({ origen: 'llm', datos: datos({ extraction_status: 'PENDIENTE' }) }),
    ],
    // Los bytes NO son el lector: el segundo dice `image` y se leyo con OCR,
    // y el cuarto dice `image` y se leyo con modelo.
    formatos: ['pdf', 'image', 'pdf', 'image'],
  }),
);

comprobar(
  'se ven los nombres de los archivos locales',
  ['factura.pdf', 'foto.jpg', 'foto.heic', 'cfdi.xml'].every(n => resultado.includes(n)),
);
comprobar(
  'el total se ve con su veredicto al lado',
  resultado.includes('$279.93') && resultado.includes('veredicto: PENDIENTE') &&
    resultado.includes('veredicto: REQUIERE_REVISION'),
);
comprobar(
  'el motivo del gate se ve, y no se traduce su codigo',
  resultado.includes('subtotal_plus_tax_mismatch(leido=193.65,esperado=213.01)'),
);
// El resumen ya dice "1 en periodo cerrado", asi que una sola aparicion de la
// frase NO prueba que la FILA lo diga: la primera version de esta comprobacion
// daba verde con `accionLabel` sin traducir. Exigen dos: resumen y fila.
comprobar(
  'el periodo cerrado se ve en el resumen Y en su fila, no como un ticket',
  resultado.includes('PERIODO_CERRADO') &&
    (resultado.match(/periodo cerrado/g) ?? []).length >= 2,
);
comprobar(
  'se distinguen los tres lectores de la carga mezclada',
  resultado.includes('texto del PDF') && resultado.includes('OCR local') &&
    resultado.includes('modelo de vision'),
);
comprobar(
  'bytes y lector se ven como dos preguntas distintas',
  resultado.includes('bytes=pdf · lector=texto del PDF') &&
    resultado.includes('bytes=image · lector=OCR local'),
);
comprobar(
  'no se imprime undefined, null ni NaN',
  !/undefined|NaN|>null</.test(resultado),
);

// La ruta del servidor nombra ademas los archivos que NO entraron. El
// backend los manda separados; si el front los mezclara en la tabla, un .docx
// pareceria un ticket que fallo en vez de un archivo que nadie leyo.
const directorio: DigitalizacionDirectorio = {
  carpeta: '/tickets/entrada',
  extensiones_permitidas: ['.pdf', '.jpg', '.xml'],
  archivos: [
    item({ origen: 'pdf_text', datos: datos({ total_amount: '1100.00', confidence_source: 'pdf_text' }) }),
    item({ origen: 'xml', datos: datos({ total_amount: '1100.00', confidence_source: 'xml' }) }),
  ],
  nombres: ['factura.pdf', 'cfdi.xml'],
  formatos: ['pdf', 'xml'],
  archivos_no_permitidos: [
    { nombre: 'notas.docx', extension: '.docx', motivo: 'Extension no permitida' },
    { nombre: 'tarjeta.xlsx', extension: '.xlsx', motivo: 'Extension no permitida' },
  ],
  archivos_omitidos_por_tope: 1,
};
const panelDirectorio = renderToStaticMarkup(
  React.createElement(PanelResultadoDirectorio, { resultado: directorio }),
);
comprobar(
  'la ruta nombra a cada archivo rechazado, no solo el conteo',
  panelDirectorio.includes('notas.docx') && panelDirectorio.includes('tarjeta.xlsx') &&
    panelDirectorio.includes('2 no permitidos'),
);
comprobar(
  'los rechazados van arriba de la tabla de leidos',
  panelDirectorio.indexOf('notas.docx') < panelDirectorio.indexOf('factura.pdf'),
);
comprobar(
  'el panel de ruta muestra bytes y lector',
  panelDirectorio.includes('bytes=pdf · lector=texto del PDF') &&
    panelDirectorio.includes('bytes=xml · lector=xml'),
);
comprobar(
  'el tope se cuenta, no se silencia',
  panelDirectorio.includes('1 por tope de corrida'),
);

// Un 500 de uvicorn responde text/plain, no JSON. El mensaje tiene que dar el
// codigo Y el cuerpo: "Error desconocido" no distingue una caida del servidor
// de un fallo del front, y lleva a repetir la peticion. Se prueba la funcion
// real con un fetch falso, no el HTML: es la capa que produjo el error.
async function comprobarError500() {
  const fetchOriginal = globalThis.fetch;
  globalThis.fetch = (async () => new Response('Internal Server Error', {
    status: 500,
    headers: { 'Content-Type': 'text/plain' },
  })) as typeof fetch;
  let mensaje = '';
  try {
    // `sinToken`: en Node no hay `localStorage`, y la puerta de caducidad
    // saltaria antes del fetch. Aqui se prueba SOLO el manejo del 500.
    await fetchApi('/scan/files', { sinToken: true });
  } catch (e) {
    mensaje = e instanceof Error ? e.message : String(e);
  } finally {
    globalThis.fetch = fetchOriginal;
  }
  comprobar(
    'un 500 en texto plano dice el codigo y el cuerpo, no "Error desconocido"',
    mensaje === 'Error 500: Internal Server Error' && !mensaje.includes('Error desconocido'),
  );
}

void comprobarError500().then(() => {
  if (fallos.length > 0) {
    console.error(`\n${fallos.length} comprobaciones fallaron:\n- ${fallos.join('\n- ')}`);
    process.exit(1);
  }
  console.log('\nTodo lo que se promete en la carga por lote se ve en pantalla.');
});
