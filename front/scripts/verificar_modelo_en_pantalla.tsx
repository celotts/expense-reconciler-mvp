/**
 * Que el nombre del modelo se pinte en pantalla, y no solo llegue a la API.
 *
 * ## POR QUE ESTE SCRIPT Y NO UN TEST DE TYPESCRIPT
 *
 * Porque `tsc` pasa con un `undefined` en pantalla. Compilar dice que los tipos
 * cuadran; no dice que el campo se pinte. Es el mismo motivo por el que existe
 * `verificar_panel_progreso.tsx`, y la diferencia con `npm run verify` completo es
 * que este NO depende de la API real ni de un login: monta el componente con
 * datos y mira **el texto que sale**.
 *
 * ## QUE MIRA
 *
 * 1. El modelo aparece junto al motor cuando hubo lectura por IA.
 * 2. NO aparece cuando no hubo modelo —y el caso se distingue del anterior.
 * 3. Sale el PROVEEDOR en el title, porque el mismo nombre de modelo en dos
 *    proveedores son dos motores distintos.
 * 4. Un `undefined` o un `null` nunca se imprime.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { PanelProgreso } from '../src/components/ScanProgreso';

const corrida = (procesados: any[]) =>
  ({
    id: 'r1',
    iniciada_at: '2026-10-10T12:00:00Z',
    terminada: true,
    archivos_vistos: procesados.length,
    // `segundos` y los dos `importe_total_*` estan porque el panel los pinta en
    // el pie, y sin ellos sale `NaNm` en pantalla. No tienen nada que ver con el
    // modelo: es la diferencia entre un doble completo y uno que solo tiene lo
    // que el test mira. La primera version de este script los omitio y fallo
    // por eso, no por el badge — que es el error clasico de un test que mide mas
    // de lo que cree.
    segundos: 12.5,
    resumen: {
      nuevos: procesados.filter((p) => p.accion === 'CREADO').length,
      con_error: 0,
      omitidos_por_tope: 0,
      sin_cambios: 0,
      duplicados: 0,
      no_soportados: 0,
      actualizados: 0,
      lecturas_por_motor: {},
      importe_total_leido: '234.00',
      importe_total_confiable: '0',
    },
    procesados,
  }) as any;

const item = (extra: any) =>
  ({
    relative_path: 'ticket.jpg',
    ticket_id: '11111111-1111-1111-1111-111111111111',
    monto: '234.00',
    extraction_status: 'PENDIENTE',
    motor: 'llm',
    accion: 'CREADO',
    confiable: false,
    es_duplicado: false,
    proveedor: 'WALMART',
    rfc: 'WAL900101XXX',
    fecha: '2026-10-09',
    subtotal: '217.27',
    iva: '8.14',
    ieps: null,
    lineas: null,
    validation_errors: 'date_missing',
    ...extra,
  }) as any;

const fallos: string[] = [];

function comprobar(nombre: string, condicion: boolean) {
  if (condicion) {
    console.log(`    ok  ${nombre}`);
  } else {
    console.log(`    ??  ${nombre}`);
    fallos.push(nombre);
  }
}

console.log('El modelo se ve en pantalla:');

// --- 1. Cuando hubo IA, el nombre sale ------------------------------------
const conModelo = renderToStaticMarkup(
  React.createElement(PanelProgreso, {
    corrida: corrida([
      item({ modelo: 'qwen2.5:3b', proveedor_ia: 'ollama' }),
    ]),
  }) as any,
);
comprobar(
  'sale el nombre del modelo',
  conModelo.includes('qwen2.5:3b'),
);
comprobar(
  'sale el motor (llm) al lado',
  conModelo.includes('llm'),
);
comprobar(
  'el title lleva el proveedor',
  conModelo.includes('ollama'),
);

// --- 2. Cuando NO hubo IA, no sale nada -----------------------------------
// El caso real de una foto leida por OCR o por reglas. Y la distincion que
// importa: `null` aqui es "no hubo modelo que responder", no "no se sabe".
const sinModelo = renderToStaticMarkup(
  React.createElement(PanelProgreso, {
    corrida: corrida([item({ motor: 'ocr', modelo: null, proveedor_ia: null })]),
  }) as any,
);
comprobar('sin modelo no sale un badge vacio', !/undefined|null|NaN/.test(sinModelo));
comprobar('el motor ocr sigue saliendo', sinModelo.includes('ocr'));

// --- 3. Nunca se imprime un valor roto ------------------------------------
const raros = renderToStaticMarkup(
  React.createElement(PanelProgreso, {
    corrida: corrida([item({ modelo: undefined, proveedor_ia: undefined })]),
  }) as any,
);
comprobar('undefined no se imprime', !raros.includes('undefined'));
comprobar('null no se imprime', !raros.includes('>null<'));

// --- 4. El motor y el modelo no se confunden -------------------------------
// `motor` es la CLASE de lector y `modelo` es el NOMBRE. Si se pintaran en el
// mismo sitio sin distinguirlos, "llm" y "qwen2.5:3b" se leerian como la misma
// informacion, que es justo lo que se quiere evitar.
comprobar(
  'el modelo va en un elemento propio, no mezclado con el motor',
  /qwen2\.5:3b<\/span>/.test(conModelo),
);

// --- 5. El aviso de modelo ausente dice la verdad ---------------------------
//
// MEDIDO el 2026-10-15. Con `qwen2.5vl:3b` instalado y `moondream` (el de
// TEXTO) ausente, el cartel decia "mientras falte, esos comprobantes caen a la
// lectura por reglas o se quedan en la cola". Es FALSO: las fotos las leia el
// modelo de vision, y la ausencia del de texto no las toca. Un aviso que dice
// una cosa que no esta pasando entrena a ignorarlo.
import { EstadoDeIa } from '../src/components/EstadoDeIa';

const estado = (extra: any) =>
  ({
    configurado: {
      proveedor: 'ollama',
      ai_habilitado: true,
      modelo_texto: 'moondream',
      modelo_vision: 'qwen2.5vl:3b',
    },
    modelos_instalados: ['qwen2.5vl:3b'],
    faltan: ['moondream'],
    disponible: true,
    error: null,
    como_instalar: ['ollama pull moondream'],
    ...extra,
  }) as any;

const montar = (estadoCompleto: any): string =>
  renderToStaticMarkup(
    React.createElement(EstadoDeIa as any, { estadoInicial: estadoCompleto } as any),
  );

// El TEXTO QUE SE VE, sin las etiquetas. La primera version de estas
// comprobaciones comparaba contra el HTML y la frase "las fotos si las lee" salia
// partida por un `<strong>`, que es exactamente el modo de fallo que el propio
// archivo critica: un test que mide el marcado en vez de lo que lee la persona.
const texto = (html: string): string => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

const soloTexto = montar(estado({}));
comprobar('avisa del modelo de texto que falta', soloTexto.includes('moondream'));
comprobar(
  'NO dice que caen a las reglas cuando si las lee el modelo de vision',
  !/esos comprobantes caen a la lectura por reglas/.test(soloTexto),
);
comprobar(
  'dice que las fotos si las lee el modelo de vision',
  /fotos s[ií] las lee/i.test(texto(soloTexto)),
);

const soloVision = montar(
  estado({ faltan: ['qwen2.5vl:3b'], como_instalar: ['ollama pull qwen2.5vl:3b'] }),
);
comprobar('avisa del modelo de vision que falta', soloVision.includes('qwen2.5vl:3b'));
comprobar(
  'dice que las fotos caen al OCR cuando falta el de vision',
  /fotos no se pueden leer con modelo/i.test(texto(soloVision)),
);

// Todo instalado: no se muestra nada. Un cartel permanente que dice "todo bien"
// ocupa sitio y no ensena nada.
const todoBien = montar(estado({ faltan: [], como_instalar: [] }));
comprobar('con todo instalado no sale el cartel', todoBien.trim() === '');

console.log('');
if (fallos.length === 0) {
  console.log('  Todas pasan.');
} else {
  console.log(`  ${fallos.length} FALLAN:`);
  for (const f of fallos) console.log(`    - ${f}`);
  process.exit(1);
}