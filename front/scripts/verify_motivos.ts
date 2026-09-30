/**
 * La cola de revisión explica los motivos del gate. Y el `default` tiene que
 * seguir siendo honesto.
 *
 * Por qué este archivo
 * -------------------
 *
 * Hay dos vocabularios y se confunden:
 *
 * - Los checks del gate viajan como CÓDIGO (`provider_missing`, `total_not_positive`).
 *   `confidence_gate.py` los escribe así y esta pantalla los traduce.
 * - Los fallos del modelo viajan como FRASE en español.
 *   `capture.motivo_de_fallo_del_modelo` devuelve `FALLOS_DEL_MODELO[clave]`, que
 *   es el texto, no la clave. Escrito a propósito: "el proveedor no se pudo leer"
 *   y "el extractor está apagado" producen el mismo ticket pero piden cosas
 *   opuestas.
 *
 * Cuando se añadió `IMAGEN_ILEGIBLE` al backend, su frase cayó en el `default` de
 * `explicar()` y la pantalla le dijo al contador "Motivo no reconocido por esta
 * version de la pantalla" sobre algo que el backend sí emite y que él sí puede
 * arreglar: retocar el documento.
 *
 * El `default` NO es un error. Es lo correcto para lo desconocido de verdad, y por
 * eso este archivo comprueba las dos cosas a la vez: que lo conocido se traduzca
 * bien, y que lo desconocido se diga en vez de inventarse.
 *
 * Estos tests corren con el verificador de login (`front/scripts/verify_login.ts`)
 * porque no hay runner de tests unitarios en el front; ver el uso en el header de
 * ese archivo.
 */

// Solo se usa lo que el modulo EXPORTA. `explicar` es privada a proposito:
// este archivo prueba el contrato publico, no los internos. La primera
// version de este script importaba `explicar` y no compilaba, porque nadie
// lo corrio antes de commitearlo.
import { explainValidationErrors } from '../src/utils/extraction';

let fallos = 0;
function check(desc: string, cond: boolean, detalle = '') {
  console.log(`  ${cond ? 'OK   ' : 'FALLA'} ${desc}${!cond && detalle ? `\n         ${detalle}` : ''}`);
  if (!cond) fallos++;
}

/** Atajo al unico punto de entrada publico del modulo. */
function explicar(codigo: string | null | undefined) {
  const problemas = explainValidationErrors(codigo);
  if (problemas.length === 0) throw new Error(`sin problemas para ${JSON.stringify(codigo)}`);
  return problemas[0];
}

/** Las frases que el backend emite de verdad, sacadas de `FALLOS_DEL_MODELO`. */
const ERROR_PARSING = 'el modelo devolvio una respuesta que no se pudo interpretar';
const AI_DISABLED = 'el extractor de IA esta apagado o no respondio';
const IMAGEN_ILEGIBLE = 'el archivo es una imagen que no se pudo decodificar';

console.log('\nLos checks del gate se traducen');
check('provider_missing', explicar('provider_missing').message.includes('proveedor'));
check('total_not_positive', explicar('total_not_positive').message.includes('importe')
  || explicar('total_not_positive').message.includes('total'));
check('malformed_rfc', explicar('malformed_rfc').message.includes('RFC'));

console.log('\nEl motivo de imagen ilegible NO cae en "no reconocido"');
{
  const p = explicar(IMAGEN_ILEGIBLE);
  check('no dice "Motivo no reconocido"',
    !p.message.includes('no reconocido'),
    `Si lo dice, el contador ve un error de la pantalla donde lo que hay es un archivo que necesita convertirse. Message: ${p.message}`);
  check('dice que se puede arreglar', /PDF|foto|convertir/i.test(p.message),
    `Message: ${p.message}`);
  check('conserva el codigo original', p.code === IMAGEN_ILEGIBLE);
}

console.log('\nLos otros dos fallos del modelo tampoco caen en "no reconocido"');
for (const [nombre, frase] of [['ERROR_PARSING', ERROR_PARSING], ['AI_DISABLED', AI_DISABLED]] as const) {
  const p = explicar(frase);
  check(`${nombre} no dice "no reconocido"`,
    !p.message.includes('no reconocido'),
    `Message: ${p.message}`);
}

console.log('\nEl default sigue siendo honesto');
{
  const raro = 'motivo_del_futuro_que_nadie_conoce';
  const p = explicar(raro);
  check('lo desconocido se muestra, no se esconde', p.message.includes(raro),
    `Message: ${p.message}`);
  check('lo desconocido se avisa, no se disimula', p.message.includes('no reconocido'));
}

console.log('\nLa lista de validation_errors se procesa entera');
{
  const varios = explainValidationErrors(`${ERROR_PARSING}; ${IMAGEN_ILEGIBLE}`);
  check('produce dos problemas', varios.length === 2, `Salieron ${varios.length}`);
  check('ninguno dice "no reconocido"',
    varios.every((p) => !p.message.includes('no reconocido')),
    JSON.stringify(varios.map((p) => p.message), null, 2));
  check('la lista vacia no rompe', explainValidationErrors('').length === 0);
  check('null no rompe', explainValidationErrors(null as unknown as string).length === 0);
}

console.log(fallos === 0 ? '\nTodo verde' : `\n${fallos} FALLAS`);
if (fallos > 0) process.exit(1);
