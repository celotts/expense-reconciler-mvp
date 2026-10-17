/**
 * El login de punta a punta, sin navegador.
 *
 * Lo que este archivo cubre es lo que un `curl` NO puede cubrir y que tampoco se
 * puede ver leyendo el codigo: que la pantalla diga lo que tiene que decir, y
 * que la logica que decide "hay sesion" o "no hay sesion" acierte en los casos
 * raros. El render es con `react-dom/server` porque lo que importa verificar es
 * el TEXTO que sale, no los pixeles.
 *
 * Las tres cosas que mas importan aqui, y que estan Broken de las formas que
 * mas molestan:
 *
 *  1. **Un 401 del login NO puede parecer "vuelve a entrar".** Es el mismo
 *     codigo que un token caducado, pero significan cosas opuestas: uno es
 *     "tu contrasena esta mal" y el otro es "tu sesion se acabo". Si se
 *     confunden, un contrasena mal escrita saca a la persona de la pantalla de
 *     entrar y la deja en un aviso sin sentido, porque nunca hubo sesion que
 *     perder.
 *
 *  2. **Un token guardado tiene que seguir ahi al leerlo.** Este parece
 *     trivial y no lo es: el respaldo en memoria de `sesion.ts` se creaba en
 *     cada llamada, asi que el token se escribia en una copia que se perdia. En
 *     un navegador normal no se nota (ahí esta `localStorage`); se nota en
 *     Safari privado y en un iPad de empresa, que es justo para lo que existe
 *     ese respaldo, y ahi el login no funciona nunca. Se verifica aqui con la
 *     memoria forzada, que es como se ve ese caso sin un iPad a mano.
 *
 *  3. **Un token caducado se tira antes de gastarlo.** Mandarlo a preguntar
 *     cuando ya se sabe que va a recibir un 401 es un round trip por session
 *     perdida, y un round trip es justo lo que hace que la pantalla parezca
 *     rota.
 *
 * Uso, desde front/:
 *     npx esbuild scripts/verify_login.ts --bundle --platform=node \
 *         --format=cjs --outfile=/tmp/login.cjs --log-level=error \
 *         --loader:.ts=tsx --loader:.css=empty \
 *         --define:import.meta='{"env":{"VITE_API_URL":"http://localhost:8000/api/v1"}}' \
 *         && node /tmp/login.cjs
 *
 * Habla con la API real, no con una falsa: el 401 y el 429 salen con su status
 * exacto y su cabecera `Retry-After`, y un mock que los devuelve bien verifica
 * el codigo de quien los handlea, no los codigos de verdad.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Login } from '../src/pages/Login';
import { entrar, comprobarSesion, salir } from '../src/services/auth';
import {
  estaVencido, leerToken, guardarToken, borrarToken, segundosParaExpirar,
  alCaducar, notificarCaducidad, avisoPorCaducidad, esSesionVencida, SesionVencida,
} from '../src/services/sesion';

const API = process.env.API_URL || 'http://localhost:8000/api/v1';
const CORREO = process.env.LOGIN_EMAIL || 'prueba@test.mx';
const CLAVE = process.env.LOGIN_PASSWORD || 'Prueba1234!';

let fallos = 0;
function check(desc: string, cond: boolean, detalle = '') {
  console.log(`  ${cond ? 'OK   ' : 'FALLA'} ${desc}${!cond && detalle ? `\n         ${detalle}` : ''}`);
  if (!cond) fallos++;
}

function texto(html: string): string {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z]+;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const noop = () => undefined;

/** El texto que una persona leeria en la pantalla de entrar.
 *
 *  `createElement` y no llamar a `Login(...)` como si fuera una funcion: `Login`
 *  usa `useState`, y los hooks necesitan un componente de verdad. Es la misma
 *  razon por la que el render va con `react-dom/server` y no con la funcion
 *  directamente. */
function loginRenderizado(aviso: Parameters<typeof Login>[0]['aviso']): string {
  return texto(renderToStaticMarkup(
    createElement(Login, { onEntro: noop, aviso }),
  ));
}

// ---------------------------------------------------------------------------
// 1. La pantalla
// ---------------------------------------------------------------------------

console.log('\nLa pantalla de entrar:');

