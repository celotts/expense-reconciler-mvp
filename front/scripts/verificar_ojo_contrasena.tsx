/**
 * Que el botón del ojo REALMENTE muestre la clave, y no solo cambie de icono.
 *
 * ## EL DEFECTO QUE ESTE SCRIPT EXISTE PARA QUE NO VUELVA
 *
 * MEDIDO: en el login, al pulsar el icono del ojo **no se veía la contraseña**;
 * seguían los asteriscos. El icono sí alternaba de un ojo a un ojo tachado, así
 * que el botón *parecía* funcionar.
 *
 * La causa era el orden de dos spreads en `components/ui.tsx`:
 *
 *     {...(esPassword ? { type: visible ? 'text' : 'password' } : {})}
 *     {...props}          // <-- props trae type="password"
 *
 * `Login.tsx:120` pasa `type="password"`, así que `props` lo traía. React aplica
 * el **último** spread, y el último era `{...props}`: el `type` calculado se
 * pisaba con el del padre en cada render. El estado `visible` sí cambiaba —de
 * ahí que el icono alternara—, pero el `input` se quedaba en `type="password"`
 * para siempre.
 *
 * Eso es lo que hace el defecto caro de diagnosticar: **el control cambia de
 * estado sin cambiar lo que controla**. Se lee como "el botón está roto" y no
 * como "el botón funciona y el atributo no llega".
 *
 * ## POR QUÉ HAY QUE LEER EL FUENTE Y NO SOLO EL HTML
 *
 * `renderToStaticMarkup` no es un navegador: **no hay a quién pulsar**. Un
 * script que monta el componente y mira el `type` inicial solo puede afirmar
 * que el campo *empieza* oculto —que es verdad y no es el defecto—.
 *
 * La primera versión de este script intentó comprobar que la clave "no queda
 * escrita en el HTML" y **falló con el bug arreglado**: `renderToStaticMarkup`
 * sí escribe `value` en el HTML estático, con o sin el botón. Es decir, la
 * comprobación afirmaba algo falso y habría dado verde con el script roto.
 *
 * La defensa real es la que usa `verificar_fecha.ts`, que es el mismo problema
 * con otro disfraz: **leer el fuente y fallar si alguien reintroduce la forma
 * mala**. El orden de los spreads es una propiedad del código, y un archivo que
 * se puede leer se puede comprobar. Por eso la comprobación central de aquí es
 * sobre `ui.tsx`, y el renderizado solo cubre lo que sí se puede ver sin DOM.
 *
 * ## POR QUÉ NO UN TEST DE TYPESCRIPT
 *
 * Porque `tsc` pasa con el bug puesto: los tipos del `Input` son correctos con
 * cualquiera de los dos órdenes, y el `type` es un atributo del DOM, no del
 * modelo de tipos. Compilar no dice nada de lo que se ve en pantalla.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { Input } from '../src/components/ui';

let fallos = 0;

function comprobar(nombre: string, ok: boolean, porQue: string): void {
  if (ok) {
    console.log(`  ok   ${nombre}`);
  } else {
    fallos++;
    console.error(`  FALLA ${nombre} — ${porQue}`);
  }
}

/** El HTML de un input, para poder mirar sus atributos sin DOM real. */
function inputDe(html: string): string {
  const m = html.match(/<input[^>]*>/);
  if (!m) throw new Error('no salió ningún input:\n' + html);
  return m[0];
}

function atributo(tag: string, nombre: string): string | null {
  const m = tag.match(new RegExp(`${nombre}="([^"]*)"`));
  return m ? m[1] : null;
}

// ---------------------------------------------------------------------------
// 1. El fuente: el `type` calculado tiene que ir DESPUES de `{...props}`
// ---------------------------------------------------------------------------

const fuente = readFileSync(resolve(process.cwd(), 'src/components/ui.tsx'), 'utf-8');

// Se recorta al `<input` del `Input`, porque hay otros `<input>` en el archivo
// (el de búsqueda del Select, por ejemplo) y el orden que importa es el de aquél.
const trozo = fuente.slice(fuente.indexOf('<input'), fuente.indexOf('/>', fuente.indexOf('<input')));

const posProps = trozo.indexOf('{...props}');
const posType = trozo.search(/\{\.\.\.\(esPassword \?/);

comprobar('el input tiene los dos spreads', posProps !== -1 && posType !== -1,
  'no se encontró `{...props}` ni el spread del type: el Input cambió de forma');

comprobar('el type calculado va DESPUES de {...props}', posType > posProps,
  'el type calculado va antes de {...props}, así que el type del padre lo pisa y el botón no muestra nada. ' +
  'React aplica el último spread.');

// Y la forma vieja, nombrada: si alguien la escribe, que el script lo diga.
const formaVieja = /\{\.\.\.\(esPassword \? \{ type:[^\n]*\}\s*:\s*\{\}\)\}\s*\n\s*\{\.\.\.props\}/;
comprobar('no está la forma vieja (type calculado antes de props)', !formaVieja.test(trozo),
  'volvió el orden que hace que el botón no muestre la clave');

// ---------------------------------------------------------------------------
// 2. El renderizado: lo que SÍ se puede ver sin DOM
// ---------------------------------------------------------------------------

{
  const html = renderToStaticMarkup(
    React.createElement(Input, {
      label: 'Contraseña',
      type: 'password',
      value: 'secreto123',
      onChange: () => {},
    })
  );
  const tag = inputDe(html);

  comprobar('un campo de contraseña sale como password', atributo(tag, 'type') === 'password',
    `type sale como "${atributo(tag, 'type')}"`);

  comprobar('el campo de contraseña trae el botón del ojo', html.includes('aria-pressed'),
    'no salió el botón: quien escribe una mayúscula donde va minúscula no tiene forma de verlo');

  // `aria-pressed` dice si la clave está a la vista AHORA. Sin esto, un lector
  // de pantalla anuncia "botón" y no dice si el campo cambió.
  comprobar('el botón dice si la clave está a la vista', /aria-pressed="false"/.test(html),
    'el botón no dice si la clave está visible ahora; un lector de pantalla no puede anunciar el cambio');
}

{
  const html = renderToStaticMarkup(
    React.createElement(Input, { label: 'Correo', type: 'email', value: 'a@b.mx', onChange: () => {} })
  );
  const tag = inputDe(html);

  comprobar('un campo que no es clave conserva su type', atributo(tag, 'type') === 'email',
    `type sale como "${atributo(tag, 'type')}"`);

  comprobar('un campo que no es clave NO trae el botón del ojo', !html.includes('aria-pressed'),
    'salió el botón del ojo en un campo que no es contraseña');
}

console.log('');
if (fallos > 0) {
  console.log(`${fallos} comprobaciones fallaron.`);
  process.exit(1);
}
console.log('El botón del ojo está bien: el type calculado le gana al del padre.');
