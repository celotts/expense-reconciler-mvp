// Baja por el arbol del grid del tablero hasta el nodo cuyo min-content ESTIRA
// la columna, y lo dice sin adivinar.
//
// POR QUE UN SONDEO PLANO NO ALCANZA
// ===================================
// Dos intentos anteriores_NO_ encontraron al culpable, y los dos fallaron por lo
// mismo: midieron el min-content de los NODOS DE TEXTO, no el de las CAJAS.
// Un nodo de texto parte su contenido en palabras y casi siempre da un
// min-content pequeno. Quien impone el ancho minimo es una CAJA:
//   - un `w-48` (192px) que no cede
//   - un flex/grid item con `min-width:auto` y contenido ancho
//   - un `<select>`, que en Chrome se dimensiona al option mas ancho
//
// Asi que aqui se baja por el subarbol del grid buscando, en cada nivel, el
// nodo con MAYOR min-content. Cuando un padre y un hijo dan casi lo mismo, el
// padre no aporta: se baja. El primer nodo cuyo min-content supera 380 y cuyo
// padre ya no lo supera es la hoja.
//
// LA REGLA QUE HACE QUE ESTO TERMINE
// ===================================
// Se corta en cuanto un padre y su hijo coinciden, mas un limite de profundidad
// (12). Sin los dos, un arbol de 400 nodos se recorre entero. Con ellos, el
// primer nivel donde el valor deja de crecer es el que manda, porque los
// descendientes de una caja ya estan dentro de su ancho.
//
// QUE DEVUELVE, Y COMO SE LEE
// ===========================
// `cadena` son los niveles desde el grid hasta la hoja, con el min-content de
// cada uno. La respuesta esta en el ULTIMO de la cadena: si es un `div.w-48`, el
// arreglo es quitar el `w-48` fijo; si es un `select`, es el ancho del option mas
// largo.

export default async function run(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  if (await page.locator('input[type=password]').count() > 0) {
    await page.locator('input[type=email]').first().fill('prueba@despacho.mx');
    await page.locator('input[type=password]').first().fill('Prueba-2026-mx');
    await page.locator('button[type=submit], form button').first().click();
    await page.waitForTimeout(2500);
  }

  return page.evaluate(() => {
    const desc = el => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      if (el.className && typeof el.className === 'string') {
        s += '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.');
      }
      return s.slice(0, 90);
    };

    const midir = (el) => {
      const caja = document.createElement('div');
      caja.style.cssText = 'position:absolute;left:-99999px;top:0;width:min-content';
      caja.appendChild(el.cloneNode(true));
      document.body.appendChild(caja);
      const w = caja.getBoundingClientRect().width;
      caja.remove();
      return Math.round(w);
    };

    // El grid del tablero es el unico con `lg:grid-cols-5`.
    const grid = document.querySelector('.grid.lg\\:grid-cols-5');
    if (!grid) return { error: 'no se encontro el grid del tablero' };

    const cadena = [];
    const hojas = [];

    // Baja HASTA LAS HOJAS, sin heuristica de parada.
    //
    // Los dos intentos anteriores usaron una regla de parada y las dos pararon
    // antes de tiempo:
    //
    //   1. `delta <= 3` => parar. Paro en el PRIMER nivel: el grid da 403 y su
    //      hijo tambien 403, delta 0, asi que reporto como culpable a
    //      `div.lg:col-span-3`, que solo reenvia lo que viene de abajo.
    //   2. `mejorMC > mc - 3` => seguir. Comparaba el min-content del hijo contra
    //      el del padre SIN descontar el padding del padre. El `Card` tiene `p-5`
    //      (20px por lado), asi que un hijo de 363 da un padre de 403: el padre
    //      es mas ancho que el hijo por sus propios bordes, y la comparacion lo
    //      leia como "el hijo ya no carga nada".
    //
    // Que un padre sea mas ancho que su hijo no dice nada del min-content de los
    // hijos: solo dice que el padre tiene padding. Por eso no se para por
    // comparacion — se baja siempre hasta que no queden hijos, y la profundidad
    // acota el recorrido.
    const bajar = (nodo, nivel) => {
      const mc = midir(nodo);
      const real = Math.round(nodo.getBoundingClientRect().width);
      const registro = {
        nivel,
        sel: desc(nodo),
        minContent: mc,
        real,
        texto: (nodo.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 55),
      };
      // La cadena alta se conserva como contexto; las hojas van aparte.
      if (nivel < 3) cadena.push(registro);
      if (!nodo.children.length) {
        if (mc >= 240) hojas.push(registro);
        return;
      }
      for (const h of nodo.children) bajar(h, nivel + 1);
    };
    bajar(grid, 0);
    hojas.sort((a, b) => b.minContent - a.minContent);

    return {
      gridReal: Math.round(grid.getBoundingClientRect().width),
      scrollWidth: document.documentElement.scrollWidth,
      cadena,
      hojas: hojas.slice(0, 10),
    };
  });
}
