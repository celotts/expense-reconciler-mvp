/**
 * Renderiza `FlujoGuiado` con datos REALES del dashboard y comprueba lo que el
 * operador lee.
 *
 * Por qué no basta con `tsc`: un componente que compila puede pintar `"38
 * undefined"` o mostrar el paso 3 en verde con 0 conciliados, y `tsc` no ve
 * ninguna de las dos cosas. Aquí se mira el texto.
 *
 * Y por qué los casos son casi todos NEGATIVOS: lo interesante de una barra de
 * progreso no es que muestre "38", sino que **no prometa** cosas que no pasó. Un
 * "✓ hecho" en el paso de conciliar cuando no hay nada registrado es la clase de
 * mentira que este proyecto no sostiene.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { FlujoGuiado, _estadoDe } from '../src/components/FlujoGuiado';
import type { DashboardResponse } from '../src/types/dashboard';

/** El dashboard con los 7 tickets de la maquina: 6 PENDIENTE, 1 REQUIERE_REVISION. */
const CON_COLA: DashboardResponse = {
  company_id: null,
  company_name: 'Todas las empresas',
  mes: {} as never,
  por_categoria: [],
  sin_clasificar: { tickets: 0, monto: '0' } as never,
  comparativo: [],
  hallazgos: [],
  tendencia: [],
  top_proveedores: [],
  por_estado: { PENDIENTE: 6, REQUIERE_REVISION: 1 },
  por_estado_conciliacion: { SIN_CONCILIAR: 0 },
  banco: {} as never,
  cola_revision: { PENDIENTE: 6, REQUIERE_REVISION: 1 },
  hay_datos: true,
  exactitud: null,
  total_historico: '451.53',
  tickets_historico: 7,
};

/** El mismo tablero, ya todo registrado y conciliado. */
const LIMPIO: DashboardResponse = {
  ...CON_COLA,
  por_estado: { AUTO_APROBADO: 5, APROBADO: 2 },
  por_estado_conciliacion: { PERFECT: 4, MANUAL: 1 },
  cola_revision: {},
};

/** Una empresa que no ha digitalizado nada. */
const VACIO: DashboardResponse = {
  ...CON_COLA,
  por_estado: {},
  por_estado_conciliacion: {},
  cola_revision: {},
  hay_datos: false,
  tickets_historico: 0,
  total_historico: '0',
};

function render(d: DashboardResponse) {
  return renderToStaticMarkup(
    React.createElement(FlujoGuiado, { dashboard: d, irA: () => {}, companyId: null })
  );
}

const falla: string[] = [];
const ok: string[] = [];
function comprobar(cond: boolean, nombre: string) {
  (cond ? ok : falla).push(nombre);
}

// ---------------------------------------------------------------------------
// Lo que se ve
// ---------------------------------------------------------------------------
const html = render(CON_COLA);

comprobar(html.includes('Camino del gasto'), 'el encabezado aparece');
comprobar(html.includes('Digitalizar'), 'el paso 1 tiene nombre');
comprobar(html.includes('Registrar'), 'el paso 2 tiene nombre');
comprobar(html.includes('Conciliar'), 'el paso 3 tiene nombre');
comprobar(html.includes('por confirmar'), 'el paso 2 dice qué falta');
comprobar(html.includes('7'), 'el total de comprobantes aparece');

// El dato que hace que la cola se entienda: 7 tickets leídos, 7 sin confirmar.
comprobar(html.includes('>7<'), 'los 7 pendientes por confirmar se pintan');

// ---------------------------------------------------------------------------
// Lo que NO se ve, y es lo importante
// ---------------------------------------------------------------------------
comprobar(!html.includes('undefined'), 'ningún undefined en pantalla');
comprobar(!html.includes('NaN'), 'ningún NaN en pantalla');
comprobar(!html.includes('null'), 'ningún null impreso');
comprobar(!html.includes('[object Object]'), 'ningún objeto sin renderizar');

// ---------------------------------------------------------------------------
// La promesa que NO se puede hacer
// ---------------------------------------------------------------------------
// Con 7 tickets y 7 sin confirmar, la conciliación tiene 0 sin cruzar. Mostrarla
// como "hecho" sería afirmar que se cruzó algo que no se cruzó.
//
// EL CONTEO VA SOBRE TODO EL HTML, y no sobre el trozo desde la palabra
// "Conciliar". La primera versión hacía `html.slice(html.indexOf('Conciliar'))` y
// por eso pasó un bug real: en el HTML la palomita va **antes** de la palabra, así
// que el recorte empezaba después de ella y el test era verde con la pantalla
// mintiendo. Solo se cazó mirando el render.
//
// El criterio correcto: con algo sin confirmar, en esta pantalla hay **exactamente
// una** palomita (digitalizar), y no tres.
const palomitas = (html.match(/✓/g) ?? []).length;
comprobar(palomitas === 1, `con 7 sin confirmar hay 1 sola palomita (hay ${palomitas})`);

comprobar(
  html.includes('no se atrevió a dar por buenos'),
  'se explica por qué hay cosas sin confirmar'
);
comprobar(html.includes('no es un error'), 'se dice que la cola no es una falla');

