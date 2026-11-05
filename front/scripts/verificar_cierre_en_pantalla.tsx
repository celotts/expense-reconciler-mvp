/**
 * Que el informe de cierre se pinte, y que no afirme mas de lo que el backend dice.
 *
 * ## POR QUE ESTE SCRIPT Y NO UN TEST DE TYPESCRIPT
 *
 * Porque `tsc` pasa con un `undefined` en pantalla, y aqui el fallo peligroso es
 * peor: pasaria con un `0%` donde el backend dijo `null`. Compilar dice que los
 * tipos cuadran; no dice que lo que se muestra sea lo que el backend afirmo.
 *
 * Por eso este script monta la PAGINA COMPLETA con el JSON REAL que devolvio
 * `GET /reports/cierre-mensual` (sacado de la API encendida, 2026-10-10) y mira
 * **el HTML que sale**. No depende de la API real ni de un login.
 *
 * ## POR QUE SE COMPRUEBA EL HTML Y NO EL OBJETO DEVUELTO
 *
 * MEDIDO, y es la razon de que este archivo se reescribiera. Una version
 * anterior comprobaba `lineaExactitud(r).titulo.includes('Exactitud')`: mira el
 * OBJETO que devuelve la funcion, no lo que se pinta. Con la defensa quitada
 * —la que recibia `Exactitud: 0.0%` cuando el backend dijo `null`— el script
 * daba **verde**, porque el objeto seguia teniendo la palabra "Exactitud".
 *
 * Tres de cuatro mutaciones pasaron sin que nada las notara. Las tresQuitaron
 * justo lo que el script pretendia proteger: el `0%` de R7, el matiz de
 * comparativo no concluyente y el mes por omision. Un test que mira el retorno
 * de la funcion que esta probando solo comprueba que la funcion se llamo.
 *
 * Asi que aqui se renderiza y se mira el texto. Es mas laborioso y por eso es
 * la unica forma de que sirva.
 *
 * ## LAS REGLAS QUE PROTEGE
 *
 * 1. **R7**: `exactitud === null` es "no disponible", NUNCA `0%`. Un cero se lee
 *    como "se midio y fallo", que el dato no sostiene.
 * 2. **R1**: sin intervalo no se muestra el punto de exactitud.
 * 3. **R5**: los dos numeros de lectura nunca se suman en una sola cifra.
 * 4. El cuarto estado: un periodo cerrado CON pendientes no se reporta como "no
 *    cierra", que seria falso.
 * 5. El comparativo contra un mes en curso no se maquilla de concluyente.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { Cierre } from '../src/pages/Cierre';
import { NavegacionProvider } from '../src/contextos/Navegacion';
import { periodoActual, periodosDisponibles, nombrePeriodo } from '../src/utils/cierre';
import type { ReporteCierreMensual } from '../src/types/api';

const fallos: string[] = [];

function comprobar(nombre: string, condicion: boolean, detalle = '') {
  if (condicion) {
    console.log(`    ok  ${nombre}`);
  } else {
    console.log(`    ??  ${nombre}${detalle ? `  (${detalle})` : ''}`);
    fallos.push(nombre);
  }
}

// ---------------------------------------------------------------------------
// El informe REAL de la API, con la evidencia vacia.
// ---------------------------------------------------------------------------
// `veredicto_global = "SIN_EVIDENCIA"` y todos los `exactitud` en `null`. Es el
// estado en el que queda una base recien creada, y es el que mas mintiria si la
// pantalla lo rellenara.
const SIN_EVIDENCIA: ReporteCierreMensual = {
  empresa_id: 'b9178e14-0809-436c-9dc6-39f6ea571d71',
  empresa_nombre: 'Comercio de Prueba SA',
  empresa_tax_id: 'TST010101XXX',
  periodo: '2026-01',
  periodo_inicio: '2026-01-01',
  periodo_fin: '2026-01-31',
  fecha_referencia: '2026-01-31',
  firmado_por: 'prueba@local.mx',

  gasto: {
    total: '0',
    tickets: 0,
    por_categoria: [],
    comparativo: {
      nombre_anterior: 'diciembre de 2025',
      monto_anterior: '0',
      tickets_anterior: 0,
      diferencia: '0',
      diferencia_pct: null,
      conclusivo: true,
      nota: null,
    },
  },
  hallazgos: [
    { tipo: 'nivel', titulo: 'Gasto habitual', detalle: 'alrededor de $0 al mes', tono: 'neutro' },
  ],
  conciliacion: {
    por_estado: { PERFECT: 0, MANUAL: 0, DISCREPANCY: 0 },
    conciliados: 0,
    sin_conciliar: 0,
    monto_discrepancias: '0',
  },
  exactitud: {
    company_id: 'b9178e14-0809-436c-9dc6-39f6ea571d71',
    objetivo: 0.96,
    nivel_confianza: 0.95,
    veredicto_global: 'SIN_EVIDENCIA',
    explicacion:
      'No hay ninguna revision de muestreo todavia, asi que no se puede afirmar nada sobre la exactitud.',
    por_origen: [
      { origen: 'llm', revisados: 0, aciertos: 0, incorrectos: 0, pendientes: 0,
        exactitud: null, intervalo_inferior: null, intervalo_superior: null,
        veredicto: 'SIN_EVIDENCIA', motivo_faltante: 'sin_muestra',
        total_revisiones_necesarias: null, campo_mas_fallido: null, conteo_por_campo: {} },
      { origen: 'pdf_text', revisados: 0, aciertos: 0, incorrectos: 0, pendientes: 0,
        exactitud: null, intervalo_inferior: null, intervalo_superior: null,
        veredicto: 'SIN_EVIDENCIA', motivo_faltante: 'sin_muestra',
        total_revisiones_necesarias: null, campo_mas_fallido: null, conteo_por_campo: {} },
      { origen: 'rules', revisados: 0, aciertos: 0, incorrectos: 0, pendientes: 0,
        exactitud: null, intervalo_inferior: null, intervalo_superior: null,
        veredicto: 'SIN_EVIDENCIA', motivo_faltante: 'sin_muestra',
        total_revisiones_necesarias: null, campo_mas_fallido: null, conteo_por_campo: {} },
    ],
  },
  exactitud_disponible: true,
  advertencia_principal:
    'La evidencia disponible NO alcanza para afirmar el objetivo de exactitud (96%). Veredicto: SIN_EVIDENCIA.',

  lectura: { leido_por_el_sistema: 0, verificado_por_una_persona: 0, pendiente_de_revision: 0 },
  verificacion: {
    en_muestra: 0, revisadas: 0, firmadas: 0, pendientes_de_firmar: 0,
    revisores: [], ultima_verificacion: null,
  },
  pendientes: {
    sin_categoria_tickets: 0, sin_categoria_monto: '0',
    sin_conciliar_tickets: 0, sin_conciliar_monto: '0.00',
    discrepancias_abiertas: 0, discrepancias_monto: '0',
    hay_pendientes: false, motivo: null,
  },
  cerrado: false,
  cerrado_por: null,
  puede_cerrarse: true,
  estado_periodo: 'CIERRE_DISPONIBLE',
};

// El caso que hace falta el CUARTO estado: `cerrado=true` y `hay_pendientes`
// true a la vez. Un front que solo mirara `cerrado` lo reportaria como "cerrado,
// todo bien", que es falso: hay pendientes que ver.
const CERRADO_CON_PENDIENTES: ReporteCierreMensual = {
  ...SIN_EVIDENCIA,
  cerrado: true,
  cerrado_por: 'contador@empresa.mx',
  puede_cerrarse: false,
  estado_periodo: 'CERRADO_CON_PENDIENTES',
  pendientes: {
    sin_categoria_tickets: 3, sin_categoria_monto: '450.00',
    sin_conciliar_tickets: 2, sin_conciliar_monto: '120.00',
    discrepancias_abiertas: 1, discrepancias_monto: '80.00',
    hay_pendientes: true,
    motivo: '3 sin categoria, 2 sin conciliar, 1 discrepancias abiertas',
  },
};

// El mismo periodo con gasto de verdad, para que la tabla de categorias se pinte.
const CON_GASTO: ReporteCierreMensual = {
  ...SIN_EVIDENCIA,
  gasto: {
    total: '1521.66',
    tickets: 12,
    por_categoria: [
      { clave: 'ALIMENTACION', etiqueta: 'Alimentación', monto: '820.00',
        tickets: 5, porcentaje: 53.9, variable: true, sin_clasificar: false },
      { clave: 'SIN_CLASIFICAR', etiqueta: 'sin clasificar', monto: '120.50',
        tickets: 1, porcentaje: 7.9, variable: false, sin_clasificar: true },
    ],
    comparativo: {
      nombre_anterior: 'diciembre de 2025',
      monto_anterior: '1200.00',
      tickets_anterior: 9,
      diferencia: '321.66',
      diferencia_pct: 26.8,
      conclusivo: true,
      nota: null,
    },
  },
};

// Comparar contra un mes EN CURSO: el backend marca `conclusivo: false` porque
// comparar 6 dias contra 31 produce una caida que no existe.
const COMPARATIVO_EN_CURSO: ReporteCierreMensual = {
  ...CON_GASTO,
  gasto: {
    ...CON_GASTO.gasto,
    comparativo: {
      nombre_anterior: 'octubre de 2025',
      monto_anterior: '5000.00',
      tickets_anterior: 40,
      diferencia: '-3478.34',
      diferencia_pct: -69.6,
      conclusivo: false,
      nota: 'El mes anterior esta en curso, asi que la comparacion no es concluyente.',
    },
  },
};

/** Renderiza la pantalla completa y devuelve el HTML.
 *
 *  El `NavegacionProvider` es obligatorio: `Cierre` llama a `useNavegacion()`
 *  para el enlace al muestreo, y fuera del provider eso es un `throw`. Sin el
 *  wrapper este script no podria montar la pagina — que es justamente lo que
 *  hay que probar. */
