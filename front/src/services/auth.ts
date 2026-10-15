/** Las tres llamadas de la sesion: entrar, comprobar, salir.
 *
 *  Va en un archivo aparte de `sesion.ts` porque ese modulo es deliberadamente
 *  tonto: no importa React, no toca `fetch`, y se puede ejercitar en node sin
 *  navegador. Que es justo por lo que sus comentarios dicen que "los
 *  verificadores" corren ahi. Si el login viviera dentro, `sesion.ts` dejaria de
 *  ser ejercitable sin navegador y habria que montar un `fetch` falso para probar
 *  una resta de dos numeros.
 *
 *  La division queda asi:
 *
 *    - `sesion.ts`  SAVER. Donde vive el token y cuando caduca. Puro.
 *    - `api.ts`     MANDAR. attaching el token a cada peticion.
 *    - `auth.ts`    ESTE ARCHIVO. Las tres llamadas de la puerta.
 *
 *  La autenticacion de verdad la hace el servidor y es la unica que vale: aqui
 *  no se comprueba ninguna firma, porque el secreto no esta en el navegador.
 */
import type { RespuestaToken, Usuario } from '../types/api';
import { fetchApi } from './api';
import { borrarToken, estaVencido, guardarToken, leerToken, esSesionVencida } from './sesion';

/** Entra y deja el token guardado.
 *
 *  Se guarda el token ANTES de devolver nada, para que el `try/catch` de quien
 *  llama no pueda quedar en un estado donde la pantalla ya cambio a "entraste"
 *  y el token no exista todavia: recargar en ese instante devolveria al login
 *  sin explicacion.
 *
 *  Lanza `Error` con el mensaje del servidor ("Correo o contrasena incorrectos",
 *  "Demasiados intentos...") para que la pantalla lo muestre tal cual. La razon
 *  de que salga el 429 con su texto y no como "error desconocido" es que el
 *  usuario tiene que saber quantos segundos esperar. */
export async function entrar(email: string, password: string): Promise<Usuario> {
  const respuesta = await fetchApi<RespuestaToken>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
    // Sin token: un 401 aqui son credenciales malas, no una sesion que caduco.
    // Sin esto, un contrasena equivocado expulsaria al usuario de la pantalla de
    // entrar hacia un aviso de "vuelve a entrar" que no tiene sentido, porque
    // nunca hubo sesion.
    sinToken: true,
  });

  guardarToken(respuesta.access_token);
  return respuesta.user;
}

/** Le pregunta al servidor quien es el dueño del token.
 *
 *  Existe para no confiar en lo que dice el `localStorage`. Un token puede seguir
 *  siendo criptograficamente valido con la cuenta dada de baja, o haber caducado
 *  mientras la pestana estaba cerrada: el navegador no se entera de ninguna de
 *  las dos cosas, y sin esta llamada la aplicacion abre(contententa) para que la
 *  primera pantalla muestre un error.
 *
 *  `null` significa "no hay sesion" y es una respuesta NORMAL, no un fallo: se
 *  llama en cada arranque y casi siempre va a devolver `null` cuando alguien
 *  entra por primera vez. Por eso no lanza. Un 401 SI borra el token, porque ahi
 *  si sabemos que no sirve; cualquier otro error (la red se cayo, el API
 *  reinicio) se relanza para que la pantalla lo diga, porque tragar un fallo de
 *  red y mostrar el login haria creer al usuario que su sesion se perdio. */
export async function comprobarSesion(): Promise<Usuario | null> {
  // Un token vencido no se manda ni a preguntar. Se descarta aqui y no en el
  // `catch`: mandar un token que sabemos muerto solo gastaria un round trip
  // para recibir el 401 que ya sabemos que va a llegar.
  const token = leerToken();
  if (!token || estaVencido(token)) {
    if (token) borrarToken();
    return null;
  }

  try {
    return await fetchApi<Usuario>('/auth/me');
  } catch (e) {
    if (esSesionVencida(e)) {
      borrarToken();
      return null;
    }
    // Cualquier otro fallo (la red se cayo, el API reinicio) NO se come el
    // token: la sesion puede seguir siendo valida y al reintentar funciona. Si
    // aqui se borrara, un fallo de red de dos segundos cerraria la sesion de
    // todo el mundo.
    throw e;
  }
}

/** Salir.
 *
 *  Borra el token y ya. No hay logout en el servidor: el token es un bearer
 *  firmado, sin lista de revocacion, asi que "salir" aqui significa "dejar de
 *  mandarlo". Un logout de verdad (invalidar el token) exigiria guardar el `exp`
 *  de cada token emitido y mirarlo en cada peticion, que para una herramienta
 *  interna no compensa: el token dura lo que dura y `ACCESS_TOKEN_EXPIRE_MINUTES`
 *  esta puesto a 8 horas, una jornada. */
export function salir(): void {
  borrarToken();
}