const html = loginRenderizado(null);
check('pide el correo', /correo/i.test(html), html);
check('pide la contrasena', /contrasena|contrase/i.test(html), html);
check('tiene boton de entrar', /entrar/i.test(html), html);
check('dice que no hay autorregistro', /autorregistro/i.test(html), html);

const htmlAviso = loginRenderizado(avisoPorCaducidad(true));
check(
  'el aviso de caducidad dice que NO se guardo nada',
  /no se guardo nada/i.test(htmlAviso),
  htmlAviso,
);
check(
  'el aviso de caducidad avisa de que hay que volver a elegir el archivo',
  /archivo/i.test(htmlAviso),
  htmlAviso,
);

const htmlAvisoTranquilo = loginRenderizado(avisoPorCaducidad(false));
check(
  'sin transaccion a medias, el aviso NO alarmista',
  !/no se guardo nada/i.test(htmlAvisoTranquilo),
  htmlAvisoTranquilo,
);

// ---------------------------------------------------------------------------
// 2. El almacen
// ---------------------------------------------------------------------------

console.log('\nEl token se guarda de verdad:');

// Sin localStorage (node), `sesion.ts` usa su respaldo en memoria. Es
// exactamente el camino que toma Safari en modo privado, y el que fallaba.
borrarToken();
guardarToken('un-token-de-prueba');
check('el token sobrevive a ser leido', leerToken() === 'un-token-de-prueba', `leido: ${leerToken()}`);
check('y se puede volver a leer igual', leerToken() === 'un-token-de-prueba', `leido: ${leerToken()}`);
borrarToken();
check('borrar lo deja en null', leerToken() === null, `leido: ${leerToken()}`);

// ---------------------------------------------------------------------------
// 3. La caducidad
// ---------------------------------------------------------------------------

console.log('\nLa caducidad:');

// `exp` en segundos desde la epoca, que es lo que viaja en el token.
const enUnaHora = Math.floor(Date.now() / 1000) + 3600;
const haceUnaHora = Math.floor(Date.now() / 1000) - 3600;

const tokenVivo = `x.${Buffer.from(JSON.stringify({ sub: 'u', email: 'a@b.mx', exp: enUnaHora })).toString('base64url')}.y`;
const tokenMuerto = `x.${Buffer.from(JSON.stringify({ sub: 'u', email: 'a@b.mx', exp: haceUnaHora })).toString('base64url')}.y`;

guardarToken(tokenVivo);
check('un token con vida NO esta vencido', !estaVencido(tokenVivo));
check('un token caducado SI esta vencido', estaVencido(tokenMuerto));
check('la basura se trata como vencida, no como valida', estaVencido('no-es-un-token'));
check('sin token, vencido', estaVencido(null));
check('segundosParaExpirar cuenta hacia adelante', (segundosParaExpirar(tokenVivo) ?? -1) > 3500);
check('segundosParaExpirar se vuelve negativo al morir', (segundosParaExpirar(tokenMuerto) ?? 0) < 0);

borrarToken();

// El margen: un token que muere dentro de 30 s esta vivo y no sirve. Sin esto,
// la peticion sale, la transaccion se corta a mitad y el trabajo se pierde.
const enVeinteSegundos = Math.floor(Date.now() / 1000) + 20;
const tokenAlLimite = `x.${Buffer.from(JSON.stringify({ sub: 'u', email: 'a@b.mx', exp: enVeinteSegundos })).toString('base64url')}.y`;
check('un token que expira en 20 s ya cuenta como vencido', estaVencido(tokenAlLimite));

// ---------------------------------------------------------------------------
// 4. El aviso a la puerta
// ---------------------------------------------------------------------------

console.log('\nLa puerta se entera:');

const recibidos: string[] = [];
const cancelar = alCaducar(a => recibidos.push(a.mensaje));
notificarCaducidad(true);
notificarCaducidad(false);
cancelar();
notificarCaducidad(true);
check('los que escuchan reciben el aviso', recibidos.length === 2, `recibidos: ${recibidos.length}`);
check('y se distingue con transaccion a medias de sin transaccion',
  recibidos[0] !== recibidos[1], `${recibidos[0]} || ${recibidos[1]}`);
check('cancelar la suscripcion detiene el aviso', recibidos.length === 2, `recibidos: ${recibidos.length}`);

