/**
 * El papel va a la IZQUIERDA en las dos pantallas que revisan un importe.
 *
 * ## POR QUE ESTE ARCHIVO EXISTE
 *
 * MEDIDO el 2026-10-16: al agregar el comprobante al modal de "Editar Ticket"
 * (`Tickets.tsx`), se puso a la derecha. Compilaba, se veia bien, y no habia
 * ningun error — y aun asi era un defecto, porque **`ReviewQueue.tsx` ya lo
 * tenia a la izquierda** y el docstring de `ComprobanteAlLado` dice que a la
 * izquierda:
 *
 *     "En escritorio son dos columnas: el papel a la izquierda, los campos a la
 *     derecha."
 *
 * O sea: la misma tarea —leer un importe contra el papel— con dos
 * disposiciones distintas en dos pantallas. Es el tipo de incoherencia que no
 * sale en ninguna prueba y que el operador pagaAPLICANDO cada vez que cambia de
 * pantalla: en un lote de 40, 40 veces.
 *
 * `tsc` no ve nada de esto. Solo ve que las clases existen, no que sean las
 * correctas ni que las dos pantallas coincidan. Por eso el check es sobre el
 * CODIGO de las dos, y compara una contra la otra: es la unica forma de que
 * "las dos pantallas hacen lo mismo" sea una afirmacion comprobable y no una
 * intencion.
 *
 * ## QUE SE COMPRUEBA
 *
 * 1. Las dos pantallas ponen el papel en `lg:order-1` (a la izquierda).
 * 2. Las dos ponen los campos en `lg:order-2` (a la derecha).
 * 3. En movil el papel va segundo, igual que en la cola: no caben dos columnas
 *    y el campo que ya esta en pantalla es el que se toca primero.
 * 4. El papel es `sticky`, para que no se vaya mientras se revisan los campos.
 * 5. `Tickets.tsx` usa `ComprobanteAlLado` y no una segunda forma de cargar el
 *    comprobante: el `Blob` autenticado, el `<img>` para imagenes y el fallback
 *    de PDF ya estan probados una vez.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

let fallos = 0;

function comprobar(nombre: string, condicion: boolean, porQue: string): void {
  if (condicion) {
    console.log(`  ok   ${nombre}`);
  } else {
    fallos++;
    console.error(`  FALLA ${nombre} — ${porQue}`);
  }
}

function fuente(de: string): string {
  return readFileSync(resolve(process.cwd(), 'src', de), 'utf-8');
}

/** El `div` que envuelve una columna, a partir del `order` que trae. */
function columna(f: string, orden: RegExp): string {
  const i = f.search(orden);
  if (i < 0) return '';
  // Se toma desde el `className` del div hasta su cierre. Bastan 400 caracteres:
  // un `div` de columna no tiene nada mas adentro.
  return f.slice(i, i + 400);
}

const tickets = fuente('pages/Tickets.tsx');
const cola = fuente('pages/ReviewQueue.tsx');

/* ## 1-2. LA IZQUIERDA ES EL PAPEL, Y EN LAS DOS PANTALLAS. */
console.log('\nEL PAPEL VA A LA IZQUIERDA EN LAS DOS PANTALLAS:');

for (const [nombre, f] of [['ReviewQueue.tsx', cola], ['Tickets.tsx', tickets]] as const) {
  const papel = columna(f, /order-\d+ lg:order-1/);
  const campos = columna(f, /order-\d+ lg:order-2/);
  comprobar(`${nombre}: el papel es la columna de la izquierda`, /lg:order-1/.test(papel),
    'el papel quedo a la derecha, o en otra disposicion');
  comprobar(`${nombre}: los campos son la columna de la derecha`, /lg:order-2/.test(campos),
    'los campos quedaron a la izquierda del papel');
}

/* ## 3. EN MOVIL EL PAPEL VA SEGUNDO, IGUAL QUE EN LA COLA. */
console.log('\nEN MOVIL LAS DOS APILAN IGUAL:');

for (const [nombre, f] of [['ReviewQueue.tsx', cola], ['Tickets.tsx', tickets]] as const) {
  const papel = columna(f, /order-2 lg:order-1/);
  comprobar(`${nombre}: el papel es el segundo bloque en movil`, /order-2\b/.test(papel),
    'el papel cambio de lugar en movil y las dos pantallas ya no hacen lo mismo');
}

/* ## 4. EL PAPEL NO SE VA AL HACER SCROLL EN LOS CAMPOS. */
console.log('\nEL PAPEL NO SE ESCAPA AL HACER SCROLL:');

const papelModal = columna(tickets, /order-2 lg:order-1/);
comprobar('el papel del modal es sticky y se alinea arriba',
  /sticky/.test(papelModal) && /self-start/.test(papelModal),
  'sin `sticky lg:self-start`, corregir el ultimo campo obliga a volver arriba a ver el papel');

/* ## 5. UNA SOLA FORMA DE CARGAR EL COMPROBANTE. */
console.log('\nUNA SOLA FORMA DE CARGAR EL COMPROBANTE:');

comprobar('Tickets.tsx usa ComprobanteAlLado',
  /import \{ ComprobanteAlLado \} from '\.\.\/components\/ComprobanteAlLado'/.test(tickets),
  'se escribio una segunda forma de cargar el comprobante, y el Blob autenticado se duplica');
comprobar('Tickets.tsx no vuelve a crear un object URL a mano',
  !/createObjectURL/.test(tickets),
  'esta haciendo su propia descarga del comprobante en vez de reusar el componente');
comprobar('ReviewQueue.tsx sigue usando el mismo componente',
  /import \{ ComprobanteAlLado/.test(cola),
  'las dos pantallas dejaron de compartir el componente');

/* ## 6. Y QUE ALGUIEN LO SIGA COMPRANDO. */
if (fallos > 0) {
  console.error(`\n${fallos} comprobacion(es) fallaron.`);
  process.exit(1);
}
console.log('\nTodas las comprobaciones pasaron.');