function pintar(informe: ReporteCierreMensual): string {
  return renderToStaticMarkup(
    React.createElement(
      NavegacionProvider,
      null,
      React.createElement(Cierre, { informeInicial: informe } as any),
    ) as any,
  );
}

// El MISMO informe pero con `exactitud: null` — el objeto entero ausente, no
// solo los `exactitud` de cada origen.
//
// MEDIDO, y es la razon de que este caso exista: la primera version del script
// usaba SOLO `SIN_EVIDENCIA`, que trae `exactitud` como OBJETO con todos sus
// `exactitud` en `null`. La rama `if (!r.exactitud)` de `lineaExactitud` —que es
// la que R7 protege— no se ejecutaba nunca, asi que poner ahi un `0%` daba
// VERDE. El test decia cubrir R7 y no lo cubria, porque nunca llego a ese codigo.
//
// Un caso que no se ejecuta es un caso sin test, por mas que el test sea largo.
const SIN_OBJECTO_EXACTITUD: ReporteCierreMensual = {
  ...SIN_EVIDENCIA,
  exactitud: null,
  advertencia_principal: null,
};

console.log('El informe de cierre se ve en pantalla:');

// --- 1. La pagina monta y pinta las 7 secciones -----------------------------
const html = pintar(SIN_EVIDENCIA);
comprobar('la pagina monta', html.length > 0);
for (const seccion of [
  '1. Identificación', '2. Resumen del gasto', '3. Hallazgos',
  '4. Estado de conciliación', '5. Exactitud', '6. Registro de verificación',
  '7. Pendientes',
]) {
  comprobar(`seccion "${seccion}"`, html.includes(seccion));
}

