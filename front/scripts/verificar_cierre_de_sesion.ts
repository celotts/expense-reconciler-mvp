/**
 * El cierre de sesion que se ve desde fuera, sin navegador.
 *
 * Borrar el token del navegador no cierra nada: un token copiado seguiria
 * sirviendo hasta `exp`. Este verificador llama a `salir()` de verdad y mira la
 * peticion que sale, no el texto del codigo: `tsc` pasa con un `await` quitado
 * y con un `fetch` que nunca se llama.
 *
 * Cuatro casos que se rompen solos si alguien "simplifica" el flujo:
 *
 *  1. El POST va con el token AUN guardado (si se borra antes, el servidor
 *     recibe un logout sin autorizacion y no revoca nada).
 *  2. Un 401 del logout no dispara el aviso de caducidad: es el resultado
 *     esperado, no una transaccion a medias.
 *  3. Un fallo de red no deja el token en el navegador: la interfaz se cierra
 *     aunque el servidor no haya podido revocar.
 *  4. Un token que ya caduco localmente no se manda a preguntar.
 *
 * Uso, desde front/:  npm run verify:sesion
 */

import { salir } from '../src/services/auth';
import { alCaducar, borrarToken, guardarToken, leerToken } from '../src/services/sesion';

let fallos = 0;
function check(desc: string, cond: boolean, detalle = ''): void {
  console.log(`  ${cond ? 'OK   ' : 'FALLA'} ${desc}${!cond && detalle ? `\n         ${detalle}` : ''}`);
  if (!cond) fallos++;
}

type Peticion = { url: string; init?: RequestInit };

function tokenValido(): string {
  const payload = {
    sub: '11111111-1111-1111-1111-111111111111',
    email: 'persona@example.com',
    exp: Math.floor(Date.now() / 1000) + 3600,
  };
  return `eyJhbGciOiJIUzI1NiJ9.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.firma`;
}

function tokenVencido(): string {
  const payload = {
    sub: '11111111-1111-1111-1111-111111111111',
    email: 'persona@example.com',
    exp: Math.floor(Date.now() / 1000) - 10,
  };
  return `eyJhbGciOiJIUzI1NiJ9.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.firma`;
}

function respuesta(status: number): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => (status === 401 ? JSON.stringify({ detail: 'La sesion fue cerrada' }) : ''),
    json: async () => undefined,
  } as Response;
}

const fetchOriginal = globalThis.fetch;
const peticiones: Peticion[] = [];

function conFetch(
  comportamiento: (url: string, init?: RequestInit) => Promise<Response>,
): void {
  peticiones.length = 0;
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    peticiones.push({ url: String(url), init });
    return comportamiento(String(url), init);
  }) as typeof globalThis.fetch;
}

function autorizacion(p: Peticion): string {
  const headers = p.init?.headers as Record<string, string> | undefined;
  return headers?.Authorization ?? headers?.authorization ?? '';
}

async function main(): Promise<void> {
  console.log('\nEl cierre de sesion en el servidor:');

  // 1. El token viaja ANTES de borrarse y el metodo es el del contrato.
  borrarToken();
  const token = tokenValido();
  guardarToken(token);
  conFetch(async () => respuesta(204));
  await salir();
  check('se llama al logout del servidor', peticiones.length === 1, `peticiones: ${peticiones.length}`);
  check('el endpoint es /auth/logout', peticiones[0]?.url.endsWith('/auth/logout') ?? false, peticiones[0]?.url ?? '');
  check('es POST', peticiones[0]?.init?.method === 'POST', String(peticiones[0]?.init?.method));
  check('lleva el token guardado', autorizacion(peticiones[0] ?? {}) === `Bearer ${token}`, autorizacion(peticiones[0] ?? {}));
  check('despues ya no hay token local', leerToken() === null, `quedo: ${leerToken()}`);

  // 2. Un 401 del logout es el resultado buscado, no una caducidad que avisar.
  borrarToken();
  guardarToken(tokenValido());
  let avisos = 0;
  const dejarDeEscuchar = alCaducar(() => { avisos += 1; });
  conFetch(async () => respuesta(401));
  await salir();
  dejarDeEscuchar();
  check('un 401 del logout NO dispara el aviso de caducidad', avisos === 0, `avisos: ${avisos}`);
  check('y el token se borra igual', leerToken() === null, `quedo: ${leerToken()}`);

  // 3. La red cae: la interfaz se cierra aunque el servidor no revocara.
  borrarToken();
  guardarToken(tokenValido());
  conFetch(async () => { throw new Error('red caida'); });
  await salir();
  check('un fallo de red no impide borrar el token', leerToken() === null, `quedo: ${leerToken()}`);

  // 4. Un token muerto no se gasta en un round trip.
  borrarToken();
  guardarToken(tokenVencido());
  conFetch(async () => respuesta(204));
  await salir();
  check('un token vencido no se manda al servidor', peticiones.length === 0, `peticiones: ${peticiones.length}`);
  check('pero tambien se borra', leerToken() === null, `quedo: ${leerToken()}`);

  // 5. Salir sin sesion es una operacion limpia, no un 401 de esos que avisan.
  borrarToken();
  conFetch(async () => respuesta(204));
  await salir();
  check('sin token no se llama al servidor', peticiones.length === 0, `peticiones: ${peticiones.length}`);

  globalThis.fetch = fetchOriginal;
}

main().then(
  () => {
    console.log(`\n${fallos === 0 ? 'TODO OK' : `${fallos} FALLOS`}`);
    process.exit(fallos === 0 ? 0 : 1);
  },
  (e: unknown) => {
    globalThis.fetch = fetchOriginal;
    console.error(e);
    process.exit(1);
  },
);