check('SesionVencida se reconoce a si mismo', esSesionVencida(new SesionVencida(true)));
check('un Error normal NO es SesionVencida', !esSesionVencida(new Error('x')));

// ---------------------------------------------------------------------------
// 5. Contra la API de verdad
// ---------------------------------------------------------------------------

// Va dentro de una funcion y no en el nivel superior porque el bundle es `cjs`
// (el formato que usan los otros verificadores) y `cjs` no admite `await` arriba
// del todo. Cambiar el formato solo por esto haria que este archivo se ejecutara
// distinto a los demas; el `await` dentro de la funcion no.
async function contraLaApiReal(): Promise<void> {
  console.log('\nContra la API real:');

  salir();
  check('tras salir no hay token', leerToken() === null);

  // Contrasena mala: el mensaje del servidor, no un "vuelve a entrar".
  try {
    await entrar(CORREO, 'contrasena-equivocada');
    check('contrasena incorrecta NO entra', false, 'el login devolvio exito');
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    check(
      'contrasena incorrecta muestra lo que dijo el servidor',
      /incorrect/i.test(mensaje),
      `mensaje: ${mensaje}`,
    );
    check(
      'y NO es el aviso de sesion caducada',
      !/vuelve a entrar|sesi[oó]n (term|caduc)/i.test(mensaje),
      `mensaje: ${mensaje}`,
    );
    check('el fallo no es SesionVencida', !esSesionVencida(e));
  }

  // Sin sesion, comprobarSesion responde null y no lanza.
  salir();
  const sinSesion = await comprobarSesion();
  check('sin token, comprobarSesion devuelve null', sinSesion === null, `devolvio: ${JSON.stringify(sinSesion)}`);

  // Con un token basura, tambien null, y ademas lo limpia.
  guardarToken('basura.que.no.es.un.token');
  const conBasura = await comprobarSesion();
  check('con token basura, comprobarSesion devuelve null', conBasura === null, `devolvio: ${JSON.stringify(conBasura)}`);
  check('y el token basura se borra', leerToken() === null, `quedo: ${leerToken()}`);

  // Un token con la forma correcta pero firmado por otro: el navegador lo
  // encuentra en el `localStorage` de una sesion anterior, o tras cambiar la
  // clave. Es "no hay sesion", no un error.
  guardarToken(
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.' +
    Buffer.from(JSON.stringify({ sub: 'u', email: 'x@y.mx', exp: Math.floor(Date.now() / 1000) + 99999 })).toString('base64url') +
    '.firmaQueNoCuadra',
  );
  const conFirmaMala = await comprobarSesion();
  check('con firma que no cuadra, comprobarSesion devuelve null', conFirmaMala === null, `devolvio: ${JSON.stringify(conFirmaMala)}`);

  // Ahora si, el camino bueno.
  const usuario = await entrar(CORREO, CLAVE);
  check('login correcto entra', usuario !== null);
  check('devuelve el correo', usuario?.email === CORREO, `email: ${usuario?.email}`);
  check('devuelve un nombre (no se inventa en el cliente)', !!usuario?.nombre, `nombre: ${usuario?.nombre}`);
  check('el token quedo guardado', !!leerToken());

  const verificado = await comprobarSesion();
  check('comprobarSesion confirma la sesion', verificado?.email === CORREO, `devolvio: ${JSON.stringify(verificado)}`);

  // El token de verdad contra un endpoint protegido.
  const r = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${leerToken()}` } });
  check('el token guardado sirve para un endpoint protegido', r.status === 200, `HTTP ${r.status}`);

  // Y uno protegido sin token, para confirmar que el token no viaja "de mas".
  const rSin = await fetch(`${API}/auth/me`);
  check('sin token, el mismo endpoint responde 401', rSin.status === 401, `HTTP ${rSin.status}`);

  salir();
}

// ---------------------------------------------------------------------------

contraLaApiReal().then(
  () => {
    console.log(`\n${fallos === 0 ? 'TODO OK' : `${fallos} FALLOS`}`);
    process.exit(fallos === 0 ? 0 : 1);
  },
  (e) => {
    // Si algo revienta, es un fallo del verificador, no del codigo que verifica.
    // Se imprime entero para que la traza llegue al fondo.
    console.error('\nEl verificador revento:');
    console.error(e);
    process.exit(2);
  },
);