// ---------------------------------------------------------------------------
// El tablero vacío: el caso que se ve el primer día
// ---------------------------------------------------------------------------
const vacio = render(VACIO);
comprobar(vacio.includes('Todavía no hay comprobantes'), 'el vacío dice qué hacer');
comprobar(vacio.includes('Digitalizar'), 'el vacío apunta al primer paso');
comprobar(!vacio.includes('undefined'), 'el vacío tampoco tiene undefined');

// ---------------------------------------------------------------------------
// El tablero limpio: los tres hechos
// ---------------------------------------------------------------------------
const limpio = render(LIMPIO);
comprobar(limpio.includes('✓'), 'un paso terminado lleva palomita');
comprobar(!limpio.includes('undefined'), 'el limpio tampoco tiene undefined');

// ---------------------------------------------------------------------------
// El cálculo del estado, sin montar React
// ---------------------------------------------------------------------------
// Un test que necesita React para comprobar una comparación no solo pesa mas:
// si falla, no se sabe si fallo la comparación o el render.
const paso = (clave: string, pendientes: number, total = 10) => ({
  clave, titulo: '', queHace: '', pagina: 'review' as const,
  pendientes, total, nota: '',
});

// Digitalizar hecho + cola de 7: registrar en curso, y conciliar BLOQUEADO.
//
// Y "bloqueado" es lo correcto: un ticket `PENDIENTE` no se concilia
// (`SETTLED_STATUSES`). Invitar a una conciliación con 7 sin registrar sería
// ofrecer una pantalla que no puede encontrar nada — y el operador no sabría si
// el sistema falló o si todavía no le tocaba.
const conCola = [
  paso('digitalizar', 0, 7),
  paso('registrar', 7),
  paso('conciliar', 5),
];
comprobar(_estadoDe(conCola[0], 0, conCola) === 'hecho', 'digitalizar hecho si leyó algo');
comprobar(_estadoDe(conCola[1], 1, conCola) === 'en_curso', 'registrar en curso si hay cola');
comprobar(
  _estadoDe(conCola[2], 2, conCola) === 'bloqueado',
  'conciliar bloqueado si hay tickets sin confirmar'
);

// Sin digitalizar NADA, el paso 2 queda bloqueado (no hay nada que registrar).
// El paso 3 no: con la cola vacía, "registrar" ya cuenta como hecho y conciliar
// queda disponible. Es lo correcto — un bloqueo en cascada de un paso vacío
// dejaría el camino inutilizable sin que nadie pudiera escribir una línea.
const sinLeer = [paso('digitalizar', 0, 0), paso('registrar', 0), paso('conciliar', 3)];
comprobar(_estadoDe(sinLeer[1], 1, sinLeer) === 'bloqueado', 'sin digitalizar, registrar bloqueado');
comprobar(
  _estadoDe(sinLeer[2], 2, sinLeer) === 'en_curso',
  'sin nada pendiente de confirmar, conciliar sí está disponible'
);

// EL CASO QUE DESTAPÓ LA CAPTURA DE PANTALLA. Está aparte porque es el que
// importa y el que la primera versión tenía mal.
//
// 0 sin cruzar **con 7 sin confirmar** NO es "conciliado": es "todavía no le
// toca". Con la lógica de antes salía `hecho` —una palomita verde sobre un paso
// en el que no se puede trabajar— porque la condición de bloqueo miraba si había
// pendientes *propios*, y cero pendientes no disparaba nada.
//
// El orden correcto es: si hay algo sin confirmar, el paso está bloqueado **pase
// lo que pase su propio contador**.
const ceroPropioConCola = [
  paso('digitalizar', 0, 7),
  paso('registrar', 7),
  paso('conciliar', 0),
];
comprobar(
  _estadoDe(ceroPropioConCola[2], 2, ceroPropioConCola) === 'bloqueado',
  '0 sin cruzar con 7 sin confirmar NO es "hecho"'
);

// Y el caso simétrico, que es el que hace la regla útil y no un parche: cuando ya
// no queda nada sin confirmar, el mismo paso sí pasa a estar disponible.
const ceroPropioSinCola = [
  paso('digitalizar', 0, 7),
  paso('registrar', 0),
  paso('conciliar', 0),
];
comprobar(
  _estadoDe(ceroPropioSinCola[2], 2, ceroPropioSinCola) === 'hecho',
  '0 sin cruzar y sin nada pendiente SÍ es "hecho"'
);

// Todo listo: los tres hechos.
const listo = [paso('digitalizar', 0, 10), paso('registrar', 0), paso('conciliar', 0)];
comprobar(_estadoDe(listo[1], 1, listo) === 'hecho', 'registrar hecho si la cola está vacía');
comprobar(_estadoDe(listo[2], 2, listo) === 'hecho', 'conciliar hecho si no falta');

// ---------------------------------------------------------------------------
console.log(`\n  ${ok.length} comprobaciones OK:`);
ok.forEach((n) => console.log(`    ok  ${n}`));
if (falla.length) {
  console.log(`\n  ${falla.length} FALLAN:`);
  falla.forEach((n) => console.log(`    XX  ${n}`));
  process.exit(1);
}
console.log('\n  Todas pasan.\n');