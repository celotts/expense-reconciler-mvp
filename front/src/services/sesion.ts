/**
 * La sesion en el navegador: donde vive el token, cuando se da por vencido y
 * que se hace cuando lo esta.
 *
 * Esto NO es una capa de autenticacion. La autenticacion la hace el servidor y
 * es la unica que vale: aqui no se comprueba ninguna firma, porque el secreto
 * no esta en el navegador y aunque estuviera, un atacante que ejecuta
 * JavaScript en la pestana ya puede llamar al API sin pasar por este archivo.
 * Lo que se hace aqui es una sola cosa, y es la que evita el peor fallo de
 * usuario posible:
 *
 *   **Comprobar la caducidad ANTES de iniciar una transaccion.**
 *
 * Sin esa comprobacion, el usuario esta a mitad de una captura, le da "Guardar",
 * el token caduca durante el envio, el servidor responde 401, y la pantalla
 * muestra un error. El trabajo no se guardo, el archivo ya se habia consumido del
 * selector, y para reintentarlo tiene que volver a buscar el comprobante. La
 * transaccion seccionada es la que hace que un sistema con caducidad se sienta
 * roto en lugar de solo molesto.
 *
 * Que NO se guarde la pantalla anterior al vencer (ver `alVencer`) es la misma
 * idea aplicada al otro lado: si al volver a entrar el usuario cayera
 * justamente donde estaba, la pantalla recargaria los datos y pareceria que el
 * guardado si seizo. No se guardo. Prefiero que se note.
 *
 * El modulo no importa React ni toca `fetch`, a proposito: asi se puede
 * ejercitar en node sin navegador, que es donde corren los verificadores.
 */

// ---------------------------------------------------------------------------
// Margen de seguridad
// ---------------------------------------------------------------------------
//
// Un token que caduca dentro de 3 segundos esta "vivo" y no sirve. La peticion
// tarda, la transaccion tarda mas, y el token se muere en el peor momento: a
// mitad del envio, cuando el servidor ya esta escribiendo.
//
// Se descuenta un margen antes de declarar el token vencido, para que la
// decision se tome con tiempo de sobra y no en el limite. Es el mismo
// concepto que la expiracion de un TLSFohttps:uir de 30 dias, que se cuenta
// desde el dia 60 y no desde el dia 90.
const MARGEN_SEGUNDOS = 30;

/** Almacenamiento donde se guarda el token.
 *
 *  Se abstrae por dos motivos: para poder ejercitar el modulo en node (donde
 *  no hay `localStorage`), y para no repetir en tres sitios la comprobacion de
 *  que exista. */
export interface AlmacenToken {
  leer(): string | null;
  escribir(valor: string): void;
  borrar(): void;
}

const CLAVE_ALMACEN = 'expense-reconciler.token';

// ---------------------------------------------------------------------------
// El token
// ---------------------------------------------------------------------------

/** Lo que nos interesa del token. Deliberadamente NO es el token entero.
 *
 *  Copiar claims a una interfaz obliga a que, si el backend anade uno, alguien
 *  tenga que venir a aca a decidir si esta aqui. Los que no estan, no se
 *  pueden leer: `claims.mas_reciente` no compila, y eso es exactamente lo que
 *  uno quiere. */
export interface Claims {
  sub: string;
  email: string;
  /** Segundos desde la epoca. Obligatorio: sin el, el token se da por
   *  vencido (ver `estaVencido`). */
  exp: number;
}

function decodificarBase64Url(texto: string): string | null {
  if (typeof atob === 'undefined') return null;
  try {
    // base64url -> base64: cambia el alfabeto y quita el relleno. Sin esto,
    // `atob` lanza en cuanto un token lleva un `-` o un `_`, que es lo normal.
    const limpio = texto.replace(/-/g, '+').replace(/_/g, '/');
    const relleno = limpio.length % 4;
    const completo = relleno === 0 ? limpio : limpio + '='.repeat(4 - relleno);
    const binario = atob(completo);
    // El payload es UTF-8, no latin-1. Sin esto, un nombre con tilde sale con
    // los bytes cambiados, y el nombre que se muestra arriba queda ilegible.
    return decodeURIComponent(
      binario
        .split('')
        .map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0'))
        .join('')
    );
  } catch {
    return null;
  }
}

