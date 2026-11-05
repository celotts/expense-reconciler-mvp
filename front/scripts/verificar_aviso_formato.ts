/** El aviso de discrepancia de formato, y por que antes salia en todos.
 *
 *  MEDIDO el 2026-10-12: el aviso "el archivo se llama X pero sus bytes son Y"
 *  salia en los **8 de 8** archivos del registro. No porque los 8 tuvieran el
 *  nombre mal, sino porque la comparacion era
 *  `'.jpeg'.includes('image')` — una EXTENSION contra una CATEGORIA, que
 *  comparadas como cadenas nunca coinciden.
 *
 *  Un aviso que sale siempre no informa: entrena al operador a ignorar el
 *  aviso, y el dia que salga uno de verdad nadie lo va a leer. Por eso estos
 *  tests fijan los dos casos —el que DEBE salir y el que NO— y no solo que la
 *  funcion devuelva algo.
 *
 *  Y fijan un caso que parece raro y no lo es: un `.heic` que por sus bytes es
 *  un JPEG **no** avisa. Los dos son imagenes; el nombre dice HEIC y el
 *  contenido dice JPEG, y eso no confunde a nadie porque el sistema leyo bien.
 *  Lo que confunde es un PDF disfrazado de foto, y eso si avisa.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  hayDiscrepanciaDeFormato,
  categoriaDeExtension,
  motorLabel,
} from '../src/utils/scan';

let fallos = 0;

function comprobar(nombre: string, condicion: boolean, porQue: string): void {
  if (condicion) {
    console.log(`  ok   ${nombre}`);
  } else {
    fallos++;
    console.error(`  FALLA ${nombre} — ${porQue}`);
  }
}

function aviso(extension: string | null, formato: string | null): boolean {
  return hayDiscrepanciaDeFormato(extension, formato);
}

console.log('\nEL AVISO QUE SALIA EN LOS 8 DE 8, Y NO DEBIA SALIR EN NINGUNO:');

comprobar(
  '.jpeg con bytes de imagen',
  aviso('.jpeg', 'image') === false,
  'una foto llamada .jpeg leida como imagen NO es discrepancia',
);
comprobar(
  '.heic con bytes de imagen',
  aviso('.heic', 'image') === false,
  'un HEIC es una imagen: coincide la categoria aunque no el formato exacto',
);
comprobar(
  '.jpg en mayusculas',
  aviso('.JPG', 'image') === false,
  'la extension se normaliza antes de comparar',
);
comprobar(
  'sin punto inicial',
  aviso('jpeg', 'image') === false,
  'algunos Exploradores entregan la extension sin el punto',
);

console.log('\nLAS QUE SI SON UNA DISCREPANCIA REAL:');

comprobar(
  '.pdf cuyos bytes son una imagen',
  aviso('.pdf', 'image') === true,
  'un PDF disfrazado de foto SI confunde a un operador',
);
comprobar(
  '.heic cuyos bytes son un PDF',
  aviso('.heic', 'pdf') === true,
  'una foto que en realidad es un PDF es el caso que hay que avisar',
);
comprobar(
  '.pdf con bytes de texto',
  aviso('.pdf', 'text') === true,
  'categorias distintas, misma logica',
);

console.log('\nLO QUE NO SE ADIVINA, PORQUE NO HAY CON QUE COMPARAR:');

comprobar(
  'extension que no esta en el mapa',
  aviso('.rar', 'image') === false,
  'sin opinion no se avisa: un aviso sin fundamento entrena a ignorar avisos',
);
comprobar(
  'sin extension',
  aviso(null, 'image') === false,
  'no hay nombre contra el que comparar',
);
comprobar(
  'sin formato detectado',
  aviso('.pdf', null) === false,
  'no hay bytes contra los que comparar',
);
comprobar(
  'extension vacia',
  aviso('.', 'image') === false,
  'un punto solo no es una extension',
);

console.log('\nLA CATEGORIA QUE SUGIERE EL NOMBRE, PARA EL MENSAJE:');

const categorias: [string | null, string | null][] = [
  ['.jpeg', 'image'],
  ['.heic', 'image'],
  ['.pdf', 'pdf'],
  ['.txt', 'text'],
  ['.rar', null],
  [null, null],
];
for (const [ext, esperado] of categorias) {
  const obtenido = categoriaDeExtension(ext);
  comprobar(
    `categoriaDeExtension(${String(ext)})`,
    obtenido === esperado,
    `era ${String(esperado)}, salio ${String(obtenido)}`,
  );
}

console.log('\nLO DE AL LADO SIGUE INTACTO:');
comprobar('motorLabel(ocr)', motorLabel('ocr') === 'OCR local', 'cambio la etiqueta');
comprobar('motorLabel(null)', motorLabel(null) === 'sin leer', 'cambio la etiqueta');

/* LA PANTALLA USA LA FUNCION, Y ESO TAMBIEN SE PRUEBA.
 * ------------------------------------------------------------
 * Las comprobaciones de arriba fijan `hayDiscrepanciaDeFormato`. Pero esa
 * funcion podria estar bien y la pantalla seguir avisando por su cuenta: si
 * `Scan.tsx` vuelve a la comparacion vieja, TODO lo de aqui pasa en verde y el
 * aviso sigue saliendo en los 8 de 8. MEDIDO: la tercera mutacion
 * (`hayDiscrepanciaDeFormato(...)` -> comparacion en crudo) daba VERDE con este
 * archivo, y por eso esta seccion existe.
 *
 * Se lee el texto de `Scan.tsx` y no se monta la pantalla porque aqui no hay
 * React: lo que importa es que la condicion que decide SI se avisa sea la
 * funcion, y eso se lee igual de bien. */
console.log('\nLA PANTALLA CONSULTA A LA FUNCION, NO A LA COMPARACION CRUDA:');

// La ruta se resuelve contra el DIRECTORIO de trabajo, no contra `import.meta.url`:
// al empaquetarse con esbuild a un `.js` en `/tmp`, `import.meta.url` apunta ahi
// y `new URL('../src/...')` no resuelve a nada — `ERR_INVALID_URL`. Los demas
// verificadores de este repo se ejecutan con `cwd` en `front/`, asi que se usa lo
// mismo y no una ruta que solo funciona en un empaquetado.
const PAGINA = resolve(process.cwd(), 'src/pages/Scan.tsx');
let fuentePantalla = '';
try {
  fuentePantalla = readFileSync(PAGINA, 'utf-8');
} catch (e) {
  throw new Error(`no se pudo leer Scan.tsx: ${String(e)}`);
}

comprobar(
  'Scan.tsx llama a hayDiscrepanciaDeFormato',
  fuentePantalla.includes('hayDiscrepanciaDeFormato('),
  'la pantalla no usa la funcion: el aviso depende de ella',
);
comprobar(
  'Scan.tsx ya no compara la extension en crudo',
  !/declared_extension\s*\.\s*includes\s*\(/.test(fuentePantalla),
  'quedo la comparacion extension-contra-categoria, que es la del bug',
);

if (fallos > 0) {
  console.error(`\n${fallos} comprobacion(es) fallaron.`);
  process.exit(1);
}
console.log('\nTodas las comprobaciones pasaron.');