// --- 2. R7: sin evidencia NO se pinta 0% -----------------------------------
comprobar(
  'sin evidencia el veredicto se dice con palabras',
  html.includes('Sin evidencia') || html.includes('no disponible'),
  'el backend dijo SIN_EVIDENCIA con exactitud=null',
);
comprobar(
  'R7: no se pinta 0% donde el backend dio null',
  !html.includes('0.0%'),
  'un 0% afirma que se midio y fallo; el dato dice que no se midio',
);
comprobar(
  'R7: los origenes sin muestra dicen "no disponible"',
  html.includes('no disponible'),
);

// El caso de `exactitud: null` OBJETO COMPLETO. Es distinto del anterior: ahi
// hay bloque de exactitud con veredicto SIN_EVIDENCIA y aqui no hay bloque.
// R7 dice "no disponible", nunca `0%` y nunca una seccion omitida.
const htmlSinObjeto = pintar(SIN_OBJECTO_EXACTITUD);
comprobar(
  'R7: sin bloque de exactitud se dice "no disponible"',
  htmlSinObjeto.includes('Exactitud: no disponible'),
  'el backend devolvio exactitud=null y eso se dice con esas palabras',
);
comprobar(
  'R7: sin bloque de exactitud NO se pinta 0%',
  !htmlSinObjeto.includes('0.0%'),
  'un 0% afirma que se midio y fallo; el backend dijo que no hay bloque',
);
comprobar(
  'R7: la seccion 5 se sigue mostrando, no se omite',
  htmlSinObjeto.includes('5. Exactitud'),
  'R7: una seccion que no se pudo calcular no se omite',
);