/** Lee el payload de un token SIN verificar la firma.
 *
 *  El nombre lo dice para que nadie lo use como comprobacion de seguridad. La
 *  firma se comprueba en el servidor, en cada peticion, y es la unica que cuenta.
 *  Aqui solo se leen dos campos: cuando caduca y de quien es, para pintar el
 *  nombre en la barra y para saber si hay que pedir la contrasena.
 *
 *  Devuelve `null` en vez de lanzar. Un token corrupto no es un error de
 *  programacion: es lo que hay en `localStorage` despues de un despliegue a
 *  medias, y la respuesta correcta es "no hay sesion", no romper la pantalla. */
export function leerClaims(token: string | null | undefined): Claims | null {
  if (!token) return null;
  const partes = token.split('.');
  if (partes.length !== 3) return null;
  const payload = decodificarBase64Url(partes[1]);
  if (!payload) return null;
  try {
    const crudo: unknown = JSON.parse(payload);
    if (typeof crudo !== 'object' || crudo === null) return null;
    const objeto = crudo as Record<string, unknown>;
    // `exp` tiene que ser un numero. Si viene como texto -- o no viene -- el
    // token se trata como vencido; la razon esta en `estaVencido`.
    if (typeof objeto.exp !== 'number' || !Number.isFinite(objeto.exp)) return null;
    return {
      sub: typeof objeto.sub === 'string' ? objeto.sub : '',
      email: typeof objeto.email === 'string' ? objeto.email : '',
      exp: objeto.exp,
    };
  } catch {
    return null;
  }
}

/** Segundos que le quedan. `null` cuando no se puede saber.
 *
 *  Negativo cuando ya caduco: no se recorta en cero, porque la diferencia entre
 *  "vencido hace 4 segundos" y "vencido" es la que permite decidir si hay que
 *  avisar o solo echar. */
export function segundosParaExpirar(
  token: string | null | undefined,
  ahora: number = Date.now(),
): number | null {
  const claims = leerClaims(token);
  if (!claims) return null;
  return Math.floor(claims.exp - ahora / 1000);
}

/** Si el token no sirve para iniciar una transaccion.
 *
 *  Un token ilegible cuenta como VENCIDO, no como valido. Es la decision que
 *  evita el fallo caro: si lo contrario se tratara como "no hay informacion,
 *  seguimos", un `localStorage` con basura mostraria la aplicacion entera y
 *  dejaria al usuario escribiendo en todos los formularios para que cada
 *  peticion devolviera 401. Con la regla de cerrar, el usuario ve la pantalla de
 *  entrar y sabe exactamente que hacer.
 *
 *  El servidor aplica la misma regla: un token sin `exp` obligatorio es
 *  rechazado alli tambien. Que los dos coincidan no es casualidad. */
export function estaVencido(
  token: string | null | undefined,
  ahora: number = Date.now(),
  margen: number = MARGEN_SEGUNDOS,
): boolean {
  const claims = leerClaims(token);
  if (!claims) return true;
  // Se compara en segundos enteros. Con milisegundos, el margen de 30 s se
  // comeria 0.03 s, que es despreciable pero hace que la prueba sea fragil.
  const limite = Math.floor(ahora / 1000) + margen;
  return claims.exp <= limite;
}

/** El aviso que se muestra al echar a alguien. */
export interface AvisoSesion {
  titulo: string;
  mensaje: string;
  /** Que hubo que hacer la transaccion? Si no se pudo, el texto tiene que
   *  decirlo, porque el usuario ya habia flavored el archivo. */
  transaccionIntentada: boolean;
}

// ---------------------------------------------------------------------------
// Almacenamiento
// ---------------------------------------------------------------------------

/** `localStorage` si existe y se puede escribir; si no, memoria.
 *
 *  El respaldo en memoria no es un detalle: `localStorage` lanza `QuotaExceeded`
 *  en modo privado de Safari, y en un iPad de una empresa eso es el caso
 *  normal, no la excepcion. Sin el respaldo, la primera peticion tras recargar
 *  tiraria la pantalla entera. */
function almacenReal(): AlmacenToken {
  try {
    const ls = typeof localStorage !== 'undefined' ? localStorage : null;
    if (!ls) throw new Error('sin localStorage');
    const prueba = '__prueba__';
    ls.setItem(prueba, '1');
    ls.removeItem(prueba);
    return {
      leer: () => ls.getItem(CLAVE_ALMACEN),
      escribir: (v) => ls.setItem(CLAVE_ALMACEN, v),
      borrar: () => ls.removeItem(CLAVE_ALMACEN),
    };
  } catch {
    // Memoria del proceso: se pierde al recargar, que es un annoyance, pero
    // mejor que la pantalla en blanco.
    return respaldoEnMemoria();
  }
}

/** El almacen de memoria. Se crea UNA vez y se comparte.
 *
 *  Esto parece un detalle y es la diferencia entre que el login funcione o no.
 *  `almacenReal` se llama en cada operacion, asi que si `respaldoEnMemoria`
 *  declarase su `let temporal` dentro de la funcion, cada llamada tendria su
 *  propia variable: escribir el token pondria el valor en una copia que se
 *  pierde al terminar la llamada, y leerlo devolveria `null` de una variable
 *  nueva. El resultado es que el token se guarda y desaparece en el acto, cada
 *  peticion sale sin el, el servidor responde 401, y el usuario vuelve al
 *  login. Para siempre.
 *
 *  Es justo el escenario para el que existe este respaldo --Safari en modo
 *  privado, un iPad de empresa-- y ahi no habria forma de entrar nunca. */
let respaldo: AlmacenToken | null = null;

function respaldoEnMemoria(): AlmacenToken {
  if (respaldo === null) {
    let temporal: string | null = null;
    respaldo = {
      leer: () => temporal,
      escribir: (v) => {
        temporal = v;
      },
      borrar: () => {
        temporal = null;
      },
    };
  }
  return respaldo;
}

const almacenPorDefecto: AlmacenToken = {
  leer: () => almacenReal().leer(),
  escribir: (v) => almacenReal().escribir(v),
  borrar: () => almacenReal().borrar(),
};

export function leerToken(almacen: AlmacenToken = almacenPorDefecto): string | null {
  return almacen.leer();
}

export function guardarToken(token: string, almacen: AlmacenToken = almacenPorDefecto): void {
  almacen.escribir(token);
}

export function borrarToken(almacen: AlmacenToken = almacenPorDefecto): void {
  almacen.borrar();
}

// ---------------------------------------------------------------------------
// Que hacer cuando caduca
// ---------------------------------------------------------------------------

/** Error de sesion caducada. Distinto de los demas a proposito.
 *
 *  Un `Error` cualquiera lo come el `catch` generico de cada pantalla y se
 *  muestra como un fallo cualquiera. Este se distingue para que la pantalla
 *  sepa que no es un fallo: es que hay que entrar de nuevo, y sobre todo para
 *  que el `catch` NO ofrezca "Reintentar", que es la instruccion que hace que
 *  el usuario repita la transaccion y termine con dos tickets. */
export class SesionVencida extends Error {
  readonly transaccionIntentada: boolean;
  constructor(transaccionIntentada: boolean) {
    super('La sesion caduco');
    this.name = 'SesionVencida';
    this.transaccionIntentada = transaccionIntentada;
  }
}

export function esSesionVencida(e: unknown): e is SesionVencida {
  return e instanceof SesionVencida;
}