// --- 3. R2: la advertencia del backend se muestra arriba -------------------
comprobar(
  'R2: sale la advertencia que compuso el backend',
  html.includes('NO alcanza para afirmar'),
);
comprobar(
  'R2: la advertencia aparece antes del bloque de exactitud',
  html.indexOf('NO alcanza para afirmar') !== -1
    && html.indexOf('NO alcanza para afirmar') < html.indexOf('5. Exactitud'),
  'R2 dice que va en la PRIMERA pagina, no como nota al pie',
);

// --- 4. El estado del periodo ----------------------------------------------
comprobar(
  'CIERRE_DISPONIBLE se dice en pantalla',
  html.includes('se puede declarar cerrado'),
);

// El caso cerrado-con-pendientes: NO puede decir "no cierra".
const htmlCerrado = pintar(CERRADO_CON_PENDIENTES);
comprobar(
  'CERRADO_CON_PENDIENTES se dice como cerrado-con-pendientes',
  htmlCerrado.includes('Cerrado, pero con pendientes'),
);
comprobar(
  'CERRADO_CON_PENDIENTES NO se reporta como "no cierra"',
  !htmlCerrado.includes('NO se puede declarar cerrado'),
  'el periodo SI esta cerrado en el registro; decir "no cierra" es falso',
);
comprobar(
  'CERRADO_CON_PENDIENTES nombra los pendientes concretos',
  htmlCerrado.includes('sin categoria') && htmlCerrado.includes('discrepancias abiertas'),
);

// --- 5. R5: los dos numeros de lectura van separados ------------------------
// Se comprueba que las DOS etiquetas existen y que NO hay una tercera que las
// junte. La mutacion que mata esto es cambiar las dos `<Fila>` por una sola con
// la suma, que deja las etiquetas... fuera de pantalla, asi que basta con que
// disappeared cualquiera de las dos para que el test lo note.
const CON_LECTURA = pintar({
  ...SIN_EVIDENCIA,
  lectura: {
    leido_por_el_sistema: 40,
    verificado_por_una_persona: 12,
    pendiente_de_revision: 8,
  },
});
comprobar('sale "Leído por el sistema"', CON_LECTURA.includes('Leído por el sistema'));
comprobar('sale "Verificado por una persona"', CON_LECTURA.includes('Verificado por una persona'));
comprobar(
  'R5: la suma de los dos NO aparece como un numero propio',
  !CON_LECTURA.includes('52'),
  '40 leidos + 12 verificados = 52, y un unico 52 seria afirmar que el '
  + 'sistema leyo 52 cosas que alguien verifico',
);
comprobar(
  'R5: el numero del sistema se muestra tal cual',
  CON_LECTURA.includes('40'),
);
comprobar(
  'R5: el numero verificado por persona se muestra tal cual',
  CON_LECTURA.includes('12'),
);