/** Que pantalla se muestra cuando caduca. */
export function avisoPorCaducidad(transaccionIntentada: boolean): AvisoSesion {
  return transaccionIntentada
    ? {
        titulo: 'Tu sesion caduco',
        mensaje:
          'No se guardo nada. Vuelve a entrar y hazlo de nuevo: el archivo se elige ' +
          'otra vez, porque no se quedo a medio camino.',
        transaccionIntentada: true,
      }
    : {
        titulo: 'Tu sesion caduco',
        mensaje: 'Vuelve a entrar para seguir.',
        transaccionIntentada: false,
      };
}

/** A donde se va cuando caduca.
 *
 *  La respuesta es siempre el login, y **nunca** se guarda la pantalla de la
 *  que se salta. Es una decision, no una forgot de implementacion:
 *
 *  Volver a la pantalla anterior suena a amabilidad y es una trampa. La
 *  transaccion seccionada ya se perdio; al volver, la pantalla se recarga y
 *  muestra la lista como estaba, y el usuario leyendolo piensa que si se
 *  guardo. Para evitar eso hace falta que el regreso sea imposible y que el
 *  mensaje lo diga.
 *
 *  Por eso esta funcion no recibe la pantalla anterior: no hay ningun sitio
 *  donde se pueda escribir, y no se puede "olvidar" limpiarlo mas adelante. */
export const RUTA_LOGIN = '#/login';

export function rutaAlVencer(): string {
  return RUTA_LOGIN;
}

// ---------------------------------------------------------------------------
// Avisar a la puerta
// ---------------------------------------------------------------------------
//
// Que la sesion caduche no lo descubre la pantalla que la peticion hizo: lo
// descubre `api.ts`, que es quien recibe el 401. Pero el login no vive en cada
// pantalla, vive en una sola, y para que aparezca hace falta que el 401 llegue
// hasta alli.
//
// Un evento global es la manera de hacerlo sin dos atajos que no me gustan:
//
//   - Que `api.ts` lance el error y que cada pantalla lo capture para avisar:
//     son 7 pantallas, y la octava se olvida. El aviso se pierde y el usuario
//     ve un error de red en vez de "vuelve a entrar".
//   - Que `api.ts` escriba en un estado global de React: eso obliga a `api.ts`
//     a importar React, y este modulo (y `api.ts`) dejan de poder probarse sin
//     navegador, que es justo lo que `sesion.ts` quiere evitar.
//
// El aviso va en un `Set` y no en un array: dos pantallas montadas a la vez
// reciben los dos, y el que se da de baja sale del Set con su fonction de
// cancelar. Un `Set` sin borrar es una fuga de memoria y un aviso duplicado en
// la segunda visita a la pantalla.
//
// Sigue sin importar React ni tocar `fetch`, que es la linea que este modulo se
// puso desde el principio.

type OyenteCaducidad = (aviso: AvisoSesion) => void;

const oyentes = new Set<OyenteCaducidad>();

/** Se suscribe a la caducidad. Devuelve como cancelar.
 *
 *  Cancelar es tan importante como suscribirse: un `useEffect` que se
 *  suscribe y no se da de baja en la limpieza monta dos oyentes la segunda vez
 *  que se renderiza la pantalla, y el aviso aparece duplicado. */
export function alCaducar(oyente: OyenteCaducidad): () => void {
  oyentes.add(oyente);
  return () => {
    oyentes.delete(oyente);
  };
}

/** Avisa a todos los que estan escuchando. Lo llama `api.ts` en un 401. */
export function notificarCaducidad(transaccionIntentada: boolean): void {
  // Se copia antes de iterar: un oyente podria darse de baja desde dentro del
  // aviso (que es justo lo que hace la puerta, al cambiar de pantalla), y
  // mutar un Set mientras se recorre lo salta.
  for (const oyente of Array.from(oyentes)) {
    oyente(avisoPorCaducidad(transaccionIntentada));
  }
}