// --- 6. El comparativo en curso no se maquilla -----------------------------
const htmlEnCurso = pintar(COMPARATIVO_EN_CURSO);
comprobar(
  'un comparativo no concluyente se marca como tal',
  htmlEnCurso.includes('no concluyente'),
);
comprobar(
  'un comparativo no concluyente NO muestra la caida como real',
  !htmlEnCurso.includes('69.6%'),
  'el backend dijo conclusivo=false: la caida existe pero no significa nada',
);

// --- 7. La tabla de categorias usa la etiqueta del backend -----------------
const htmlGasto = pintar(CON_GASTO);
comprobar('sale la etiqueta de la categoria', htmlGasto.includes('Alimentación'));
comprobar('la sin clasificar se marca como tal', htmlGasto.includes('sin clasificar'));
comprobar(
  'la categoria sin clasificar NO se muestra con una etiqueta inventada',
  !htmlGasto.includes('undefined'),
);

// --- 8. El periodo por omision --------------------------------------------
const p = periodoActual();
comprobar('el periodo por omision tiene forma AAAA-MM', /^\d{4}-(0[1-9]|1[0-2])$/.test(p), `sale: ${p}`);
function periodoDe(f: Date): string {
  return `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, '0')}`;
}
comprobar(
  'el periodo por omision es el mes de HOY, no el anterior',
  p === periodoDe(new Date()),
  `sale: ${p}`,
);

// ## POR QUE `periodoActual` ACEPTA UNA FECHA
// MEDIDO: con `periodoActual` usando `toISOString()`, el test de "es el mes de
// hoy" daba VERDE.
//
// El defecto de `toISOString()` solo se reproduce cerca de medianoche: convierte
// a UTC, y en Mexico (UTC-6) el dia 1 a las 20:00 sale con el dia 31 del mes
// anterior. Un test que mira "hoy" da por bueno el defecto todos los dias salvo
// esa franja, que es justo cuando se cierra el mes.
//
// Por eso `periodoActual` acepta una fecha: el test la pasa con un dia 1 a las
// 20:30 hora local — que en UTC es el dia 2 — y el fallo aparece siempre, no
// solo en la franja horaria en la que se esta corriendo el test.
const A_MEDIA_NOCHE_UTC = new Date(2026, 0, 1, 20, 30, 0);
comprobar(
  'el dia 1 del mes devuelve ESE mes, no el anterior',
  periodoActual(A_MEDIA_NOCHE_UTC) === '2026-01',
  `sale: ${periodoActual(A_MEDIA_NOCHE_UTC)}. Con toISOString() esta fecha da `
  + `'2026-01-02' en Mexico: el mes cambia.`,
);
comprobar(
  'el ultimo dia del anio NO se convierte en enero del siguiente',
  periodoActual(new Date(2025, 11, 31, 20, 30, 0)) === '2025-12',
  'el cruce de ano es donde un corrimiento se lleva el periodo entero',
);
comprobar(
  'sin argumento sigue siendo el mes de hoy',
  periodoActual() === periodoDe(new Date()),
);
comprobar('nombrePeriodo pone el mes en palabras', nombrePeriodo('2026-01') === 'enero de 2026');
comprobar('nombrePeriodo con periodo raro no revienta', nombrePeriodo('basura') === 'basura');
comprobar('el selector ofrece 24 meses atras', periodosDisponibles(24).length === 24);

// --- 9. Ningun valor roto llega a pantalla ---------------------------------
for (const h of [html, htmlCerrado, htmlEnCurso, htmlGasto]) {
  comprobar('no aparece undefined', !h.includes('undefined'));
  comprobar('no aparece NaN', !h.includes('NaN'));
  comprobar('no aparece null como texto', !h.includes('>null<'));
}

if (fallos.length > 0) {
  console.log(`\n  ${fallos.length} fallo(s):`);
  for (const f of fallos) console.log(`    - ${f}`);
  process.exit(1);
}
console.log('\n  Todo ok');